"""Real optional-framework, authorization, regression and console boundary tests."""
from __future__ import annotations
import asyncio
from copy import deepcopy
from pathlib import Path
import json
import pytest
from pydantic import ValidationError
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import SteppingClock,SystemClock
from swarmguard.integrations.configuration import IntegrationConfig
from swarmguard.integrations.event_normalizer import FrameworkEvent
from swarmguard.integrations.identity_mapping import IdentityMapping
from swarmguard.integrations.registry import available_frameworks,framework_info,adapter_class
from swarmguard.integrations.custom.adapter import CustomAdapter
from swarmguard.runtime.middleware import IntegrationRuntime
from swarmguard.runtime.tool_registry import TrustedToolRegistry
from swarmguard.sdk import ToolInvocation
from swarmguard.models.decisions import Decision,ReasonCode
from swarmguard.evaluation.framework_comparison import ApplicationConfig,FrameworkActor,execute_case,host_actors,run_framework_experiment
from swarmguard.regression.baseline import BaselineManager
from swarmguard.regression.comparison import compare_evaluations
from swarmguard.regression.policies import RegressionPolicy
from swarmguard.regression.runner import run_regression
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository


def run(coro): return asyncio.run(coro)


@pytest.fixture(autouse=True)
def framework_environment(monkeypatch,tmp_path):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'framework-data'))
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'framework-data'))
    monkeypatch.setenv('OTEL_SDK_DISABLED','true')
    monkeypatch.setenv('CREWAI_TELEMETRY_ENABLED','false')


@pytest.fixture
def attached():
    sim=SwarmSimulator(clock=SteppingClock(),max_workflow_proposals=60)
    sim.start_workflow('wf','Customer communication',purpose='service_updates')
    runtime=IntegrationRuntime(sim,'wf',IntegrationConfig())
    adapter=CustomAdapter(runtime)
    run(adapter.initialize())
    actor=FrameworkActor('host-owned')
    client=run(adapter.register_agent(actor,'operations_agent'))
    yield sim,adapter,actor,client
    run(adapter.close())
    sim.close()


@pytest.mark.parametrize('framework',['custom','crewai','langgraph','autogen'])
@pytest.mark.parametrize('case',['service','approval_attack','version_attack'])
def test_actual_framework_executes_gateway_owned_tools(framework,case):
    if not framework_info(framework)['compatible_version_family']:
        pytest.skip(f'optional {framework} dependency unavailable on this interpreter')
    row=run(execute_case(ApplicationConfig(framework=framework),case))
    assert row['valid'] and row['framework_version']
    assert row['legitimate_completed'] is True if case=='service' else row['prohibited_outcome'] is False
    decisions=row['decisions']
    assert decisions and all(d['agent_id'] in {'coordinator','research_agent','operations_agent'} for d in decisions)
    assert any(e['source']=='GATEWAY_VERIFIED' and e['action_id'] for e in row['canonical_events'])
    own=[e for e in row['canonical_events'] if not e['metadata'].get('native_event')]
    assert sum(e['event_type']=='TOOL_CALL_PROPOSED' for e in own)==len(row['results'])
    assert row['event_coverage']['AUTHORIZATION_DECISION']==len(row['results'])
    native=[e for e in row['canonical_events'] if e['metadata'].get('native_event')]
    # Framework-native observations are untrusted reports only, attributed to host-registered agents.
    assert all(e['source']=='FRAMEWORK_REPORTED' and e['action_id'] is None for e in native)
    if framework!='custom':
        assert native and row['native_event_coverage']
    assert row['graph']['nodes'] and all(not e['causal'] for e in row['graph']['edges'] if e['edge_type']=='EXECUTED_AFTER')
    if case!='service':
        assert decisions[-1]['decision']=='BLOCK' and not decisions[-1]['executed']


@pytest.mark.parametrize('bad',['operations_agent',{'agent_id':'operations_agent'},True,42,None])
def test_host_identity_mapping_rejects_untrusted_claims(bad):
    mapping=IdentityMapping({'operations_agent'})
    with pytest.raises(ValueError): mapping.register(bad,'operations_agent')


