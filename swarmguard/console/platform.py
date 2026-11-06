"""Stage 6 researcher controls. No remote identities, arbitrary tools or policy inputs."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal
from fastapi import FastAPI, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from swarmguard.agents.simulator import SwarmSimulator
from swarmguard.clock import Clock
from swarmguard.console.auth import OperatorAuth, OPERATOR
from swarmguard.console.models import Command
from swarmguard.evaluation.framework_comparison import ApplicationConfig, execute_case, run_framework_experiment
from swarmguard.integrations.registry import available_frameworks,framework_info
from swarmguard.models.approvals import ApprovalStatus
from swarmguard.models.risk import ObservationWindow
from swarmguard.models.workflows import WorkflowStatus
from swarmguard.regression.baseline import BaselineManager
from swarmguard.regression.policies import RegressionPolicy
from swarmguard.regression.runner import run_regression
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository

ACTIVE = {'QUEUED','RUNNING','WAITING_APPROVAL','CANCEL_REQUESTED'}


class IntegrationLaunch(Command):
    config: ApplicationConfig = Field(default_factory=ApplicationConfig)
    case: Literal['service','approval_attack','version_attack'] = 'service'
    human_approval: Literal['INTERACTIVE','SIMULATED'] = 'INTERACTIVE'


class RegressionLaunch(Command):
    config: ApplicationConfig = Field(default_factory=ApplicationConfig)
    baseline_id: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    policy: RegressionPolicy = Field(default_factory=RegressionPolicy)


class BaselineDesignation(Command):
    experiment_id: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')


@dataclass
class RuntimeOwner:
    job_id: str
    workflow_ids: list[str]
    current_scenario: str
    status: str = 'RUNNING'
    completed_at: str | None = None
    config: Any = field(default_factory=lambda: SimpleNamespace(execution_mode='MOCK_AUTONOMOUS'))


class PlatformJobs:
    def __init__(self, db: Database, clock: Clock, worker: asyncio.Semaphore, revision: str) -> None:
        self.db,self.clock,self.worker,self.revision = db,clock,worker,revision
        self.repo=PlatformRepository(db)
        self.baselines=BaselineManager(self.repo,clock)
        self.tasks: dict[str,asyncio.Task[None]]={}
        self.cancelled: set[str]=set()
        self.simulators: dict[str,SwarmSimulator]={}
        self.owners: dict[str,RuntimeOwner]={}
        for r in self.repo.list('stage6_job'):
            if r['status'] in ACTIVE:
                _,version=self.repo.get('stage6_job',r['job_id'])
                r['status']='INTERRUPTED'
                r['completed_at']=clock.now().isoformat()
                self.repo.save('stage6_job',r['job_id'],r,expected_version=version,actor='swarmguard.recovery',occurred_at=clock.now().isoformat())

    def save(self, record: dict[str,Any]) -> None:
        _,version=self.repo.get('stage6_job',record['job_id'])
        self.repo.save('stage6_job',record['job_id'],record,expected_version=version,
                       actor='swarmguard.platform_worker',occurred_at=self.clock.now().isoformat())

    def start(self, kind: str, body: IntegrationLaunch | RegressionLaunch | ApplicationConfig) -> dict[str,Any]:
        config=body if isinstance(body,ApplicationConfig) else body.config
        if not framework_info(config.framework)['compatible_version_family']:
            raise HTTPException(422,'framework unavailable on the server interpreter; inspect integration capabilities')
        if sum(not t.done() for t in self.tasks.values())>=3:
            raise HTTPException(429,'Stage 6 queue capacity exhausted')
        if isinstance(body,RegressionLaunch):
            baseline=self.repo.get('baseline',body.baseline_id)
            if not baseline or baseline[0]['application']!=config.application:
                raise HTTPException(422,'baseline not found for this application')
        jid=self.db.next_id('platform_job')
        record={'job_id':jid,'kind':kind,'status':'QUEUED','command':body.model_dump(),'created_at':self.clock.now().isoformat(),
                'completed_at':None,'error':None,'result':None,'progress':{},'execution_mode':'MOCK_SYNTHETIC'}
        self.repo.create('stage6_job',jid,record,actor=OPERATOR,occurred_at=self.clock.now().isoformat())
        self.tasks={key:t for key,t in self.tasks.items() if not t.done()}
        self.tasks[jid]=asyncio.create_task(self._run(record,body))
        return record

    def cancel(self, jid: str) -> None:
        stored=self.repo.get('stage6_job',jid)
        if not stored:
            raise HTTPException(404,'job not found')
        record,_=stored
        if record['status'] in ACTIVE:
            self.cancelled.add(jid)
            record['status']='CANCEL_REQUESTED'
            self.save(record)

    async def _run(self, record: dict[str,Any], body: IntegrationLaunch | RegressionLaunch | ApplicationConfig) -> None:
        config=body if isinstance(body,ApplicationConfig) else body.config
        jid=record['job_id']
        sim=None
        try:
            async with asyncio.timeout(config.max_runtime_seconds):
                async with self.worker:
                    if jid in self.cancelled:
                        record['status']='CANCELLED'
                        return
                    record['status']='RUNNING'
                    self.save(record)
                    def progress(evaluation: dict[str,Any]) -> None:
                        record['progress']={'experiment_id':evaluation['experiment_id'],'completed_trials':len(evaluation['rows']),
                                            'metrics':evaluation['metrics']}
                        self.save(record)
                    if isinstance(body,IntegrationLaunch):
                        council=None
                        if config.ethics_council:
                            from swarmguard.ethics.general_council import build_specialized_council
                            from swarmguard.ethics.synthetic import rules_responder
                            from swarmguard.llm.mock_provider import MockLLMProvider
                            from swarmguard.llm.usage import BudgetPolicy,GuardedProvider
                            council=build_specialized_council(GuardedProvider(MockLLMProvider(responder=rules_responder),BudgetPolicy(max_requests=60)))
                        sim=SwarmSimulator(self.db.path,clock=self.clock,max_workflow_proposals=60,council=council,
                            workflow_enforcement=ObservationWindow.GLOBAL if config.monitoring in ('ENFORCE','ESCALATE') else None)
                        wid=self.db.next_id('external_wf')
                        owner=RuntimeOwner(jid,[wid],config.framework)
                        self.owners[wid]=owner
                        self.simulators[jid]=sim
                        async def approval_handler(simulator: SwarmSimulator, approval_id: str) -> None:
                            record['status']=owner.status='WAITING_APPROVAL'
                            self.save(record)
                            while simulator.approvals.get(approval_id).status is ApprovalStatus.PENDING:
                                if jid in self.cancelled:
                                    raise asyncio.CancelledError
                                await asyncio.sleep(.05)
                            if simulator.approvals.get(approval_id).status is not ApprovalStatus.APPROVED:
                                raise RuntimeError('trusted operator rejected the reviewed operation')
                            record['status']=owner.status='RUNNING'
                            self.save(record)
                        row=await execute_case(config,body.case,sim=sim,workflow_id=wid,
                            approval_handler=approval_handler if body.human_approval=='INTERACTIVE' else None)
                        record['result']=row
                    elif isinstance(body,RegressionLaunch):
                        baseline=self.repo.get('baseline',body.baseline_id)[0]
                        record['result']=await run_regression(config,baseline,self.db,policy=body.policy,revision=self.revision,
                                                             cancelled=lambda:jid in self.cancelled,progress=progress,clock=self.clock)
                    else:
                        record['result']=await run_framework_experiment(config,self.db,revision=self.revision,
                            cancelled=lambda:jid in self.cancelled,progress=progress,clock=self.clock)
                    if jid in self.cancelled:
                        record['status']='CANCELLED'
                    elif record['result'].get('status') in ('FAILED','TIMED_OUT','INTERRUPTED'):
                        record['status']='FAILED'
                    else:
                        record['status']='COMPLETED'
        except asyncio.CancelledError:
            record['status']='CANCELLED' if jid in self.cancelled else 'INTERRUPTED'
        except TimeoutError:
            record['status'],record['error']='TIMED_OUT','Job runtime exhausted, including queue and approval wait'
        except Exception as exc:
            record['status'],record['error']='FAILED',f'{type(exc).__name__}: {str(exc)[:400]}'
        finally:
            record['completed_at']=self.clock.now().isoformat()
            for owner in self.owners.values():
                if owner.job_id==jid:
                    owner.status,owner.completed_at=record['status'],record['completed_at']
                    if sim and record['status']!='COMPLETED' and sim.monitor.get_workflow(owner.workflow_ids[0]):
                        sim.monitor.set_workflow_status(owner.workflow_ids[0],WorkflowStatus.HALTED)
            if sim:
                sim.close()
            self.simulators.pop(jid,None)
            self.save(record)
            self.cancelled.discard(jid)

    async def close(self) -> None:
        tasks=[t for t in self.tasks.values() if not t.done()]
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


def register_platform_routes(app: FastAPI,auth: OperatorAuth) -> None:
    def read() -> PlatformRepository:
        return PlatformRepository(app.state.reader)
    def require_read(request: Request) -> None:
        if not auth.session(request): raise HTTPException(401,'researcher login required')
    def get(kind: str,rid: str) -> dict[str,Any]:
        result=read().get(kind,rid)
        if not result: raise HTTPException(404,'record not found')
        return result[0]

    @app.get('/api/integrations')
    async def integrations(request: Request) -> dict[str,Any]:
        require_read(request)
        return {'available':available_frameworks(),'connections':read().list('integration'),'jobs':read().list('stage6_job')}

    @app.get('/api/integrations/{integration_id}')
    async def integration(integration_id: str,request: Request) -> dict[str,Any]:
        require_read(request)
        return get('integration',integration_id)

    @app.get('/api/integrations/{integration_id}/{section}')
    async def integration_records(integration_id: str,section: str,request: Request,after_seq: int=Query(0,ge=0)) -> Any:
        require_read(request)
        record=get('integration',integration_id)
        if section=='agents': return record['registered_agents']
        if section=='tools': return record['tools']
        if section=='events': return read().integration_events(integration_id,after_seq)
        raise HTTPException(404,'unknown section')

    @app.get('/api/runtime/events')
    async def runtime_events(request: Request,after_seq: int=Query(0,ge=0)) -> list[dict[str,Any]]:
        require_read(request)
        return read().integration_events(after_seq=after_seq)

    @app.get('/api/platform/jobs')
    async def jobs(request: Request) -> list[dict[str,Any]]:
        require_read(request)
        return read().list('stage6_job')

    @app.get('/api/platform/jobs/{job_id}')
    async def job(job_id: str,request: Request) -> dict[str,Any]:
        require_read(request)
        return get('stage6_job',job_id)

    @app.post('/api/integrations/runs',status_code=202)
    async def start_integration(body: IntegrationLaunch,request: Request) -> dict[str,Any]:
        auth.require(request)
        return app.state.platform.start('integration',body)

    @app.post('/api/evaluation/frameworks/runs',status_code=202)
    async def start_evaluation(body: ApplicationConfig,request: Request) -> dict[str,Any]:
        auth.require(request)
        return app.state.platform.start('evaluation',body)

    @app.get('/api/evaluation/frameworks')
    async def evaluations(request: Request) -> list[dict[str,Any]]:
        require_read(request)
        return read().list('framework_evaluation')

    @app.get('/api/evaluation/runtime-overhead')
    async def overhead(request: Request) -> list[dict[str,Any]]:
        require_read(request)
        return read().list('overhead')

    @app.post('/api/platform/jobs/{job_id}/cancel')
    async def cancel(job_id: str,body: Command,request: Request) -> dict[str,bool]:
        auth.require(request)
        app.state.platform.cancel(job_id)
        return {'cancel_requested':True}

    @app.get('/api/regression/baselines')
    async def baselines(request: Request) -> list[dict[str,Any]]:
        require_read(request)
        return read().list('baseline')

    @app.post('/api/regression/baselines',status_code=201)
    async def designate(body: BaselineDesignation,request: Request) -> dict[str,Any]:
        auth.require(request)
        manager=app.state.platform.baselines
        try:
            return manager.designate(body.experiment_id,authority=manager.host_authority(),principal=OPERATOR)
        except (ValueError,KeyError) as exc:
            raise HTTPException(422,str(exc)) from None

    @app.post('/api/regression/runs',status_code=202)
    async def regression_start(body: RegressionLaunch,request: Request) -> dict[str,Any]:
        auth.require(request)
        return app.state.platform.start('regression',body)

    @app.get('/api/regression/runs')
    async def regression_runs(request: Request) -> list[dict[str,Any]]:
        require_read(request)
        return read().list('regression')

    @app.get('/api/regression/runs/{run_id}')
    @app.get('/api/regression/runs/{run_id}/report')
    async def regression_report(run_id: str,request: Request) -> dict[str,Any]:
        require_read(request)
        return get('regression',run_id)
