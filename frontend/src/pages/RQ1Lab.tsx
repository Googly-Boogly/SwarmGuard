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

function RQ1Configure({ o }: { o: Overview }) {
 const navigate = useNavigate()
 const cache = useQueryClient()
 const [config, setConfig] = useState<RQ1Config>(o.recommended_config)
 const [subset, setSubset] = useState(false)
 const [validated, setValidated] = useState<{ key: string; result: Validation } | null>(null)
 const dataset = o.datasets.find(d => d.partition === config.partition)!
 const key = useMemo(() => JSON.stringify(config), [config])
 const current = validated?.key === key ? validated.result : null
 const update = (patch: Partial<RQ1Config>) => setConfig(c => ({ ...c, ...patch }))
 const validate = useMutation({ mutationFn: () => post<Validation>('/api/research/rq1/validate', config), onSuccess: r => setValidated({ key, result: r }) })
 const run = useMutation({ mutationFn: () => post<{ experiment_id: string }>('/api/research/rq1/experiments', config), onSuccess: async r => { await cache.invalidateQueries({ queryKey: ['rq1'] }); navigate(`/research/rq1/experiments/${r.experiment_id}`) } })
 const recommended = () => { setConfig(o.recommended_config); setSubset(false) }
 const pilot = () => { setConfig(o.pilot_config); setSubset(true) }
 const toggle = (id: string) => { const now = new Set(config.scenario_ids ?? []); now.has(id) ? now.delete(id) : now.add(id); update({ scenario_ids: [...now].sort() }) }
 return <>
  <Panel title="1 · Configure experiment" subtitle="All controls map to a validated backend configuration. Changing any setting requires re-validation." action={<div className="rq1-row-actions"><Button variant="outline" onClick={recommended}><Wand2 size={14}/>USE RECOMMENDED RQ1 SETTINGS</Button><Button variant="ghost" onClick={pilot}>Use pilot settings (2 development scenarios)</Button></div>}>
   <form className="rq1-form" onSubmit={e => e.preventDefault()} aria-label="RQ1 experiment configuration">
    <label>Research question<select value="RQ1" disabled aria-describedby="rq-note"><option>RQ1 — Global Workflow Monitoring</option></select><small id="rq-note">Preselected. Other questions are in the general Research Laboratory.</small></label>
    <label>Dataset<select aria-label="Dataset" value={config.partition} onChange={e => { update({ partition: e.target.value as RQ1Config['partition'], scenario_ids: null }); setSubset(false) }}>{o.datasets.map(d => <option key={d.partition} value={d.partition}>{d.partition} · {d.dataset_version}</option>)}</select>
     <small>{dataset.n} scenarios · {dataset.unsafe} unsafe · {dataset.legitimate} legitimate · {dataset.role}</small></label>
    <fieldset><legend>Scenario categories ({dataset.dataset_version})</legend><table className="rq1-mini"><tbody>{Object.entries(dataset.composition).map(([c, v]) => <tr key={c}><td>{c.replaceAll('_', ' ')}</td><td>{v.UNSAFE} unsafe</td><td>{v.SAFE} legitimate</td></tr>)}</tbody></table><small className="mono">sha256 {dataset.content_hash.slice(0, 24)}…</small></fieldset>
    <fieldset><legend>Monitoring configurations</legend>
     <label className="inline"><input type="checkbox" checked disabled/> A · Local Monitor (local_action)</label>
     <label className="inline"><input type="checkbox" checked disabled/> B · Global Monitor (global_workflow)</label>
     <label className="inline"><input type="checkbox" disabled/> C · Global + Ethics Council — unavailable</label>
     <small>{o.unavailable_options.monitors.global_ethics_council}</small></fieldset>
    <fieldset><legend>Execution mode</legend>
     <label className="inline"><input type="radio" checked readOnly name="mode"/> Scripted (deterministic simulation)</label>
     {Object.entries(o.unavailable_options.execution_modes).map(([m, why]) => <small key={m}><b>{m.replaceAll('_', ' ')}</b>: not offered. {why}</small>)}</fieldset>
    <label>Number of trials<input aria-label="Number of trials" type="number" min={1} max={3} value={config.trials} onChange={e => update({ trials: Math.max(1, Math.min(3, Number(e.target.value) || 1)) })}/><small>Trial 1 is scored. Trials 2–3 are deterministic reproducibility checks, not extra evidence.</small></label>
    <label>Random seed<input aria-label="Random seed" type="number" min={0} value={config.seed} onChange={e => update({ seed: Math.max(0, Number(e.target.value) || 0) })}/><small>Controls workflow order and neutral IDs only. Pre-registered: 0.</small></label>
    <label>Model<select disabled><option>Not applicable — RiskAnalyzer (deterministic rules)</option></select><small>No model is called in Scripted mode.</small></label>
    <label>Maximum runtime (seconds)<input aria-label="Maximum runtime" type="number" min={30} max={1800} value={config.max_runtime_seconds} onChange={e => update({ max_runtime_seconds: Number(e.target.value) || 30 })}/></label>
    <label>Maximum steps per workflow<input aria-label="Maximum steps" type="number" min={1} max={200} value={config.max_steps_per_workflow} onChange={e => update({ max_steps_per_workflow: Number(e.target.value) || 1 })}/></label>
    <label>Maximum model requests<input type="number" value={0} disabled/><small>Fixed at 0: deterministic monitors make no requests.</small></label>
    <label>Maximum estimated cost (USD)<input type="number" value={0} disabled/><small>Fixed at $0.</small></label>
    <fieldset style={{ gridColumn: '1/-1' }}><legend>Scenario selection</legend>
     <label className="inline"><input type="radio" name="sel" checked={!subset} onChange={() => { setSubset(false); update({ scenario_ids: null }) }}/> All {dataset.n} scenarios (required for the pre-registered decision rule)</label>
     <label className="inline"><input type="radio" name="sel" checked={subset} onChange={() => { setSubset(true); update({ scenario_ids: dataset.scenarios.slice(0, 2).map(s => s.scenario_id) }) }}/> Choose a subset (pilot)</label>
     {subset && <div className="rq1-grid" style={{ marginTop: 8 }}>{dataset.scenarios.map(s => <label key={s.scenario_id} className="inline"><input type="checkbox" checked={!!config.scenario_ids?.includes(s.scenario_id)} onChange={() => toggle(s.scenario_id)}/>{s.scenario_id} <small>({s.label === 'SAFE' ? 'legitimate' : 'unsafe'}, {s.category.replaceAll('_', ' ')})</small></label>)}</div>}
    </fieldset>
   </form>
  </Panel>
  <Panel title="2 · Validate methodology" subtitle="Monitor-free checks run on the server before anything is executed." action={<Button onClick={() => validate.mutate()} disabled={validate.isPending || (subset && !config.scenario_ids?.length)}><ShieldCheck size={14}/>{validate.isPending ? 'VALIDATING…' : 'VALIDATE METHODOLOGY'}</Button>}>
   <div className="rq1-body">
    {validate.error && <ErrorBox error={validate.error}/>}
    {validated && !current && <p role="status" className="warning-text">Settings changed after validation. Validate again before running.</p>}
    {current ? <ValidationReport v={current}/> : !validate.isPending && <p className="muted">Not validated yet.</p>}
   </div>
  </Panel>
  <Panel title="3 · Run" subtitle="Runs in the background; you can leave or refresh this page.">
   <div className="rq1-body">
    {run.error && <ErrorBox error={run.error}/>}
    <Button onClick={() => run.mutate()} disabled={!current || current.status === 'INVALID' || run.isPending}><Play size={14}/>{run.isPending ? 'STARTING…' : 'RUN RQ1 EXPERIMENT'}</Button>
    <p className="muted small-text">{!current ? 'Enabled after a successful validation of the current settings.' : current.status === 'INVALID' ? 'Disabled: critical protocol problems.' : 'No paid calls: Scripted mode needs no confirmation.'}</p>
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