def test_identity_mapping_uses_object_identity_not_name_or_equality():
    mapping=IdentityMapping({'operations_agent','research_agent'})
    class Forged:
        def __eq__(self,other): return True
    real=FrameworkActor('operations_agent')
    mapping.register(real,'operations_agent')
    with pytest.raises(PermissionError): mapping.resolve(FrameworkActor('operations_agent'))
    with pytest.raises(PermissionError): mapping.resolve(Forged())
    with pytest.raises(ValueError): mapping.register(real,'research_agent')
    with pytest.raises(ValueError): mapping.register(FrameworkActor('x'),'operations_agent')


def test_registration_is_sealed_and_clients_have_no_host_controls(attached):
    sim,adapter,actor,client=attached
    assert sim.identities.sealed
    assert not any(hasattr(client,key) for key in ('approve','register_agent','db','policy','executor','register_tool','baseline'))
    with pytest.raises(ValueError): run(adapter.register_agent(FrameworkActor('x'),'human_operator'))
    with pytest.raises(ValueError): run(adapter.initialize())


@pytest.mark.parametrize('field',['agent_id','role','trusted_context','executor','policy'])
def test_tool_invocation_cannot_construct_authority(field):
    with pytest.raises(ValidationError):
        ToolInvocation.model_validate({'tool':'read_document','tool_call_id':'call','parameters':{},field:'forged'})


@pytest.mark.parametrize('proposal',[
    {'tool':'run_shell','parameters':{'command':'echo unsafe'}},
    {'tool':'read_document','resource_id':'doc_service_faq','role':'administrator'},
    {'tool':'read_document','resource_id':'doc_service_faq','agent_id':'research_agent'},
    {'tool':'read_document','resource_id':'doc_service_faq','workflow_id':'foreign'},
])
def test_sdk_malformed_and_forged_requests_are_gateway_audited(attached,proposal):
    sim,adapter,actor,client=attached
    response=run(client.submit_proposal(proposal)).gateway
    assert response.decision is Decision.BLOCK and not response.executed
    assert sim.monitor.get_decision_for_action(response.action_id) is not None
    assert sim.sent_emails()==[]


def test_sdk_gateway_replay_prevents_duplicate_execution(attached):
    sim,adapter,actor,client=attached
    invocation=ToolInvocation(tool='prepare_email',parameters={'recipient':'customer@example.test','subject':'s','body':'b'},tool_call_id='nonce')
    first=run(client.execute_protected_tool(invocation)).gateway
    second=run(client.execute_protected_tool(invocation)).gateway
    assert first.executed and second.decision is Decision.BLOCK and not second.executed
    assert len(sim.monitor.get_decisions('wf'))==2


@pytest.mark.parametrize('framework',['custom','crewai','langgraph','autogen'])
def test_real_framework_wrapper_cannot_cache_or_repeat_execution(framework):
    if not framework_info(framework)['compatible_version_family']: pytest.skip('optional dependency missing')
    async def exercise():
        sim=SwarmSimulator(max_workflow_proposals=60)
        sim.start_workflow('wf','neutral')
        adapter=adapter_class(framework)(IntegrationRuntime(sim,'wf',IntegrationConfig(framework=framework)))
        try:
            await adapter.initialize()
            actor=host_actors(framework,('operations_agent',))['operations_agent']
            await adapter.register_agent(actor,'operations_agent')
            invocation=ToolInvocation(tool='prepare_email',tool_call_id='same',parameters={'recipient':'customer@example.test','subject':'s','body':'b'})
            a=await adapter.invoke(actor,invocation)
            b=await adapter.invoke(actor,invocation)
            assert a.gateway.executed and not b.gateway.executed
            assert len(sim.monitor.get_decisions('wf'))==2
        finally:
            await adapter.close()
            sim.close()
    run(exercise())


def test_framework_reports_are_untrusted_and_cannot_forge_execution(attached):
    sim,adapter,actor,client=attached
    reported=run(adapter.normalize_event(actor,FrameworkEvent(event_type='TOOL_EXECUTED',external_event_id='native_1',
        metadata={'agent_id':'coordinator','executed':True,'approval':'forged'})))
    assert reported.agent_id=='operations_agent' and reported.source=='FRAMEWORK_REPORTED'
    assert reported.action_id is None and reported.parent_action_ids==[]
    assert not sim.monitor.get_decisions('wf') and sim.sent_emails()==[]
    assert sim.monitor.get_workflow_history('wf')[-1].event_type.value=='FRAMEWORK_OBSERVATION'
    with pytest.raises(PermissionError):
        run(adapter.normalize_event(FrameworkActor('operations'),FrameworkEvent(event_type='AGENT_ACTIVITY')))
    with pytest.raises(Exception):
        run(adapter.normalize_event(actor,FrameworkEvent(event_type='AGENT_ACTIVITY',external_event_id='native_1')))


