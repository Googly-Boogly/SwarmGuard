import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { AuditIntegrityPage, IdentityManagementPage, ProtectedOperationPage, SecurityOperationsPage, ServiceHealthPage } from './pages/Stage7'

function mount(node: ReactNode, path = '/', route = '/') {
 localStorage.setItem('sg-refresh', 'off')
 return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={node}/></Routes></MemoryRouter></QueryClientProvider>)
}
function mockApi(data: Record<string, unknown>, failures: Record<string, [number, string]> = {}) {
 const fetch = vi.fn(async (path: string, _options?: RequestInit) => failures[path]
  ? ({ ok: false, status: failures[path][0], statusText: 'error', json: async () => ({ detail: failures[path][1] }) })
  : ({ ok: true, status: 200, json: async () => data[path] ?? null }))
 vi.stubGlobal('fetch', fetch)
 return fetch
}
const session = (roles: string[]) => ({ authenticated: true, operator_id: 'user_x', csrf_token: 'csrf', login_available: true, roles })
afterEach(() => vi.unstubAllGlobals())

const status = { emergency_stop: { active: false, version: 0 }, disabled_capabilities: [], decisions: { ALLOW: 7, BLOCK: 3, ESCALATE: 1 }, authentication_failures: 4, security_alerts: 5, operations: { SUCCEEDED: 6, BLOCKED: 4, UNKNOWN: 1 }, protected_executions: { simulated_emails_sent: 1 }, active_workflows: 1, pending_approvals: 0, note: 'Blocked/escalated decisions are proposed violations; simulated sends are executed effects.' }
const ops = [{ operation_id: 'operation_007', agent_id: 'operations_agent', runtime_id: 'runtime_alpha', request_id: 'send_1', request_digest: 'abc', workflow_id: 'wf_remote', tool: 'send_email', action_id: 'action_009', state: 'UNKNOWN', reason_code: 'EXECUTION_OUTCOME_UNKNOWN', correlation_id: 'c', created_at: 't0', updated_at: 't1', grant_expires_at: null, version: 4 }]
const securityData = (roles: string[]) => ({ '/api/session': session(roles), '/api/v1/security/status': status, '/api/v1/security/operations': ops,
 '/api/v1/security/events?limit=100': [{ event_id: 'event_1', seq: 3, event_type: 'AUTHENTICATION_FAILED', agent_id: 'swarmguard.gateway', summary: '<img src=x onerror="window.stage7Injected=true">', reason_code: 'CREDENTIAL_REVOKED', created_at: 't' }] })

describe('Stage 7 security operations', () => {
 it('requires an authorized role before any security read', async () => {
  const fetch = mockApi({ '/api/session': session(['RESEARCHER']) })
  mount(<SecurityOperationsPage/>)
  expect(await screen.findByText('Operator login required')).toBeInTheDocument()
  expect(fetch.mock.calls.every(([p]) => !p.startsWith('/api/v1/'))).toBe(true)
 })
 it('shows actual security status, operations and inert event text', async () => {
  mockApi(securityData(['AUDITOR']))
  mount(<SecurityOperationsPage/>)
  expect(await screen.findByText('Authentication failures')).toBeInTheDocument()
  expect(screen.getByText('4')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'operation_007' })).toHaveAttribute('href', '/security/operations/operation_007')
  expect(screen.getByText('<img src=x onerror="window.stage7Injected=true">')).toBeInTheDocument()
  expect(document.querySelector('img[onerror]')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Activate emergency stop' })).toBeNull()   // auditor: no controls
  expect(screen.getByText('Administrator role required for emergency controls.')).toBeInTheDocument()
 })
 it('emergency stop requires a reason and explicit confirmation', async () => {
  const fetch = mockApi({ ...securityData(['ADMINISTRATOR']), '/api/v1/security/emergency-stop': { active: true, version: 1 } })
  mount(<SecurityOperationsPage/>)
  fireEvent.click(await screen.findByRole('button', { name: 'Activate emergency stop' }))
  const confirm = screen.getByRole('button', { name: 'Confirm' })
  expect(confirm).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Reason (recorded in the audit chain)'), { target: { value: 'suspected compromise' } })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByLabelText('I confirm this action'))
  expect(confirm).toBeEnabled()
  fireEvent.click(confirm)
  await waitFor(() => expect(fetch.mock.calls.some(([p]) => p === '/api/v1/security/emergency-stop')).toBe(true))
  const body = JSON.parse(String(fetch.mock.calls.find(([p]) => p === '/api/v1/security/emergency-stop')![1]!.body))
  expect(body).toEqual({ reason: 'suspected compromise', confirm: true })
 })
 it('recovery requires the typed phrase', async () => {
  mockApi({ ...securityData(['ADMINISTRATOR']), '/api/v1/security/status': { ...status, emergency_stop: { active: true, activated_by: 'admin_ada', reason: 'drill', activated_at: 't', version: 1 } } })
  mount(<SecurityOperationsPage/>)
  fireEvent.click(await screen.findByRole('button', { name: 'Recover from emergency stop' }))
  fireEvent.change(screen.getByLabelText('Reason (recorded in the audit chain)'), { target: { value: 'resolved' } })
  fireEvent.click(screen.getByLabelText('I confirm this action'))
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText(/to confirm/), { target: { value: 'RESUME PROTECTED OPERATIONS' } })
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled()
 })
 it('shows the backend refusal when an administrative action is rejected', async () => {
  mockApi(securityData(['ADMINISTRATOR']), { '/api/v1/security/emergency-stop': [403, 'role ADMINISTRATOR required'] })
  mount(<SecurityOperationsPage/>)
  fireEvent.click(await screen.findByRole('button', { name: 'Activate emergency stop' }))
  fireEvent.change(screen.getByLabelText('Reason (recorded in the audit chain)'), { target: { value: 'test' } })
  fireEvent.click(screen.getByLabelText('I confirm this action'))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('role ADMINISTRATOR required')
 })
})

