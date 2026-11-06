"""Measured synthetic overhead; mandatory gateway remains on in every protected case."""
from __future__ import annotations
from statistics import median, quantiles
from typing import Any
from swarmguard.evaluation.framework_comparison import ApplicationConfig, execute_case


async def measure_runtime_overhead(framework: str = 'custom', trials: int = 3) -> dict[str, Any]:
    if not 1 <= trials <= 20:
        raise ValueError('trials must be between 1 and 20')
    rows = []
    for condition in ('DETERMINISTIC_GATEWAY','PASSIVE_MONITORING','GLOBAL_GUARD','ETHICS_COUNCIL'):
        samples = []
        action_times = []
        for _ in range(trials):
            config = ApplicationConfig(framework=framework,
                monitoring='ENFORCE' if condition=='GLOBAL_GUARD' else 'OBSERVE',ethics_council=condition=='ETHICS_COUNCIL',
                collect_monitoring=condition!='DETERMINISTIC_GATEWAY')
            row = await execute_case(config,'service')
            samples.append(row['elapsed_ms'])
            action_times.extend(row['action_latency_ms'])
        rows.append({'configuration':condition,'n':trials,'total_ms':sum(samples),'median_workflow_ms':median(samples),
            'median_action_ms':median(action_times),'p95_action_ms':quantiles(action_times,n=100,method='inclusive')[94] if len(action_times)>=100 else None,
            'action_samples':len(action_times),'throughput_per_second':len(action_times)/(sum(samples)/1000),
            'llm_tokens':None,'estimated_cost_usd':0.0,'execution_ms':samples})
    return {'framework':framework,'rows':rows,'is_synthetic':True,
        'note':'All conditions retain deterministic authorization. Deterministic gateway omits optional assessments; passive monitoring adds both windows. No unsafe unguarded protected baseline is run. No isolated DB-write attribution or statistical performance claim.'}
