"""Offline historical-005 verification only: no workflow, monitor, provider or live DB runs."""
from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from tools.rq1_005_snapshot import SCHEMA, decode, digest, safety_errors
from tools.verify_rq1_followup import verify

SNAPSHOT = Path('research/rq1/experiment_005_snapshot.json')


@pytest.fixture(scope='module')
def snapshot():
    return json.loads(SNAPSHOT.read_text())


@pytest.fixture(scope='module')
def report(snapshot):
    return verify(snapshot)


def test_snapshot_identity_and_original_configuration(snapshot, report):
    assert snapshot['schema_version']==SCHEMA
    assert report['experiment_id']=='rq1_experiment_005'
    assert snapshot['experiment']['config']['llm_mode']=='live'
    assert snapshot['experiment']['code_revision']=='unavailable'


def test_distinct_workflows_and_ground_truth(snapshot, report):
    assert (report['workflow_trials'],report['legitimate'],report['unsafe'])==(24,12,12)
    truths=snapshot['result']['results']['ground_truth']
    assert len({t['workflow_id'] for t in truths})==len({t['scenario_id'] for t in truths})==24


def test_paired_predictions_and_reconstructed_confusion_matrices(snapshot, report):
    rows=snapshot['result']['results']['predictions']
    assert len(rows)==48
    assert all({p['configuration'] for p in rows if p['workflow_id']==f'wf_{i:03d}'}=={'local_action','global_workflow'} for i in range(1,25))
    lo,gl=(report['deterministic'][c] for c in ('local_action','global_workflow'))
    assert [lo[k] for k in ('tp','fp','tn','fn')]==[5,3,9,7]
    assert [gl[k] for k in ('tp','fp','tn','fn')]==[11,0,12,1]
    assert lo['recall']==5/12 and lo['precision']==5/8 and lo['false_positive_rate']==3/12
    assert gl['recall']==11/12 and gl['precision']==1 and gl['f1']==22/23
    assert lo['accuracy']==14/24 and gl['accuracy']==23/24


def test_separate_historical_live_llm_metrics_without_inference(report):
    llm=report['exploratory_llm']
    assert llm['new_calls']==0 and llm['requests']==194
    assert [llm['metrics']['llm_local'][k] for k in ('tp','fp','tn','fn')]==[12,12,0,0]
    assert [llm['metrics']['llm_global'][k] for k in ('tp','fp','tn','fn')]==[12,9,3,0]
    assert llm['estimated_cost_usd']==0.210459


def test_recorded_ablation_and_failure_evidence(report):
    assert report['ablations']['recorded_checkpoints']==21
    assert report['ablations']['alert_removed_by']=={'without_resource_history':21,'without_prior_events':6}
    assert report['findings']['global_false_negative_ids']==['rq1_dal_u06']
    assert len(report['findings']['only_global_unsafe_ids'])==6
    assert report['findings']['local_false_positive_ids']==['rq1_dal_l01','rq1_dal_l02','rq1_dal_l03']


def test_public_integrity_and_honest_sanitization_provenance(snapshot, report):
    assert report['valid'] and report['overall_verification']=='PASS'
    assert snapshot['provenance']['original_result_hashes'] is None
    assert not snapshot['provenance']['sanitization']['original_evidence_payloads_independently_hashable']
    assert all(p['sha256']!=p['original_sha256'] for p in snapshot['evidence'])


@pytest.mark.parametrize('change',['label','prediction','metric','llm','ablation','missing_evidence'])
def test_tampering_fails_verification(snapshot, change):
    altered=copy.deepcopy(snapshot)
    r=altered['result']
    if change=='label':r['results']['ground_truth'][0]['label']='SAFE'
    elif change=='prediction':r['results']['predictions'][0]['prediction']='TAMPERED'
    elif change=='metric':r['analysis']['primary']['local_action']['tp']+=1
    elif change=='llm':r['llm_analysis']['rows'][0]['prediction']='NOT_DETECTED'
    elif change=='ablation':r['verification']['ablation_diagnostic'][0]['alert_removed_by']=[]
    else:altered['evidence'].pop()
    with pytest.raises(AssertionError):verify(altered)


def test_repacked_prediction_tampering_still_fails_against_saved_checkpoints(snapshot):
    altered=copy.deepcopy(snapshot)
    packed=altered['evidence'][0];ev=decode(packed)
    cell=ev['checkpoints'][0]['evaluations']['local_action']
    cell['output']['recommendation']='BLOCK';cell['alert']=True
    raw=json.dumps(ev,sort_keys=True,separators=(',',':')).encode()
    import hashlib
    packed['sha256']=hashlib.sha256(raw).hexdigest()
    packed['payload']=base64.b64encode(zlib.compress(raw)).decode()
    packed['deterministic_checkpoints_sha256']=digest([c['evaluations'] for c in ev['checkpoints']])
    # Update public checksums too: internal consistency must independently reject the forged prediction.
    for key in altered['integrity']['components']:altered['integrity']['components'][key]=digest(altered[key])
    altered['integrity']['content_sha256']=digest({k:v for k,v in altered.items() if k!='integrity'})
    with pytest.raises(AssertionError,match='checkpoint|prediction|first alert|metric'):verify(altered)


def test_data_safety_including_compressed_payloads(snapshot):
    assert not safety_errors({k:v for k,v in snapshot.items() if k!='evidence'})
    for packed in snapshot['evidence']:assert not safety_errors(decode(packed))
    assert safety_errors({'api_key':'synthetic-test-only'})
    assert safety_errors({'headers':{'Authorization':'synthetic-test-only'}})


def test_existing_006_verifier_regression():
    six=json.loads(Path('research/rq1/experiment_006_snapshot.json').read_text())
    assert verify(six)['workflow_trials']==60


def test_cli_has_no_database_network_or_site_package_dependency():
    script=Path('tools/verify_rq1_followup.py').resolve()
    snapshot=SNAPSHOT.resolve()
    code='''import runpy,sys

def guard(event,args):
    if event.startswith('socket.') or event=='sqlite3.connect':
        raise RuntimeError('offline verification attempted network or database access')
sys.addaudithook(guard)
sys.argv=[sys.argv[1],'--snapshot',sys.argv[2]]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
    completed=subprocess.run([sys.executable,'-I','-S','-c',code,str(script),str(snapshot)],
        capture_output=True,text=True,env={'PATH':os.defpath},timeout=120)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)['overall_verification']=='PASS'
