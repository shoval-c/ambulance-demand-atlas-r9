"""Build and verify an R9 city using the public GHSL 100 m population and activity grids."""
import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path

import h3
import numpy as np
import rasterio
import shapely as sh
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.windows import Window,from_bounds
from shapely.geometry import Polygon
from evidence import bi, body, inspect, require
from timing import period_rates

PERIODS=['weekday_day','weekday_night','shabbat']
SHARES=np.array([[.45,.55],[.85,.15],[.75,.25]])
PERIOD_NOTE=bi('No observed hourly city series is used. The same annual average hourly rate is used for all periods. Residents and non-residential built surface change each cell’s share across periods.','אין במודל סדרת שיגורים עירונית לפי שעה. לכל התקופות משמש אותו ממוצע עירוני שנתי לשעה. האוכלוסייה והשטח הבנוי שאינו למגורים משנים את חלקו של כל משושה.')
GRID_NOTE=bi('City polygons clip the H3 grid. The European Commission’s GHS-POP and non-residential GHS-BUILT-S, 2020 epoch, are sampled at four subpixels per 100 m pixel. Edge cells with no sampled exposure receive zero modeled demand.','משושי H3 נחתכים בגבול העיר. נתוני האוכלוסייה והשטח הבנוי שאינו למגורים של הנציבות האירופית לשנת 2020 נדגמים בארבע נקודות לכל פיקסל של 100 מטר. למשושי שוליים ללא חשיפה שנדגמה משויך ביקוש אפס.')
LABELS={'pop':bi('Estimated residents (GHSL 2020)','אומדן תושבים (GHSL 2020)'),'nonres':bi('Non-residential built surface (m², 2020)','שטח בנוי שאינו למגורים (מ״ר, 2020)')}
GRID_URLS=['https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php','https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php']

def project(geometry,src,dst):
    return sh.transform(geometry,Transformer.from_crs(src,dst,always_xy=True).transform,interleaved=False)

def parts(g):
    if g.geom_type=='Polygon':yield g
    elif hasattr(g,'geoms'):
        for child in g.geoms:yield from parts(child)

def grid(geom):
    centre=geom.representative_point()
    crs=f'+proj=laea +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m'
    region=sh.union_all(list(parts(sh.make_valid(project(geom,4326,crs)))))
    buffered=project(region.buffer(500),crs,4326)
    ids=sorted(h3.geo_to_cells(buffered.__geo_interface__,9))
    require(ids,'Empty city grid')
    full=project(np.asarray([Polygon([(x,y) for y,x in h3.cell_to_boundary(h)]) for h in ids],dtype=object),4326,crs)
    clipped=sh.intersection(full,region)
    keep=sh.area(clipped)>=1
    require(abs(sh.area(clipped[keep]).sum()-region.area)<max(10,region.area*.0001),'H3 coverage did not reproduce the city area')
    return np.array(ids)[keep].tolist(),clipped[keep],sh.area(full[keep]),region,crs

def exposure(layer,polys,crs):
    base='https://jrc-ghsl.s3.amazonaws.com/'
    prefix=layer+'/r2023a/54009/100m/2020/'
    listing=ET.fromstring(body(base+'?list-type=2&max-keys=10&prefix='+prefix))
    keys=[k.text for k in listing.findall('.//{*}Key') if k.text.endswith('.tif')]
    require(len(keys)==1,'GHSL raster not uniquely identified: '+layer)
    url=base+keys[0]
    result=np.zeros(len(polys)+1)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',GDAL_HTTP_TIMEOUT='45',GDAL_HTTP_MAX_RETRY='3'),rasterio.open('/vsicurl/'+url) as src:
        geoms=project(polys,crs,src.crs)
        tree=sh.STRtree(geoms)
        bounds=sh.total_bounds(geoms)
        w=from_bounds(*bounds,src.transform)
        x0,y0=math.floor(w.col_off),math.floor(w.row_off)
        for y in range(y0,math.ceil(w.row_off+w.height),512):
            for x in range(x0,math.ceil(w.col_off+w.width),512):
                window=Window(x,y,min(512,math.ceil(w.col_off+w.width)-x),min(512,math.ceil(w.row_off+w.height)-y))
                ix=sorted(tree.query(sh.box(*src.window_bounds(window)),predicate='intersects'))
                if not ix:continue
                arr=src.read(1,window=window,masked=True).filled(0).clip(min=0)
                tf=src.window_transform(window)*rasterio.Affine.scale(.5)
                labels=rasterize([(geoms[i],int(i)+1) for i in ix],out_shape=(2*arr.shape[0],2*arr.shape[1]),transform=tf,dtype='int32')
                for dy in (0,1):
                    for dx in (0,1):
                        result+=np.bincount(labels[dy::2,dx::2].ravel(),weights=arr.ravel()*.25,minlength=len(result))
    require(result[1:].sum()>0,'No usable public raster signal for '+layer)
    return result[1:],url

def blocking(load,n):
    b=1.
    for j in range(1,n+1):b=load*b/(j+load*b)
    return b

