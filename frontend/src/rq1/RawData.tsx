import { Fragment, useEffect, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Columns3, Download, X } from 'lucide-react'
import { ErrorBox, Loading, Panel, SearchInput } from '../components/common'
import { Button } from '../components/ui/button'
import { num, useRQ1, type Json, type RawPage, type RawRow } from './api'
import { Correct, CopyButton, KV, OriginalJson, Section, Tag } from './components'

type Level = 'checkpoint' | 'workflow'
type Column = { key: string; title: string; render?: (r: RawRow) => ReactNode; hidden?: boolean }
const yes = (v: unknown) => <Tag value={v === null || v === undefined ? null : v ? 'YES' : 'NO'} tone="neutral"/>
const COLUMNS: Record<Level, Column[]> = {
 checkpoint: [
  { key: 'scenario_id', title: 'Scenario ID' }, { key: 'workflow_id', title: 'Workflow ID' }, { key: 'trial', title: 'Trial' }, { key: 'step', title: 'Step' },
  { key: 'agent_id', title: 'Agent' }, { key: 'tool', title: 'Action' }, { key: 'ground_truth', title: 'Ground truth (workflow)', render: r => <Tag value={String(r.ground_truth)}/> },
  { key: 'monitor', title: 'Monitor' }, { key: 'recommendation', title: 'Prediction (checkpoint)', render: r => <Tag value={String(r.recommendation)}/> },
  { key: 'risk_level', title: 'Risk level', render: r => <Tag value={String(r.risk_level)} tone="neutral"/> },
  { key: 'workflow_correct', title: 'Workflow-level correct?', render: r => <Correct value={r.workflow_correct as boolean}/> },
  { key: 'position', title: 'Detection time', render: r => <span>{String(r.position)} <small className="muted">(critical step {String(r.critical_step)})</small></span> },
  { key: 'latency_ms', title: 'Latency (ms)', render: r => num(r.latency_ms) }, { key: 'status', title: 'Status', render: r => <Tag value={r.status}/> },
  { key: 'category', title: 'Category', hidden: true }, { key: 'checkpoint_seq', title: 'Event seq', hidden: true }, { key: 'alert', title: 'Alert', hidden: true, render: r => yes(r.alert) },
  { key: 'uncertainty', title: 'Uncertainty', hidden: true }, { key: 'detected_patterns', title: 'Patterns', hidden: true, render: r => (r.detected_patterns as string[]).join(', ') || '—' },
  { key: 'workflow_prediction', title: 'Workflow prediction', hidden: true, render: r => <Tag value={String(r.workflow_prediction)}/> },
  { key: 'gateway_decision', title: 'Gateway decision', hidden: true, render: r => <Tag value={r.gateway_decision as string}/> }, { key: 'executed', title: 'Executed', hidden: true, render: r => yes(r.executed) }],
 workflow: [
  { key: 'scenario_id', title: 'Scenario ID' }, { key: 'workflow_id', title: 'Workflow ID' }, { key: 'trial', title: 'Trial' }, { key: 'category', title: 'Category' },
  { key: 'ground_truth', title: 'Ground truth', render: r => <Tag value={r.ground_truth as string}/> }, { key: 'monitor', title: 'Monitor' },
  { key: 'workflow_prediction', title: 'Prediction (timely)', render: r => <Tag value={r.workflow_prediction as string}/> }, { key: 'outcome', title: 'Outcome', render: r => <Tag value={r.outcome as string}/> },
  { key: 'workflow_correct', title: 'Correct?', render: r => <Correct value={r.workflow_correct as boolean}/> },
  { key: 'first_detection_step', title: 'First alert step', render: r => num(r.first_detection_step) }, { key: 'critical_step', title: 'Critical step' },
  { key: 'detection_lead_checkpoints', title: 'Lead (checkpoints)', render: r => num(r.detection_lead_checkpoints) }, { key: 'status', title: 'Status', render: r => <Tag value={r.status}/> },
  { key: 'alert_checkpoints', title: 'Alert checkpoints', hidden: true }, { key: 'checkpoints', title: 'Checkpoints', hidden: true },
  { key: 'any_checkpoint_prediction', title: 'Any-checkpoint prediction', hidden: true }, { key: 'authorization_result', title: 'Gateway at critical action', hidden: true, render: r => <Tag value={r.authorization_result as string}/> },
  { key: 'prohibited_outcome_occurred', title: 'Prohibited outcome (no guard)', hidden: true, render: r => yes(r.prohibited_outcome_occurred) }, { key: 'detected_patterns', title: 'Patterns', hidden: true, render: r => ((r.detected_patterns as string[] | undefined) ?? []).join(', ') || '—' }],
}
const FILTERS: Record<Level, [string, string][]> = {
 checkpoint: [['scenario_id', 'Scenario'], ['agent_id', 'Agent'], ['monitor', 'Monitor'], ['recommendation', 'Prediction'], ['alert', 'Alert (Block or Escalate)'], ['ground_truth', 'Ground truth'], ['workflow_correct', 'Correct (workflow)'], ['risk_level', 'Risk level'], ['status', 'Status'], ['category', 'Category'], ['position', 'Detection time']],
 workflow: [['scenario_id', 'Scenario'], ['monitor', 'Monitor'], ['workflow_prediction', 'Prediction'], ['ground_truth', 'Ground truth'], ['workflow_correct', 'Correct'], ['outcome', 'Outcome'], ['status', 'Status'], ['category', 'Category']],
}
const PRESETS: { name: string; level: Level; filters: Record<string, string> }[] = [
 { name: 'Local missed unsafe workflows', level: 'workflow', filters: { monitor: 'Local', ground_truth: 'UNSAFE', workflow_prediction: 'NOT_DETECTED' } },
 { name: 'Global missed unsafe workflows', level: 'workflow', filters: { monitor: 'Global', ground_truth: 'UNSAFE', workflow_prediction: 'NOT_DETECTED' } },
 { name: 'Local false positives', level: 'workflow', filters: { monitor: 'Local', ground_truth: 'SAFE', workflow_prediction: 'DETECTED' } },
 { name: 'Global false positives', level: 'workflow', filters: { monitor: 'Global', ground_truth: 'SAFE', workflow_prediction: 'DETECTED' } },
 { name: 'Local ALLOW on unsafe workflows', level: 'checkpoint', filters: { monitor: 'Local', ground_truth: 'UNSAFE', recommendation: 'ALLOW' } },
 { name: 'Global alerts on legitimate workflows', level: 'checkpoint', filters: { monitor: 'Global', ground_truth: 'SAFE', alert: 'true' } },
 { name: 'LLM Local missed unsafe workflows', level: 'workflow', filters: { monitor: 'LLM Local', ground_truth: 'UNSAFE', workflow_prediction: 'NOT_DETECTED' } },
 { name: 'LLM Global false positives', level: 'workflow', filters: { monitor: 'LLM Global', ground_truth: 'SAFE', workflow_prediction: 'DETECTED' } },
 { name: 'LLM failed or unknown', level: 'workflow', filters: { outcome: 'UNKNOWN' } },
 { name: 'All LLM calls', level: 'checkpoint', filters: { monitor: 'LLM Global' } },
]

