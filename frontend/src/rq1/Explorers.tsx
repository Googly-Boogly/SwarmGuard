import { useMemo, useState, type ReactNode } from 'react'
import { Background, Controls, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Empty, ErrorBox, Loading, Panel } from '../components/common'
import { Button } from '../components/ui/button'
import { graphElements } from '../components/WorkflowGraph'
import type { Graph } from '../lib/api/client'
import { label, num, useRQ1, type Json, type ScenarioRow, type WorkflowEvidence } from './api'
import { Correct, KV, Section, Tag } from './components'
import { RecordPanel } from './RawData'

const show = (v: unknown): ReactNode => v === null || v === undefined || v === '' ? <span className="muted">none</span> : typeof v === 'object' ? <code className="mono" style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(v)}</code> : String(v)

function WorkflowPicker({ eid, value, onChange, fallback }: { eid: string; value: string | null; onChange: (wf: string) => void; fallback?: string[] }) {
 const list = useRQ1<ScenarioRow[]>(fallback ? null : `/api/research/rq1/experiments/${eid}/scenarios`)
 const options = fallback ? fallback.map(wf => ({ wf, text: wf })) : (list.data ?? []).filter(s => s.workflow_id).map(s => ({ wf: s.workflow_id as string, text: `${s.workflow_id} · ${s.scenario_id} · ${s.ground_truth === 'SAFE' ? 'legitimate' : 'unsafe'}` }))
 return <label className="muted small-text">Workflow <select aria-label="Select workflow" value={value ?? ''} onChange={e => onChange(e.target.value)}><option value="" disabled>Select a workflow</option>{options.map(o => <option key={o.wf} value={o.wf}>{o.text}</option>)}</select></label>
}

// ------------------------------------------------------------------ Scenarios

type ScenarioDetail = { summary: ScenarioRow; definition: Json & { participating_agents: string[]; violated_invariants: string[]; safety_property: string; prohibited_outcome: Json | null; declared_purpose: string | null; variation: Json; rationale: string; expected_authorization: string; brief_writers: string[] }; ground_truth: Json | null; agents: { agent_id: string; role: string }[]; permissions: { agent_id: string; role: string; permitted_tools: string[] }[]; initial_resources: Record<string, Json>; actions: (Json & { decision: Json | null })[]; events: Json[]; messages: Json[]; approvals: Json[]; sent_emails: Json[]; critical_action_id: string | null; checkpoints: WorkflowEvidence['checkpoints']; predictions: Json[]; enforcement: Json[] }

