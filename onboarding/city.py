"""Process an authorized city request in isolation, then verify the public Pages deployment."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from build import generate
from evidence import ROOT, SESSION, NeedsResearch, norm, require, resolve
from integrate import integrate

REPO='shoval-c/ambulance-demand-atlas-r9'
BASE='https://shoval-c.github.io/ambulance-demand-atlas-r9/'
REMOTE='https://github.com/'+REPO+'.git'
ALIASES={'tel-aviv':['Tel Aviv','Tel Aviv, Israel','Tel Aviv–Yafo, Israel'],'london':['London','London, UK','London, England','Greater London, UK'],'paris':['Paris','Paris, France'],'new-york':['New York City','New York City, USA','NYC'],'sioux-falls':['Sioux Falls, South Dakota','Sioux Falls, SD'],'arlington':['Arlington, Texas','Arlington, TX']}

def run(*args,cwd=None):
    return subprocess.run(list(map(str,args)),cwd=cwd,text=True,check=True,stdout=subprocess.PIPE).stdout.strip()

def api(path,*args):
    output=run('gh','api','repos/'+REPO+'/'+path,*args)
    return json.loads(output) if output else {}

def listed():
    page=1
    while True:
        items=api(f'issues?state=open&per_page=100&page={page}&sort=created&direction=asc')
        for issue in items:
            if 'pull_request' in issue or not issue['title'].startswith('[City]') or issue['user']['login'].lower()!=REPO.split('/')[0]:continue
            match=re.fullmatch(r'\s*### City and jurisdiction\s*\n+([^\n]{2,180})\s*',issue.get('body') or '')
            if match:yield issue,match[1].strip()
        if len(items)<100:return
        page+=1

def files_for(slug):
    if slug=='tel-aviv':return ['tel-aviv.html','tel-aviv-h3-ambulance-full.csv','ambulance-atlas-csv.zip']
    files=['index.html','cities.js','data/'+slug+'.json','data/'+slug+'.csv','ambulance-atlas-csv.zip','audit/index.html']
    return files

def public_check(root,slug,revision):
    files=files_for(slug)
    if (root/'audit/cities'/f'{slug}.json').exists():files+=['audit/cities/'+slug+'.json']
    pending={name:hashlib.sha256((root/name).read_bytes()).digest() for name in files}
    for attempt in range(40):
        for name in list(pending):
            try:
                url=BASE+name+'?'+urlencode({'rev':revision[:12],'check':attempt})
                with SESSION.get(url,stream=True,timeout=(8,45)) as r:
                    if not r.ok:continue
                    digest=hashlib.sha256()
                    for chunk in r.iter_content(262144):digest.update(chunk)
                    if digest.digest()==pending[name]:del pending[name]
            except Exception as exc:print('Public check:',type(exc).__name__,file=sys.stderr)
        if not pending:return
        time.sleep(8)
    raise RuntimeError('Pages deployment pending or contents differ: '+', '.join(pending))

def job(city,dry,extra_profile=None):
    with tempfile.TemporaryDirectory(prefix='h3-city-') as t:
        stage=Path(t)/'build';checkout=Path(t)/'atlas'
        run('git','clone','--quiet','--depth=1','--branch','main',REMOTE,checkout)
        ref=run('git','rev-parse','HEAD',cwd=checkout)
        existing={norm(v):k for k,aliases in ALIASES.items() for v in aliases}
        for record in (checkout/'audit/cities').glob('*.json'):
            profile=json.loads(record.read_text())['profile']
            for alias in profile['aliases']:existing[norm(alias)]=profile['slug']
        slug=existing.get(norm(city))
        if not slug:
            if extra_profile:
                profile=json.loads(extra_profile.read_text())
                require(norm(city) in [norm(v) for v in profile['aliases']],'Profile does not match requested city')
            else:profile=resolve(city)
            slug=profile['slug']
            if not (checkout/'audit/cities'/f'{slug}.json').exists():
                model,proof=generate(profile,stage)
                if dry:return f"Verified {model['count']} cells and {model['count']*3} CSV rows. Workload {model['annual']:,}."
                integrate(checkout,stage,profile,model,proof)
                (checkout/'onboarding/profiles').mkdir(exist_ok=True)
                if extra_profile: (checkout/'onboarding/profiles'/f'{slug}.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
                run('git','add','--all',cwd=checkout)
                run('git','commit','--quiet','-m','Add verified city '+slug,cwd=checkout)
                run('git','push','origin','HEAD:main',cwd=checkout)
                ref=run('git','rev-parse','HEAD',cwd=checkout)
        if dry:return 'Already published: '+slug
        # A repeated request also verifies a previously pushed build before closing the ticket.
        public_check(checkout,slug,ref)
        page='tel-aviv.html?lang=en' if slug=='tel-aviv' else 'atlas.html?'+urlencode({'city':slug,'lang':'en'})
        csvfile='tel-aviv-h3-ambulance-full.csv' if slug=='tel-aviv' else 'data/'+slug+'.csv'
        return f'[Open the city map]({BASE}{page}) · [Download the full CSV]({BASE}{csvfile})'

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    mode=ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--city');mode.add_argument('--queue',action='store_true');mode.add_argument('--issue',type=int)
    ap.add_argument('--publish',action='store_true');ap.add_argument('--profile',type=Path)
    args=ap.parse_args()
    if args.queue:
        print(json.dumps([dict(number=i['number'],city=c,updated_at=i['updated_at']) for i,c in listed()],ensure_ascii=False));return
    if args.issue:
        match=next(((i,c) for i,c in listed() if i['number']==args.issue),None)
        require(match is not None,'No matching open, owner-submitted city request')
        issue,city=match
    else:city=args.city
    result=job(city,not args.publish,args.profile)
    if args.issue and args.publish:
        # Guard against a request edited while the build/deployment was in progress.
        current=api(f'issues/{issue["number"]}')
        if current['state']=='open' and current.get('body')==issue.get('body'):
            stamp='<!-- h3-atlas-published -->'
            comments=api(f'issues/{issue["number"]}/comments?per_page=100')
            if not any(stamp in (c.get('body') or '') for c in comments):
                api(f'issues/{issue["number"]}/comments','-X','POST','-f','body='+stamp+'\nPublished and verified. '+result)
            api(f'issues/{issue["number"]}','-X','PATCH','-f','state=closed')
    print(result)

if __name__=='__main__':
    try:main()
    except NeedsResearch as e:
        print('NEEDS_RESEARCH:',e,file=sys.stderr);sys.exit(2)
