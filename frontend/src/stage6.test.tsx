import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { IntegrationDetailPage, IntegrationsPage, RegressionResultsPage, RuntimeMonitoringPage, SafetyRegressionPage } from './pages/Stage6'

function mount(node: ReactNode, path = '/', route = '/') {
 localStorage.setItem('sg-refresh', 'off')
 return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={node}/></Routes></MemoryRouter></QueryClientProvider>)
}
function mockApi(data: Record<string, unknown>, failures: Record<string, number> = {}) {
 const fetch = vi.fn(async (path: string, _options?: RequestInit) => failures[path]
  ? ({ ok: false, status: failures[path], statusText: 'error', json: async () => ({ detail: 'researcher login required' }) })
  : ({ ok: true, status: 200, json: async () => data[path] ?? null }))
 vi.stubGlobal('fetch', fetch)
 return fetch
}
afterEach(() => vi.unstubAllGlobals())

const caps = { agent_lifecycle_observation: true, tool_call_observation: true, tool_call_interception: true, trusted_execution_wrapping: true, human_approval_integration: true, workflow_graph_reconstruction: true, event_replay: true, automatic_global_hooks: false, remote_transport: false }
const catalog = { available: [
 { framework: 'custom', framework_version: '3.13.18', status: 'AVAILABLE', installed: true, compatible_version_family: true, capabilities: caps, scope: 'Explicit host registration and gateway-owned wrappers only.' },
 { framework: 'crewai', framework_version: null, status: 'DISCONNECTED', installed: false, compatible_version_family: false, capabilities: { ...caps, tool_call_interception: false }, scope: 'scope' },
], connections: [{ integration_id: 'integration_001', framework: 'custom', framework_version: '3.13.18', status: 'ERROR', mode: 'ENFORCED', monitoring: 'OBSERVE', registered_agents: ['operations_agent'], capabilities: caps, workflow_ids: [], tools: [], errors: ['RuntimeError: dependency failed'], verification: 'v', execution_boundary: 'b' }], jobs: [] }

describe('Stage 6 integrations', () => {
 it('requires a researcher session before reading integration records', async () => {
  const fetch = mockApi({ '/api/session': { authenticated: false } })
  mount(<IntegrationsPage/>)
  expect(await screen.findByText('Researcher login required')).toBeInTheDocument()
  expect(fetch.mock.calls.every(([path]) => !path.startsWith('/api/integrations'))).toBe(true)
 })
 it('shows actual versions, unavailable capabilities, disabled unsupported frameworks and recent errors', async () => {
  mockApi({ '/api/session': { authenticated: true }, '/api/integrations': catalog })
  mount(<IntegrationsPage/>)
  expect(await screen.findByText('Installed version: 3.13.18')).toBeInTheDocument()
  expect(screen.getByText('Installed version: not installed')).toBeInTheDocument()
  expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0)
  expect(screen.getByRole('option', { name: 'crewai · unavailable' })).toBeDisabled()
  expect(screen.getByText('RuntimeError: dependency failed')).toBeInTheDocument()
 })
 it('launches a run without identities, tools, approvals or policies in the body', async () => {
  const fetch = mockApi({ '/api/session': { authenticated: true }, '/api/integrations': catalog, '/api/integrations/runs': { job_id: 'platform_job_001' } })
  mount(<IntegrationsPage/>)
  await screen.findByText('Installed version: 3.13.18')
  fireEvent.click(screen.getByRole('button', { name: 'Run framework workflow' }))
  await waitFor(() => expect(fetch.mock.calls.some(([path]) => path === '/api/integrations/runs')).toBe(true))
  const body = JSON.parse(String(fetch.mock.calls.find(([path]) => path === '/api/integrations/runs')![1]!.body))
  expect(body).toEqual({ config: { framework: 'custom' }, case: 'service', human_approval: 'INTERACTIVE' })
 })
 it('shows event coverage with unobserved kinds and dropped unattributed events', async () => {
  const record = { ...catalog.connections[0], status: 'DISCONNECTED', errors: [], event_coverage: { TOOL_CALL_PROPOSED: 4, AUTHORIZATION_DECISION: 4 }, unattributed_native_events: 2, checkpoint_refs: ['ckpt_a'] }
  mockApi({ '/api/session': { authenticated: true }, '/api/integrations/integration_001': record, '/api/integrations/integration_001/events': [] })
  mount(<IntegrationDetailPage/>, '/integrations/integration_001', '/integrations/:integrationId')
  expect(await screen.findByText('Event coverage')).toBeInTheDocument()
  expect(screen.getAllByText('not observed').length).toBe(9)
  expect(screen.getByText(/dropped, not mapped to any identity\): 2/)).toBeInTheDocument()
  expect(screen.getByText('No normalized events recorded')).toBeInTheDocument()
 })
})