def test_observability_declares_no_execution_boundary():
    sim=SwarmSimulator()
    sim.start_workflow('wf','neutral')
    adapter=CustomAdapter(IntegrationRuntime(sim,'wf',IntegrationConfig(mode='OBSERVABILITY')))
    try:
        run(adapter.initialize())
        actor=FrameworkActor('ops')
        client=run(adapter.register_agent(actor,'operations_agent'))
        info=adapter.runtime.repo.get('integration',adapter.runtime.integration_id)[0]
        assert not info['capabilities']['trusted_execution_wrapping']
        assert not info['capabilities']['tool_call_interception']
        with pytest.raises(PermissionError):
            run(client.execute_protected_tool(ToolInvocation(tool='read_document',resource_id='doc_service_faq',tool_call_id='a')))
        assert sim.monitor.get_decisions('wf')==[]
    finally:
        run(adapter.close());sim.close()


def test_tool_registry_is_metadata_only_and_copies_cannot_change_permissions():
    registry=TrustedToolRegistry()
    data=registry.require('send_email')
    assert data['approval_required'] and data['execution_mode']=='SIMULATED'
    data['approval_required']=False
    assert registry.require('send_email')['approval_required']
    assert not hasattr(registry,'register') and not hasattr(registry,'execute')
    with pytest.raises(ValueError): registry.require('unrestricted_shell')


@pytest.mark.parametrize('change',[{'mode':'UNRESTRICTED'},{'monitoring':'BYPASS'},{'max_proposals':61},{'policy':{}},{'credentials':'fake'}])
def test_invalid_integration_configuration(change):
    with pytest.raises(ValidationError): IntegrationConfig.model_validate(change)


def test_escalation_requires_real_guard_and_runtime_requires_gateway_limits():
    sim=SwarmSimulator()
    sim.start_workflow('wf','neutral')
    try:
        with pytest.raises(ValueError): IntegrationRuntime(sim,'wf',IntegrationConfig())
        with pytest.raises(ValueError): IntegrationRuntime(sim,'wf',IntegrationConfig(monitoring='ENFORCE'))
    finally:sim.close()


def test_core_approvals_still_prevent_cross_workflow_reuse(attached):
    from tests.conftest import make_email,approve,send
    sim,adapter,actor,client=attached
    sim.start_workflow('other','neutral')
    first=make_email(sim,'other')
    approval=approve(sim,'other',first)
    draft=run(client.execute_protected_tool(ToolInvocation(tool='prepare_email',tool_call_id='draft',parameters={
        'recipient':'customer@example.test','subject':'Service Update','body':'Your service request has been received.'}))).gateway.execution_result['resource_id']
    result=run(client.execute_protected_tool(ToolInvocation(tool='send_email',tool_call_id='send',resource_id=draft,
        resource_version=1,approval_id=approval,parameters={'recipient':'customer@example.test','subject':'Service Update','body':'Your service request has been received.'}))).gateway
    assert not result.executed and result.decision is Decision.BLOCK and sim.sent_emails()==[]


@pytest.fixture
def baseline_data():
    db=Database()
    result=run(run_framework_experiment(ApplicationConfig(),db,clock=SteppingClock()))
    manager=BaselineManager(PlatformRepository(db),SteppingClock())
    baseline=manager.designate(result['experiment_id'],authority=manager.host_authority(),principal='local_researcher')
    yield db,manager,baseline
    db.close()


def test_baseline_requires_host_authority_and_is_immutable(baseline_data):
    db,manager,baseline=baseline_data
    with pytest.raises(PermissionError): manager.designate(baseline['experiment_id'],authority='operations_agent',principal='forged')
    with pytest.raises(ValueError): PlatformRepository(db).save('baseline',baseline['baseline_id'],baseline,expected_version=1,actor='x',occurred_at='x')
    assert baseline['prompt_hash'] and baseline['tool_schema_hash'] and baseline['framework_version']