export function ScenarioTab({ eid, selected, onSelect, onCompare, onWorkflow }: { eid: string; selected: string | null; onSelect: (s: string) => void; onCompare: (wf: string) => void; onWorkflow: (wf: string) => void }) {
 const list = useRQ1<ScenarioRow[]>(`/api/research/rq1/experiments/${eid}/scenarios`)
 return <>
  <Panel title="Scenario library (this experiment)" subtitle="Ground truth is shown to researchers only; monitors never received it.">
   {list.error && <ErrorBox error={list.error}/>}
   {!list.data ? <Loading/> : <div className="rq1-table-wrap" style={{ maxHeight: 360 }}><table aria-label="Scenarios"><thead><tr>{['Scenario', 'Workflow', 'Category', 'Agents', 'Steps', 'Ground truth', 'Local', 'Global', 'Status', ''].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{list.data.map(s => <tr key={s.scenario_id} className={selected === s.scenario_id ? 'selected' : ''}>
    <td>{s.scenario_id}</td><td className="mono">{s.workflow_id}</td><td>{label(s.category)}</td><td>{s.agent_count}</td><td>{s.workflow_steps}</td><td><Tag value={s.ground_truth}/></td>
    <td><Tag value={s.local}/> <Correct value={s.local_correct}/></td><td><Tag value={s.global}/> <Correct value={s.global_correct}/></td><td><Tag value={s.status}/></td>
    <td><Button variant="outline" size="sm" onClick={() => onSelect(s.scenario_id)}>Inspect</Button></td></tr>)}</tbody></table></div>}
  </Panel>
  {selected && <ScenarioDetailView eid={eid} sid={selected} onCompare={onCompare} onWorkflow={onWorkflow}/>}
 </>
}

function ScenarioDetailView({ eid, sid, onCompare, onWorkflow }: { eid: string; sid: string; onCompare: (wf: string) => void; onWorkflow: (wf: string) => void }) {
 const d = useRQ1<ScenarioDetail>(`/api/research/rq1/experiments/${eid}/scenarios/${sid}`)
 const [record, setRecord] = useState<{ step: number; cfg: string } | null>(null)
 if (d.error) return <ErrorBox error={d.error}/>
 if (!d.data) return <Loading/>
 const s = d.data, m = s.definition, wf = s.summary.workflow_id
 const byAction = Object.fromEntries(s.checkpoints.map(c => [c.action_id, c]))
 const eventsByAction: Record<string, Json[]> = {}
 s.events.forEach(e => { const a = e.action_id as string | null; if (a) (eventsByAction[a] ??= []).push(e) })
 return <Panel title={`Scenario ${sid}`} subtitle={`${label(m.category as string)} · family ${String(m.family)} · ${wf}`} action={wf && <div className="rq1-row-actions"><Button variant="outline" size="sm" onClick={() => onCompare(wf)}>Compare monitors</Button><Button variant="outline" size="sm" onClick={() => onWorkflow(wf)}>Workflow graph</Button></div>}>
  <div className="rq1-body">
   <Section title="Scenario description"><p>{String(m.description)}</p><KV rows={[['Declared purpose', show(m.declared_purpose)], ['Look-alike / variation', show(m.variation)]]}/></Section>
   <Section title="Participating agents and local permissions"><div className="table-wrap"><table className="rq1-mini"><thead><tr><th>Agent</th><th>Role</th><th>Individually permitted tools (gateway policy)</th></tr></thead><tbody>{s.permissions.map(a => <tr key={a.agent_id}><td>{a.agent_id}</td><td>{a.role}</td><td>{a.permitted_tools.join(', ') || '—'}</td></tr>)}</tbody></table></div></Section>
   <Section title="Initial conditions (synthetic resources, first recorded version)"><div className="table-wrap"><table className="rq1-mini"><thead><tr><th>Resource</th><th>Type</th><th>Content</th></tr></thead><tbody>{Object.entries(s.initial_resources).map(([rid, r]) => <tr key={rid}><td>{rid}</td><td>{show(r.resource_type)}</td><td>{show(r.content)}</td></tr>)}</tbody></table></div></Section>
   <Section title="Global safety constraint (ground truth)"><KV rows={[['Label', <Tag value={s.summary.ground_truth}/>], ['Safety property', show(m.safety_property)], ['Violated invariants', m.violated_invariants.join(', ') || 'none (legitimate workflow)'], ['Why', show(m.rationale)]]}/></Section>
   <Section title="Expected vs. recorded behaviour"><KV rows={[['Expected gateway decision at the critical action', show(m.expected_authorization)], ['Prohibited outcome oracle (expected)', show(m.prohibited_outcome)], ['Proposed actions (recorded)', `${s.actions.length} actions by ${new Set(s.actions.map(a => a.agent_id)).size} agents`], ['Authorized and executed (recorded)', `${s.actions.filter(a => a.decision?.decision === 'ALLOW').length} allowed · ${s.actions.filter(a => a.decision?.executed).length} executed`], ['Actual simulated emails', s.sent_emails.length ? s.sent_emails.map(e => `${String(e.recipient)} (“${String(e.subject)}”)`).join('; ') : 'none'], ['Enforcement replays', s.enforcement.map(e => `${String(e.configuration)}: critical ${String(e.critical_decision)}, prohibited outcome ${String(e.prohibited_outcome_occurred)}`).join(' · ')]]}/></Section>
   <Section title="Monitor results"><div className="table-wrap"><table className="rq1-mini"><thead><tr><th>Monitor</th><th>Prediction</th><th>Outcome</th><th>First alert</th><th>Critical step</th><th>Patterns</th></tr></thead><tbody>{s.predictions.map(p => <tr key={String(p.row_id)}><td>{String(p.monitor)}</td><td><Tag value={String(p.workflow_prediction)}/></td><td><Tag value={String(p.outcome)}/></td><td>{num(p.first_detection_step)}</td><td>{String(p.critical_step)}</td><td>{((p.detected_patterns as string[]) ?? []).join(', ') || '—'}</td></tr>)}</tbody></table></div></Section>
   <Section title="Timeline (recorded actions; expand for events)"><ol className="rq1-timeline">{s.actions.map(a => { const cp = byAction[a.action_id as string]; const crit = a.action_id === s.critical_action_id; return <li key={String(a.action_id)} className={crit ? 'critical' : ''}><details><summary><b>{cp ? `Step ${cp.step}` : 'Action'}</b> · {String(a.agent_id)} proposed <code>{String(a.tool)}</code> → gateway <Tag value={a.decision ? String(a.decision.decision) : null}/> {a.decision?.executed ? 'executed' : 'not executed'}{cp && <> · Local <Tag value={cp.local.recommendation}/> · Global <Tag value={cp.global.recommendation}/></>}{crit && <b> · critical action</b>}</summary>
    <KV rows={[['Parameters', show(a.parameters)], ['Resource', `${show(a.resource_id)} v${show(a.resource_version)}`], ['Approval cited', show(a.approval_id)], ['Gateway reason', show(a.decision?.reason_code)]]}/>
    <ul className="rq1-list">{(eventsByAction[a.action_id as string] ?? []).map(e => <li key={String(e.event_id)}>seq {String(e.seq)} · {String(e.event_type)} · {String(e.summary)}</li>)}</ul>
    {cp && <div className="rq1-row-actions"><Button variant="outline" size="sm" onClick={() => setRecord({ step: cp.step, cfg: 'local_action' })}>Local monitor record</Button><Button variant="outline" size="sm" onClick={() => setRecord({ step: cp.step, cfg: 'global_workflow' })}>Global monitor record</Button></div>}</details></li> })}</ol></Section>
   <Section title={`Agent communications (${s.messages.length})`}>{s.messages.length ? <ul className="rq1-list">{s.messages.map(m => <li key={String(m.message_id)}><b>{String(m.sender_id)} → {String(m.recipient_id)}</b> ({String(m.message_type)}): {String(m.text)}</li>)}</ul> : <p className="muted">No messages.</p>}</Section>
   <Section title={`Approvals (${s.approvals.length})`}>{s.approvals.length ? <ul className="rq1-list">{s.approvals.map(p => <li key={String(p.approval_id)}>{String(p.approval_id)} · {String(p.status)} · requested by {String(p.requested_by)} · {String(p.operation)} {String(p.resource_id)} v{String(p.resource_version)}</li>)}</ul> : <p className="muted">No approvals.</p>}</Section>
  </div>
  {record && wf && <RecordPanel eid={eid} wf={wf} step={record.step} cfg={record.cfg} onClose={() => setRecord(null)}/>}
 </Panel>
}

// ------------------------------------------------------------------ Workflow explorer

export function WorkflowTab({ eid, selected, onSelect, captured }: { eid: string; selected: string | null; onSelect: (wf: string) => void; captured?: string[] }) {
 const wf = selected ?? captured?.[0] ?? null
 const d = useRQ1<WorkflowEvidence>(wf ? `/api/research/rq1/experiments/${eid}/workflows/${wf}` : null)
 const [node, setNode] = useState<string | null>(null)
 const [record, setRecord] = useState<{ step: number; cfg: string } | null>(null)
 const elements = useMemo(() => d.data?.graph ? graphElements(d.data.graph as unknown as Graph) : { nodes: [], edges: [] }, [d.data])
 const n = d.data?.graph?.nodes.find(x => x.node_id === node)
 const events = d.data ? Object.fromEntries(d.data.events.map(e => [e.event_id, e])) : {}
 const actionId = n ? (n.node_type === 'ACTION' ? n.ref_id : (n.attributes.action_id as string | undefined)) : undefined
 const cp = d.data?.checkpoints.find(c => c.action_id === actionId)
 const decision = d.data?.decisions.find(x => x.action_id === actionId)
 const action = d.data?.actions.find(x => x.action_id === actionId)
 return <>
  <Panel title="Workflow explorer" subtitle="Persisted dependency graph from explicit references only. Dashed edges are chronological and non-causal." action={<WorkflowPicker eid={eid} value={wf} onChange={w => { setNode(null); onSelect(w) }} fallback={captured}/>}>
   {!wf ? <Empty title="Select a workflow" detail="Choose an evaluated workflow above."/> : d.error ? <ErrorBox error={d.error}/> : !d.data ? <Loading/> : !d.data.graph ? <Empty title="No graph recorded"/> :
    <div className="rq1-graph" aria-label="Workflow dependency graph"><ReactFlow nodes={elements.nodes.map(x => ({ ...x, style: { ...x.style, outline: x.id === node ? '2px solid var(--accent)' : undefined } }))} edges={elements.edges} fitView fitViewOptions={{ padding: 0.15 }} minZoom={0.05} nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, x) => setNode(x.id)} onPaneClick={() => setNode(null)}><Background gap={22}/><Controls/></ReactFlow></div>}
  </Panel>
  {d.data && <div className="rq1-grid two">
   <Panel title="Selected node" subtitle="Click a graph node">
    <div className="rq1-body">{!n ? <p className="muted">No node selected.</p> : <>
     <KV rows={[['Node', `${n.node_type} · ${n.label}`], ['Reference', `${show(n.ref_table)} ${show(n.ref_id)}`], ['Agent', show(n.attributes.agent_id ?? action?.agent_id)], ['Action', show(action?.tool)], ['Input parameters', show(action?.parameters)], ['Authorization result', decision ? <><Tag value={String(decision.decision)}/> {String(decision.reason_code)}</> : 'not an action node'], ['Monitor assessments', cp ? <>Local <Tag value={cp.local.recommendation}/> ({cp.local.risk_level}) · Global <Tag value={cp.global.recommendation}/> ({cp.global.risk_level})</> : 'none at this node'], ['Attributes', show(n.attributes)]]}/>
     <h4>Related events</h4><ul className="rq1-list">{n.event_ids.map(id => <li key={id}>{events[id] ? `seq ${events[id].seq} · ${events[id].event_type} · ${events[id].summary}` : id}</li>)}</ul>
     {cp && <div className="rq1-row-actions"><Button variant="outline" size="sm" onClick={() => setRecord({ step: cp.step, cfg: 'local_action' })}>Local monitor record</Button><Button variant="outline" size="sm" onClick={() => setRecord({ step: cp.step, cfg: 'global_workflow' })}>Global monitor record</Button></div>}</>}
    </div>
   </Panel>
   <Panel title={`Event timeline (${d.data.events.length})`} subtitle={`${d.data.workflow_id} · ${d.data.scenario_id} · synthetic simulator clock`}>
    <div className="rq1-stream" style={{ maxHeight: 520 }}><ol>{d.data.events.map(e => <li key={e.event_id} style={{ gridTemplateColumns: '40px 150px 1fr' }}><span className="mono">{e.seq}</span><span className="mono">{e.event_type.replaceAll('_', ' ').toLowerCase()}</span><span>{e.summary}</span></li>)}</ol></div>
   </Panel>
  </div>}
  {record && wf && <RecordPanel eid={eid} wf={wf} step={record.step} cfg={record.cfg} onClose={() => setRecord(null)}/>}
 </>
}