describe('Stage 6 runtime monitoring', () => {
 it('renders normalized events with their evidence source', async () => {
  mockApi({ '/api/session': { authenticated: true }, '/api/platform/jobs': [], '/api/runtime/events': [
   { event_id: 'canonical_001', seq: 1, integration_id: 'integration_001', framework: 'crewai', workflow_id: 'wf_1', agent_id: 'operations_agent', event_type: 'TOOL_CALL_PROPOSED', source: 'FRAMEWORK_REPORTED', action_id: null, tool_call_id: 'call_001', timestamp: '2026-11-06T16:00:00Z', metadata: {} },
   { event_id: 'canonical_002', seq: 2, integration_id: 'integration_001', framework: 'crewai', workflow_id: 'wf_1', agent_id: 'operations_agent', event_type: 'AUTHORIZATION_DECISION', source: 'GATEWAY_VERIFIED', action_id: 'action_001', tool_call_id: 'call_001', timestamp: '2026-11-06T16:00:01Z', metadata: {} }] })
  mount(<RuntimeMonitoringPage/>)
  expect(await screen.findByText('FRAMEWORK_REPORTED')).toBeInTheDocument()
  expect(screen.getByText('GATEWAY_VERIFIED')).toBeInTheDocument()
  expect(screen.getByText('unavailable')).toBeInTheDocument()   // missing action reference is shown as missing
 })
 it('shows an error state when the backend refuses the request', async () => {
  mockApi({ '/api/session': { authenticated: true }, '/api/platform/jobs': [] }, { '/api/runtime/events': 401 })
  mount(<RuntimeMonitoringPage/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('researcher login required')
 })
})

const metrics = (prohibited: number, completion: number | null) => ({ n: 3, failed_trials: 0, prohibited_outcomes: prohibited, prohibited_outcome_rate: prohibited / 3, legitimate_completion: completion, mean_workflow_ms: 10, detection: { global_workflow: { precision: null, recall: 1, f1: null, false_positive_rate: 0 } } })
const report = { run_id: 'regression_001', baseline_id: 'baseline_001', candidate_id: 'framework_eval_002', baseline_version: 'fixture-safe-v1', candidate_config: { application_version: 'fixture-redirected-v2' }, classification: 'CONFIRMED_THRESHOLD_VIOLATION', ci_passed: false, exit_code: 1, sample_counts: { baseline: 3, candidate: 3 }, mandatory_violation: true, uncertainty: 'Deterministic synthetic thresholds; no statistical significance.', metrics: [{ metric: 'precision', baseline_value: null, candidate_value: null, difference: null, classification: 'INCONCLUSIVE' }, { metric: 'prohibited_outcome_rate', baseline_value: 0, candidate_value: 0.333, difference: 0.333, classification: 'NO_REGRESSION_DETECTED' }], new_failures: [{ case_id: 'service', trial: 0, failure: 'prohibited_execution' }], resolved_failures: [], missing_comparisons: [], baseline_metrics: metrics(0, 1), candidate: { experiment_id: 'framework_eval_002', status: 'COMPLETED', config: { application: 'synthetic_service', application_version: 'fixture-redirected-v2', framework: 'custom' }, metrics: metrics(1, 0), rows: [], suite_version: 'stage6-core-v1', unrun_trials: 0 }, benchmark_version: 'stage6-core-v1', execution_mode: 'MOCK_SYNTHETIC' }

describe('Stage 6 safety regression', () => {
 it('configures a candidate evaluation with bounded fields only', async () => {
  const fetch = mockApi({ '/api/session': { authenticated: true }, '/api/integrations': catalog, '/api/evaluation/frameworks': [], '/api/regression/baselines': [], '/api/regression/runs': [], '/api/evaluation/frameworks/runs': { job_id: 'platform_job_002' } })
  mount(<SafetyRegressionPage/>)
  expect(await screen.findByText('No evaluations yet')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Start safety regression' })).toBeDisabled()   // no approved baseline selected
  fireEvent.change(screen.getByLabelText('Controlled application profile'), { target: { value: 'redirected' } })
  fireEvent.click(screen.getByRole('button', { name: 'Evaluate application' }))
  await waitFor(() => expect(fetch.mock.calls.some(([path]) => path === '/api/evaluation/frameworks/runs')).toBe(true))
  const body = JSON.parse(String(fetch.mock.calls.find(([path]) => path === '/api/evaluation/frameworks/runs')![1]!.body))
  expect(body).toEqual({ framework: 'custom', application: 'synthetic_service', application_version: 'fixture-candidate-v2', profile: 'redirected' })
 })
 it('renders the CI decision, sample counts, new failures and undefined metrics without zero-filling', async () => {
  mockApi({ '/api/session': { authenticated: true }, '/api/regression/runs/regression_001': report, '/api/evaluation/runtime-overhead': [] })
  mount(<RegressionResultsPage/>, '/regression/regression_001', '/regression/:runId')
  expect(await screen.findByText('CONFIRMED THRESHOLD VIOLATION')).toHaveClass('badge', 'danger')
  expect(screen.getByText(/Exit code:/)).toHaveTextContent('Exit code: 1 · Gate passed: false')
  expect(screen.getByText(/n baseline 3, candidate 3/)).toBeInTheDocument()
  expect(screen.getAllByText('undefined').length).toBe(3)
  expect(screen.getByText(/prohibited_execution/)).toBeInTheDocument()
  expect(screen.getByText('No overhead experiment recorded in this database')).toBeInTheDocument()
 })
 it('renders measured runtime overhead with insufficient-sample percentiles', async () => {
  mockApi({ '/api/session': { authenticated: true }, '/api/regression/runs/regression_001': report, '/api/evaluation/runtime-overhead': [
   { framework: 'custom', is_synthetic: true, note: 'All conditions retain deterministic authorization.', rows: [{ configuration: 'DETERMINISTIC_GATEWAY', n: 3, median_workflow_ms: 20, median_action_ms: 1.5, p95_action_ms: null, action_samples: 30, throughput_per_second: 500, llm_tokens: null }] }] })
  mount(<RegressionResultsPage/>, '/regression/regression_001', '/regression/:runId')
  expect(await screen.findByText('DETERMINISTIC_GATEWAY')).toBeInTheDocument()
  expect(screen.getByText('insufficient samples')).toBeInTheDocument()
  expect(screen.getAllByText('unavailable').length).toBeGreaterThan(0)
 })
})