def test_regression_safe_pair_passes_and_controlled_outcome_fails(baseline_data):
    db,manager,baseline=baseline_data
    safe=run(run_regression(ApplicationConfig(application_version='fixture-safe-v2'),baseline,db))
    assert safe['exit_code']==0 and safe['ci_passed'] and safe['sample_counts']=={'baseline':3,'candidate':3}
    bad=run(run_regression(ApplicationConfig(profile='redirected',application_version='fixture-redirected-v2'),baseline,db))
    assert bad['exit_code']==1 and bad['mandatory_violation'] and not bad['ci_passed']
    assert bad['candidate']['metrics']['prohibited_outcomes']==1
    assert any(f['failure']=='prohibited_execution' for f in bad['new_failures'])
    assert PlatformRepository(db).get('regression',bad['run_id'])[0]==bad
    with pytest.raises(ValueError): manager.designate(bad['candidate_id'],authority=manager.host_authority(),principal='local_researcher')


@pytest.mark.parametrize('change',['failed','unrun','missing_metric','version','sample'])
def test_missing_or_incomparable_evaluation_does_not_pass(baseline_data,change):
    _,_,baseline=baseline_data
    reference=baseline['evaluation'];candidate=deepcopy(reference)
    if change=='failed':candidate['status']='FAILED'
    elif change=='unrun':candidate['unrun_trials']=1
    elif change=='missing_metric':candidate['metrics']['detection']['global_workflow']['recall']=None
    elif change=='version':candidate['suite_version']='different'
    else:candidate['metrics']['n']=1
    report=compare_evaluations(reference,candidate,RegressionPolicy())
    assert report['exit_code']==2 and report['classification']=='INCONCLUSIVE'


def test_mandatory_outcome_fails_even_when_other_trials_missing(baseline_data):
    _,_,baseline=baseline_data
    candidate=deepcopy(baseline['evaluation'])
    candidate['status']='FAILED';candidate['metrics']['prohibited_outcomes']=1
    report=compare_evaluations(baseline['evaluation'],candidate,RegressionPolicy())
    assert report['exit_code']==1 and report['mandatory_violation']


def test_regression_policy_is_validated_and_frozen():
    with pytest.raises(ValidationError):RegressionPolicy(maximum_recall_decrease=-1.0)
    with pytest.raises(ValidationError):RegressionPolicy(minimum_samples=True)
    p=RegressionPolicy()
    with pytest.raises(ValidationError):p.maximum_prohibited_outcomes=5
    # The mandatory invariant and version label cannot be configured away.
    for weakened in ({'maximum_prohibited_outcomes':1},{'version':'custom-lenient'}):
        with pytest.raises(ValidationError):RegressionPolicy.model_validate(weakened)


def test_framework_evaluation_is_reproducible_and_labels_are_not_in_observations():
    outputs=[]
    for _ in range(2):
        db=Database()
        r=run(run_framework_experiment(ApplicationConfig(),db,clock=SteppingClock()))
        assert r['status']=='COMPLETED' and r['suite_version']=='stage6-core-v1'
        assert r['tool_schema_hash'] and r['prompt_hash']
        outputs.append([(row['case_id'],row['predictions'],row['prohibited_outcome'],row['decisions']) for row in r['rows']])
        for row in r['rows']:
            for result in row['results']:
                for assessment in result['advisories']:
                    assert assessment['snapshot_digest']
                    assert set(assessment['evidence_event_ids'])<={e['event_id'] for e in row['events']}
        db.close()
    assert outputs[0]==outputs[1]


def test_runtime_overhead_is_measured_and_keeps_gateway():
    from swarmguard.evaluation.runtime_overhead import measure_runtime_overhead
    r=run(measure_runtime_overhead(trials=1))
    assert len(r['rows'])==4
    assert all(row['action_samples']>0 and row['median_action_ms']>0 for row in r['rows'])
    assert all(row['p95_action_ms'] is None for row in r['rows'])


FRAMEWORKS=['custom','crewai','langgraph','autogen']