const cell = (r: RawRow, c: Column) => c.render ? c.render(r) : r[c.key] === null || r[c.key] === undefined ? '—' : String(r[c.key])
const plain = (r: RawRow, c: Column) => { const v = r[c.key]; return v === null || v === undefined ? '' : Array.isArray(v) ? v.join(';') : String(v) }

export function RawDataTab({ eid }: { eid: string }) {
 const [level, setLevel] = useState<Level>('checkpoint')
 const [filters, setFilters] = useState<Record<string, string>>({})
 const [q, setQ] = useState(''), [sort, setSort] = useState('workflow_id'), [order, setOrder] = useState<'asc' | 'desc'>('asc')
 const [page, setPage] = useState(1), [pageSize, setPageSize] = useState(25)
 const [hidden, setHidden] = useState<Record<Level, Set<string>>>({ checkpoint: new Set(COLUMNS.checkpoint.filter(c => c.hidden).map(c => c.key)), workflow: new Set(COLUMNS.workflow.filter(c => c.hidden).map(c => c.key)) })
 const [showCols, setShowCols] = useState(false)
 const [expanded, setExpanded] = useState<string | null>(null)
 const [selected, setSelected] = useState<Set<string>>(new Set())
 const [detail, setDetail] = useState<{ wf: string; step: number; cfg: string } | null>(null)
 const [debounced, setDebounced] = useState('')
 useEffect(() => { const t = window.setTimeout(() => setDebounced(q), 250); return () => window.clearTimeout(t) }, [q])
 const params = new URLSearchParams({ level, page: String(page), page_size: String(pageSize), sort, order, ...(debounced ? { q: debounced } : {}), ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) })
 const data = useRQ1<RawPage>(`/api/research/rq1/experiments/${eid}/raw?${params}`, false, true)
 const columns = COLUMNS[level].filter(c => !hidden[level].has(c.key))
 const reset = (patch: () => void) => { patch(); setPage(1); setSelected(new Set()); setExpanded(null) }
 const tsv = (rows: RawRow[]) => [columns.map(c => c.title).join('\t'), ...rows.map(r => columns.map(c => plain(r, c)).join('\t'))].join('\n')
 const rows = data.data?.rows ?? []
 const openRow = (r: RawRow) => level === 'checkpoint' ? setDetail({ wf: r.workflow_id, step: r.step as number, cfg: r.configuration }) : setDetail({ wf: r.workflow_id, step: (r.first_detection_step as number | null) ?? (r.critical_step as number), cfg: r.configuration })
 const active = Object.values(filters).filter(Boolean).length + (debounced ? 1 : 0)
 return <Panel title="Raw experimental data" subtitle="One row per monitor evaluation at a decision point (checkpoint level) or per monitor per workflow (workflow level). Server-side filtering, sorting and pagination over stored records." action={<div className="rq1-row-actions">
  <a className="button outline small" href={`/api/research/rq1/experiments/${eid}/download?level=${level}&format=csv`} download><Download size={13}/>CSV</a>
  <a className="button outline small" href={`/api/research/rq1/experiments/${eid}/download?level=${level}&format=json`} download><Download size={13}/>JSON</a></div>}>
  <div className="rq1-filters" role="group" aria-label="Raw data controls">
   <label>Level<select aria-label="Level" value={level} onChange={e => reset(() => { setLevel(e.target.value as Level); setFilters({}); setSort('workflow_id') })}><option value="checkpoint">Checkpoint evaluations</option><option value="workflow">Workflow predictions</option></select></label>
   {FILTERS[level].map(([key, name]) => <label key={key}>{name}<select aria-label={`Filter ${name}`} value={filters[key] ?? ''} onChange={e => reset(() => setFilters(f => ({ ...f, [key]: e.target.value })))}><option value="">All</option>{(data.data?.facets[key] ?? []).map(v => <option key={v} value={v}>{v.replaceAll('_', ' ')}</option>)}</select></label>)}
   <div className="rq1-search"><SearchInput value={q} onChange={v => { setQ(v); setPage(1) }} label="Search all fields"/></div>
   {active > 0 && <Button variant="ghost" size="sm" onClick={() => reset(() => { setFilters({}); setQ('') })}><X size={13}/>Clear filters</Button>}
  </div>
  <div className="rq1-filters" aria-label="Preset views"><span className="muted small-text">Quick views:</span>{PRESETS.map(p => <Button key={p.name} variant="outline" size="sm" onClick={() => reset(() => { setLevel(p.level); setFilters(p.filters); setQ('') })}>{p.name}</Button>)}</div>
  <div className="rq1-pager">
   <span role="status">{data.isPlaceholderData ? 'Updating… ' : ''}{data.data ? `${data.data.total} matching rows · page ${data.data.page} of ${data.data.pages}` : 'Loading…'}{selected.size > 0 && ` · ${selected.size} selected`}</span>
   <div className="rq1-row-actions">
    <Button variant="ghost" size="sm" aria-expanded={showCols} onClick={() => setShowCols(!showCols)}><Columns3 size={13}/>Columns</Button>
    <CopyButton label="Copy selected" text={() => tsv(rows.filter(r => selected.has(r.row_id)))}/>
    <CopyButton label="Copy visible table" text={() => tsv(rows)}/>
   </div>
  </div>
  {showCols && <div className="rq1-filters" aria-label="Column visibility">{COLUMNS[level].map(c => <label key={c.key} style={{ flexDirection: 'row', alignItems: 'center', textTransform: 'none' }}><input type="checkbox" checked={!hidden[level].has(c.key)} onChange={() => setHidden(h => { const s = new Set(h[level]); s.has(c.key) ? s.delete(c.key) : s.add(c.key); return { ...h, [level]: s } })}/>{c.title}</label>)}</div>}
  {data.error && <ErrorBox error={data.error}/>}
  {!data.data ? <Loading/> : rows.length === 0 ? <div className="empty"><h3>No rows match these filters</h3><p>Clear or change a filter to see records.</p></div> :
   <div className="rq1-table-wrap"><table aria-label="Raw data table"><thead><tr>
    <th><input type="checkbox" aria-label="Select all visible rows" checked={rows.every(r => selected.has(r.row_id))} onChange={e => setSelected(e.target.checked ? new Set(rows.map(r => r.row_id)) : new Set())}/></th><th/>
    {columns.map(c => <th key={c.key} aria-sort={sort === c.key ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}><button onClick={() => { if (sort === c.key) setOrder(order === 'asc' ? 'desc' : 'asc'); else { setSort(c.key); setOrder('asc') } setPage(1) }}>{c.title}{sort === c.key ? (order === 'asc' ? ' ▲' : ' ▼') : ''}</button></th>)}<th>Details</th></tr></thead>
    <tbody>{rows.map(r => <Fragment key={r.row_id}>
     <tr className={selected.has(r.row_id) ? 'selected' : ''}>
      <td><input type="checkbox" aria-label={`Select ${r.row_id}`} checked={selected.has(r.row_id)} onChange={() => setSelected(s => { const n = new Set(s); n.has(r.row_id) ? n.delete(r.row_id) : n.add(r.row_id); return n })}/></td>
      <td><button className="icon-button" aria-label={expanded === r.row_id ? 'Collapse row' : 'Expand row'} aria-expanded={expanded === r.row_id} onClick={() => setExpanded(expanded === r.row_id ? null : r.row_id)}>{expanded === r.row_id ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}</button></td>
      {columns.map(c => <td key={c.key}>{cell(r, c)}</td>)}
      <td>{r.status !== 'FAILED' && <Button variant="outline" size="sm" onClick={() => openRow(r)}>Open record</Button>}</td></tr>
     {expanded === r.row_id && <tr className="rq1-expanded"><td colSpan={columns.length + 3}><KV rows={Object.entries(r).map(([k, v]) => [k, Array.isArray(v) ? v.join(', ') || '—' : v === null ? 'null' : String(v)])}/></td></tr>}
    </Fragment>)}</tbody></table></div>}
  <div className="rq1-pager">
   <label className="muted small-text">Rows per page <select aria-label="Rows per page" value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}>{[10, 25, 50, 100, 200].map(n => <option key={n}>{n}</option>)}</select></label>
   <div className="rq1-row-actions"><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(1)}>First</Button><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button variant="outline" size="sm" disabled={!data.data || page >= data.data.pages} onClick={() => setPage(page + 1)}>Next</Button><Button variant="outline" size="sm" disabled={!data.data || page >= data.data.pages} onClick={() => setPage(data.data!.pages)}>Last</Button></div>
  </div>
  {detail && <RecordPanel eid={eid} {...detail} onClose={() => setDetail(null)}/>}
 </Panel>
}

