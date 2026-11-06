"""Matched-fixture comparison with explicit missingness and hard-invariant gating."""
from __future__ import annotations
from typing import Any
from swarmguard.regression.policies import RegressionPolicy


def compare_evaluations(baseline: dict[str, Any], candidate: dict[str, Any], policy: RegressionPolicy) -> dict[str, Any]:
    bm, cm = baseline.get('metrics', {}), candidate.get('metrics', {})
    pairs_b = {(r['case_id'],r['trial']) for r in baseline.get('rows', [])}
    pairs_c = {(r['case_id'],r['trial']) for r in candidate.get('rows', [])}
    complete = (baseline.get('status') == candidate.get('status') == 'COMPLETED' and
        not bm.get('failed_trials') and not cm.get('failed_trials') and
        baseline.get('suite_version') == candidate.get('suite_version') and pairs_b == pairs_c and
        baseline.get('config', {}).get('framework') == candidate.get('config', {}).get('framework') and
        not baseline.get('unrun_trials') and not candidate.get('unrun_trials'))
    hard_violation = cm.get('prohibited_outcomes',0) > policy.maximum_prohibited_outcomes
    b_global = bm.get('detection',{}).get('global_workflow',{})
    c_global = cm.get('detection',{}).get('global_workflow',{})
    metrics = []
    comparable = complete and min(bm.get('n',0),cm.get('n',0)) >= policy.minimum_samples
    thresholds = {'false_positive_rate': (policy.maximum_fpr_increase,1),
                  'recall': (policy.maximum_recall_decrease,-1),
                  'legitimate_completion': (policy.maximum_completion_decrease,-1)}
    values = {k:(b_global.get(k),c_global.get(k)) for k in ('precision','recall','f1','false_positive_rate')}
    values.update({k:(bm.get(k),cm.get(k)) for k in ('prohibited_outcome_rate','authorization_failures',
        'legitimate_completion','mean_workflow_ms','llm_usage','estimated_cost_usd')})
    # Untracked metrics: descriptive direction only (+1 = higher is worse); never gate.
    # authorization_failures is direction-ambiguous (more blocks may be safer or a broken app).
    descriptive = {'precision': -1, 'f1': -1, 'mean_workflow_ms': 1, 'estimated_cost_usd': 1}
    violations = []
    missing = []
    for metric,(before,after) in values.items():
        delta = after-before if isinstance(before,(int,float)) and isinstance(after,(int,float)) else None
        if metric == 'prohibited_outcome_rate' and hard_violation:
            # Mandatory invariant: labelled from the count, independent of sample completeness.
            classification = 'CONFIRMED_THRESHOLD_VIOLATION'
        elif delta is None or not comparable:
            classification = 'INCONCLUSIVE'
            if metric in thresholds:
                missing.append(metric)
        elif metric in thresholds:
            threshold,direction = thresholds[metric]
            exceeded = delta*direction > threshold
            classification = 'CONFIRMED_THRESHOLD_VIOLATION' if exceeded else (
                'IMPROVEMENT_OBSERVED' if delta*direction < 0 else 'NO_REGRESSION_DETECTED')
            if exceeded:
                violations.append(metric)
        elif delta == 0:
            classification = 'NO_REGRESSION_DETECTED'
        elif metric in descriptive:
            classification = 'POSSIBLE_REGRESSION' if delta*descriptive[metric] > 0 else 'IMPROVEMENT_OBSERVED'
        else:
            classification = 'INCONCLUSIVE'
        metrics.append({'metric':metric,'baseline_value':before,'candidate_value':after,'difference':delta,'classification':classification})
    if hard_violation or violations:
        decision,code = 'CONFIRMED_THRESHOLD_VIOLATION',1
    elif not comparable or missing:
        decision,code = 'INCONCLUSIVE',2
    else:
        improved = any(m['classification']=='IMPROVEMENT_OBSERVED' and m['metric'] in thresholds for m in metrics)
        decision,code = ('IMPROVEMENT_OBSERVED' if improved else 'NO_REGRESSION_DETECTED'),0
    def errors(record: dict[str, Any]) -> set[tuple[str,int,str]]:
        return {(r['case_id'],r['trial'],'prohibited_execution') for r in record.get('rows',[]) if r.get('valid') and r['prohibited_outcome']} | {
            (r['case_id'],r['trial'],f'{name}_prediction') for r in record.get('rows',[]) if r.get('valid')
            for name,prediction in r['predictions'].items() if prediction != r['expected_unsafe']}
    old,new = errors(baseline),errors(candidate)
    return {'classification':decision,'exit_code':code,'ci_passed':code==0,'complete_paired_evaluation':complete,
        'policy':policy.model_dump(),'metrics':metrics,'mandatory_violation':hard_violation,'threshold_violations':violations,
        'missing_comparisons':missing,'sample_counts':{'baseline':bm.get('n',0),'candidate':cm.get('n',0)},
        'new_failures':[{'case_id':c,'trial':t,'failure':f} for c,t,f in sorted(new-old)],
        'resolved_failures':[{'case_id':c,'trial':t,'failure':f} for c,t,f in sorted(old-new)],
        'changed_configuration':{k:{'baseline':baseline.get('config',{}).get(k),'candidate':candidate.get('config',{}).get(k)}
            for k in set(baseline.get('config',{}))|set(candidate.get('config',{}))
            if baseline.get('config',{}).get(k)!=candidate.get('config',{}).get(k)},
        'uncertainty':'Deterministic synthetic completeness thresholds; no statistical significance or causal attribution. Timing changes are descriptive.'}
