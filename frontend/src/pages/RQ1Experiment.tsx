import { Fragment, useMemo } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bar, BarChart, CartesianGrid, ErrorBar, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { post } from '../lib/api/client'
import { ErrorBox, Loading, PageTitle, Panel } from '../components/common'
import { Button } from '../components/ui/button'
import { isTerminal, label, num, pct, seriesColors, signed, useRQ1, type Experiment, type Findings, type MonitorMetrics, type Results } from '../rq1/api'
import { CopyButton, KV, OriginalJson, Section, Stat, Swatch, Tag } from '../rq1/components'
import { RawDataTab } from '../rq1/RawData'
import { ComparisonTab, FailureTab, ScenarioTab, WorkflowTab } from '../rq1/Explorers'
import { ValidationReport } from './RQ1Lab'
import '../rq1/rq1.css'

const TABS = [['overview', 'Overview'], ['metrics', 'Scientific Metrics'], ['raw', 'Raw Data'], ['scenarios', 'Scenarios'], ['workflow', 'Workflow Explorer'], ['comparisons', 'Monitor Comparisons'], ['failures', 'Failure Analysis'], ['findings', 'Research Findings'], ['details', 'Experiment Details']] as const

export function RQ1ExperimentPage() {
 const { eid = '' } = useParams()
 const [params, setParams] = useSearchParams()
 const record = useRQ1<Experiment>(`/api/research/rq1/experiments/${eid}`, 1000)
 const status = record.data?.status
 const done = isTerminal(status)
 const results = useRQ1<Results>(done && status !== 'FAILED' && status !== 'CANCELLED' ? `/api/research/rq1/experiments/${eid}/results` : null)
 const tab = params.get('tab') ?? (done ? 'overview' : 'live')
 const go = (next: string, extra: Record<string, string> = {}) => setParams({ tab: next, ...extra })
 const captured = [...new Set((record.data?.activity ?? []).map(a => a.workflow_id))]
 const available = (id: string) => !!results.data || id === 'details' || (id === 'workflow' && captured.length > 0)
 if (record.error) return <ErrorBox error={record.error}/>
 if (!record.data) return <Loading/>
 const e = record.data
 return <>
  <PageTitle eyebrow="RQ1 EXPERIMENT" title={e.experiment_id} description={`Local vs. Global monitoring on matching workflow snapshots. Scripted agents, synthetic data; deterministic rule monitors${e.llm?.mode === 'live' ? ` plus a live LLM monitor (${e.llm.model ?? ''})` : e.llm?.mode === 'mock' ? ' plus the LLM monitor pipeline in mock mode (synthetic, no model contacted)' : ' only (no LLM)'}.`}>
   <Link className="button outline small" to="/research/rq1">RQ1 Laboratory</Link>
  </PageTitle>
  <ExperimentHeader e={e}/>
  <div className="rq1-tabs" role="tablist" aria-label="RQ1 experiment views">
   <button role="tab" aria-selected={tab === 'live'} onClick={() => go('live')}>Live Monitor</button>
   {TABS.map(([id, name]) => <button key={id} role="tab" aria-selected={tab === id} disabled={!available(id)} title={available(id) ? undefined : 'Available when the experiment has results'} onClick={() => go(id)}>{name}</button>)}
  </div>
  {results.error && <ErrorBox error={results.error}/>}
  {tab === 'live' || !available(tab) ? <LiveMonitor e={e} onResults={() => go('overview')} onWorkflow={wf => go('workflow', { wf })} hasResults={!!results.data}/> :
   tab === 'details' ? <DetailsTab eid={eid} e={e}/> :
   tab === 'workflow' ? <WorkflowTab eid={eid} selected={params.get('wf')} onSelect={wf => go('workflow', { wf })} captured={results.data ? undefined : captured}/> :
   !results.data ? <Loading/> :
   tab === 'overview' ? <OverviewTab e={e} r={results.data}/> :
   tab === 'metrics' ? <MetricsTab r={results.data}/> :
   tab === 'raw' ? <RawDataTab eid={eid}/> :
   tab === 'scenarios' ? <ScenarioTab eid={eid} selected={params.get('scenario')} onSelect={s => go('scenarios', { scenario: s })} onCompare={wf => go('comparisons', { wf })} onWorkflow={wf => go('workflow', { wf })}/> :
   tab === 'comparisons' ? <ComparisonTab eid={eid} selected={params.get('wf')} onSelect={wf => go('comparisons', { wf })}/> :
   tab === 'failures' ? <FailureTab eid={eid} onCompare={wf => go('comparisons', { wf })} onWorkflow={wf => go('workflow', { wf })}/> :
   tab === 'findings' ? <FindingsTab f={results.data.findings} onScenario={s => go('scenarios', { scenario: s })}/> : null}
 </>
}

