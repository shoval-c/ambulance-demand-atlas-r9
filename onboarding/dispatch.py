"""Run one explicitly dispatched city build in a fresh GitHub Actions checkout."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from city import ALIASES, BASE, public_check, run
from evidence import ROOT, NeedsResearch, norm, resolve
from build import generate
from integrate import integrate

def note(text):
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:print(text,file=f)

def main():
    city=os.environ['ATLAS_CITY'].strip()
    records=[json.loads(f.read_text()) for f in (ROOT/'audit/cities').glob('*.json')]
    existing={norm(a):slug for slug,aliases in ALIASES.items() for a in aliases}
    existing.update({norm(a):r['profile']['slug'] for r in records for a in r['profile']['aliases']})
    slug=existing.get(norm(city))
    if slug is None:
        profile=resolve(city);slug=profile['slug']
    else:profile=next((r['profile'] for r in records if r['profile']['slug']==slug),None)
    added=False
    if profile is not None:
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp);model,proof=generate(profile,stage)
            if (ROOT/'data'/f'{slug}.csv').exists():
                if (stage/f'{slug}.csv').read_bytes()!=(ROOT/'data'/f'{slug}.csv').read_bytes():
                    raise NeedsResearch('Published city changed. Review the difference before replacing it.')
            else:integrate(ROOT,stage,profile,model,proof);added=True
            note(f"Verified {model['count']:,} H3 R9 cells, {3*model['count']:,} rows and {proof['annual']:,} annual workload records.")
    else:
        note('This is one of the six original studies. The run verifies the existing public artifacts.')
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:print(f'slug={slug}\nadded={str(added).lower()}',file=f)
    if os.environ.get('ATLAS_MODE')=='Preview':note('Preview complete. No publication requested.')

if __name__=='__main__':
    try:main()
    except NeedsResearch as e:
        note('### Source review required\n'+str(e)+'\n\nFor a first-time jurisdiction, submit this same city to the [one-time research runner](../../blob/main/onboarding/RESEARCH.md). It researches primary sources and validates them before publishing.')
        raise SystemExit(2)
