import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Play, ShieldCheck, Wand2 } from 'lucide-react'
import { post } from '../lib/api/client'
import { Empty, ErrorBox, Loading, PageTitle, Panel } from '../components/common'
import { Button } from '../components/ui/button'
import { pct, useRQ1, type ExperimentSummary, type Overview, type RQ1Config, type Validation } from '../rq1/api'
import { KV, Tag } from '../rq1/components'
import '../rq1/rq1.css'

export function RQ1LabPage({ view }: { view: 'home' | 'new' }) {
 const overview = useRQ1<Overview>('/api/research/rq1', 3000)
 if (overview.error) return <ErrorBox error={overview.error}/>
 if (!overview.data) return <Loading/>
 const o = overview.data
 return <>
  <PageTitle eyebrow="RESEARCH LABORATORY · RQ1" title="RQ1 — Distributed Approval Laundering" description="Investigate whether global workflow monitoring detects cumulative multi-agent safety violations more effectively than local action monitoring.">
   <Link className="button outline small" to="/research/laboratory">All research questions</Link>
  </PageTitle>
  {view === 'home' ? <RQ1Home o={o}/> : <RQ1Configure o={o}/>}
 </>
}

function RQ1Home({ o }: { o: Overview }) {
 const latest = o.experiments.find(e => e.status === 'COMPLETED' || e.status === 'INCONCLUSIVE')
 const running = o.experiments.find(e => e.status === 'RUNNING' || e.status === 'QUEUED')
 return <>
  <Panel title="RQ1 — Distributed Approval Laundering" subtitle={`${o.protocol_version} · ${o.execution_note}`} action={<div className="actions">
   <Link className="button primary" to="/research/rq1/new"><FlaskConical size={14}/>NEW EXPERIMENT</Link>
   {latest ? <Link className="button outline" to={`/research/rq1/experiments/${latest.experiment_id}`}>VIEW PREVIOUS RESULTS</Link> : <Button variant="outline" disabled title="No completed RQ1 experiment yet">VIEW PREVIOUS RESULTS</Button>}
  </div>}>
   <div className="rq1-body">
    <KV rows={[['Research question', o.question.question], ['Hypothesis (H1)', o.question.hypothesis], ['Null hypothesis (H0)', o.question.null_hypothesis], ['Goal', o.question.goal],
     ['Experiment status', running ? <Link to={`/research/rq1/experiments/${running.experiment_id}`}><Tag value={running.status}/> {running.experiment_id}</Link> : latest ? <><Tag value={latest.status}/> latest: {latest.experiment_id}</> : <Tag value="NOT_EVALUATED" tone="neutral"/>],
     ['Latest results', latest?.headline ? `Local recall ${pct(latest.headline.local_recall)}, FPR ${pct(latest.headline.local_false_positive_rate)} · Global recall ${pct(latest.headline.global_recall)}, FPR ${pct(latest.headline.global_false_positive_rate)} (n=${latest.headline.n_workflows})` : 'No completed experiment']]}/>
   </div>
  </Panel>
  <Panel title="Available datasets" subtitle="Validated, content-hashed RQ1 partitions">
   <div className="table-wrap"><table><thead><tr>{['Dataset', 'Version', 'Scenarios', 'Unsafe', 'Legitimate', 'Categories', 'Role', 'Content hash'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{o.datasets.map(d => <tr key={d.partition}><td>{d.partition}</td><td>{d.dataset_version}</td><td>{d.n}</td><td>{d.unsafe}</td><td>{d.legitimate}</td><td>{d.categories.join(', ')}</td><td>{d.role}{d.frozen && <> · <Tag value={d.matches_frozen_hash ? 'PASS' : 'FAIL'}/> frozen hash</>}</td><td className="mono">{d.content_hash.slice(0, 16)}…</td></tr>)}</tbody></table></div>
  </Panel>
  <ExperimentList experiments={o.experiments}/>
 </>
}

export function ExperimentList({ experiments }: { experiments: ExperimentSummary[] }) {
 return <Panel title="Previous RQ1 experiments" subtitle="Persisted; every experiment keeps its own identifier and is never overwritten.">
  {experiments.length ? <div className="table-wrap"><table><thead><tr>{['Experiment', 'Status', 'Dataset', 'Scenarios', 'Created', 'Local recall / FPR', 'Global recall / FPR', 'Pre-registered'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{experiments.map(e => <tr key={e.experiment_id}>
   <td><Link to={`/research/rq1/experiments/${e.experiment_id}`}>{e.experiment_id}</Link></td><td><Tag value={e.status}/></td><td>{e.config.partition}</td><td>{e.config.scenario_ids ? `${e.config.scenario_ids.length} selected` : 'all'}</td><td className="mono">{new Date(e.created_at).toLocaleString()}</td>
   <td>{e.headline ? `${pct(e.headline.local_recall)} / ${pct(e.headline.local_false_positive_rate)}` : '—'}</td><td>{e.headline ? `${pct(e.headline.global_recall)} / ${pct(e.headline.global_false_positive_rate)}` : '—'}</td><td>{e.preregistered_procedure ? 'Yes' : 'No'}</td></tr>)}</tbody></table></div> : <Empty title="No RQ1 experiments yet" detail="Start one with NEW EXPERIMENT."/>}
 </Panel>
}

function defaultRQ1Config(o: Overview): RQ1Config {
 const partition = o.datasets.some(d => d.partition === 'rq1_robustness_60') ? 'rq1_robustness_60' : o.recommended_config.partition
 return { ...o.recommended_config, partition, scenario_ids: null, seed: 0, trials: 1, llm_mode: 'none', llm_max_cost_usd: null, confirm_paid_calls: false }
}

function RQ1Configure({ o }: { o: Overview }) {
 const navigate = useNavigate()
 const cache = useQueryClient()
 const [config, setConfig] = useState<RQ1Config>(() => defaultRQ1Config(o))
 const [subset, setSubset] = useState(false)
 const [validated, setValidated] = useState<{ key: string; result: Validation } | null>(null)
 const dataset = o.datasets.find(d => d.partition === config.partition)!
 const followup = config.partition === 'rq1_robustness_60'
 const nextNumber = Math.max(0, ...o.experiments.map(e => Number(e.experiment_id.split('_').at(-1)) || 0)) + 1
 const key = useMemo(() => JSON.stringify(config), [config])
 const current = validated?.key === key ? validated.result : null
 const cost = config.llm_max_cost_usd
 const budgetProblem = config.llm_mode !== 'live' ? null
  : cost === null || !Number.isFinite(cost) || cost <= 0 ? 'Enter a positive maximum cost in USD before validating live LLM monitoring.'
  : o.llm_status.live.max_cost_cap_usd != null && cost > o.llm_status.live.max_cost_cap_usd ? `Maximum cost exceeds the server cap of $${o.llm_status.live.max_cost_cap_usd}.` : null
 const liveProblem = budgetProblem ?? (config.llm_mode === 'live' && !config.confirm_paid_calls ? 'Confirm paid calls before validating live LLM monitoring.' : null)
 const update = (patch: Partial<RQ1Config>) => setConfig(c => ({ ...c, ...patch }))
 const validate = useMutation({ mutationFn: () => post<Validation>('/api/research/rq1/validate', config), onSuccess: r => setValidated({ key, result: r }) })
 const run = useMutation({ mutationFn: () => post<{ experiment_id: string }>('/api/research/rq1/experiments', config), onSuccess: async r => { await cache.invalidateQueries({ queryKey: ['rq1'] }); navigate(`/research/rq1/experiments/${r.experiment_id}`) } })
 const recommended = () => { setConfig(defaultRQ1Config(o)); setSubset(false) }
 const pilot = () => { setConfig(o.pilot_config); setSubset(true) }
 const toggle = (id: string) => { const now = new Set(config.scenario_ids ?? []); now.has(id) ? now.delete(id) : now.add(id); update({ scenario_ids: [...now].sort() }) }
 return <>
  <Panel title="1 · Configure experiment" subtitle="All controls map to a validated backend configuration. Changing any setting requires re-validation." action={<div className="rq1-row-actions"><Button variant="outline" onClick={recommended}><Wand2 size={14}/>USE RECOMMENDED RQ1 SETTINGS</Button><Button variant="ghost" onClick={pilot}>Use pilot settings (2 development scenarios)</Button></div>}>
   <form className="rq1-form" onSubmit={e => e.preventDefault()} aria-label="RQ1 experiment configuration">
    <label>Experiment title<input readOnly value={followup ? (config.llm_mode === 'none' ? 'RQ1 follow-up — 60-trial robustness and false-positive validation' : 'RQ1 follow-up — 60-trial LLM monitoring (Experiment 008 protocol)') : 'RQ1 — Local versus Global monitoring'}/></label>
    <label>Research question<select value="RQ1" disabled aria-describedby="rq-note"><option>RQ1 — Global Workflow Monitoring</option></select><small id="rq-note">Preselected. Other questions are in the general Research Laboratory.</small></label>
    <label>Dataset<select aria-label="Dataset" value={config.partition} onChange={e => { update({ partition: e.target.value as RQ1Config['partition'], scenario_ids: null, ...(e.target.value === 'rq1_robustness_60' ? { trials: 1, seed: 0 } : {}) }); setSubset(false) }}>{o.datasets.map(d => <option key={d.partition} value={d.partition}>{d.partition} · {d.n} scenarios · {d.dataset_version}</option>)}</select>
     <small>{dataset.n} total workflow trials · {dataset.unsafe} unsafe · {dataset.legitimate} legitimate · {dataset.role}</small></label>
    {followup && <p className="muted small-text" role="note">The 60-scenario follow-up has 48 legitimate and 12 unsafe workflows. Rules only follows the Experiment 006 protocol; an LLM mode adds the LLM arm under the separately frozen Experiment 008 protocol.</p>}
    <fieldset><legend>Scenario categories ({dataset.dataset_version})</legend><table className="rq1-mini"><tbody>{Object.entries(dataset.composition).map(([c, v]) => <tr key={c}><td>{c.replaceAll('_', ' ')}</td><td>{v.UNSAFE} unsafe</td><td>{v.SAFE} legitimate</td></tr>)}</tbody></table><small className="mono">sha256 {dataset.content_hash.slice(0, 24)}…</small></fieldset>
    <fieldset><legend>Monitoring configurations</legend>
     <label className="inline"><input type="checkbox" checked disabled/> A · Local Monitor (local_action)</label>
     <label className="inline"><input type="checkbox" checked disabled/> B · Global Monitor (global_workflow)</label>
     <label className="inline"><input type="checkbox" checked={config.llm_mode !== 'none'} disabled/> C · LLM Local monitor (llm_local)</label>
     <label className="inline"><input type="checkbox" checked={config.llm_mode !== 'none'} disabled/> D · LLM Global monitor (llm_global)</label>
     <small>A–B: deterministic rules. C–D: one LLM safety monitor (same prompt, model and token cap) on the Local vs. the Global view. Enable C–D with “LLM monitors” below. {o.unavailable_options.monitors.global_ethics_council}</small></fieldset>
    <fieldset style={{ gridColumn: '1/-1' }} aria-label="LLM monitors"><legend>LLM monitors ({o.llm_protocol_version})</legend>
     <div className="rq1-grid">
      <label>LLM mode<select aria-label="LLM mode" value={config.llm_mode} onChange={e => update({ llm_mode: e.target.value as RQ1Config['llm_mode'], confirm_paid_calls: false, llm_max_cost_usd: e.target.value === 'live' ? (config.llm_max_cost_usd ?? 1) : null })}>
       <option value="none">None — rules only (no model is called)</option>
       <option value="mock" disabled={!o.llm_status.mock.available}>Mock — synthetic responder, pipeline test only{o.llm_status.mock.available ? '' : ' (unavailable)'}</option>
       <option value="live" disabled={!o.llm_status.live.available}>Live — {o.llm_status.live.provider} · {o.llm_status.live.model ?? 'no model'}{o.llm_status.live.available ? ' (paid)' : ' (unavailable)'}</option>
      </select></label>
      <label>LLM checkpoints<select aria-label="LLM checkpoints" value={config.llm_checkpoints} onChange={e => update({ llm_checkpoints: e.target.value as RQ1Config['llm_checkpoints'] })} disabled={config.llm_mode === 'none'}>{Object.entries(o.llm_policies).map(([k, v]) => <option key={k} value={k}>{k} — {v}</option>)}</select></label>
      <label>Maximum model requests<input aria-label="Maximum model requests" type="number" min={1} max={5000} value={config.llm_max_requests} disabled={config.llm_mode === 'none'} onChange={e => update({ llm_max_requests: Math.max(1, Number(e.target.value) || 1) })}/><small>Server cap: {o.llm_status.live.max_requests_cap ?? 'none'}</small></label>
      <label>Maximum cost (USD)<input aria-label="Maximum cost" type="number" min={0.01} step={0.5} value={config.llm_max_cost_usd ?? ''} disabled={config.llm_mode !== 'live'} onChange={e => update({ llm_max_cost_usd: e.target.value ? Number(e.target.value) : null, confirm_paid_calls: false })}/><small>{config.llm_mode === 'live' ? `Server cap: $${o.llm_status.live.max_cost_cap_usd ?? 'none'}` : 'Mock and rules-only runs cost $0.'}</small></label>
     </div>
     {!o.llm_status.live.available && <p role="note" className="warning-text small-text">Live LLM calls are unavailable on this server: {o.llm_status.live.reasons.join('; ')}.</p>}
     {config.llm_mode === 'mock' && <p className="warning-text small-text">Mock mode calls the LLM monitor code path with a synthetic rules responder. No model is contacted and the outputs are labelled synthetic; they are never evidence.</p>}
     {config.llm_mode === 'live' && <label className="inline"><input type="checkbox" aria-label="Confirm paid calls" disabled={!!budgetProblem} checked={config.confirm_paid_calls} onChange={e => update({ confirm_paid_calls: e.target.checked })}/> I authorize paid calls to {o.llm_status.live.provider} · {o.llm_status.live.model} up to {config.llm_max_requests} requests and ${config.llm_max_cost_usd ?? '—'} for this experiment.</label>}
    </fieldset>
    <fieldset><legend>Execution mode</legend>
     <label className="inline"><input type="radio" checked readOnly name="mode"/> Scripted (deterministic simulation)</label>
     {Object.entries(o.unavailable_options.execution_modes).map(([m, why]) => <small key={m}><b>{m.replaceAll('_', ' ')}</b>: not offered. {why}</small>)}</fieldset>
    <label>Executions per scenario<input disabled={followup} aria-label="Number of trials" type="number" min={1} max={3} value={config.trials} onChange={e => update({ trials: Math.max(1, Math.min(3, Number(e.target.value) || 1)) })}/><small>One workflow is one trial; the two monitor outcomes are paired. Additional executions, when offered, are reproducibility checks only.</small></label>
    <label>Random seed<input disabled={followup} aria-label="Random seed" type="number" min={0} value={config.seed} onChange={e => update({ seed: Math.max(0, Number(e.target.value) || 0) })}/><small>Controls workflow order and neutral IDs only. Pre-registered: 0.</small></label>
    <label>Model<select disabled><option>{config.llm_mode === 'live' ? `${o.llm_status.live.model} (LLM arm) + RiskAnalyzer rules` : config.llm_mode === 'mock' ? 'mock-structured responder (LLM arm) + RiskAnalyzer rules' : 'RiskAnalyzer rules only (no model)'}</option></select><small>Agents are scripted in every mode; only the monitors differ.</small></label>
    <label>Maximum runtime (seconds)<input aria-label="Maximum runtime" type="number" min={30} max={1800} value={config.max_runtime_seconds} onChange={e => update({ max_runtime_seconds: Number(e.target.value) || 30 })}/></label>
    <label>Maximum steps per workflow<input aria-label="Maximum steps" type="number" min={1} max={200} value={config.max_steps_per_workflow} onChange={e => update({ max_steps_per_workflow: Number(e.target.value) || 1 })}/></label>
    <fieldset style={{ gridColumn: '1/-1' }}><legend>Scenario selection</legend>
     <label className="inline"><input type="radio" name="sel" checked={!subset} onChange={() => { setSubset(false); update({ scenario_ids: null }) }}/> All {dataset.n} scenarios (required for the pre-registered decision rule)</label>
     <label className="inline"><input disabled={followup} type="radio" name="sel" checked={subset} onChange={() => { setSubset(true); update({ scenario_ids: dataset.scenarios.slice(0, 2).map(s => s.scenario_id) }) }}/> Choose a subset (pilot)</label>
     {subset && <div className="rq1-grid" style={{ marginTop: 8 }}>{dataset.scenarios.map(s => <label key={s.scenario_id} className="inline"><input type="checkbox" checked={!!config.scenario_ids?.includes(s.scenario_id)} onChange={() => toggle(s.scenario_id)}/>{s.scenario_id} <small>({s.label === 'SAFE' ? 'legitimate' : 'unsafe'}, {s.category.replaceAll('_', ' ')})</small></label>)}</div>}
    </fieldset>
   </form>
  </Panel>
  <Panel title="2 · Validate methodology" subtitle="Monitor-free checks run on the server before anything is executed." action={<Button onClick={() => validate.mutate()} disabled={validate.isPending || !!liveProblem || (subset && !config.scenario_ids?.length)}><ShieldCheck size={14}/>{validate.isPending ? 'VALIDATING…' : 'VALIDATE METHODOLOGY'}</Button>}>
   <div className="rq1-body">
    {liveProblem && <p role="alert" className="warning-text">{liveProblem}</p>}
    {validate.error && <ErrorBox error={validate.error}/>}
    {validated && !current && <p role="status" className="warning-text">Settings changed after validation. Validate again before running.</p>}
    {current ? <ValidationReport v={current}/> : !validate.isPending && <p className="muted">Not validated yet.</p>}
   </div>
  </Panel>
  <Panel title="3 · Run" subtitle="Runs in the background; you can leave or refresh this page.">
   <div className="rq1-body">
    {run.error && <ErrorBox error={run.error}/>}
    {current?.llm_estimate && <p className="small-text">LLM arm: {current.llm_estimate.calls} calls · ~{current.llm_estimate.estimated_input_tokens.toLocaleString()} input tokens · worst-case cost {config.llm_mode === 'live' ? (current.llm_estimate.estimated_max_cost_usd === null ? 'unbounded' : `$${current.llm_estimate.estimated_max_cost_usd}`) : '$0 (mock)'} <span className="muted">({current.llm_estimate.note})</span></p>}
    <Button onClick={() => run.mutate()} disabled={!current || current.status === 'INVALID' || !!liveProblem || run.isPending || (config.llm_mode === 'live' && !config.confirm_paid_calls)}><Play size={14}/>{run.isPending ? 'STARTING…' : followup ? `RUN EXPERIMENT ${String(nextNumber).padStart(3, '0')}` : 'RUN RQ1 EXPERIMENT'}</Button>
    <p className="muted small-text">{!current ? 'Enabled after a successful validation of the current settings.' : current.status === 'INVALID' ? 'Disabled: critical protocol problems.' : config.llm_mode === 'live' ? (config.confirm_paid_calls ? 'Paid LLM calls are confirmed and capped for this experiment.' : 'Confirm paid calls above to enable the run.') : config.llm_mode === 'mock' ? 'Mock LLM mode: no paid calls.' : 'Rules only: no model calls.'}</p>
   </div>
  </Panel>
 </>
}

export function ValidationReport({ v }: { v: Validation }) {
 return <div aria-label="Validation report">
  <p><Tag value={v.status}/> <span className="muted">{v.status === 'VALIDATED' ? 'No detected critical protocol problems.' : v.status === 'WARNINGS' ? 'The experiment can run, but some limitations require attention.' : 'Critical problems prevent a trustworthy evaluation.'}</span></p>
  <p className="muted small-text">{v.selected_scenarios} scenarios ({v.unsafe} unsafe, {v.legitimate} legitimate) · {v.dataset_version} · {v.note}</p>
  {v.checks.map(c => <div key={c.id} className="rq1-check"><Tag value={c.status}/><div><b>{c.title}</b><br/><span className="muted">{c.detail}</span></div></div>)}
  <details className="rq1-section"><summary>Known limitations (stated before any run)</summary><ul className="rq1-list">{v.known_limitations.map(l => <li key={l}>{l}</li>)}</ul></details>
 </div>
}