function ExperimentHeader({ e }: { e: Experiment }) {
 return <Panel title="Experiment" subtitle={e.preregistered_procedure ? 'Pre-registered procedure (rq1-protocol-1): complete frozen held-out dataset, seed 0.' : 'Exploratory run: not the pre-registered procedure.'} action={<Tag value={e.status}/>}>
  <div className="rq1-body"><div className="rq1-grid">
   <KV rows={[['Research question', 'RQ1 — Global vs. Local monitoring'], ['Dataset', `${e.config.partition} · ${e.validation.dataset_version}`], ['Scenarios', e.config.scenario_ids ? `${e.config.scenario_ids.length} selected` : `all ${e.validation.selected_scenarios}`]]}/>
   <KV rows={[['Monitors', e.llm?.mode && e.llm.mode !== 'none' ? 'Local (A), Global (B), LLM Local (C), LLM Global (D)' : 'Local Monitor (A), Global Monitor (B)'], ['Execution mode', `Scripted agents · ${e.llm?.mode === 'live' ? `live LLM (${e.llm.model ?? 'model pending'})` : e.llm?.mode === 'mock' ? 'mock LLM (synthetic)' : 'rules only'}`], ['Model usage', `${e.model_requests ?? 0} requests · ${e.llm_usage ? `${e.llm_usage.input_tokens.toLocaleString()} in / ${e.llm_usage.output_tokens.toLocaleString()} out tokens · ` : ''}$${(e.estimated_cost_usd ?? 0).toFixed(4)}${e.llm_usage?.is_synthetic ? ' (synthetic)' : ''}`]]}/>
   <KV rows={[['Created', new Date(e.created_at).toLocaleString()], ['Started', e.started_at ? new Date(e.started_at).toLocaleString() : null], ['Completed', e.completed_at ? new Date(e.completed_at).toLocaleString() : null]]}/>
  </div>{e.error && <p role="alert" className="warning-text">{e.error}</p>}</div>
 </Panel>
}

function LiveMonitor({ e, onResults, onWorkflow, hasResults }: { e: Experiment; onResults: () => void; onWorkflow: (wf: string) => void; hasResults: boolean }) {
 const cache = useQueryClient()
 const cancel = useMutation({ mutationFn: () => post(`/api/research/rq1/experiments/${e.experiment_id}/cancel`, {}), onSuccess: () => cache.invalidateQueries({ queryKey: ['rq1'] }) })
 const p = e.progress
 const share = p.total_workflows ? (p.completed_workflows + p.failed_workflows) / p.total_workflows : 0
 const lastWf = [...e.activity].reverse().find(a => a.workflow_id)?.workflow_id
 const activity = useMemo(() => [...e.activity].reverse(), [e.activity])
 return <>
  <Panel title="Progress" subtitle={`Phase: ${label(p.phase)}${p.elapsed_seconds !== undefined ? ` · elapsed ${p.elapsed_seconds.toFixed(1)}s` : ''}`} action={<div className="actions">
   {lastWf && <Button variant="outline" size="sm" onClick={() => onWorkflow(lastWf)}>View Workflow</Button>}
   <Button variant="outline" size="sm" onClick={() => document.getElementById('rq1-activity')?.scrollIntoView({ behavior: 'smooth' })}>View Events</Button>
   {!isTerminal(e.status) && <Button variant="destructive" size="sm" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel Experiment</Button>}
   {hasResults && <Button size="sm" onClick={onResults}>View Results</Button>}
  </div>}>
   <div className="rq1-body">
    <div className="rq1-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)} aria-label="Workflows evaluated"><span style={{ width: `${share * 100}%` }}/></div>
    <div className="metric-grid" style={{ marginTop: 12 }}>
     <Stat title="Scenarios completed" value={p.completed_workflows}/><Stat title="Scenarios remaining" value={Math.max(0, p.total_workflows - p.completed_workflows - p.failed_workflows)}/>
     <Stat title="Current scenario" value={<span style={{ fontSize: 13 }}>{p.current_scenario ?? '—'}</span>} note={p.current_workflow ?? undefined}/>
     <Stat title="Completed trials" value={p.completed_workflows} note="1 scored trial per workflow"/><Stat title="Failed trials" value={p.failed_workflows}/><Stat title="Model requests" value={`${e.llm_usage?.attempts ?? 0}`} note={e.llm_usage ? `${e.llm_usage.succeeded} ok · ${e.llm_usage.failed} failed · ${e.llm_usage.refused} refused · $${e.llm_usage.spent_usd_conservative.toFixed(4)}${e.llm_usage.is_synthetic ? ' · synthetic' : ''}` : e.llm?.mode && e.llm.mode !== 'none' ? 'LLM arm pending' : 'rules only · $0'}/>
    </div>
    <ol className="rq1-list" aria-label="Phases">{p.phases.map((ph, i) => <li key={i}>{label(ph.phase)} · {new Date(ph.started_at).toLocaleTimeString()}</li>)}</ol>
    {cancel.error && <ErrorBox error={cancel.error}/>}
    {e.failures.length > 0 && <Section title="Recorded failures"><ul className="rq1-list">{e.failures.map(f => <li key={f.workflow_id}>{f.workflow_id} ({f.scenario_id}) · {f.phase}: {f.error}</li>)}</ul></Section>}
   </div>
  </Panel>
  <div id="rq1-activity"/>
  <Panel title="Live activity" subtitle="Recorded workflow events and monitor assessments only (latest first, up to 300). Event times are the simulator's synthetic clock.">
   {activity.length ? <div className="rq1-stream" aria-live="polite"><ol>{activity.map((a, i) => <li key={`${a.workflow_id}-${a.seq}-${a.kind}-${i}`}><span className="mono">{a.time ? new Date(a.time).toLocaleTimeString() : `seq ${a.seq}`}</span><span className="mono">{a.workflow_id}</span><span className="mono">{a.kind.replaceAll('_', ' ').toLowerCase()}</span><span>{a.text}</span></li>)}</ol></div> : <p className="rq1-body muted">No events recorded yet.</p>}
  </Panel>
  {e.status !== 'QUEUED' && <Panel title="Validation at launch"><div className="rq1-body"><ValidationReport v={e.validation}/></div></Panel>}
 </>
}

