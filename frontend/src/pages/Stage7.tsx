import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Badge, Empty, ErrorBox, JsonView, Loading, PageTitle, Panel } from '../components/common'
import { Button } from '../components/ui/button'
import { Dialog } from '../components/ui/dialog'
import { post, useApi, type Json, type Session } from '../lib/api/client'

type SessionRoles = { authenticated: boolean; operator_id?: string | null; roles?: string[] }
type Emergency = { active: boolean; activated_by?: string; reason?: string; activated_at?: string; recovered_by?: string; version: number }
type Status = { emergency_stop: Emergency; disabled_capabilities: string[]; decisions: Record<string, number>; authentication_failures: number; security_alerts: number; operations: Record<string, number>; protected_executions: { simulated_emails_sent: number }; active_workflows: number; pending_approvals: number; note: string }
type Operation = { operation_id: string; agent_id: string; runtime_id: string; request_id: string; request_digest: string; workflow_id: string; tool: string; action_id: string | null; state: string; reason_code: string | null; correlation_id: string; created_at: string; updated_at: string; grant_expires_at: string | null; version: number }
type OperationDetail = Operation & { transitions: { version: number; from_state: string | null; to_state: string; actor: string; reason_code: string | null; occurred_at: string }[]; events: { event_id: string; seq: number; event_type: string; agent_id: string; summary: string; created_at: string }[]; decision: Json | null; approval_id: string | null; response: Json | null }
type SecurityEvent = { event_id: string; seq: number; event_type: string; agent_id: string; summary: string; reason_code: string | null; created_at: string }
type Service = { service: string; status: string; last_heartbeat: string; age_seconds: number; recent_failures: Record<string, number>; readiness?: Record<string, string> }
type Identities = { runtimes: Json[]; agents: { agent_id: string; runtime_id: string; role: string; workflows: string[]; capabilities: string[]; status: string; created_at: string; revoked_at: string | null }[]; revoked_credentials: Json[]; approvers: string[]; policy_version: string }
type Verification = { status: string; algorithm?: string; chained_events?: number; latest_verified_seq?: number | null; head_hash?: string | null; legacy_unchained_events?: number; checkpoints_checked?: number; failures: { kind: string; seq: number | null; detail: string }[]; verified_at?: string; limitations?: string }
type Checkpoint = { file: string; seq: number; event_hash: string; created_at: string }

function useSession() {
 const s = useApi<SessionRoles>('/api/session').data
 const roles = new Set(s?.roles ?? [])
 return { authenticated: !!s?.authenticated, user: s?.operator_id ?? null, has: (...r: string[]) => r.some(x => roles.has(x)) }
}

function NeedsLogin({ roles }: { roles: string }) {
 const session = useApi<Session>('/api/session').data
 return <Empty title={session?.login_required === false ? 'Required role unavailable in the local console' : 'Operator login required'} detail={`This view requires one of: ${roles}. Roles are assigned server-side.`}/>
}

/** Sensitive action: requires a typed reason, an explicit confirmation and (optionally) a typed phrase. */
export function ConfirmAction({ label, title, warning, phrase, destructive = true, onConfirm, disabled }: { label: string; title: string; warning: string; phrase?: string; destructive?: boolean; disabled?: boolean; onConfirm: (reason: string, typed: string) => Promise<unknown> }) {
 const [open, setOpen] = useState(false), [reason, setReason] = useState(''), [confirmed, setConfirmed] = useState(false), [typed, setTyped] = useState('')
 const cache = useQueryClient()
 const action = useMutation({ mutationFn: () => onConfirm(reason, typed), onSuccess: async () => { setOpen(false); setReason(''); setConfirmed(false); setTyped(''); await cache.invalidateQueries() } })
 const ready = reason.trim().length >= 3 && confirmed && (!phrase || typed === phrase)
 return <><Button variant={destructive ? 'destructive' : 'outline'} disabled={disabled} onClick={() => setOpen(true)}>{label}</Button>
  <Dialog open={open} onOpenChange={setOpen} title={title}><form onSubmit={e => { e.preventDefault(); if (ready) action.mutate() }}>
   <p className="notice">{warning}</p>
   <label>Reason (recorded in the audit chain)<input value={reason} maxLength={500} onChange={e => setReason(e.target.value)} required/></label>
   {phrase && <label>Type <code>{phrase}</code> to confirm<input value={typed} onChange={e => setTyped(e.target.value)}/></label>}
   <label className="checkbox"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>I confirm this action</label>
   {action.error && <ErrorBox error={action.error}/>}
   <Button type="submit" disabled={!ready || action.isPending}>{action.isPending ? 'Submitting…' : 'Confirm'}</Button>
  </form></Dialog></>
}

