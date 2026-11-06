"""Continuous safety runner over the same framework evaluation components."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any
from swarmguard.clock import Clock, SystemClock
from swarmguard.evaluation.framework_comparison import ApplicationConfig,run_framework_experiment
from swarmguard.regression.comparison import compare_evaluations
from swarmguard.regression.policies import RegressionPolicy
from swarmguard.storage.database import Database
from swarmguard.storage.repositories import PlatformRepository


async def run_regression(config: ApplicationConfig, baseline: dict[str, Any], db: Database, *,
                         policy: RegressionPolicy | None = None, revision: str = 'unavailable',
                         cancelled: Callable[[],bool] | None = None,
                         progress: Callable[[dict[str,Any]],None] | None = None,
                         clock: Clock | None = None) -> dict[str, Any]:
    if baseline['application'] != config.application:
        raise ValueError('baseline belongs to a different application')
    clock = clock or SystemClock()
    candidate = await run_framework_experiment(config,db,revision=revision,cancelled=cancelled,progress=progress,clock=clock)
    report = compare_evaluations(baseline['evaluation'],candidate,policy or RegressionPolicy())
    record = {**report,'run_id':db.next_id('regression'),'baseline_id':baseline['baseline_id'],
        'candidate_id':candidate['experiment_id'],'candidate_config':config.model_dump(),'created_at':clock.now().isoformat(),
        'candidate':candidate,'baseline_metrics':baseline['evaluation']['metrics'],'baseline_version':baseline['application_version'],
        'benchmark_version':candidate['suite_version'],'execution_mode':'MOCK_SYNTHETIC','is_research_result':False}
    PlatformRepository(db).create('regression',record['run_id'],record,actor='swarmguard.regression',occurred_at=clock.now().isoformat())
    return record