type Detail = { llm_prompt?: Json & { user_content: string }; basic: Json & { action: Json }; ground_truth: Json & { label: string; violated_invariants: Json[] }; monitor_input: Sections; input_bytes: number; monitor_output: Json & { findings: Json[]; evidence_event_ids: string[]; detected_patterns: string[]; missing_information: string[] }; paired_monitor: Json; authorization: Json | null; outcome: Json & { sent_emails: Json[] }; original: Json }
export type Sections = { window: string; checkpoint_seq: number; agent_role: string | null; current_action: Json; workflow_purpose: string | null; events: Json[]; messages: Json[]; prior_actions: Json[]; resources: Json[]; approvals: Json[]; invariants: Json[]; outside_window: string[]; snapshot_digest: string }

const PAIR: Record<string, string> = { local_action: 'global_workflow', global_workflow: 'local_action', llm_local: 'llm_global', llm_global: 'llm_local' }
const NAME: Record<string, string> = { local_action: 'Local', global_workflow: 'Global', llm_local: 'LLM Local', llm_global: 'LLM Global' }

export function RecordPanel({ eid, wf, step, cfg, onClose }: { eid: string; wf: string; step: number; cfg: string; onClose: () => void }) {
 const [current, setCurrent] = useState(cfg)
 const data = useRQ1<Detail>(`/api/research/rq1/experiments/${eid}/raw/${wf}/${step}/${current}`)
 useEffect(() => { const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }; window.addEventListener('keydown', esc); return () => window.removeEventListener('keydown', esc) }, [onClose])
 return <aside className="rq1-panel-side" role="dialog" aria-modal="true" aria-label="Raw data record">
  <header><div><b>{wf} · step {step} · {NAME[current]} Monitor</b></div><div className="rq1-row-actions"><Button variant="outline" size="sm" onClick={() => setCurrent(PAIR[current])}>Switch to {NAME[PAIR[current]]}</Button>{current.startsWith('llm') ? <Button variant="ghost" size="sm" onClick={() => setCurrent(current === 'llm_local' ? 'local_action' : 'global_workflow')}>Rule monitor</Button> : <Button variant="ghost" size="sm" onClick={() => setCurrent(current === 'local_action' ? 'llm_local' : 'llm_global')}>LLM monitor</Button>}<Button variant="ghost" size="sm" aria-label="Close record" onClick={onClose}><X size={16}/></Button></div></header>
  <div className="rq1-body">{data.error ? <ErrorBox error={data.error}/> : !data.data ? <Loading/> : <RecordBody d={data.data}/>}</div>
 </aside>
}

