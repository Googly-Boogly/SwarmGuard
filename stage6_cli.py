"""Entry-point environment, exclusive exports, and trusted local researcher commands."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from swarmguard.clock import SystemClock
from swarmguard.evaluation.framework_comparison import ApplicationConfig,run_framework_experiment
from swarmguard.integrations.registry import available_frameworks,framework_info
from swarmguard.regression.baseline import BaselineManager
from swarmguard.regression.runner import run_regression
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository
from stage5_cli import revision


class ConfigParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def parser(kind: str) -> argparse.ArgumentParser:
    p=ConfigParser(description=f'Stage 6 {kind}; actual frameworks, synthetic tools, no paid models')
    p.add_argument('--framework',choices=['custom','crewai','langgraph','autogen','all'],default='custom' if kind!='framework_evaluation' else 'all')
    p.add_argument('--provider',choices=['mock'],default='mock')
    p.add_argument('--output',type=Path,default=Path('results/stage6'))
    p.add_argument('--baseline',default=None,help='Saved baseline ID or approved baseline JSON file')
    p.add_argument('--create-baseline',action='store_true')
    p.add_argument('--application-version',default='fixture-safe-v1')
    p.add_argument('--profile',choices=['standard','redirected'],default='standard')
    p.add_argument('--suite',choices=['swarmguard_core'],default='swarmguard_core')
    p.add_argument('--trials',type=int,default=1)
    p.add_argument('--require-all',action='store_true')
    p.add_argument('--write-approved-baseline',type=Path)
    p.add_argument('--overhead',action='store_true')
    return p


def environment(output: Path) -> None:
    output.mkdir(parents=True,exist_ok=True)
    # Framework bookkeeping and tracing are explicit; never change HOME or API keys.
    os.environ.setdefault('CREWAI_STORAGE_DIR',str((output/'framework-data').resolve()))
    os.environ.setdefault('XDG_DATA_HOME',str((output/'framework-data').resolve()))
    os.environ.setdefault('OTEL_SDK_DISABLED','true')
    os.environ.setdefault('CREWAI_TELEMETRY_ENABLED','false')


def export(record: dict[str,Any], output: Path, name: str) -> Path:
    path=output/f'{name}.json'
    with path.open('x',encoding='utf-8') as f:
        json.dump(record,f,indent=2,sort_keys=True,allow_nan=False)
        f.write('\n')
    return path


async def execute(kind: str,args: argparse.Namespace) -> int:
    environment(args.output)
    db=Database(args.output/'platform.db')
    repo=PlatformRepository(db)
    try:
        if kind in ('demo','framework_evaluation'):
            names=[info['framework'] for info in available_frameworks() if info['compatible_version_family']] if args.framework=='all' else [args.framework]
            if args.require_all and len(names)!=4:
                print('Not all optional frameworks are available. No compatibility claim is made.')
                return 2
            for fw in names:
                if not framework_info(fw)['compatible_version_family']:
                    raise ValueError(f'{fw} unavailable on this interpreter; install its supported optional dependency group')
                cfg=ApplicationConfig(framework=fw,trials=args.trials)
                result=await run_framework_experiment(cfg,db,revision=revision())
                path=export(result,args.output,result['experiment_id'])
                print(json.dumps({'framework':fw,'version':result['framework_version'],'status':result['status'],
                    'metrics':result['metrics'],'output':str(path)},indent=2))
                if result['status']!='COMPLETED':
                    print(json.dumps([r for r in result['rows'] if not r['valid']],indent=2))
                    return 2
                service=next(r for r in result['rows'] if r['case_id']=='service')
                attacks=[r for r in result['rows'] if r['case_id']!='service']
                if not service['legitimate_completed'] or any(r['prohibited_outcome'] for r in attacks):
                    return 1
                if kind=='demo':
                    manager=BaselineManager(repo,SystemClock())
                    baseline=manager.designate(result['experiment_id'],authority=manager.host_authority(),principal='local_researcher')
                    candidate=ApplicationConfig(framework=fw,profile='redirected',application_version='fixture-redirected-v2')
                    report=await run_regression(candidate,baseline,db,revision=revision())
                    export(report,args.output,report['run_id'])
                    print('Controlled synthetic regression:',report['classification'],'gate exit',report['exit_code'])
                    if not report['mandatory_violation'] or report['exit_code']!=1:
                        return 2
            if args.overhead:
                from swarmguard.evaluation.runtime_overhead import measure_runtime_overhead
                overhead=await measure_runtime_overhead(names[0],trials=3)
                oid=db.next_id('overhead')
                repo.create('overhead',oid,overhead,actor='local_researcher',occurred_at=SystemClock().now().isoformat())
                print('Overhead:',export(overhead,args.output,oid))
            print('Stage 6 demonstration check: PASS' if kind=='demo' else 'Framework evaluation check: PASS')
            return 0
        cfg=ApplicationConfig(framework=args.framework,profile=args.profile,application_version=args.application_version,trials=args.trials)
        manager=BaselineManager(repo,SystemClock())
        if args.create_baseline:
            result=await run_framework_experiment(cfg,db,revision=revision())
            export(result,args.output,result['experiment_id'])
            baseline=manager.designate(result['experiment_id'],authority=manager.host_authority(),principal='local_researcher')
            target=args.write_approved_baseline
            if target:
                target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('x') as f: json.dump(baseline,f,indent=2,sort_keys=True)
            print('Official baseline:',baseline['baseline_id'])
            return 0
        reference=args.baseline or '.ci/swarmguard-baseline.json'
        if Path(reference).is_file():
            baseline=json.loads(Path(reference).read_text())
        else:
            stored=repo.get('baseline',reference)
            if not stored: raise ValueError('baseline not found; explicitly designate a completed safe evaluation first')
            baseline=stored[0]
        report=await run_regression(cfg,baseline,db,revision=revision())
        print(json.dumps({k:report[k] for k in ('run_id','baseline_id','candidate_id','classification','exit_code','sample_counts','mandatory_violation','metrics')},indent=2))
        print('Report:',export(report,args.output,report['run_id']))
        return report['exit_code']
    finally:
        db.close()


def main(kind: str) -> None:
    try:
        args=parser(kind).parse_args()
        code=asyncio.run(execute(kind,args))
    except (ValueError,KeyError) as exc:
        print(f'Invalid configuration: {exc}')
        code=3
    except Exception as exc:
        print(f'Evaluation error: {type(exc).__name__}: {str(exc)[:500]}')
        code=2
    raise SystemExit(code)