def generate(profile,dest):
    dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
    region,proof=inspect(profile)
    rates=period_rates(profile,region,proof)
    timing=profile.get("timing")
    note=timing["note"] if timing else PERIOD_NOTE
    ids,polys,areas,region,crs=grid(region)
    pop,pop_url=exposure('ghs-pop',polys,crs)
    built,built_url=exposure('ghs-built-s-nres',polys,crs)
    raw=np.stack((pop,built),axis=1)
    proof['raster_sources']=[pop_url,built_url]
    proof['raster_city_totals']=raw.sum(axis=0).tolist()
    contributions=SHARES[:,None,:]*(raw/raw.sum(axis=0))*rates[:,None,None]
    demand=contributions.sum(axis=2)
    require(np.allclose(demand.sum(axis=1),rates),'Workload totals did not reconcile')
    ranks=[]
    for values in np.round(demand,12):ranks.append((1+len(ids)-np.searchsorted(np.sort(values),values,side='right')).tolist())
    x0,y0,x1,y1=region.bounds
    scale=min(720/(x1-x0),560/(y1-y0))
    def xy(x,y):return [round(380+(x-(x0+x1)/2)*scale,3),round(300-(y-(y0+y1)/2)*scale,3)]
    def path(poly):
        rings=[]
        for p in parts(poly.simplify(2)):
            for ring in (p.exterior,*p.interiors):
                rings.append('M'+'L'.join(','.join(map(str,xy(x,y))) for x,y in list(ring.coords)[:-1])+'Z')
        return ''.join(rings)
    idx={v:k for k,v in enumerate(ids)}
    slug=profile['slug']; city=profile['city']; w=profile['workload']; policy=profile['standard']
    year=w['start']+' – '+str(date.fromordinal(date.fromisoformat(w['end']).toordinal()-1))
    url_sources=[dict(title=profile[k]['title'],url=profile[k]['url']) for k in ('workload','boundary','standard')]
    if timing:url_sources.append(dict(title=timing['title'],url=timing['url']))
    url_sources.extend(dict(title=LABELS[key]['en'],url=url) for key,url in zip(('pop','nonres'),GRID_URLS))
    method=bi(profile['scope']['en']+'. '+policy['finding']['en'],profile['scope']['he']+'. '+policy['finding']['he'])
    fields='city h3_r9 local_area period city_rank shared_ambulances busiest_catchment_period estimated_cell_workload_per_hour estimated_catchment_workload_per_hour city_workload_per_hour prob_all_busy prob_all_busy_one_fewer cell_inside_city_pct center_lat center_lon catchment_h3_ids annual_source_count annual_source_year annual_source_unit assumed_busy_minutes target_availability_pct pop nonres pop_contribution_per_hour nonres_contribution_per_hour'.split()
    cells=[]; histogram=Counter()
    with (dest/(slug+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(fields)
        for i,(cell,g) in enumerate(zip(ids,polys)):
            ring=sorted(set(h3.grid_disk(cell,1))&idx.keys())
            local=demand[:,[idx[h] for h in ring]].sum(axis=1)
            peak=int(local.argmax());load=local[peak];n=1
            while blocking(load,n)>.05:n+=1
            busy,less=blocking(load,n),blocking(load,n-1)
            require(busy<=.05 and (n==1 or less>.05),'Nonminimal coverage estimate')
            pos=g.representative_point();name=city
            signal=dict(zip(LABELS,raw[i].round(4)))
            cellpath=path(g)
            require(cellpath and h3.get_resolution(cell)==9,'Invalid clipped R9 cell')
            cells.append(dict(id=cell,path=cellpath,xy=xy(pos.x,pos.y),name=name,n=n,rate=demand[:,i].round(10).tolist(),local=local.round(10).tolist(),rank=[r[i] for r in ranks],raw=signal,points=[dict(zip(LABELS,val.round(10))) for val in contributions[:,i]],peak=peak,ring=ring,busy=round(busy,10),less=round(less,10)))
            histogram[n]+=1
            for j,period in enumerate(PERIODS):
                writer.writerow([city['en'],cell,name['en'],period,ranks[j][i],n,PERIODS[peak] if load else 'all_equal_no_signal',demand[j,i],local[j],rates[j],busy,less,round(100*g.area/areas[i],3),*h3.cell_to_latlng(cell),'|'.join(ring),proof['annual'],year,w['unit']['en'],60,95,*raw[i],*contributions[j,i]])
    model=dict(slug=slug,city=city,count=len(ids),annual=proof['annual'],year=year,unit=w['unit'],method=method,time_note=note,grid_note=GRID_NOTE,sources=url_sources,field_labels=LABELS,weights=[dict(zip(LABELS,np.round(ws*100,3))) for ws in SHARES],period_rates=rates.tolist(),contours=[],km_px=1000*scale,cells=cells,histogram=histogram)
    (dest/(slug+'.json')).write_text(json.dumps(model,ensure_ascii=False,allow_nan=False,separators=(',',':')))
    with (dest/(slug+'.csv')).open(encoding='utf-8-sig',newline='') as f:
        records=list(csv.DictReader(f))
    require(len(records)==3*len(ids),'Incomplete city CSV')
    for j,period in enumerate(PERIODS):
        rows=[r for r in records if r['period']==period]
        require({r['h3_r9'] for r in rows}==set(ids) and [int(r['city_rank']) for r in rows]==ranks[j],'CSV cell or rank mismatch')
    return model,proof
