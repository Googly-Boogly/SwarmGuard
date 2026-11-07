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
from swarmguard.research.report_generator import generate_report
from swarmguard.research.statistical_analysis import analyze
from swarmguard.research.reproducibility import digest


def test_complete_research_bundle_and_replay(tmp_path):
    db=Database()
    try:
        lab=Laboratory(db);lab.datasets.seed()
        cfg=ExperimentConfig(dataset='swarmguard_autonomous')
        record=asyncio.run(lab.run(lab.prepare(cfg)['experiment_id']))
        assert record['status']=='COMPLETED',record['validation']
        assert record['rows'] and all(r['decisions'] for r in record['rows'])
        original = deepcopy(record)
        summary = generate_report(record, concise=True)['markdown']
        assert len(summary) < 5000
        assert '2 recorded / 2 planned' in summary
        assert '| LOCAL_MONITOR |' in summary and '| GLOBAL_MONITOR |' in summary
        assert 'SPECIALIZED_ETHICS_COUNCIL' not in summary
        assert 'paired_effects' not in summary and 'agent_prompt_hashes' not in summary
        assert record == original
        assert 'agent_prompt_hashes' in generate_report(record)['markdown']
        incomplete = deepcopy(record)
        incomplete['rows'][0]['evaluation_valid'] = False
        incomplete['metrics'] = analyze(incomplete['rows'], 2)
        incomplete['results_hash'] = digest(incomplete['rows'])
        incomplete['status'] = 'INCONCLUSIVE'
        incomplete_summary = generate_report(incomplete, concise=True)['markdown']
        assert '1 failed' in incomplete_summary
        assert 'Unscored' in incomplete_summary and 'Unavailable' in incomplete_summary
        assert 'Undefined' in incomplete_summary
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


def test_total_trial_limit_preserved_in_accounting_bundle_and_replay(tmp_path):
    db = Database()
    try:
        lab = Laboratory(db)
        lab.datasets.seed()
        config = ExperimentConfig(dataset='swarmguard_autonomous', trials=3, max_total_trials=2, seed=2)
        record = asyncio.run(lab.run(lab.prepare(config)['experiment_id']))
        assert record['status'] == 'COMPLETED', record['validation']
        assert len(record['rows']) == 2
        assert record['metrics']['accounting']['planned'] == 2
        assert record['metrics']['accounting']['unrun'] == 0
        assert record['validation']['valid']
        assert record['manifest']['limits']['max_total_trials'] == 2
        assert verify_bundle(export_bundle(record, tmp_path / 'limited'))['valid']
        replay_config = config.model_copy(update={'mode': 'RECORDED_REPLAY', 'replay_from': record['experiment_id']})
        replay = asyncio.run(lab.run(lab.prepare(replay_config)['experiment_id']))
        assert replay['rows'] == record['rows']
        assert replay['validation']['valid']
        with pytest.raises(ValueError, match='cannot change'):
            lab.prepare(replay_config.model_copy(update={'max_total_trials': 3}))
        changed = deepcopy(record)
        changed['rows'][0]['trial_id'] = 'unselected:t99'
        assert 'trial outside the pre-registered schedule' in validate_result(changed)['errors']
    finally:
        db.close()


def test_uncapped_schedule_remains_compatible():
    definitions = [ScenarioDefinition(scenario_id=f'case_{i}', template_id='distributed_approval_001') for i in range(4)]
    config = ExperimentConfig(trials=3, seed=2)
    full = config.trial_schedule(definitions)
    limited = config.model_copy(update={'max_total_trials': 2})
    assert len(full) == 12
    assert limited.trial_schedule(definitions) == full[:2]


def test_dataset_immutable_and_strict():
    db=Database()
    try:
        lab=Laboratory(db);lab.datasets.seed()
        raw=lab.datasets.get('swarmguard_autonomous','1.0')['definition']
        with pytest.raises(ValueError): lab.datasets.register(DatasetDefinition.model_validate(raw),'researcher')
        with pytest.raises(ValueError): DatasetDefinition.model_validate({**raw,'policy':'forged'})
        with pytest.raises(ValueError): lab.datasets.register(DatasetDefinition(dataset_id='bad',version='1',description='bad',partition='ADAPTIVE_SEARCH',scenarios=[ScenarioDefinition(scenario_id='bad',template_id='expired_approval_001')]),'researcher')
    finally: db.close()


