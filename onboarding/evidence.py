"""Resolve a city against reviewed public sources. Only maintainers create profiles."""
import csv
import hashlib
import io
import json
import logging
import re
from datetime import date
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
SESSION = requests.Session()
SESSION.mount('https://', HTTPAdapter(max_retries=Retry(total=2,backoff_factor=1,status_forcelist=[429,502,503,504],respect_retry_after_header=False)))
logging.getLogger('pypdf').setLevel(logging.ERROR)
VIC_REPORT = 'https://www.ambulance.vic.gov.au/sites/default/files/2026-09/2025-26-Q4-Ambulance-Response-Quarter-4-FY2025-26.pdf'
VIC_BOUNDARY = 'https://geo.abs.gov.au/arcgis/rest/services/ASGS2025/LGA/MapServer/0/query'
VIC_POLICY = 'https://www.ambulance.vic.gov.au/our-performance'

class NeedsResearch(ValueError):
    """Insufficient evidence to publish this scope."""

def require(condition, message):
    if not condition:
        raise NeedsResearch(message)

def bi(en, he):return {'en':en, 'he':he}

@lru_cache(maxsize=24)
def body(url):
    parts=urlsplit(url)
    require(parts.scheme=='https' and parts.hostname and not parts.username,'Expected a public HTTPS source')
    response=SESSION.get(url,timeout=(15,90)); response.raise_for_status()
    return response.content

def norm(text):
    return ' '.join(unescape(text).casefold().split())

def read_text(source):
    content=body(source['url'])
    if source['format']=='pdf':
        pages=PdfReader(io.BytesIO(content)).pages
        return '\n'.join(pages[i-1].extract_text(extraction_mode='layout') for i in source['pages'])
    if source['format']=='html':
        text=content.decode('utf-8','replace')
        text=re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', text,flags=re.I|re.S)
        return re.sub('<[^>]+>',' ',text)
    raise NeedsResearch('Unsupported document format')

def verify_passages(source):
    text=read_text(source)
    require(source.get('markers') and all(norm(s) in norm(text) for s in source['markers']), 'Published evidence changed: '+source['url'])
    return text

def resolve(request):
    raw=request.strip()
    require(2<=len(raw)<=180 and '\n' not in raw,'Enter a single city and its jurisdiction.')
    for file in sorted((ROOT/'onboarding/profiles').glob('*.json')):
        profile=json.loads(file.read_text())
        if norm(raw) in [norm(x) for x in profile['aliases']]:
            return profile
    parts=[p.strip() for p in raw.split(',')]
    if len(parts)>1 and [p.lower() for p in parts[1:]] in (['victoria'],['vic'],['victoria','australia'],['vic','australia']):
        name=re.sub(r'^city of\s+','',parts[0],flags=re.I).strip()
        require(re.fullmatch(r"[A-Za-z][A-Za-z .'-]{0,65}",name),'Unrecognized Victorian council name')
        return victoria(name)
    raise NeedsResearch('Research required for this city and jurisdiction. Find its official boundary, city-level EMS workload and applicable service standard, then add a reproducible profile.')

def victoria(name):
    def query(where, geometry):
        return VIC_BOUNDARY+'?'+urlencode(dict(where=where,outFields='LGA_CODE_2025,LGA_NAME_2025,STATE_NAME_2021',returnGeometry=geometry,outSR=4326,f='geojson'))
    url=query("STATE_NAME_2021 = 'Victoria' AND UPPER(LGA_NAME_2025) = '"+name.upper().replace("'","''")+"'",'true')
    features=json.loads(body(url)).get('features',[])
    require(len(features)==1,'No unique Victorian LGA matched this name. Specify the council boundary.')
    name=features[0]['properties']['LGA_NAME_2025']
    slug='au-vic-'+features[0]['properties']['LGA_CODE_2025']
    he='מלבורן' if name=='Melbourne' else name
    performance=body(VIC_POLICY).decode('utf-8')
    reports=re.findall(r'href=["\']([^"\']+\.pdf)["\']',performance,re.I)
    dated=[]
    for href in reports:
        match=re.search(r'(20\d\d)-(\d\d)-Q([1-4])',href,re.I)
        if match:
            link=urljoin(VIC_POLICY,unescape(href))
            if urlsplit(link).hostname=='www.ambulance.vic.gov.au':dated.append((int(match[1]),int(match[3]),link))
    require(dated,'No official quarterly reports found on the service website')
    latest=max(dated)[2]
    return dict(slug=slug,aliases=[name+', Victoria, Australia'],city=bi(name+' (city council)',he+' (רשות מקומית)'),scope=bi('Victorian local government area of '+name,'תחום הרשות המקומית '+he),
        boundary=dict(title='Australian Bureau of Statistics · 2025 local government areas',url=url,field='LGA_NAME_2025',value=name),
        workload=dict(title='Ambulance Victoria · FY2025/26 Q4, LGA Code 1 and Code 2 tables',url=VIC_REPORT,format='victoria',name=name,start='2025-07-01',end='2026-07-01',unit=bi('Code 1 and Code 2 incidents included in response reporting','אירועים בדרגות 1 ו־2 שנכללו בדיווח זמני התגובה')),
        standard=dict(title='Ambulance Victoria · official response targets in latest quarterly report, p. 1',url=latest,format='pdf',pages=[1],markers=['within 15 minutes for 85% of incidents state-wide','within 15 minutes for 90% of incidents in centres with populations greater than 7,500','first AV resource arrives on scene'],finding=bi('Ambulance Victoria targets a first response to Code 1 within 15 minutes for 85% statewide and 90% in centres above 7,500 residents. The clock runs from receipt of the 000 call to arrival of the first AV resource.','יעד השירות לקריאות בדרגה 1 הוא הגעת המשאב הראשון בתוך 15 דקות ב־85% מהמקרים במדינה וב־90% במרכזים עם יותר מ־7,500 תושבים. המדידה מתחילה בקבלת שיחת 000.'),reviewed='2026-11-18',jurisdiction='Victoria, Australia',authority_type='Published service performance target'))

