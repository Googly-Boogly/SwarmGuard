import asyncio
from copy import deepcopy
import pytest
from swarmguard.storage.database import Database
from swarmguard.research.laboratory import Laboratory
from swarmguard.research.experiment_designer import ExperimentConfig
from swarmguard.research.export import export_bundle, verify_bundle
from swarmguard.research.validation import validate_result
from swarmguard.research.statistical_analysis import wilson_interval, holm_adjust
from swarmguard.datasets.registry import DatasetDefinition
from swarmguard.models.adversarial import ScenarioDefinition


def test_complete_research_bundle_and_replay(tmp_path):
    db=Database()
    try:
        lab=Laboratory(db);lab.datasets.seed()
        cfg=ExperimentConfig(dataset='swarmguard_autonomous')
        record=asyncio.run(lab.run(lab.prepare(cfg)['experiment_id']))
        assert record['status']=='COMPLETED',record['validation']
        assert record['rows'] and all(r['decisions'] for r in record['rows'])
        path=export_bundle(record,tmp_path/'bundle')
        assert verify_bundle(path)['valid']
        replay=asyncio.run(lab.run(lab.prepare(cfg.model_copy(update={'mode':'RECORDED_REPLAY','replay_from':record['experiment_id']}))['experiment_id']))
        assert replay['rows']==record['rows']
        assert replay['usage']['attempts']==0
        changed=deepcopy(record);changed['rows'][0]['ground_truth']['label']='UNKNOWN'
        assert not validate_result(changed)['valid']
        with pytest.raises(ValueError):
            asyncio.run(lab.run(record['experiment_id']))
        (path/'metrics.json').write_text('{}')
        with pytest.raises(ValueError): verify_bundle(path)
    finally: db.close()


def test_autonomous_pipeline():
    db=Database()
    try:
        lab=Laboratory(db);lab.datasets.seed()
        result=asyncio.run(lab.run(lab.prepare(ExperimentConfig(dataset='swarmguard_autonomous',mode='MOCK_AUTONOMOUS'))['experiment_id']))
        assert result['status']=='COMPLETED',result
        assert result['validation']['valid']
        unsafe=next(r for r in result['rows'] if r['ground_truth']['label']=='UNSAFE')
        assert unsafe['feedback']['prohibited_outcome_occurred'] is True
        assert all(r['events'] and r['assessments'] for r in result['rows'])
    finally: db.close()


def test_dataset_immutable_and_strict():
    db=Database()
    try:
        lab=Laboratory(db);lab.datasets.seed()
        raw=lab.datasets.get('swarmguard_autonomous','1.0')['definition']
        with pytest.raises(ValueError): lab.datasets.register(DatasetDefinition.model_validate(raw),'researcher')
        with pytest.raises(ValueError): DatasetDefinition.model_validate({**raw,'policy':'forged'})
        with pytest.raises(ValueError): lab.datasets.register(DatasetDefinition(dataset_id='bad',version='1',description='bad',partition='ADAPTIVE_SEARCH',scenarios=[ScenarioDefinition(scenario_id='bad',template_id='expired_approval_001')]),'researcher')
    finally: db.close()


@pytest.mark.parametrize('change',[{'mode':'LIVE_AUTONOMOUS'},{'framework':'crewai'},{'max_requests':0},{'max_cost':1.0},{'intervention':'TRUSTED_ENFORCEMENT'}])
def test_unsupported_config_fails(change):
    with pytest.raises(ValueError): ExperimentConfig.model_validate(change)


def test_statistics():
    assert wilson_interval(0,0) is None
    interval=wilson_interval(50,100)
    assert interval['low']==pytest.approx(.4038315,abs=1e-6)
    assert interval['high']==pytest.approx(.5961685,abs=1e-6)
    assert holm_adjust([.01,.04,.03])==pytest.approx([.03,.06,.06])
