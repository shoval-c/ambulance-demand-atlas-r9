"""Run from a checkout: python -m pip install h3==4.1.2 requests==2.34.1 pypdf==7.1.1; python audit/verify.py
Writes audit/results.json. Exit 1 means a review is needed; declared data gaps are reported separately.
"""
import csv
import gzip
import hashlib
import io
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import h3
import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.getLogger('pypdf').setLevel(logging.ERROR)
HERE = Path(__file__).resolve().parent
REGISTRY = json.loads((HERE / 'sources.json').read_text())
PERIODS = {'weekday_day', 'weekday_night', 'shabbat'}
EXPECTED_ROLES = {'standard', 'boundary', 'residents', 'activity', 'workload', 'timing'}

def normalize(raw, compact=False):
    raw = ' '.join(unescape(raw).lower().split())
    return re.sub(r'[\s,\u202f]', '', raw) if compact else raw

def html_text(raw):
    raw = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', raw, flags=re.I|re.S)
    return normalize(re.sub(r'<[^>]*>', ' ', raw))

def fetch(session, url, **options):
    response = session.get(url, timeout=(10,35), **options)
    response.raise_for_status()
    return response

def pdf_text(blob, pages=None):
    pdf = PdfReader(io.BytesIO(blob))
    return ' '.join(pdf.pages[i].extract_text() or '' for i in (pages if pages is not None else range(len(pdf.pages))))

def monthly_count(session, url):
    page = fetch(session,url).text
    links = {urljoin(url, p) for p in re.findall(r'href="([^"]*2025[^"]*\.pdf)"',page,re.I) if '2025-' in p.lower() and 'annual' not in p.lower()}
    months = set()
    total = 0
    for link in sorted(links):
        m = re.search(r'/(\d{2})-2025-',link)
        if not m or int(m[1]) in months:raise ValueError('Unexpected or duplicate monthly report')
        months.add(int(m[1]))
        content = pdf_text(fetch(session,link).content)
        matches = re.findall(r'Priority\s*([123])\s*Compliance\s*:[^:]*?Total\s*Calls\s*:\s*([\d,]+)',content,re.I)
        if sorted(k for k,_ in matches)!=['1','2','3']:raise ValueError('Monthly table unreadable: '+link)
        total+=sum(int(v.replace(',','')) for _,v in matches)
    if months!=set(range(1,13)):raise ValueError('Incomplete monthly series')
    return total

def inspect_source(r):
    outcome = {'city':r['city'],'role':r['role']}
    if r['kind']=='gap':return dict(outcome,status='data_gap',detail='No observed local EMS time profile in this study.')
    try:
        with requests.Session() as session:
            session.mount('https://',HTTPAdapter(max_retries=Retry(total=1,backoff_factor=.5,status_forcelist=[429,502,503,504],respect_retry_after_header=False)))
            url=r.get('probe',r['url'])
            kind=r['kind']
            if kind=='monthly':
                count=monthly_count(session,url)
            elif kind=='gzip_csv':
                with fetch(session,url,stream=True) as response:
                    field_names=gzip.GzipFile(fileobj=response.raw).readline(2048).decode()
                content=field_names
            elif kind=='arc':
                data=fetch(session,url,params={'f':'json'}).json()
                if data.get('type') not in {'Table','Feature Layer'} or not data.get('fields'):raise ValueError('Expected ArcGIS feature layer and fields')
                content=json.dumps([data['name'],[f['name'] for f in data['fields']]],ensure_ascii=False)
            else:
                response=fetch(session,url)
                if kind=='count':count=int(response.json()[0]['n'])
                elif kind=='json':content=json.dumps(response.json()[r['json_key']],ensure_ascii=False)
                elif kind=='pdf':content=pdf_text(response.content,r.get('pages'))
                elif kind=='html':content=html_text(response.text)
                else:raise ValueError('Unknown source format')
            if kind in ('monthly','count'):
                if count!=r['expected']:raise ValueError(f"Expected {r['expected']}, received {count}")
                return dict(outcome,status='passed',detail=f'Reproduced {count:,} for the named study year.')
            compact=r.get('compact',False)
            norm=normalize(content,compact)
            missing=[m for m in r['markers'] if normalize(m,compact) not in norm]
            if missing:raise ValueError('Content missing: '+', '.join(missing))
            if r['role']=='standard':
                # Full documents are conservative fingerprints. Even an unrelated edit can prompt a review.
                digest=hashlib.sha256(normalize(content).encode()).hexdigest()
                outcome['document_sha256']=digest
                if r.get('reviewed_sha256') and digest!=r['reviewed_sha256']:raise ValueError('Reviewed policy document changed')
            return dict(outcome,status='passed',detail='Source content and configured evidence markers matched.')
    except Exception as exc:
        return dict(outcome,status='review',detail=str(exc)[:300])

def inspect_grid(city):
    path=(HERE.parent/'tel-aviv-h3-ambulance-full.csv') if city=='tel-aviv' else HERE.parent/'data'/f'{city}.csv'
    ranks={p:{} for p in PERIODS}
    with path.open(encoding='utf-8-sig',newline='') as fp:
        for row in csv.DictReader(fp):
            cell=row['h3_r9']; period=row['period']; rank=int(row.get('city_rank') or row['rank'])
            if not h3.is_valid_cell(cell) or h3.get_resolution(cell)!=9 or cell in ranks[period]:raise ValueError(f'Invalid or duplicate H3 cell {cell}')
            ranks[period][cell]=rank
    ids=set(next(iter(ranks.values())))
    if not ids or any(set(values)!=ids or min(values.values())!=1 or max(values.values())>len(ids) for values in ranks.values()):raise ValueError('Incomplete period coverage or ranks')
    if not EXPECTED_ROLES <= {r['role'] for r in REGISTRY if r['city']==city}:raise ValueError('Incomplete source inventory')
    return len(ids)

if __name__=='__main__':
    cities=sorted({r['city'] for r in REGISTRY})
    grids={city:inspect_grid(city) for city in cities}
    with ThreadPoolExecutor(max_workers=8) as pool:
        checks=list(pool.map(inspect_source,REGISTRY))
    result={'checked_utc':datetime.now(timezone.utc).isoformat(),'h3_r9_cells':grids,'checks':checks}
    (HERE/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for c in checks:print(c['city'],c['role'],c['status'],c['detail'],flush=True)
    sys.exit(any(c['status']=='review' for c in checks))
