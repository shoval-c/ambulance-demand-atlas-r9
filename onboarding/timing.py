"""Integrate a verified hour-of-week profile over the actual study calendar."""
import csv
import io
from datetime import datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
import numpy as np
from astral import Observer
from astral.sun import dusk,sunset
from evidence import body,require

def period_rates(profile,geom,proof):
    rate=proof['annual']/(24*proof['days'])
    if 'timing' not in profile:return np.full(3,rate)
    t=profile['timing']
    rows=csv.DictReader(io.StringIO(body(t['url']).decode('utf-8-sig')))
    hourly={}
    for row in rows:
        if not all(row[k]==str(v) for k,v in t['match'].items()):continue
        key=(int(row[t['weekday_field']]),int(row[t['hour_field']]))
        require(key not in hourly,'Duplicate weekday and hour')
        hourly[key]=float(row[t['value_field']])
    require(set(hourly)=={(day,h) for day in range(7) for h in range(24)} and all(np.isfinite(x) and x>=0 for x in hourly.values()) and sum(hourly.values())>0,'Expected 168 valid hour-of-week values')
    tz=ZoneInfo(t['timezone']); p=geom.representative_point();observer=Observer(p.y,p.x)
    start,end=[datetime.fromisoformat(profile['workload'][k]).replace(tzinfo=tz).astimezone(timezone.utc) for k in ('start','end')]
    sums=np.zeros(3);hours=np.zeros(3);weight=0;cuts={}
    for n in range(int((end-start).total_seconds()//3600)):
        now=start+n*timedelta(hours=1); local=now.astimezone(tz);d=local.date();day=local.weekday();hour=local.hour
        val=hourly[(day,hour)]
        shab=0
        if day in (4,5):
            if d not in cuts:cuts[d]=(sunset(observer,d,tz) if day==4 else dusk(observer,d,tzinfo=tz)).astimezone(timezone.utc)
            portion=np.clip((cuts[d]-now).total_seconds()/3600,0,1)
            shab=1-portion if day==4 else portion
        daypart=day<5 and 6<=hour<19
        night=(day<5 and hour>=19) or ((day-1)%7<5 and hour<6)
        frac=np.array([daypart*(1-shab),night*(1-shab),shab])
        hours+=frac;sums+=val*frac;weight+=val
    require(min(hours)>0 and weight>0,'Incomplete local period definition')
    return proof['annual']*sums/weight/hours
