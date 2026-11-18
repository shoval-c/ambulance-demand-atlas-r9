"""Integrate a verified build in a clean staging checkout before a single Git commit."""
import csv
import io
import json
import re
import shutil
import zipfile
from html import escape
from pathlib import Path
from evidence import require
from build import GRID_NOTE,PERIOD_NOTE,GRID_URLS

def read_json(path):return json.loads(path.read_text())
def dump(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def insert(path,lang,html):
    start=f'<!-- new-city-{lang} -->';end=f'<!-- /new-city-{lang} -->'
    s=path.read_text();require(s.count(start)==s.count(end)==1,'Missing city template slot in '+str(path))
    i=s.index(end);path.write_text(s[:i]+html+s[i:])

def integrate(root,stage,p,m,proof):
    slug=p['slug']; d=root/'data';audit=root/'audit'
    timing=p.get('timing');period_note=m['time_note']
    report_dir=audit/'cities';report_dir.mkdir(exist_ok=True)
    registry=root/'cities.js'
    cities=json.loads(registry.read_text().split(' = ',1)[1].strip().removesuffix(';'))
    require(slug not in cities,'City already exists. Reuse its existing public link.')
    require(not any((d/(slug+ext)).exists() for ext in ('.csv','.json')),'A city file already exists')
    cities[slug]=[p['city']['en'],p['city']['he']]
    registry.write_text('window.atlasCities = '+json.dumps(cities,ensure_ascii=False)+';\n')
    for ext in ('.csv','.json'):shutil.copyfile(stage/(slug+ext),d/(slug+ext))
    audit_file=report_dir/(slug+'.json')
    dump(audit_file,{'profile':p,'verification':proof,'h3_resolution':9,'cells':m['count'],'rows':3*m['count'],'time_assumption':period_note,'grid_method':GRID_NOTE})
    lo,hi=min(m['histogram']),max(m['histogram'])
    for lang in ('en','he'):
        en=lang=='en'
        name=escape(p['city'][lang]);unit=escape(m['unit'][lang]);finding=escape(p['standard']['finding'][lang])
        link=f'<a href="atlas.html?city={slug}&amp;lang={lang}">{name}</a>'
        row='<tr>'+''.join('<td>'+v+'</td>' for v in (link,escape(m['year']),unit,f"{m['annual']:,}",f"{m['count']:,}",f'{lo}–{hi}'))+'</tr>'
        for file in ('atlas.html','index.html'):insert(root/file,lang,row)
        insert(root/'tel-aviv.html',lang,' '+link)
        section=f'<section><h2>{name}</h2><p>{finding}</p><p>{m["annual"]:,} {unit} · {m["year"]}. {escape(period_note[lang])}</p>'
        section+='<ul>'+''.join('<li><a href="'+escape(v['url'],quote=True)+'">'+escape(v['title'])+'</a></li>' for v in m['sources'])+'</ul>'
        section+=f'<a href="cities/{slug}.json">'+('Verification record' if en else 'רשומת אימות')+'</a></section>'
        insert(audit/'index.html',lang,section)
    sourcefile=audit/'source-audit.csv'
    findings={'boundary':p['boundary'],'workload':p['workload'],'standard':p['standard'],
              'residents':dict(title='European Commission JRC · GHS-POP 2020',url=GRID_URLS[0]),
              'activity':dict(title='European Commission JRC · GHS-BUILT-S non-residential 2020',url=GRID_URLS[1]),'timing':timing or dict(title='Equal citywide hourly rate assumed',url='')}
    with sourcefile.open(encoding='utf-8-sig',newline='') as f:fields=csv.DictReader(f).fieldnames
    result_path=audit/'results.json'
    results=read_json(result_path)
    results['h3_r9_cells'][slug]=m['count']
    with sourcefile.open('a',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fields)
        for role,s in findings.items():
            status='data_gap' if role=='timing' and not timing else 'passed'
            detail={'boundary':'Matched a unique official feature.','workload':f"Reproduced {m['annual']:,} in the study year.",'standard':'Reviewed jurisdiction and service target. Matched evidence passages in the official document.','residents':'Sampled public GHSL population raster inside the boundary.','activity':'Sampled public GHSL non-residential raster inside the boundary.','timing':period_note['en']}[role]
            url=s['url'];probe=proof['raster_sources'][0 if role=='residents' else 1] if role in ('residents','activity') else url
            results['checks'].append(dict(city=slug,role=role,status=status,detail=detail,checked_on=proof['verified_on']))
            w.writerow(dict(city=slug,role=role,source=s['title'],source_url=url,probe_url=probe,check_type='onboarding',automated_result=status,automated_detail=detail,last_run_utc=proof['verified_on'],policy_review_date=s.get('reviewed',''),policy_finding_en=s.get('finding',{}).get('en',''),policy_finding_he=s.get('finding',{}).get('he',''),coverage_en=p['scope']['en'],coverage_he=p['scope']['he']))
    dump(result_path,results)
    archive=root/'ambulance-atlas-csv.zip';replacement=stage/'all.zip'
    with zipfile.ZipFile(archive) as old,zipfile.ZipFile(replacement,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as new:
        summary=io.StringIO(old.read('city-summary.csv').decode('utf-8-sig'),newline='');summary.seek(0,2)
        csv.writer(summary).writerow([slug,m['city']['en'],m['year'],m['unit']['en'],m['annual'],m['count'],lo,hi,m['unit']['he']])
        for item in old.infolist():
            if item.filename not in ('city-summary.csv','source-audit.csv','README.txt'):new.writestr(item,old.read(item))
        new.writestr('README.txt',old.read('README.txt').decode()+'\n'+m['city']['en']+'\n'+p['scope']['en']+'\n'+period_note['en']+'\n'+p['standard']['finding']['en']+'\n'+'\n'.join(v['url'] for v in m['sources'])+'\n')
        new.writestr('city-summary.csv','\ufeff'+summary.getvalue())
        new.write(d/(slug+'.csv'),slug+'.csv');new.write(audit_file,'audit/'+slug+'.json');new.write(sourcefile,'source-audit.csv')
    with zipfile.ZipFile(replacement) as z:
        require(z.testzip() is None and z.read(slug+'.csv')==(stage/(slug+'.csv')).read_bytes(),'Archive verification failed')
    shutil.move(replacement,archive)