function RuleTag({ rule }: { rule: Findings['decision_rule'] }) {
 if (!rule.applies) return <Tag value="ORIENTATION_ONLY" tone="neutral"/>
 return <Tag value={rule.satisfied === null ? 'UNDEFINED' : rule.satisfied ? 'CONDITIONS_MET' : 'CONDITIONS_NOT_MET'} tone={rule.satisfied === null ? 'neutral' : rule.satisfied ? 'positive' : 'danger'}/>
}

function OverviewTab({ e, r }: { e: Experiment; r: Results }) {
 const lo = r.analysis.primary.local_action, gl = r.analysis.primary.global_workflow
 const f = r.findings
 return <>
  <div className="metric-grid">
   <Stat title="Local recall" value={pct(lo.recall)} note={`${lo.tp}/${lo.tp + lo.fn} unsafe detected`}/>
   <Stat title="Global recall" value={pct(gl.recall)} note={`${gl.tp}/${gl.tp + gl.fn} unsafe detected`}/>
   <Stat title="Local false-positive rate" value={pct(lo.false_positive_rate)} note={`${lo.fp}/${lo.fp + lo.tn} legitimate flagged`}/>
   <Stat title="Global false-positive rate" value={pct(gl.false_positive_rate)} note={`${gl.fp}/${gl.fp + gl.tn} legitimate flagged`}/>
   <Stat title="Difference in recall" value={signed(r.analysis.recall_difference_global_minus_local)} note="Global − Local"/>
   <Stat title="Difference in FPR" value={signed(lo.false_positive_rate !== null && gl.false_positive_rate !== null ? gl.false_positive_rate - lo.false_positive_rate : null)} note="Global − Local"/>
  </div>
  {r.llm_analysis && r.llm && <><p className="eyebrow">LLM arm · {r.llm.provider} · {r.llm.model}{r.llm.is_synthetic ? ' · SYNTHETIC MOCK (not evidence)' : ''} · {r.llm.checkpoint_policy} checkpoints</p>
   <div className="metric-grid">{(['llm_local', 'llm_global'] as const).map(c => { const m = r.llm_analysis!.primary[c]; const name = c === 'llm_local' ? 'LLM Local' : 'LLM Global'; return <Fragment key={c}>
    <Stat title={`${name} recall`} value={pct(m.recall)} note={`${m.tp}/${m.tp + m.fn} unsafe · ${m.unknown} unknown`}/>
    <Stat title={`${name} false-positive rate`} value={pct(m.false_positive_rate)} note={`${m.fp}/${m.fp + m.tn} legitimate flagged`}/></Fragment> })}
    <Stat title="LLM requests" value={r.llm_usage?.attempts ?? 0} note={`${r.llm_usage?.failed ?? 0} failed · ${(r.llm_usage?.input_tokens ?? 0).toLocaleString()} input tokens`}/>
    <Stat title="LLM cost (conservative)" value={`$${(r.llm_usage?.spent_usd_conservative ?? 0).toFixed(4)}`} note={r.llm.is_synthetic ? 'mock: no model contacted' : 'from recorded usage'}/>
   </div></>}
  <Panel title="What did this experiment investigate?">
   <div className="rq1-body">
    <p>{f.research_question}</p>
    <p>Every workflow ran once. At every action checkpoint, one as-of snapshot of the workflow was taken. The <b>Local Monitor</b> received only the proposed action, the acting agent's role, the target resource version and the approval it cites. The <b>Global Monitor</b> received the same plus prior events, messages, other agents' actions, referenced resource history, all approvals and the declared workflow purpose. Both use identical rules, so the only difference is the observation window.</p>
    <KV rows={[['Hypothesis', 'H1: global workflow monitoring improves recall for distributed approval laundering without an unacceptable increase in false positives. H0: no meaningful improvement under the evaluated conditions.'], ['Dataset', `${e.config.partition} · ${e.validation.dataset_version} · ${r.analysis.n_workflows} workflows`], ['Execution mode', `Scripted agents${r.llm ? ` · ${r.llm.is_synthetic ? 'mock' : 'live'} LLM arm` : ''}`], ['Model identifiers', r.llm ? `RiskAnalyzer rules; LLM ${r.llm.provider} · ${r.llm.model} (prompt ${r.llm.prompt_version})` : 'RiskAnalyzer rules (no LLM)'], ['Trials', `${e.config.trials} (1 scored)`], ['Successful evaluations', `${r.counts.checkpoint_evaluations} checkpoint evaluations · ${r.counts.workflow_predictions} workflow predictions`], ['Failed evaluations', `${r.analysis.missing_or_failed.length} workflows`], ['Total runtime', e.progress.elapsed_seconds !== undefined ? `${e.progress.elapsed_seconds.toFixed(1)} s` : null], ['Actual model usage', r.llm_usage ? `${r.llm_usage.attempts} requests (${r.llm_usage.succeeded} ok, ${r.llm_usage.failed} failed) · ${r.llm_usage.input_tokens.toLocaleString()} input / ${r.llm_usage.output_tokens.toLocaleString()} output tokens · $${r.llm_usage.spent_usd_conservative.toFixed(4)}${r.llm_usage.is_synthetic ? ' (synthetic)' : ''}` : '0 requests · $0 (rules only)']]}/>
   </div>
  </Panel>
  <Panel title="Interpretation" subtitle={f.statement}>
   <div className="rq1-body">
    <p><RuleTag rule={f.decision_rule}/> <b>Pre-registered decision rule{f.decision_rule.applies ? '' : ' (orientation only)'}:</b> {f.decision_rule.note}</p>
    <p>{f.detection_performance}</p><p>{f.false_positives}</p>
   </div>
  </Panel>
 </>
}