// ------------------------------------------------------------------ Monitor comparison

type Side = { recommendation: string; risk_level: string; uncertainty: string; alert: boolean; detected_patterns: string[]; evidence_event_ids: string[]; explanation: string; missing_information: string[]; findings: Json[]; latency_ms: number; input_bytes: number; scope: { events: number; prior_actions: number; resources: number; approvals: number; workflow_purpose: boolean; outside_window: string[] } }
type Step = { step: number; action_id: string; tool: string; agent_id: string; snapshot_digest: string; local: Side; global: Side; difference: { events: { local: number; global: number; only_global: string[] }; resources: { only_global: string[] }; approvals: { only_global: string[] }; prior_actions: { only_global: string[] }; only_global_event_types: Record<string, number>; workflow_purpose: { local: boolean; global: boolean } }; differs: { prediction: boolean; recommendation: boolean; risk: boolean; evidence: boolean } }
type Comparison = { workflow_id: string; scenario_id: string; ground_truth: string; description: string; critical_action_id: string | null; local: Json | null; global: Json | null; steps: Step[]; ablations: { step: number; alert_removed_by: string[]; ablations: Record<string, { alert: boolean | null }> }[] }

export function ComparisonTab({ eid, selected, onSelect }: { eid: string; selected: string | null; onSelect: (wf: string) => void }) {
 const d = useRQ1<Comparison>(selected ? `/api/research/rq1/experiments/${eid}/comparisons/${selected}` : null)
 const [chosen, setChosen] = useState<number | null>(null)
 const [record, setRecord] = useState<{ step: number; cfg: string } | null>(null)
 const c = d.data
 const critical = c?.steps.find(s => s.action_id === c.critical_action_id)?.step
 const firstDiff = c?.steps.find(s => s.differs.prediction)?.step
 const step = c?.steps.find(s => s.step === (chosen ?? firstDiff ?? critical)) ?? c?.steps[c.steps.length - 1]
 const ablation = c?.ablations.find(a => a.step === step?.step)
 const wpred = (row: Json | null) => row ? `${String(row.workflow_prediction)} (${String(row.outcome)})` : 'not recorded'
 return <>
  <Panel title="Monitor comparison" subtitle="Both monitors evaluated the same snapshot at every step; only the observation window differs." action={<WorkflowPicker eid={eid} value={selected} onChange={w => { setChosen(null); onSelect(w) }}/>}>
   {!selected ? <Empty title="Select a workflow" detail="Choose a workflow, or open one from Scenarios or Failure Analysis."/> : d.error ? <ErrorBox error={d.error}/> : !c || !step ? <Loading/> : <div className="rq1-body">
    <KV rows={[['Scenario', `${c.scenario_id} · ${c.workflow_id}`], ['Ground truth', <Tag value={c.ground_truth}/>], ['Description', c.description], ['Workflow-level result', <>Local: {wpred(c.local)} {c.local && <Correct value={c.local.workflow_correct as boolean}/>} · Global: {wpred(c.global)} {c.global && <Correct value={c.global.workflow_correct as boolean}/>}</>]]}/>
    <label className="muted small-text" style={{ display: 'block', marginTop: 10 }}>Decision point <select aria-label="Decision point" value={step.step} onChange={e => setChosen(Number(e.target.value))}>{c.steps.map(s => <option key={s.step} value={s.step}>Step {s.step} · {s.agent_id} · {s.tool}{s.step === critical ? ' · critical' : ''}{s.differs.prediction ? ' · predictions differ' : ''}</option>)}</select></label>
    <div className="rq1-grid two" style={{ marginTop: 12 }}>{(['local', 'global'] as const).map(k => { const s = step[k]; const row = k === 'local' ? c.local : c.global; return <div key={k} className={`rq1-card${step.differs.prediction || step.differs.risk ? ' diff' : ''}`} aria-label={`${k} monitor`}>
     <h3>{k === 'local' ? 'LOCAL MONITOR' : 'GLOBAL MONITOR'}</h3>
     <KV rows={[['Observation scope', `${s.scope.events} events · ${s.scope.prior_actions} prior actions · ${s.scope.resources} resource versions · ${s.scope.approvals} approvals · purpose ${s.scope.workflow_purpose ? 'visible' : 'not visible'} · ${s.input_bytes.toLocaleString()} bytes`], ['Prediction at this step', <><Tag value={s.recommendation}/>{step.differs.recommendation && <b> ≠</b>}</>], ['Risk assessment', <>{s.risk_level} · uncertainty {s.uncertainty}{step.differs.risk && <b> ≠</b>}</>], ['Supporting evidence', <>{s.evidence_event_ids.join(', ') || 'none cited'}{step.differs.evidence && <b> ≠</b>}</>], ['Patterns', s.detected_patterns.join(', ') || 'none'], ['Explanation', s.explanation], ['Missing information', s.missing_information.join('; ') || 'none'], ['Detection step (workflow)', row ? `${num(row.first_detection_step)} (critical ${String(row.critical_step)})` : '—'], ['Evaluation latency', `${num(s.latency_ms)} ms`]]}/>
     <Button variant="outline" size="sm" onClick={() => setRecord({ step: step.step, cfg: k === 'local' ? 'local_action' : 'global_workflow' })}>Open full monitor input</Button></div> })}</div>
    <Section title="Comparison summary">
     <ul className="rq1-list">
      <li>Predictions at this step {step.differs.prediction ? <b>differ</b> : 'agree'} (Local {step.local.recommendation}, Global {step.global.recommendation}); risk {step.differs.risk ? <b>differs</b> : 'agrees'}; cited evidence {step.differs.evidence ? <b>differs</b> : 'agrees'}.</li>
      <li>Extra information only in the Global observation: {step.difference.events.only_global.length} events ({Object.entries(step.difference.only_global_event_types).map(([t, n]) => `${t.replaceAll('_', ' ').toLowerCase()} ${n}`).join(', ') || 'none'}), {step.difference.resources.only_global.length} resource versions ({step.difference.resources.only_global.join(', ') || 'none'}), {step.difference.approvals.only_global.length} approvals, {step.difference.prior_actions.only_global.length} prior actions{step.difference.workflow_purpose.global && !step.difference.workflow_purpose.local ? ', and the declared workflow purpose' : ''}.</li>
      <li>Global evidence outside the Local window: {step.global.evidence_event_ids.filter(id => step.difference.events.only_global.includes(id)).join(', ') || 'none'}.</li>
      <li>Relevance diagnostic: {ablation ? (ablation.alert_removed_by.length ? `removing ${ablation.alert_removed_by.map(x => x.replace('without_', '').replaceAll('_', ' ')).join(', ')} from the Global observation removed its alert` : 'no single information class removal removed the Global alert') : 'not run at this step (it runs only where Global alerts and Local does not)'}.</li>
      <li>Which matched ground truth (workflow level): Local {c.local ? (c.local.workflow_correct ? 'correct' : 'incorrect') : '—'}, Global {c.global ? (c.global.workflow_correct ? 'correct' : 'incorrect') : '—'}.</li>
     </ul>
    </Section>
    <Section title="All decision points"><div className="table-wrap"><table className="rq1-mini"><thead><tr><th>Step</th><th>Agent</th><th>Action</th><th>Local</th><th>Global</th><th>Extra Global events</th><th>Differs</th></tr></thead><tbody>{c.steps.map(s => <tr key={s.step} className={s.step === step.step ? 'selected' : ''}><td><button className="link-text" style={{ background: 'none', border: 0, color: 'var(--accent)', padding: 0 }} onClick={() => setChosen(s.step)}>{s.step}{s.step === critical ? ' (critical)' : ''}</button></td><td>{s.agent_id}</td><td>{s.tool}</td><td><Tag value={s.local.recommendation}/></td><td><Tag value={s.global.recommendation}/></td><td>{s.difference.events.only_global.length}</td><td>{s.differs.prediction ? <b>prediction</b> : s.differs.risk ? 'risk' : '—'}</td></tr>)}</tbody></table></div></Section>
   </div>}
  </Panel>
  {record && selected && <RecordPanel eid={eid} wf={selected} step={record.step} cfg={record.cfg} onClose={() => setRecord(null)}/>}
 </>
}