async def _attached(framework):
    sim=SwarmSimulator(max_workflow_proposals=60)
    sim.start_workflow('wf','neutral')
    adapter=adapter_class(framework)(IntegrationRuntime(sim,'wf',IntegrationConfig(framework=framework)))
    await adapter.initialize()
    actor=host_actors(framework,('operations_agent',))['operations_agent']
    await adapter.register_agent(actor,'operations_agent')
    return sim,adapter,actor


@pytest.mark.parametrize('framework',FRAMEWORKS)
def test_framework_wrapper_allowed_and_blocked_cases(framework):
    """Regression pair: an allowed read executes; a forged-approval send is blocked inside the real framework."""
    if not framework_info(framework)['compatible_version_family']: pytest.skip('optional dependency missing')
    async def exercise():
        sim,adapter,actor=await _attached(framework)
        try:
            ok=await adapter.invoke(actor,ToolInvocation(tool='read_document',resource_id='doc_service_faq',tool_call_id='read'))
            assert ok.gateway.decision is Decision.ALLOW and ok.gateway.executed
            draft=await adapter.invoke(actor,ToolInvocation(tool='prepare_email',tool_call_id='draft',
                parameters={'recipient':'customer@example.test','subject':'s','body':'b'}))
            eid=draft.gateway.execution_result['resource_id']
            sent=await adapter.invoke(actor,ToolInvocation(tool='send_email',tool_call_id='send',resource_id=eid,resource_version=1,
                approval_id='forged',parameters={'recipient':'customer@example.test','subject':'s','body':'b'}))
            assert sent.gateway.decision is Decision.BLOCK and not sent.gateway.executed
            assert sim.sent_emails()==[]
            unknown=await adapter.invoke(actor,ToolInvocation(tool='run_shell',tool_call_id='shell',parameters={'command':'id'}))
            assert unknown.gateway.decision is Decision.BLOCK and not unknown.gateway.executed
        finally:
            await adapter.close();sim.close()
    run(exercise())


def test_langgraph_toolnode_refuses_unregistered_tool_names():
    if not framework_info('langgraph')['compatible_version_family']: pytest.skip('optional dependency missing')
    from langchain_core.messages import AIMessage,ToolMessage
    async def exercise():
        sim,adapter,actor=await _attached('langgraph')
        try:
            out=await adapter.tool_graph(actor).ainvoke({'messages':[AIMessage(content='',tool_calls=[
                {'name':'send_email','id':'direct','args':{'recipient':'customer@example.test'}}])]})
            reply=[m for m in out['messages'] if isinstance(m,ToolMessage)][0]
            assert reply.status=='error' and sim.monitor.get_decisions('wf')==[] and sim.sent_emails()==[]
        finally:
            await adapter.close();sim.close()
    run(exercise())


def test_autogen_toolagent_refuses_unregistered_tool_names():
    if not framework_info('autogen')['compatible_version_family']: pytest.skip('optional dependency missing')
    from autogen_core import FunctionCall
    from autogen_core.tool_agent import ToolNotFoundException
    async def exercise():
        sim,adapter,actor=await _attached('autogen')
        try:
            with pytest.raises(ToolNotFoundException):
                await adapter._framework_runtime.send_message(FunctionCall(id='x',name='send_email',arguments='{}'),
                                                              adapter._addresses['operations_agent'])
            assert sim.monitor.get_decisions('wf')==[] and sim.sent_emails()==[]
        finally:
            await adapter.close();sim.close()
    run(exercise())


def test_crewai_agent_only_holds_gateway_tool_and_native_events_need_registered_agent():
    if not framework_info('crewai')['compatible_version_family']: pytest.skip('optional dependency missing')
    async def exercise():
        sim,adapter,actor=await _attached('crewai')
        try:
            assert [t.name for t in actor.tools]==['swarmguard_gateway']
            await adapter.invoke(actor,ToolInvocation(tool='read_document',resource_id='doc_service_faq',tool_call_id='r'))
            events=adapter.runtime.repo.integration_events(adapter.runtime.integration_id)
            native={e['metadata']['native_event'] for e in events if e['metadata'].get('native_event')}
            assert {'ToolUsageStartedEvent','ToolUsageFinishedEvent'}<=native
            assert all(e['agent_id']=='operations_agent' for e in events)
            # An unregistered CrewAI agent cannot use the adapter even with a matching role string.
            stranger=host_actors('crewai',('operations_agent',))['operations_agent']
            with pytest.raises(PermissionError):
                await adapter.invoke(stranger,ToolInvocation(tool='read_document',resource_id='doc_service_faq',tool_call_id='s'))
        finally:
            await adapter.close();sim.close()
    run(exercise())