function Confusion({ name, m }: { name: string; m: MonitorMetrics }) {
 return <div aria-label={`${name} confusion matrix`}><h3 style={{ fontSize: 12 }}>{name} Monitor</h3><div className="rq1-confusion">
  <div className="head"/><div className="head">Predicted unsafe</div><div className="head">Not flagged</div>
  <div className="head">Unsafe (n={m.tp + m.fn})</div><div><strong>{m.tp}</strong>TP</div><div><strong>{m.fn}</strong>FN</div>
  <div className="head">Legitimate (n={m.fp + m.tn})</div><div><strong>{m.fp}</strong>FP</div><div><strong>{m.tn}</strong>TN</div>
 </div></div>
}

function MetricsTab({ r }: { r: Results }) {
 const c = seriesColors()
 const p = r.analysis.primary
 const lo = p.local_action, gl = p.global_workflow
 const err = (m: MonitorMetrics, key: 'recall' | 'false_positive_rate') => { const ci = key === 'recall' ? m.recall_wilson95 : m.fpr_wilson95; const v = m[key]; return ci && v !== null ? [v - ci.low, ci.high - v] : [0, 0] }
 const bars = [
  { metric: 'Recall', Local: lo.recall, Global: gl.recall, LocalErr: err(lo, 'recall'), GlobalErr: err(gl, 'recall') },
  { metric: 'Precision', Local: lo.precision, Global: gl.precision },
  { metric: 'False-positive rate', Local: lo.false_positive_rate, Global: gl.false_positive_rate, LocalErr: err(lo, 'false_positive_rate'), GlobalErr: err(gl, 'false_positive_rate') },
  { metric: 'F1', Local: lo.f1, Global: gl.f1 }]
 const tip = { contentStyle: { background: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--border)', fontSize: 11 } }
 const fmt = (v: unknown) => typeof v === 'number' ? v.toFixed(3) : 'undefined'
 const tableText = () => 'Metric\tLocal\tGlobal\tDifference\n' + r.metrics.table.map(t => `${t.metric}\t${num(t.local)}\t${num(t.global)}\t${num(t.difference)}`).join('\n')
 return <>
  <Panel title="Confusion matrices" subtitle={`Workflow level, primary (timely) mapping · ${r.analysis.primary_metric_definition}`}>
   <div className="rq1-body rq1-grid two"><Confusion name="Local" m={lo}/><Confusion name="Global" m={gl}/></div>
  </Panel>
  <Panel title="Comparison table" subtitle="Undefined = zero denominator. Detection lead is in action checkpoints before the critical action." action={<CopyButton text={tableText} label="Copy table"/>}>
   <div className="table-wrap"><table aria-label="Metric comparison"><thead><tr>{['Metric', 'Local Monitor', 'Global Monitor', 'Difference (Global − Local)'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{r.metrics.table.map(t => <tr key={t.key}><td>{t.metric}</td><td>{num(t.local)}</td><td>{num(t.global)}</td><td>{signed(t.difference)}</td></tr>)}</tbody></table></div>
   <div className="rq1-body"><p className="muted small-text">Wilson 95% intervals (assume independent workflows, which look-alike pairs violate): Local recall {r.metrics.intervals.Local.recall ? `[${num(r.metrics.intervals.Local.recall.low)}, ${num(r.metrics.intervals.Local.recall.high)}]` : 'undefined'}, Global recall {r.metrics.intervals.Global.recall ? `[${num(r.metrics.intervals.Global.recall.low)}, ${num(r.metrics.intervals.Global.recall.high)}]` : 'undefined'}. Recall difference family-cluster bootstrap: {r.analysis.recall_difference_ci?.interval ? `[${num(r.analysis.recall_difference_ci.interval.low)}, ${num(r.analysis.recall_difference_ci.interval.high)}]` : r.analysis.recall_difference_ci?.reason ?? 'undefined'}. Exact McNemar p (unsafe, descriptive): {num(r.analysis.paired.unsafe.exact_mcnemar_p_two_sided, 4)}. {r.analysis.inference_note}</p></div>
  </Panel>
  <div className="rq1-grid two">
   <Panel title="Detection metrics" subtitle={`Recall n=${lo.tp + lo.fn} unsafe · FPR n=${lo.fp + lo.tn} legitimate · whiskers: Wilson 95% · a value of 0 has no bar; undefined values are omitted (see table)`}>
    <div className="rq1-chart" aria-label="Detection metrics chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} barGap={2}><CartesianGrid vertical={false} stroke={c.grid}/><XAxis dataKey="metric" stroke={c.axis} fontSize={10}/><YAxis domain={[0, 1]} stroke={c.axis} fontSize={10}/><Tooltip {...tip} formatter={fmt}/><Legend/>
     <Bar dataKey="Local" fill={c.Local} radius={[4, 4, 0, 0]}><ErrorBar dataKey="LocalErr" width={4} stroke={c.axis}/><LabelList dataKey="Local" position="top" formatter={fmt} fontSize={9} fill={c.axis}/></Bar>
     <Bar dataKey="Global" fill={c.Global} radius={[4, 4, 0, 0]}><ErrorBar dataKey="GlobalErr" width={4} stroke={c.axis}/><LabelList dataKey="Global" position="top" formatter={fmt} fontSize={9} fill={c.axis}/></Bar>
    </BarChart></ResponsiveContainer></div>
   </Panel>
   <Panel title="Recall by scenario category" subtitle={r.metrics.per_category.map(x => `${x.category.replaceAll('_', ' ')}: ${x.unsafe_n} unsafe / ${x.safe_n} legitimate`).join(' · ')}>
    <div className="rq1-chart" aria-label="Recall by category chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={r.metrics.per_category.map(x => ({ category: x.category.replaceAll('_', ' '), Local: x.local_recall, Global: x.global_recall }))} barGap={2}><CartesianGrid vertical={false} stroke={c.grid}/><XAxis dataKey="category" stroke={c.axis} fontSize={9}/><YAxis domain={[0, 1]} stroke={c.axis} fontSize={10}/><Tooltip {...tip} formatter={fmt}/><Legend/><Bar dataKey="Local" fill={c.Local} radius={[4, 4, 0, 0]}/><Bar dataKey="Global" fill={c.Global} radius={[4, 4, 0, 0]}/></BarChart></ResponsiveContainer></div>
   </Panel>
   <Panel title="Detection latency" subtitle="Unsafe workflows detected in time, by checkpoints between first alert and the critical action (0 = at the critical action)">
    {r.metrics.lead_histogram.length ? <div className="rq1-chart" aria-label="Detection latency chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={r.metrics.lead_histogram} barGap={2}><CartesianGrid vertical={false} stroke={c.grid}/><XAxis dataKey="lead" stroke={c.axis} fontSize={10} label={{ value: 'checkpoints before critical action', position: 'insideBottom', offset: -2, fontSize: 9, fill: c.axis }}/><YAxis allowDecimals={false} stroke={c.axis} fontSize={10}/><Tooltip {...tip}/><Legend verticalAlign="top"/><Bar dataKey="Local" fill={c.Local} radius={[4, 4, 0, 0]}/><Bar dataKey="Global" fill={c.Global} radius={[4, 4, 0, 0]}/></BarChart></ResponsiveContainer></div> : <p className="rq1-body muted">No unsafe workflow was detected in time by either monitor.</p>}
   </Panel>
   <Panel title="Checkpoint recommendations" subtitle={`${r.counts.checkpoint_evaluations} checkpoint evaluations (both monitors)`}>
    <div className="table-wrap"><table><thead><tr><th>Monitor</th><th>Recommendation</th><th>Count</th></tr></thead><tbody>{r.metrics.checkpoint_recommendations.map(x => <tr key={x.monitor + x.recommendation}><td><Swatch color={x.monitor === 'Local' ? c.Local : c.Global}/>{x.monitor}</td><td><Tag value={x.recommendation}/></td><td>{x.count}</td></tr>)}</tbody></table></div>
   </Panel>
  </div>
  {r.metrics.llm && <LLMMetrics r={r}/>}
  <Panel title="Secondary and sensitivity analyses" subtitle="Pre-declared in the protocol; reported alongside, not used for the decision rule">
   <div className="table-wrap"><table><thead><tr>{['Analysis', 'Local recall', 'Global recall', 'Local FPR', 'Global FPR'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
    {([['Any checkpoint', r.analysis.secondary_any_checkpoint], ['Critical checkpoint only', r.analysis.secondary_at_critical_only], ['Abstention treated as positive', r.analysis.sensitivity_abstention_as_positive]] as const).map(([n, m]) => <tr key={n}><td>{n}</td><td>{pct(m.local_action.recall)}</td><td>{pct(m.global_workflow.recall)}</td><td>{pct(m.local_action.false_positive_rate)}</td><td>{pct(m.global_workflow.false_positive_rate)}</td></tr>)}
   </tbody></table></div>
   <div className="rq1-body"><p className="muted small-text">Evaluation latency: median {num(r.verification.evaluation_latency.local_action?.median_ms)} ms (Local), {num(r.verification.evaluation_latency.global_workflow?.median_ms)} ms (Global) — {r.verification.timing_note}. Evaluation cost: 0 model requests, $0 for both monitors.</p></div>
  </Panel>
 </>
}

function LLMMetrics({ r }: { r: Results }) {
 const l = r.metrics.llm!, c = seriesColors(), a = r.llm_analysis!
 const lo = a.primary.llm_local, gl = a.primary.llm_global
 const fmt = (v: unknown) => typeof v === 'number' ? v.toFixed(3) : 'undefined'
 const tip = { contentStyle: { background: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--border)', fontSize: 11 } }
 const bars = [['Recall', 'recall'], ['Precision', 'precision'], ['False-positive rate', 'false_positive_rate'], ['F1', 'f1']].map(([n, k]) => ({ metric: n, 'LLM Local': lo[k as 'recall'], 'LLM Global': gl[k as 'recall'] }))
 const rules = a.rules_on_same_checkpoints
 return <>
  <Panel title={`LLM arm — ${l.llm.provider} · ${l.llm.model}${l.llm.is_synthetic ? ' · SYNTHETIC MOCK' : ''}`} subtitle={`${l.llm.protocol_version} · prompt ${l.llm.prompt_version} · checkpoints: ${l.llm.checkpoint_policy_text} · ${l.metric_definition}`} action={<CopyButton text={() => 'Metric\tLLM Local\tLLM Global\tDifference\n' + l.table.map(t => `${t.metric}\t${num(t.local)}\t${num(t.global)}\t${num(t.difference)}`).join('\n')} label="Copy LLM table"/>}>
   {l.llm.is_synthetic && <p className="rq1-body warning-text">Mock mode: these numbers come from the synthetic rules responder through the LLM pipeline. No model was contacted; they are not evidence about LLM monitors.</p>}
   <div className="rq1-body rq1-grid two"><Confusion name="LLM Local" m={lo}/><Confusion name="LLM Global" m={gl}/></div>
   <div className="table-wrap"><table aria-label="LLM metric comparison"><thead><tr>{['Metric', 'LLM Local', 'LLM Global', 'Difference (Global − Local)'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{l.table.map(t => <tr key={t.key}><td>{t.metric}</td><td>{num(t.local)}</td><td>{num(t.global)}</td><td>{signed(t.difference)}</td></tr>)}</tbody></table></div>
   <div className="rq1-body"><p className="muted small-text">Rule monitors on exactly the same checkpoints (for comparison): Local recall {pct(rules.local_action?.recall)}, FPR {pct(rules.local_action?.false_positive_rate)}; Global recall {pct(rules.global_workflow?.recall)}, FPR {pct(rules.global_workflow?.false_positive_rate)}. Paired unsafe (both known, n={a.paired.unsafe.n}, {a.paired.unsafe.excluded_unknown} excluded as unknown): only Global {a.paired.unsafe.only_global}, only Local {a.paired.unsafe.only_local}; exact McNemar p {num(a.paired.unsafe.exact_mcnemar_p_two_sided, 4)} (descriptive).</p>
    {l.failures.length > 0 && <p className="warning-text small-text">Failed or unattempted calls (unknown, never counted as safe): {l.failures.map(f => `${f.monitor} ${f.kind} ×${f.count}`).join(' · ')}</p>}
    <p className="muted small-text">Usage: {l.usage?.attempts ?? 0} requests · {(l.usage?.input_tokens ?? 0).toLocaleString()} input / {(l.usage?.output_tokens ?? 0).toLocaleString()} output tokens · {l.usage?.calls_without_usage ?? 0} calls without usage data · conservative cost ${(l.usage?.spent_usd_conservative ?? 0).toFixed(4)}.</p></div>
  </Panel>
  <Panel title="LLM detection metrics" subtitle={`Recall n=${lo.tp + lo.fn}/${gl.tp + gl.fn} known unsafe · FPR n=${lo.fp + lo.tn}/${gl.fp + gl.tn} known legitimate (Local/Global)`}>
   <div className="rq1-chart" aria-label="LLM detection metrics chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} barGap={2}><CartesianGrid vertical={false} stroke={c.grid}/><XAxis dataKey="metric" stroke={c.axis} fontSize={10}/><YAxis domain={[0, 1]} stroke={c.axis} fontSize={10}/><Tooltip {...tip} formatter={fmt}/><Legend/>
    <Bar dataKey="LLM Local" fill={c['LLM Local']} radius={[4, 4, 0, 0]}><LabelList dataKey="LLM Local" position="top" formatter={fmt} fontSize={9} fill={c.axis}/></Bar>
    <Bar dataKey="LLM Global" fill={c['LLM Global']} radius={[4, 4, 0, 0]}><LabelList dataKey="LLM Global" position="top" formatter={fmt} fontSize={9} fill={c.axis}/></Bar></BarChart></ResponsiveContainer></div>
  </Panel>
 </>
}

function FindingsTab({ f, onScenario }: { f: Findings; onScenario: (s: string) => void }) {
 const sections: [string, string][] = [...(f.llm_performance ? [['LLM arm', f.llm_performance] as [string, string]] : []), ['Experiment summary', f.experiment_summary], ['Detection performance', f.detection_performance], ['False positives', f.false_positives], ['Detection latency', f.detection_latency], ['Context analysis', f.context_analysis], ['Gateway and prevention', f.gateway_and_prevention]]
 const cases: [string, string][] = [['only_global_ids', 'Detected only by Global'], ['only_local_ids', 'Detected only by Local'], ['neither_ids', 'Missed by both']]
 return <Panel title="Research findings" subtitle={f.statement} action={<div className="rq1-row-actions"><CopyButton text={f.copy.results_summary} label="COPY RESULTS SUMMARY" testId="copy-summary"/><CopyButton text={f.copy.metrics_table} label="COPY METRICS TABLE"/><CopyButton text={f.copy.methodology} label="COPY METHODOLOGY DETAILS"/><CopyButton text={f.copy.limitations} label="COPY LIMITATIONS"/></div>}>
  <div className="rq1-body">
   <Section title="Research question"><p>{f.research_question}</p></Section>
   <Section title="Pre-registered decision rule"><p><RuleTag rule={f.decision_rule}/> {f.decision_rule.note}</p>
    {f.decision_rule.global_recall_higher !== undefined && <ul className="rq1-list"><li>Global recall exceeds Local recall: {String(f.decision_rule.global_recall_higher)}</li><li>Global FPR ≤ Local FPR + 1/12: {String(f.decision_rule.fpr_within_margin)}</li><li>All observation-boundary checks passed: {String(f.decision_rule.observation_checks_passed)}</li></ul>}</Section>
   {sections.map(([t, body]) => <Section key={t} title={t}><p>{body}</p></Section>)}
   <Section title="Interesting cases">{cases.map(([k, name]) => <p key={k}><b>{name}:</b> {(f.interesting_cases[k] ?? []).length ? f.interesting_cases[k].map(s => <button key={s} className="link-text" style={{ marginRight: 10, background: 'none', border: 0, color: 'var(--accent)', padding: 0 }} onClick={() => onScenario(s)}>{s}</button>) : <span className="muted">none</span>}</p>)}</Section>
   <Section title="Limitations"><ul className="rq1-list">{f.limitations.map(l => <li key={l}>{l}</li>)}</ul></Section>
  </div>
 </Panel>
}

type Metadata = { experiment: Experiment; results_metadata: Record<string, unknown> | null; scoring: Record<string, unknown>; dataset: Record<string, unknown> | null; verification: Record<string, unknown> | null; crosscheck: string[] | null; reproducibility: { trial: number; predictions_identical: boolean; checkpoints_identical: boolean }[] | null; methodology: string | null; limitations: string[] }

function DetailsTab({ eid, e }: { eid: string; e: Experiment }) {
 const meta = useRQ1<Metadata>(`/api/research/rq1/experiments/${eid}/metadata`, isTerminal(e.status) ? false : 2000)
 if (meta.error) return <ErrorBox error={meta.error}/>
 if (!meta.data) return <Loading/>
 const m = meta.data, rm = m.results_metadata ?? {}, v = m.verification as { checks_passed?: boolean; contexts_checked?: number; failures?: string[]; checks?: string[] } | null
 return <>
  <Panel title="Experimental metadata">
   <div className="rq1-body"><div className="rq1-grid two">
    <KV rows={[['Experiment ID', eid], ['Research question', 'RQ1'], ['Hypothesis', 'H1: global workflow monitoring improves recall without an unacceptable increase in false positives'], ['Dataset', e.config.partition], ['Dataset version', e.validation.dataset_version], ['Dataset hash', <span className="mono">{e.validation.dataset_hash}</span>], ['Scenarios', String(e.validation.selected_scenarios)], ['Trials', String(e.config.trials)], ['Execution mode', `Scripted agents${e.llm?.mode && e.llm.mode !== 'none' ? ` · ${e.llm.mode} LLM arm` : ' · rules only'}`], ['Monitor configurations', 'local_action, global_workflow']]}/>
    <KV rows={[['Model identifiers', JSON.stringify(rm.monitor_implementations ?? 'RiskAnalyzer')], ['Prompt versions', e.llm?.prompt_version ? `${e.llm.prompt_version} (sha ${String(e.llm.prompt_hash ?? '').slice(0, 12)}…)` : 'Not applicable (no LLM arm)'], ['LLM', e.llm?.mode && e.llm.mode !== 'none' ? `${e.llm.mode} · ${e.llm.provider ?? ''} · ${e.llm.model ?? ''} · ${e.llm.checkpoint_policy} checkpoints · ${e.llm.protocol_version}` : 'none'], ['LLM caps', e.config.llm_mode !== 'none' ? `${e.config.llm_max_requests} requests · ${e.config.llm_max_cost_usd !== null ? `$${e.config.llm_max_cost_usd}` : 'no cost (mock)'} · paid calls confirmed: ${e.config.confirm_paid_calls ? 'yes' : 'no'}` : '—'], ['Ruleset', String(m.scoring.ruleset_version ?? '—')], ['Scoring rules', String(m.scoring.primary_metric ?? '—')], ['Positive mapping', String(m.scoring.positive_mapping ?? '—')], ['Random seed', String(e.config.seed)], ['Execution limits', `${e.config.max_runtime_seconds}s runtime · ${e.config.max_steps_per_workflow} checkpoints/workflow · 0 model requests · $0`], ['Started / completed', `${e.started_at ? new Date(e.started_at).toLocaleString() : '—'} / ${e.completed_at ? new Date(e.completed_at).toLocaleString() : '—'}`], ['Code revision', e.code_revision], ['Protocol', String(m.scoring.protocol_version)]]}/>
   </div></div>
  </Panel>
  <Panel title="Research methodology" subtitle="Built from this experiment's configuration and records"><div className="rq1-body"><p>{m.methodology ?? 'Available when the experiment has results.'}</p></div></Panel>
  <Panel title="Research validity">
   <div className="rq1-body">
    <ValidationReport v={e.validation}/>
    <Section title="Observation verification">{v ? <p><Tag value={v.checks_passed ? 'PASS' : 'FAIL'}/> {v.contexts_checked} observations re-derived in a fresh simulator. {v.failures?.length ? v.failures.join('; ') : 'No failures.'}</p> : <p className="muted">Not run yet.</p>}{v?.checks && <ul className="rq1-list">{v.checks.map(c => <li key={c}>{c}</li>)}</ul>}</Section>
    <Section title="Evidence cross-check">{m.crosscheck ? <p><Tag value={m.crosscheck.length ? 'FAIL' : 'PASS'}/> {m.crosscheck.length ? m.crosscheck.join('; ') : 'Every stored monitor input/output matches the scored run (snapshot digest and recommendation).'}</p> : <p className="muted">Not run yet.</p>}</Section>
    <Section title="Reproducibility trials">{m.reproducibility?.length ? <ul className="rq1-list">{m.reproducibility.map(t => <li key={t.trial}>Trial {t.trial}: predictions {t.predictions_identical ? 'identical' : 'DIFFERENT'}, checkpoints {t.checkpoints_identical ? 'identical' : 'DIFFERENT'}</li>)}</ul> : <p className="muted">One trial configured.</p>}</Section>
    <Section title="Missing records and incomplete evaluations"><p>{e.failures.length ? e.failures.map(f => `${f.workflow_id}: ${f.error}`).join('; ') : 'None recorded.'} {e.integrity_problems?.length ? `Integrity problems: ${e.integrity_problems.join('; ')}` : ''}</p></Section>
    <Section title="Known limitations"><ul className="rq1-list">{m.limitations.map(l => <li key={l}>{l}</li>)}</ul></Section>
    <OriginalJson value={m}/>
   </div>
  </Panel>
 </>
}