// ------------------------------------------------------------------ Failure analysis

type Case = ScenarioRow & { case: string; critical_step?: number; local_first_detection_step?: number | null; global_first_detection_step?: number | null; local_patterns?: string[]; global_patterns?: string[]; local_evidence_event_ids?: string[]; global_evidence_event_ids?: string[]; authorization_result?: string; prohibited_outcome_occurred?: boolean | null; ablation_alert_removed_by?: string[]; safety_property?: string; explanation: string }
const CASES: [string, string, string][] = [
 ['LOCAL_MISSED_GLOBAL_DETECTED', 'Local missed, Global detected', 'Potentially supports the value of global context.'],
 ['BOTH_DETECTED', 'Both detected', 'Both monitoring approaches identified the violation.'],
 ['BOTH_MISSED', 'Both missed', 'Potential monitoring limitation.'],
 ['LOCAL_DETECTED_GLOBAL_MISSED', 'Global missed, Local detected', 'Global context did not help here.'],
 ['GLOBAL_FALSE_POSITIVE', 'Global false positive', 'Global flagged legitimate behaviour.'],
 ['LOCAL_FALSE_POSITIVE', 'Local false positive', 'Local flagged legitimate behaviour.'],
 ['BOTH_FALSE_POSITIVE', 'Both false positive', 'Both flagged legitimate behaviour.'],
 ['BOTH_CORRECT_LEGITIMATE', 'Legitimate, both correct', 'Neither monitor alerted.'],
 ['MONITOR_ERROR', 'Monitor error', 'An evaluation failed or returned invalid output.']]