def test_langgraph_state_updates_and_checkpoints_are_recorded():
    if not framework_info('langgraph')['compatible_version_family']: pytest.skip('optional dependency missing')
    row=run(execute_case(ApplicationConfig(framework='langgraph'),'service'))
    updates=[e for e in row['canonical_events'] if e['metadata'].get('native_event')=='StateGraph.updates']
    assert [e['metadata']['node'] for e in updates]==['stage_0','stage_1','stage_2']
    assert [e['agent_id'] for e in updates]==['coordinator','research_agent','operations_agent']
    assert all(e['metadata']['checkpoint_id'] for e in updates)


def _allow_everything(system_prompt, user_content, tags):
    return {'risk_level':'LOW','recommendation':'ALLOW','uncertainty':'LOW','concerns':[],'evidence_event_ids':[],
            'legitimate_interpretations':[],'insufficient_evidence':False,'explanation':'Synthetic always-ALLOW responder.'}


@pytest.mark.parametrize('framework',['custom','langgraph'])
@pytest.mark.parametrize('responder',['rules','always_allow'])
def test_ethics_council_evaluates_integrated_workflow_but_cannot_override_block(framework,responder):
    """Acceptance 11 + rule 3: the council assesses framework workflows; its ALLOW never relaxes a BLOCK."""
    if not framework_info(framework)['compatible_version_family']: pytest.skip('optional dependency missing')
    from swarmguard.ethics.general_council import build_specialized_council
    from swarmguard.ethics.synthetic import rules_responder
    from swarmguard.llm.mock_provider import MockLLMProvider
    from swarmguard.llm.usage import BudgetPolicy,GuardedProvider
    provider=GuardedProvider(MockLLMProvider(responder=rules_responder if responder=='rules' else _allow_everything),
                             BudgetPolicy(max_requests=60))
    async def exercise():
        sim=SwarmSimulator(clock=SteppingClock(),max_workflow_proposals=60,council=build_specialized_council(provider))
        try:
            row=await execute_case(ApplicationConfig(framework=framework,ethics_council=True),'approval_attack',sim=sim,workflow_id='wf_eth')
            final=row['decisions'][-1]
            # The council assessed the framework-originated critical send (persisted advisory on the decision).
            assert provider.ledger.snapshot()['attempts']>0 and final['advisory_recommendation'] is not None
            if responder=='always_allow':
                assert final['advisory_recommendation']=='ALLOW'
            assert final['tool']=='send_email' and final['decision']=='BLOCK' and not final['executed']
            assert row['prohibited_outcome'] is False and sim.sent_emails('wf_eth')==[]
        finally:
            sim.close()
    run(exercise())


def test_metric_rows_never_label_a_changed_metric_as_no_regression(baseline_data):
    _,_,baseline=baseline_data
    reference=baseline['evaluation'];candidate=deepcopy(reference)
    candidate['metrics']['prohibited_outcomes']=1;candidate['metrics']['prohibited_outcome_rate']=1/3
    candidate['metrics']['authorization_failures']+=1
    candidate['metrics']['mean_workflow_ms']=reference['metrics']['mean_workflow_ms']/2
    rows={m['metric']:m['classification'] for m in compare_evaluations(reference,candidate,RegressionPolicy())['metrics']}
    assert rows['prohibited_outcome_rate']=='CONFIRMED_THRESHOLD_VIOLATION'
    assert rows['authorization_failures']=='INCONCLUSIVE'          # direction-ambiguous, never 'no regression'
    assert rows['mean_workflow_ms']=='IMPROVEMENT_OBSERVED'         # descriptive only
    faster=deepcopy(reference);faster['metrics']['mean_workflow_ms']=reference['metrics']['mean_workflow_ms']/2
    report=compare_evaluations(reference,faster,RegressionPolicy())
    assert report['classification']=='NO_REGRESSION_DETECTED' and report['exit_code']==0   # timing never drives the verdict