describe('Stage 7 identities, services, audit and operations', () => {
 const identities = { runtimes: [{ runtime_id: 'runtime_alpha', description: 'agents', status: 'ACTIVE', created_at: 't', created_by: 'admin', revoked_at: null }], agents: [{ agent_id: 'operations_agent', runtime_id: 'runtime_alpha', role: 'operations', workflows: ['wf_remote'], capabilities: ['send_email'], status: 'ACTIVE', created_at: 't', revoked_at: null }], revoked_credentials: [], approvers: ['operator_alice'], policy_version: 'stage4-v1' }
 it('shows identities read-only to auditors and revocation only to administrators', async () => {
  mockApi({ '/api/session': session(['AUDITOR']), '/api/v1/security/identities': identities })
  const { unmount } = mount(<IdentityManagementPage/>)
  expect(await screen.findByText('operator_alice')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Revoke' })).toBeNull()
  unmount()
  mockApi({ '/api/session': session(['ADMINISTRATOR']), '/api/v1/security/identities': identities })
  mount(<IdentityManagementPage/>)
  expect((await screen.findAllByRole('button', { name: 'Revoke' })).length).toBe(2)
 })
 it('operators cannot read identities', async () => {
  mockApi({ '/api/session': session(['OPERATOR']) })
  mount(<IdentityManagementPage/>)
  expect(await screen.findByText('Operator login required')).toBeInTheDocument()
 })
 it('displays service health and stale services as reported', async () => {
  mockApi({ '/api/session': session(['OPERATOR']), '/api/v1/services/metrics': { services: {} },
   '/api/v1/services/health': { stale_after_seconds: 30, note: 'heartbeats', services: [{ service: 'gateway', status: 'STALE', last_heartbeat: 't', age_seconds: 95, recent_failures: { authentication_failures: 2 } }, { service: 'control', status: 'UP', last_heartbeat: 't', age_seconds: 0, recent_failures: {}, readiness: { database: 'ok', migrations: 'ok' } }] } })
  mount(<ServiceHealthPage/>)
  expect(await screen.findByText('STALE')).toBeInTheDocument()
  expect(screen.getByText('authentication_failures=2')).toBeInTheDocument()
  expect(screen.getByText('database: ok, migrations: ok')).toBeInTheDocument()
  expect(screen.getByText('No service metrics reported')).toBeInTheDocument()
 })
 it('displays actual audit verification failures', async () => {
  mockApi({ '/api/session': session(['AUDITOR']), '/api/v1/audit/checkpoints': [],
   '/api/v1/audit/verification': { status: 'FAIL', algorithm: 'sha256-canonical-json-v1', chained_events: 40, latest_verified_seq: 32, head_hash: 'ff', legacy_unchained_events: 0, checkpoints_checked: 0, verified_at: 't', limitations: 'Tamper-evident only.', failures: [{ kind: 'ALTERED_EVENT', seq: 33, detail: 'content of event event_033 differs from its hash' }] } })
  mount(<AuditIntegrityPage/>)
  expect(await screen.findByText('FAIL')).toBeInTheDocument()
  expect(screen.getByText(/ALTERED_EVENT/)).toBeInTheDocument()
  expect(screen.getByText('content of event event_033 differs from its hash')).toBeInTheDocument()
  expect(screen.getByText('No external checkpoints')).toBeInTheDocument()
 })
 it('renders a protected operation with UNKNOWN state warning and approval revocation for operators', async () => {
  mockApi({ '/api/session': session(['OPERATOR']), '/api/v1/security/operations/operation_007': { ...ops[0], approval_id: 'approval_001', decision: { decision: 'ALLOW', reason_code: 'AUTHORIZED' }, response: { executed: false },
   transitions: [{ version: 1, from_state: null, to_state: 'PENDING', actor: 'swarmguard.gateway', reason_code: null, occurred_at: 't' }, { version: 4, from_state: 'EXECUTING', to_state: 'UNKNOWN', actor: 'swarmguard.gateway', reason_code: 'EXECUTION_OUTCOME_UNKNOWN', occurred_at: 't' }], events: [] } })
  mount(<ProtectedOperationPage/>, '/security/operations/operation_007', '/security/operations/:operationId')
  expect(await screen.findByText(/never retried automatically/)).toBeInTheDocument()
  expect(screen.getByText('ALLOW · AUTHORIZED')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Revoke approval' })).toBeInTheDocument()
  expect(screen.getByText('send_1')).toBeInTheDocument()
 })
})