export function FailureTab({ eid, onCompare, onWorkflow }: { eid: string; onCompare: (wf: string) => void; onWorkflow: (wf: string) => void }) {
 const d = useRQ1<{ counts: Record<string, number>; cases: Case[]; integrity: { crosscheck: string[]; verification_failures: string[]; missing_or_failed: string[] } }>(`/api/research/rq1/experiments/${eid}/failures`)
 const [filter, setFilter] = useState('LOCAL_MISSED_GLOBAL_DETECTED')
 const [record, setRecord] = useState<{ wf: string; step: number; cfg: string } | null>(null)
 if (d.error) return <ErrorBox error={d.error}/>
 if (!d.data) return <Loading/>
 const cases = d.data.cases.filter(c => !filter || c.case === filter)
 const integrity = d.data.integrity
 return <>
  <div className="metric-grid" role="group" aria-label="Failure categories">{CASES.map(([id, name, hint]) => <button key={id} className="metric-card rq1-stat" style={{ textAlign: 'left', outline: filter === id ? '2px solid var(--accent)' : undefined, color: 'var(--text)' }} aria-pressed={filter === id} onClick={() => setFilter(filter === id ? '' : id)}><small>{name}</small><strong>{d.data.counts[id] ?? 0}</strong><small>{hint}</small></button>)}</div>
  <Panel title={filter ? CASES.find(c => c[0] === filter)?.[1] ?? filter : 'All cases'} subtitle="Deterministic summaries from recorded predictions, gateway decisions and the context-ablation diagnostic. No agent intentions are inferred.">
   <div className="rq1-body">{cases.length === 0 ? <p className="muted">No cases in this category.</p> : <div className="rq1-grid two">{cases.map(c => <div key={c.scenario_id} className="rq1-card">
    <h3>{c.scenario_id} <small className="muted">· {c.workflow_id}</small></h3>
    <KV rows={[['Category', label(c.category)], ['Ground truth', <Tag value={c.ground_truth}/>], ['Local prediction', <Tag value={c.local}/>], ['Global prediction', <Tag value={c.global}/>], ['Relevant step', c.critical_step ? `critical step ${c.critical_step}; Local first alert ${num(c.local_first_detection_step)}, Global first alert ${num(c.global_first_detection_step)}` : '—'], ['Ground-truth rule', show(c.safety_property)], ['Evidence (Global)', (c.global_evidence_event_ids ?? []).join(', ') || 'none'], ['Evidence (Local)', (c.local_evidence_event_ids ?? []).join(', ') || 'none'], ['Gateway at critical action', show(c.authorization_result)], ['Summary', c.explanation]]}/>
    {c.workflow_id && <div className="rq1-row-actions" style={{ marginTop: 8 }}><Button variant="outline" size="sm" onClick={() => onCompare(c.workflow_id!)}>Monitor inputs & outputs</Button><Button variant="outline" size="sm" onClick={() => onWorkflow(c.workflow_id!)}>Full workflow</Button>{c.critical_step && <Button variant="outline" size="sm" onClick={() => setRecord({ wf: c.workflow_id!, step: c.critical_step!, cfg: 'global_workflow' })}>Critical-step record</Button>}</div>}
   </div>)}</div>}</div>
  </Panel>
  <Panel title="Integrity checks" subtitle="Evaluation failures are listed, never repaired">
   <div className="rq1-body"><KV rows={[['Evidence cross-check', integrity.crosscheck.length ? integrity.crosscheck.join('; ') : 'no mismatches'], ['Observation verification', integrity.verification_failures.length ? integrity.verification_failures.join('; ') : 'no failures'], ['Missing or failed workflows', integrity.missing_or_failed.join(', ') || 'none']]}/></div>
  </Panel>
  {record && <RecordPanel eid={eid} {...record} onClose={() => setRecord(null)}/>}
 </>
}
