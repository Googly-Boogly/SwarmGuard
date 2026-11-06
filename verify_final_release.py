"""Non-destructive verification. Skipped external checks never count as passed."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    checks=[]
    for name,command in [('core_research_report_export',[sys.executable,'demo_final.py']),('separated_security',[sys.executable,'verify_stage7.py'])]:
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=180)
            checks.append({'check':name,'status':'PASS' if result.returncode==0 else 'FAIL','returncode':result.returncode,'output':result.stdout[-2000:]+result.stderr[-1000:]})
        except (OSError,subprocess.TimeoutExpired) as exc:
            checks.append({'check':name,'status':'FAIL','reason':str(exc)})
    required=['README.md','CITATION.cff','CONTRIBUTING.md','SECURITY.md','docs/FINAL_ARCHITECTURE.md','docs/FINAL_RELEASE_REPORT.md']
    checks.append({'check':'documentation','status':'PASS' if all(Path(p).is_file() for p in required) else 'FAIL'})
    checks.append({'check':'license','status':'PASS' if Path('LICENSE').is_file() else 'FAIL','reason':'Owner licensing decision required if absent'})
    for name in ('full_backend_suite','frontend','browser','Docker','PostgreSQL','live_API'):
        checks.append({'check':name,'status':'UNVERIFIED','reason':'Separate command evidence required; this script does not infer it'})
    report={'checks':checks,'release_readiness':'NOT_READY_FOR_RESEARCH_RELEASE' if any(c['status']!='PASS' for c in checks) else 'READY_FOR_RESEARCH_RELEASE'}
    print(json.dumps(report,indent=2))
    if args.output:
        with args.output.open('x') as handle: json.dump(report,handle,indent=2)
    return 0 if report['release_readiness']=='READY_FOR_RESEARCH_RELEASE' else 2
if __name__=='__main__': raise SystemExit(main())