def vic_count(w):
    pdf=PdfReader(io.BytesIO(body(w['url'])))
    values=[]
    for i,code in [(4,1),(9,2)]:
        text=pdf.pages[i].extract_text(extraction_mode='layout')
        header=norm(text.split('Alpine')[0])
        require('lga name' in header and all(f'qtr {q} 2025/26' in header for q in range(1,5)), 'Unexpected LGA report geography or year')
        group=(r'\s+[\d.]+%' if code==1 else '')+r'\s+\d+:\d{2}\s+([\d,]+)'
        matches=re.findall(r'^\s*'+re.escape(w['name'])+group*5+r'\s*$',text,re.M|re.I)
        require(len(matches)==1,'No unique five-quarter row for '+w['name'])
        values.extend(int(x.replace(',','')) for x in matches[0][1:])
    return sum(values), values

def workload_count(w):
    if w['format']=='victoria':return vic_count(w)
    if w['format']=='csv':
        rows=csv.DictReader(io.StringIO(body(w['url']).decode('utf-8-sig')))
        require(bool(w.get('match')),'Missing workload geography and period filter')
        matched=[r for r in rows if all(r[k]==str(v) for k,v in w['match'].items())]
        require(len(matched)==1,'Expected one matching workload row')
        values=[int(matched[0][k].replace(',','')) for k in w['count_fields']]
    elif w['format'] in ('html','pdf'):
        text=verify_passages(w)
        hits=re.findall(w['count_regex'],text,re.M|re.I)
        require(len(hits)==1 and isinstance(hits[0],str),'Expected one count captured in its city/year context')
        values=[int(hits[0].replace(',','').replace(' ',''))]
    elif w['format']=='json':
        records=json.loads(body(w['url']))
        for key in w.get('record_path',[]):records=records[key]
        require(isinstance(records,list) and len(records)==1,'Expected exactly one filtered city/year count')
        values=[int(records[0][w['count_field']])]
    else:raise NeedsResearch('Add an extractor for this workload format.')
    require(values and min(values)>=0,'Invalid component counts')
    return sum(values),values

def inspect(p):
    require(re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',p['slug']),'Invalid city identifier')
    b,w,s=(p[k] for k in ('boundary','workload','standard'))
    features=json.loads(body(b['url']))['features']
    chosen=[f for f in features if str(f['properties'].get(b['field']))==str(b['value'])]
    require(len(chosen)==1,'Boundary must match exactly one named city or official city identifier')
    geom=shape(chosen[0]['geometry'])
    require(geom.geom_type in ('Polygon','MultiPolygon') and not geom.is_empty,'Invalid city polygon')
    total,components=workload_count(w)
    start,end=map(date.fromisoformat,(w['start'],w['end']))
    require((end-start).days in (365,366) and end<=date.today() and total>0,'Expected a complete, observed year and positive workload')
    verify_passages(s)
    reviewed=date.fromisoformat(s['reviewed'])
    require(0<=(date.today()-reviewed).days<=365,'Review the local standard against its current official publication')
    require(s.get('jurisdiction') and s.get('authority_type') and all(s['finding'].get(k) for k in ('en','he')),'Policy scope, authority and bilingual interpretation required')
    evidence={'annual':total,'components':components,'days':(end-start).days,'verified_on':date.today().isoformat(),
              'source_sha256':{k:hashlib.sha256(body(p[k]['url'])).hexdigest() for k in ('boundary','workload','standard')}}
    return geom,evidence
