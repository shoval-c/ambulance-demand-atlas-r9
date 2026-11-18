"""Compare rebuilt data across platforms while preserving IDs, categories and integer results."""
import csv
import math
from itertools import zip_longest

EXACT=set('city h3_r9 local_area period city_rank shared_ambulances busiest_catchment_period catchment_h3_ids annual_source_count annual_source_year annual_source_unit assumed_busy_minutes target_availability_pct'.split())

def same_csv(left,right):
    with left.open(encoding='utf-8-sig',newline='') as a,right.open(encoding='utf-8-sig',newline='') as b:
        reader,other=csv.DictReader(a),csv.DictReader(b)
        if reader.fieldnames!=other.fieldnames:return False
        for r,s in zip_longest(reader,other):
            if r is None or s is None:return False
            for k in r:
                if r[k]==s[k]:continue
                if k in EXACT or not math.isclose(float(r[k]),float(s[k]),rel_tol=1e-9,abs_tol=1e-8):return False
    return True