@pytest.mark.parametrize('change',[{'mode':'LIVE_AUTONOMOUS'},{'framework':'crewai'},{'max_requests':0},{'max_cost':1.0},{'intervention':'TRUSTED_ENFORCEMENT'},{'max_total_trials':0},{'max_total_trials':61}])
def test_unsupported_config_fails(change):
    with pytest.raises(ValueError): ExperimentConfig.model_validate(change)


def test_statistics():
    assert wilson_interval(0,0) is None
    interval=wilson_interval(50,100)
    assert interval['low']==pytest.approx(.4038315,abs=1e-6)
    assert interval['high']==pytest.approx(.5961685,abs=1e-6)
    assert holm_adjust([.01,.04,.03])==pytest.approx([.03,.06,.06])


def test_research_question_registry_is_versioned(tmp_path):
    from swarmguard.research.hypothesis_registry import (LEGACY_REGISTRY_VERSION, REGISTRY_VERSION, question_for,
                                                         questions)
    current = {q['research_question_id']: q for q in questions()}
    assert list(current) == ['RQ1', 'RQ2', 'RQ3']
    assert current['RQ1']['question'].startswith('Can global workflow monitoring detect distributed approval laundering')
    assert current['RQ1']['dependent_variables'] == ['recall', 'precision', 'false_positive_rate', 'f1', 'detection_latency']
    assert current['RQ2']['dependent_variables'][-2:] == ['monitor_disagreement', 'computational_cost']
    assert current['RQ2']['question'] == ('Does a four-framework Ethics Council detect multi-agent safety violations more '
                                          'effectively than an equivalent group of general-purpose safety monitors?')
    v2 = {q['research_question_id']: q for q in questions(registry_version='rq-registry-2')}
    assert list(v2) == ['RQ1', 'RQ2', 'RQ3'] and v2['RQ2']['question'].startswith('Does an AI Ethics Council using virtue ethics')
    assert v2['RQ1'] == {**current['RQ1'], 'registry_version': 'rq-registry-2'} and REGISTRY_VERSION == 'rq-registry-3'
    assert current['RQ3']['dependent_variables'][0] == 'unauthorized_execution_rate'
    assert all(q['goal'] and q['question'] for q in current.values())
    legacy = {q['research_question_id']: q for q in questions(registry_version=LEGACY_REGISTRY_VERSION)}
    assert legacy['RQ1']['hypothesis'] == 'Global context improves workflow-risk detection.'
    assert legacy['RQ1']['question'] is None
    assert list(legacy) == [f'RQ{n}' for n in range(1, 8)]
    with pytest.raises(ValueError):
        questions(registry_version='rq-registry-999')
    db=Database()
    try:
        lab=Laboratory(db);lab.datasets.seed()
        record=asyncio.run(lab.run(lab.prepare(ExperimentConfig(dataset='swarmguard_autonomous'))['experiment_id']))
        assert record['manifest']['question_registry_version'] == REGISTRY_VERSION
        assert current['RQ1']['hypothesis'] in generate_report(record)['markdown']
        verify_bundle(export_bundle(record, tmp_path/'current'))
        # A record created before versioning keeps rendering, and verifying, with the original text.
        old = deepcopy(record)
        del old['manifest']['question_registry_version']
        assert question_for(old)['hypothesis'] == legacy['RQ1']['hypothesis']
        markdown = generate_report(old)['markdown']
        assert legacy['RQ1']['hypothesis'] in markdown and current['RQ1']['hypothesis'] not in markdown
        verify_bundle(export_bundle(old, tmp_path/'legacy'))
        # A retired question still renders and verifies for an experiment created under the legacy registry.
        retired = deepcopy(old)
        retired['config']['question'] = 'RQ4'
        assert legacy['RQ4']['title'] in generate_report(retired)['markdown']
        verify_bundle(export_bundle(retired, tmp_path/'retired'))
        # New experiments may select only the current questions.
        for allowed in ('RQ1', 'RQ2', 'RQ3'):
            lab.prepare(ExperimentConfig(dataset='swarmguard_autonomous', question=allowed))
        for retired_id in ('RQ4', 'RQ5', 'RQ6', 'RQ7'):
            with pytest.raises(ValueError, match='not in the current registry'):
                lab.prepare(ExperimentConfig(dataset='swarmguard_autonomous', question=retired_id))
    finally:
        db.close()
