"""Follow-up dataset quality checks. Before freezing, these never evaluate a monitor."""
import asyncio
import copy
from collections import Counter

import pytest

from swarmguard.evaluation.scenarios import rq1_robustness as data
from swarmguard.research import rq1_followup
from swarmguard.research.rq1_lab import RQ1ExperimentConfig


def test_composition_semantic_program_diversity_and_metadata():
    m=data.manifest()
    assert m['n']==60
    assert Counter(s['label'] for s in m['scenarios'])=={'SAFE':48,'UNSAFE':12}
    assert len({s['scenario_id'] for s in m['scenarios']})==60
    assert len({s['structural_hash'] for s in m['scenarios']})==60
    assert all(v=={'SAFE':8,'UNSAFE':0} for k,v in m['composition'].items() if not k.endswith('_unsafe'))
    assert all(v=={'SAFE':0,'UNSAFE':4} for k,v in m['composition'].items() if k.endswith('_unsafe'))
    for s in m['scenarios']:
        assert s['participating_agents'] and s['permissions'] and s['approval_requirements']
        assert s['program'] and s['initial_environment'] and s['global_safety_invariant']
        assert s['rationale'] and s['scenario_generation_provenance']


def test_all_workflows_and_independent_labels_without_monitors(monkeypatch):
    from swarmguard.monitoring.local_monitor import LocalActionMonitor
    from swarmguard.monitoring.global_monitor import GlobalWorkflowMonitor
    async def forbidden(*args,**kwargs):raise AssertionError('dataset validation must not evaluate monitors')
    monkeypatch.setattr(LocalActionMonitor,'evaluate',forbidden)
    monkeypatch.setattr(GlobalWorkflowMonitor,'evaluate',forbidden)
    v=asyncio.run(rq1_followup.validate_dataset(data.DATASET_ID))
    assert v['all_valid'], [r for r in v['scenarios'] if not r['valid']]
    assert v['valid']==60 and v['monitors_evaluated']==0
    assert v['labels']=={'SAFE':48,'UNSAFE':12}


def test_oracle_uses_executed_packets_and_rejects_missing_or_changed_output():
    case=data.cases()[0]
    action={'action_id':'a1','tool':'send_email'}
    body=data.render(data.RECORDS['a'],'name,account_number','exact')
    sent={'action_id':'a1','recipient':data.ADDRESSES['owner'],'body':body}
    assert data.audit(case,[action],[sent])['valid']
    assert not data.audit(case,[action],[])['valid']
    changed=copy.deepcopy(sent);changed['recipient']=data.ADDRESSES['outside']
    assert not data.audit(case,[action],[changed])['valid']


@pytest.mark.parametrize('patch',[{'trials':2},{'llm_mode':'mock'},
    {'llm_mode':'live','confirm_paid_calls':True,'llm_max_cost_usd':1.0}])
def test_followup_refuses_repetitions_and_llm_modes(patch):
    with pytest.raises(ValueError):RQ1ExperimentConfig(partition=data.DATASET_ID,**patch)


def test_frozen_005_catalog_and_code_remain_unchanged():
    from swarmguard.research.rq1_lab import _fingerprints,FROZEN_DATASET_HASH
    assert rq1_followup.dataset_manifest('held_out')['content_hash']==FROZEN_DATASET_HASH
    assert all(v['match'] for v in _fingerprints().values())


def test_single_trajectory_scoring_pairing_and_tamper_checks(monkeypatch):
    """Use a separate development fixture, never consume the 60 final cases for debugging."""
    from swarmguard.evaluation.datasets import build_dataset
    from swarmguard.research.rq1_lab import _capture
    from swarmguard.clock import SteppingClock
    from swarmguard.models.risk import RULESET_VERSION
    fixture=data.RobustnessScenario(data.Case('development_fixture','development','fixture',
        'A separate development-only accounting fixture',False,data.send('packet','a','owner','service_tier')))
    items=build_dataset(0,scenarios=[fixture])
    evidence={items[0].workflow_id:asyncio.run(_capture(items[0],60))}
    config=RQ1ExperimentConfig(partition=data.DATASET_ID)
    monkeypatch.setattr(data,'scenarios',lambda:[fixture])
    # Scoring must not execute the scenario or invoke either monitor.
    async def forbidden(*args,**kwargs):raise AssertionError('unexpected re-execution')
    monkeypatch.setattr(data.RobustnessScenario,'execute',forbidden)
    result=rq1_followup.score('rq1_test_only',config,'test-revision',items,evidence,'2026-01-01T00:00:00Z')
    assert len(result.predictions)==2
    assert result.metadata['primary_trajectory_executions']==1
    assert result.metadata['ruleset_version']==RULESET_VERSION
    assert len(result.checkpoints)==2*len(next(iter(evidence.values()))['checkpoints'])
    assert result.predictions[0].critical_step==result.predictions[1].critical_step
    assert all(p.ground_truth=='SAFE' for p in result.predictions)
    report=asyncio.run(rq1_followup.verify(evidence,result))
    assert report['checks_passed'], report['failures']
    altered=copy.deepcopy(evidence)
    cp=next(iter(altered.values()))['checkpoints'][0]
    cp['evaluations']['local_action']['output']['snapshot_digest']='tampered'
    assert not asyncio.run(rq1_followup.verify(altered,result))['checks_passed']