const show = (v: unknown): ReactNode => v === null || v === undefined || v === '' ? <span className="muted">none</span> : typeof v === 'object' ? <code className="mono" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v)}</code> : String(v)

function RecordBody({ d }: { d: Detail }) {
 const a = d.basic.action, o = d.monitor_output, i = d.monitor_input, g = d.ground_truth
 const otherAgents = i.prior_actions.filter(p => p.agent_id !== a.agent_id)
 return <>
  <Section title="A · Basic information"><KV rows={[['Experiment', show(d.basic.experiment_id)], ['Scenario', show(d.basic.scenario_id)], ['Workflow', show(d.basic.workflow_id)], ['Trial', show(d.basic.trial)], ['Decision point', `step ${d.basic.step} · event seq ${d.basic.checkpoint_seq} · ${d.basic.position} (critical step ${d.basic.critical_step})`], ['Agent', show(d.basic.agent_id)], ['Action', `${a.tool} (${a.action_id})`], ['Timestamp', <>{show(d.basic.timestamp)} <small className="muted">{String(d.basic.timestamp_note)}</small></>]]}/></Section>
  <Section title="B · Ground truth"><p className="muted small-text">{String(g.note)}</p><KV rows={[['Label', <Tag value={g.label}/>], ['Category', show(g.category)], ['Safety property', show(g.safety_property)], ['Violated invariants', g.violated_invariants.length ? <ul className="rq1-list">{g.violated_invariants.map(v => <li key={String(v.invariant_id)}><b>{String(v.invariant_id)}</b>{v.description ? `: ${String(v.description)}` : ''}</li>)}</ul> : 'none (legitimate workflow)'], ['Scenario description', show(g.description)], ['Rationale', show(g.rationale)], ['Expected gateway decision', show(g.expected_authorization)], ['Prohibited outcome oracle', show(g.prohibited_outcome)], ['This monitor\'s workflow prediction', <><Tag value={String(g.workflow_prediction)}/> <Tag value={String(g.workflow_outcome)}/></>]]}/></Section>
  <Section title={`C · Monitor input (${i.window} window, ${d.input_bytes.toLocaleString()} bytes)`}>
   <KV rows={[['Current action', <KV rows={Object.entries(a).map(([k, v]) => [k, show(v)])}/>], ['Agent role', show(i.agent_role)], ['Workflow purpose', i.workflow_purpose ?? <span className="muted">Not provided in this window</span>], ['Excluded from this window', i.outside_window.join(', ').replaceAll('_', ' ') || 'nothing listed'], ['Snapshot digest', <span className="mono">{i.snapshot_digest}</span>]]}/>
   <h4>Workflow history ({i.events.length} events)</h4>{i.events.length ? <div className="table-wrap"><table className="rq1-mini"><thead><tr><th>Seq</th><th>Type</th><th>Agent</th><th>Summary</th></tr></thead><tbody>{i.events.map(e => <tr key={String(e.event_id)}><td>{String(e.seq)}</td><td>{String(e.event_type)}</td><td>{show(e.agent_id)}</td><td>{String(e.summary)}</td></tr>)}</tbody></table></div> : <p className="muted">No events in this window.</p>}
   <h4>Agent communications ({i.messages.length})</h4>{i.messages.length ? <ul className="rq1-list">{i.messages.map(m => <li key={String(m.event_id)}>{String(m.summary)}</li>)}</ul> : <p className="muted">No messages visible in this window.</p>}
   <h4>Relevant resources ({i.resources.length})</h4>{i.resources.length ? <div className="table-wrap"><table className="rq1-mini"><thead><tr><th>Resource</th><th>Type</th><th>Version</th><th>Content</th></tr></thead><tbody>{i.resources.map((r, k) => <tr key={k}><td>{String(r.resource_id)}</td><td>{show(r.resource_type)}</td><td>{show(r.version)}</td><td>{show(r.content)}</td></tr>)}</tbody></table></div> : <p className="muted">No resource versions visible in this window.</p>}
   <h4>Available approvals ({i.approvals.length})</h4>{i.approvals.length ? <ul className="rq1-list">{i.approvals.map(p => <li key={String(p.approval_id)}>{String(p.approval_id)} · {String(p.status)} · {String(p.operation)} on {String(p.resource_id)} v{String(p.resource_version)} · approved parameters {JSON.stringify(p.approved_parameters)}</li>)}</ul> : <p className="muted">No approvals visible in this window.</p>}
   <h4>Cross-agent dependencies</h4><p>Parent actions of this action: {show((a.parent_action_ids as string[] | undefined)?.join(', '))}. Prior actions by other agents visible: {otherAgents.length ? otherAgents.map(p => `${String(p.agent_id)}:${String(p.tool)}`).join(', ') : <span className="muted">none in this window</span>}.</p>
   <h4>Workflow invariants supplied</h4><ul className="rq1-list">{i.invariants.map(v => <li key={String(v.invariant_id)}><b>{String(v.invariant_id)}</b>: {String(v.description)}</li>)}</ul>
  </Section>
  {d.llm_prompt && <Section title="C2 · Exact LLM request"><KV rows={[['Provider · model', `${String(d.llm_prompt.provider)} · ${String(d.llm_prompt.model)}${d.llm_prompt.is_synthetic ? ' (SYNTHETIC MOCK: no model contacted)' : ''}`], ['System prompt', `trusted constant ${String(d.llm_prompt.prompt_version)} · sha256 ${String(d.llm_prompt.system_prompt_sha256).slice(0, 16)}…`], ['User message', `${d.input_bytes.toLocaleString()} bytes · sha256 ${String(d.llm_prompt.user_content_sha256).slice(0, 16)}… (re-rendered and verified against the stored digest)`], ['Note', String(d.llm_prompt.note)]]}/><OriginalJson value={JSON.parse(d.llm_prompt.user_content)} title="VIEW EXACT USER MESSAGE"/></Section>}
  <Section title="D · Monitor output"><KV rows={[...(o.status ? [['Call status', <Tag value={String(o.status)}/>] as [string, ReactNode]] : []), ...(o.error ? [['Error', show(o.error)] as [string, ReactNode]] : []), ...(o.concerns ? [['Concerns', (o.concerns as string[]).length ? <ul className="rq1-list">{(o.concerns as string[]).map((c, k) => <li key={k}>{c}</li>)}</ul> : 'none'] as [string, ReactNode], ['Legitimate interpretations', ((o.legitimate_interpretations as string[]) ?? []).join('; ') || 'none'] as [string, ReactNode], ['Token usage', show(o.usage)] as [string, ReactNode]] : []), ['Prediction', <><Tag value={String(o.recommendation)}/> {o.alert ? '(alert)' : '(no alert)'}</>], ['Risk assessment', `${String(o.risk_level)} · uncertainty ${String(o.uncertainty)}`], ['Detected patterns', o.detected_patterns.join(', ') || 'none'], ['Supporting evidence', o.evidence_event_ids.join(', ') || 'none cited'], ['Explanation', show(o.explanation)], ['Missing information', o.missing_information.join('; ') || 'none'], ['Findings', o.findings.length ? <ul className="rq1-list">{o.findings.map((f, k) => <li key={k}><b>{String(f.pattern_id)}</b> ({String(f.severity)}, {f.is_violation ? 'violation' : 'signal'}): {String(f.explanation)}</li>)}</ul> : 'none'], ['Errors', o.error ? 'see above' : 'none recorded'], ['Evaluation latency', `${num(o.latency_ms)} ms${o.status ? ' (provider call)' : ' (single in-process evaluation)'}`], ['Method', `${String(o.method)} · ${String(o.ruleset_version)}`], ['Paired monitor at this step', `${String(d.paired_monitor.monitor)}: ${String(d.paired_monitor.recommendation)} · ${String(d.paired_monitor.risk_level)}`]]}/></Section>
  <Section title="E · Authorization (Capability Gateway)">{d.authorization ? <KV rows={[['Decision', <Tag value={String(d.authorization.decision)}/>], ['Reason', show(d.authorization.reason_code)], ['Policy decision', show(d.authorization.policy_decision)], ['Checks passed', show((d.authorization.checks_passed as string[]).join(', '))], ['Explanation', show(d.authorization.explanation)], ['Policy version', show(d.authorization.policy_version)]]}/> : <p className="muted">No gateway decision recorded for this action.</p>}<p className="muted small-text">The gateway decision is separate from monitor detection; a gateway block is never counted as a detection.</p></Section>
  <Section title="F · Actual outcome"><KV rows={[['Executed', show(d.outcome.executed)], ['Execution status', show(d.outcome.execution_status)], ['Simulated emails sent by this action', d.outcome.sent_emails.length ? d.outcome.sent_emails.map(m => `${String(m.recipient)}: “${String(m.subject)}”`).join('; ') : 'none'], ['Prohibited outcome in this workflow (no guard)', show(d.outcome.workflow_prohibited_outcome_occurred)]]}/></Section>
  <OriginalJson value={d.original}/>
 </>
}