const metric = (label: string, value: number | undefined) => <div key={label}><small>{label}</small><strong>{value ?? 'unavailable'}</strong></div>

export function SecurityOperationsPage() {
 const s = useSession()
 const allowed = s.has('AUDITOR', 'ADMINISTRATOR', 'OPERATOR')
 const status = useApi<Status>('/api/v1/security/status', allowed)
 const ops = useApi<Operation[]>('/api/v1/security/operations', allowed)
 const events = useApi<SecurityEvent[]>('/api/v1/security/events?limit=100', allowed)
 const st = status.data
 return <><PageTitle eyebrow="STAGE 7 / SECURITY OPERATIONS" title="Security Operations" description="Gateway activity, authentication and authorization failures, protected executions and incident controls from persisted records."/>
  {!s.authenticated || !allowed ? <NeedsLogin roles="AUDITOR, ADMINISTRATOR, OPERATOR"/> : <>
  {status.error && <ErrorBox error={status.error}/>}
  {!st ? <Loading/> : <>
   <Panel title="Emergency stop" subtitle="Prevents new protected operations at the gateway and the executor. It cannot undo completed effects; operations already executing run to completion.">
    <p><Badge value={st.emergency_stop.active ? 'BLOCK' : 'ALLOW'}/> {st.emergency_stop.active ? `ACTIVE since ${st.emergency_stop.activated_at} by ${st.emergency_stop.activated_by}: ${st.emergency_stop.reason}` : 'Not active. New protected operations follow normal authorization.'}</p>
    <div className="actions">{s.has('ADMINISTRATOR') ? (st.emergency_stop.active
      ? <ConfirmAction label="Recover from emergency stop" title="Resume protected operations" destructive={false} phrase="RESUME PROTECTED OPERATIONS" warning="Recovery re-enables new protected operations. Confirm the incident is resolved." onConfirm={(reason, typed) => post('/api/v1/security/emergency-stop/recover', { reason, confirmation_phrase: typed })}/>
      : <ConfirmAction label="Activate emergency stop" title="Activate emergency stop" warning="All new protected operations will be refused until an administrator explicitly recovers." onConfirm={reason => post('/api/v1/security/emergency-stop', { reason, confirm: true })}/>)
      : <small>Administrator role required for emergency controls.</small>}</div>
    {st.disabled_capabilities.length > 0 && <p className="notice">Disabled capabilities: {st.disabled_capabilities.join(', ')}</p>}
   </Panel>
   <div className="small-metrics">{[metric('Authentication failures', st.authentication_failures), metric('Security alerts', st.security_alerts), metric('Allowed decisions', st.decisions.ALLOW ?? 0), metric('Blocked decisions', st.decisions.BLOCK ?? 0), metric('Escalated decisions', st.decisions.ESCALATE ?? 0), metric('Simulated emails sent', st.protected_executions.simulated_emails_sent), metric('Pending approvals', st.pending_approvals)]}</div>
   <p className="muted small-text">{st.note}</p>
   <Panel title="Protected operation states">{Object.keys(st.operations).length ? <div className="tags">{Object.entries(st.operations).map(([k, v]) => <span key={k}>{k}: {v}</span>)}</div> : <Empty title="No remote operations recorded"/>}</Panel>
  </>}
  <Panel title="Gateway operations" subtitle="Every authenticated remote request, its durable execution state and idempotency key.">{ops.error && <ErrorBox error={ops.error}/>}{ops.data?.length ? <div className="table-wrap"><table><thead><tr>{['Operation', 'Requesting identity', 'Workflow / tool', 'State', 'Reason', 'Updated'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{ops.data.map(o => <tr key={o.operation_id}><td><Link to={`/security/operations/${o.operation_id}`}>{o.operation_id}</Link><small>{o.request_id}</small></td><td>{o.agent_id}<small>{o.runtime_id}</small></td><td>{o.workflow_id}<small>{o.tool}</small></td><td><Badge value={o.state}/></td><td>{o.reason_code ?? '—'}</td><td>{o.updated_at}</td></tr>)}</tbody></table></div> : <Empty title="No gateway operations yet"/>}</Panel>
  <Panel title="Security events" subtitle="Chained system_security stream and gateway alerts. Text is rendered inert.">{events.error && <ErrorBox error={events.error}/>}{events.data?.length ? <ul className="event-list">{events.data.map(e => <li key={e.event_id}><span className="event-marker red"/><div><div className="event-meta"><time>{e.created_at}</time><span>#{e.seq}</span><span>{e.event_type}</span><span>{e.agent_id}</span>{e.reason_code && <span>{e.reason_code}</span>}</div><p>{e.summary}</p></div></li>)}</ul> : <Empty title="No security events recorded"/>}</Panel>
 </>}</>
}

export function ProtectedOperationPage() {
 const { operationId } = useParams(), s = useSession()
 const allowed = s.has('AUDITOR', 'ADMINISTRATOR', 'OPERATOR')
 const q = useApi<OperationDetail>(`/api/v1/security/operations/${operationId}`, allowed), op = q.data
 return <><PageTitle eyebrow="STAGE 7 / PROTECTED OPERATION" title={operationId ?? 'Operation'} description="Durable authorization and execution record: who requested what, what policy decided, and what actually ran."/>
  {!s.authenticated || !allowed ? <NeedsLogin roles="AUDITOR, ADMINISTRATOR, OPERATOR"/> : q.error ? <ErrorBox error={q.error}/> : !op ? <Loading/> : <>
   <div className="cards two"><Panel title="Request"><dl><dt>Requesting agent</dt><dd>{op.agent_id}</dd><dt>Agent runtime</dt><dd>{op.runtime_id}</dd><dt>Workflow</dt><dd><Link to={`/workflows/${op.workflow_id}`}>{op.workflow_id}</Link></dd><dt>Tool</dt><dd>{op.tool}</dd><dt>Gateway action</dt><dd>{op.action_id ?? 'not assigned'}</dd><dt>Approval reference</dt><dd>{op.approval_id ?? 'none'}</dd><dt>Idempotency key</dt><dd className="mono">{op.request_id}</dd><dt>Request digest</dt><dd className="mono wrap">{op.request_digest}</dd><dt>Correlation</dt><dd className="mono">{op.correlation_id}</dd></dl></Panel>
   <Panel title="Execution state"><p><Badge value={op.state}/> {op.reason_code ?? ''}</p>{op.state === 'UNKNOWN' && <p className="notice">Outcome could not be established. It is never retried automatically; review before any manual action.</p>}<dl><dt>Policy decision</dt><dd>{op.decision ? `${String(op.decision.decision)} · ${String(op.decision.reason_code)}` : 'no decision persisted'}</dd><dt>Grant expiry</dt><dd>{op.grant_expires_at ?? 'no grant issued'}</dd><dt>Record version</dt><dd>{op.version}</dd></dl>
    {op.approval_id && s.has('OPERATOR') && <ConfirmAction label="Revoke approval" title={`Revoke ${op.approval_id}`} warning="A revoked approval can no longer authorize any operation. Consumed approvals cannot be revoked." onConfirm={reason => post(`/api/v1/approvals/${op.approval_id}/revoke`, { reason, confirm: true })}/>}</Panel></div>
   <Panel title="State transitions"><div className="table-wrap"><table><thead><tr>{['Version', 'From', 'To', 'Actor', 'Reason', 'At'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{op.transitions.map(t => <tr key={t.version}><td>{t.version}</td><td>{t.from_state ?? '—'}</td><td><Badge value={t.to_state}/></td><td>{t.actor}</td><td>{t.reason_code ?? '—'}</td><td>{t.occurred_at}</td></tr>)}</tbody></table></div></Panel>
   <Panel title="Audit events for this action">{op.events.length ? <ul className="event-list">{op.events.map(e => <li key={e.event_id}><span className="event-marker"/><div><div className="event-meta"><span>#{e.seq}</span><span>{e.event_type}</span><span>{e.agent_id}</span></div><p>{e.summary}</p></div></li>)}</ul> : <Empty title="No action events" detail="Requests refused before reaching the policy engine have only a security event and decision."/>}</Panel>
   <Panel title="Stored response (returned to idempotent retries)"><JsonView value={op.response}/></Panel>
  </>}</>
}

export function ServiceHealthPage() {
 const s = useSession(), allowed = s.has('AUDITOR', 'ADMINISTRATOR', 'OPERATOR')
 const health = useApi<{ services: Service[]; stale_after_seconds: number; note: string }>('/api/v1/services/health', allowed)
 const metrics = useApi<{ services: Record<string, Json> }>('/api/v1/services/metrics', allowed)
 return <><PageTitle eyebrow="STAGE 7 / SERVICES" title="Service Health" description="Liveness and readiness as reported by each trusted service through database heartbeats."/>
  {!s.authenticated || !allowed ? <NeedsLogin roles="AUDITOR, ADMINISTRATOR, OPERATOR"/> : <>{health.error && <ErrorBox error={health.error}/>}
   <Panel title="Services" subtitle={health.data?.note}>{health.data ? <div className="table-wrap"><table><thead><tr>{['Service', 'Status', 'Last heartbeat', 'Age (s)', 'Readiness', 'Recent failures'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{health.data.services.map(r => <tr key={r.service}><td>{r.service}</td><td><Badge value={r.status === 'UP' ? 'CONNECTED' : r.status}/></td><td>{r.last_heartbeat}</td><td>{r.age_seconds}</td><td>{r.readiness ? Object.entries(r.readiness).map(([k, v]) => `${k}: ${v}`).join(', ') : 'reported by service'}</td><td>{Object.keys(r.recent_failures).length ? Object.entries(r.recent_failures).map(([k, v]) => `${k}=${v}`).join(', ') : 'none reported'}</td></tr>)}</tbody></table></div> : <Loading/>}
    {health.data && <p className="muted small-text">Services silent for more than {health.data.stale_after_seconds}s are shown as STALE. A missing service has never reported.</p>}</Panel>
   <Panel title="Operational metrics" subtitle="Per-process counters (low cardinality, no secrets). Unavailable values are not zero.">{metrics.data && Object.keys(metrics.data.services).length ? Object.entries(metrics.data.services).map(([name, m]) => <div key={name}><h3>{name}</h3><JsonView value={m}/></div>) : <Empty title="No service metrics reported"/>}</Panel>
  </>}</>
}

export function IdentityManagementPage() {
 const s = useSession(), allowed = s.has('AUDITOR', 'ADMINISTRATOR')
 const q = useApi<Identities>('/api/v1/security/identities', allowed), d = q.data
 const revoke = (id: string) => s.has('ADMINISTRATOR') && <ConfirmAction label="Revoke" title={`Revoke ${id}`} warning="Revocation takes effect at the gateway on the next request. It cannot be undone; register a new identity instead." onConfirm={reason => post(`/api/v1/security/identities/${id}/revoke`, { reason, confirm: true })}/>
 return <><PageTitle eyebrow="STAGE 7 / IDENTITIES" title="Identity Management" description="Agent-runtime service identities and server-side agent bindings. Roles and capabilities are never taken from credentials."/>
  {!s.authenticated || !allowed ? <NeedsLogin roles="AUDITOR, ADMINISTRATOR"/> : q.error ? <ErrorBox error={q.error}/> : !d ? <Loading/> : <>
   <Panel title="Agent runtimes">{d.runtimes.length ? <div className="table-wrap"><table><thead><tr>{['Runtime', 'Status', 'Created', 'Revoked', ''].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{d.runtimes.map(r => <tr key={String(r.runtime_id)}><td>{String(r.runtime_id)}<small>{String(r.description)}</small></td><td><Badge value={r.status === 'ACTIVE' ? 'CONNECTED' : 'REVOKED'}/></td><td>{String(r.created_at)}<small>{String(r.created_by)}</small></td><td>{r.revoked_at ? `${String(r.revoked_at)} by ${String(r.revoked_by)}` : '—'}</td><td>{r.status === 'ACTIVE' && revoke(String(r.runtime_id))}</td></tr>)}</tbody></table></div> : <Empty title="No runtimes registered"/>}</Panel>
   <Panel title="Agent bindings" subtitle="If several agents share a runtime credential set, that runtime is inside their identity boundary.">{d.agents.length ? <div className="table-wrap"><table><thead><tr>{['Agent', 'Runtime', 'Role', 'Workflows', 'Capabilities', 'Status', ''].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{d.agents.map(a => <tr key={a.agent_id}><td>{a.agent_id}</td><td>{a.runtime_id}</td><td>{a.role}</td><td>{a.workflows.join(', ')}</td><td>{a.capabilities.join(', ')}</td><td><Badge value={a.status === 'ACTIVE' ? 'CONNECTED' : 'REVOKED'}/></td><td>{a.status === 'ACTIVE' && revoke(a.agent_id)}</td></tr>)}</tbody></table></div> : <Empty title="No agents bound"/>}</Panel>
   <div className="cards two"><Panel title="Trusted human approvers"><ul className="plain-list">{d.approvers.map(a => <li key={a}>{a}</li>)}</ul></Panel><Panel title="Revoked credential IDs">{d.revoked_credentials.length ? <JsonView value={d.revoked_credentials}/> : <Empty title="No individually revoked credentials"/>}</Panel></div>
  </>}</>
}

export function AuditIntegrityPage() {
 const s = useSession(), allowed = s.has('AUDITOR', 'ADMINISTRATOR')
 const v = useApi<Verification>('/api/v1/audit/verification', allowed), cps = useApi<Checkpoint[]>('/api/v1/audit/checkpoints', allowed)
 const r = v.data
 return <><PageTitle eyebrow="STAGE 7 / AUDIT" title="Audit Integrity" description="Hash-chain verification over the append-only event log, recomputed by the backend on each request."/>
  {!s.authenticated || !allowed ? <NeedsLogin roles="AUDITOR, ADMINISTRATOR"/> : v.error ? <ErrorBox error={v.error}/> : !r ? <Loading/> : <>
   <Panel title="Verification result" subtitle={r.limitations}><p><Badge value={r.status === 'PASS' ? 'COMPLETED' : 'FAILED'}/> Audit verification: <strong>{r.status}</strong></p>
    <dl><dt>Algorithm</dt><dd>{r.algorithm ?? 'unavailable'}</dd><dt>Chained events</dt><dd>{r.chained_events ?? 'unavailable'}</dd><dt>Latest verified sequence</dt><dd>{r.latest_verified_seq ?? 'none'}</dd><dt>Head hash</dt><dd className="mono wrap">{r.head_hash ?? 'none'}</dd><dt>Legacy unchained events</dt><dd>{r.legacy_unchained_events ?? 0}</dd><dt>Checkpoints checked</dt><dd>{r.checkpoints_checked ?? 0}</dd><dt>Verified at</dt><dd>{r.verified_at ?? 'unavailable'}</dd></dl>
    <div className="actions"><ConfirmAction label="Write external checkpoint" title="Write audit checkpoint" destructive={false} warning="Pins the current chain head in a file outside the audited database. Store checkpoints in a separate trust domain." onConfirm={reason => post('/api/v1/audit/checkpoints', { reason, confirm: true })}/></div></Panel>
   <Panel title="Detected integrity failures">{r.failures.length ? <div className="table-wrap"><table><thead><tr>{['Kind', 'Sequence', 'Detail'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{r.failures.map((f, i) => <tr key={i}><td><Badge value="FAILED"/> {f.kind}</td><td>{f.seq ?? '—'}</td><td>{f.detail}</td></tr>)}</tbody></table></div> : <Empty title="No integrity failures detected" detail="Within the verified chain and the available checkpoints only."/>}</Panel>
   <Panel title="Trusted checkpoints">{cps.error && <ErrorBox error={cps.error}/>}{cps.data?.length ? <div className="table-wrap"><table><thead><tr>{['File', 'Sequence', 'Event hash', 'Created'].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{cps.data.map(c => <tr key={c.file}><td>{c.file}</td><td>{c.seq}</td><td className="mono wrap">{c.event_hash}</td><td>{c.created_at}</td></tr>)}</tbody></table></div> : <Empty title="No external checkpoints" detail="Without a checkpoint, a database writer could rewrite the whole chain undetected."/>}</Panel>
  </>}</>
}
