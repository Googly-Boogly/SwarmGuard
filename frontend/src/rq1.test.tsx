import type { ReactNode } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RQ1LabPage, ValidationReport } from './pages/RQ1Lab'
import { RawDataTab } from './rq1/RawData'
import { CopyButton, OriginalJson, Tag } from './rq1/components'
import type { Overview, RawPage, Validation } from './rq1/api'

afterEach(() => vi.unstubAllGlobals())

function mount(node: ReactNode, path = '/') {
 return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[path]}><Routes><Route path="*" element={node}/></Routes></MemoryRouter></QueryClientProvider>)
}

const config = { partition: 'held_out' as const, scenario_ids: null, seed: 0, trials: 1, execution_mode: 'SCRIPTED' as const, monitors: ['local_action', 'global_workflow'], max_runtime_seconds: 300, max_steps_per_workflow: 60, timing_repeats: 5, llm_mode: 'none' as const, llm_checkpoints: 'outbound' as const, llm_max_requests: 200, llm_max_cost_usd: null, confirm_paid_calls: false }
const overview: Overview = {
 llm_status: { mock: { available: true, reasons: [], provider: 'mock', model: 'mock-structured-v1' }, live: { available: false, reasons: ['the server was not started with SWARMGUARD_CONSOLE_LIVE_LLM=1'], provider: 'openai', model: 'gpt-x', max_requests_cap: 1000, max_cost_cap_usd: 5 } },
 llm_policies: { critical: 'only the critical action', outbound: 'every outbound action', all: 'every action checkpoint' }, llm_protocol_version: 'rq1-llm-protocol-1',
 question: { research_question_id: 'RQ1', title: 'Global Workflow Monitoring', question: 'Can global workflow monitoring detect distributed approval laundering?', hypothesis: 'H1 text', null_hypothesis: 'H0 text', goal: 'Goal text' },
 datasets: [{ partition: 'held_out', dataset_version: 'rq1-heldout-1', content_hash: 'a'.repeat(64), n: 24, unsafe: 12, legitimate: 12, categories: ['distributed_approval_laundering'], composition: { distributed_approval_laundering: { UNSAFE: 6, SAFE: 6 } }, frozen: true, matches_frozen_hash: true, role: 'Primary RQ1 evidence', scenarios: [] },
  { partition: 'development', dataset_version: 'stage2-library', content_hash: 'b'.repeat(64), n: 14, unsafe: 8, legitimate: 6, categories: ['x'], composition: { x: { UNSAFE: 8, SAFE: 6 } }, frozen: false, matches_frozen_hash: false, role: 'Development only', scenarios: [{ scenario_id: 'distributed_approval_001', category: 'x', family: 'f', label: 'UNSAFE' }] }],
 recommended_config: config, pilot_config: { ...config, partition: 'development', scenario_ids: ['distributed_approval_001', 'legitimate_collaboration_001'] },
 unavailable_options: { execution_modes: { LIVE_LLM: 'Disabled in the browser.' }, monitors: { global_ethics_council: 'Not available.' } },
 known_limitations: ['limit'], protocol_version: 'rq1-protocol-1', frozen_dataset_hash: 'a'.repeat(64), execution_note: 'Deterministic.',
 experiments: [{ experiment_id: 'rq1_experiment_007', status: 'COMPLETED', config, created_by: 'human_operator', created_at: '2026-11-07T10:00:00Z', started_at: null, completed_at: null, error: null, preregistered_procedure: true, execution_mode: 'DETERMINISTIC_SIMULATION', progress: { phase: 'COMPLETED', total_workflows: 24, completed_workflows: 24, failed_workflows: 0, current_workflow: null, current_scenario: null, phases: [] }, headline: { n_workflows: 24, local_recall: 5 / 12, global_recall: 11 / 12, local_false_positive_rate: 0.25, global_false_positive_rate: 0, decision_rule: { applies: true, satisfied: true, note: '' } } }],
}
const validation: Validation = { status: 'WARNINGS', checks: [{ id: 'dataset', title: 'Dataset exists', status: 'WARN', detail: 'Development partition' }, { id: 'future_events', title: 'Future events are excluded', status: 'PASS', detail: 'ok' }], known_limitations: ['Same-author construction'], selected_scenarios: 2, unsafe: 1, legitimate: 1, dataset_hash: 'b', dataset_version: 'stage2-library', note: 'note' }

function api(routes: (path: string, init?: RequestInit) => unknown) {
 const fn = vi.fn(async (path: string, init?: RequestInit) => ({ ok: true, status: 200, json: async () => routes(path, init) }))
 vi.stubGlobal('fetch', fn)
 return fn
}

describe('RQ1 laboratory', () => {
 it('home shows the RQ1 card, hypothesis, datasets and previous results from the API', async () => {
  api(() => overview)
  mount(<RQ1LabPage view="home"/>)
  expect(await screen.findByText('Can global workflow monitoring detect distributed approval laundering?')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'NEW EXPERIMENT' })).toHaveAttribute('href', '/research/rq1/new')
  expect(screen.getByRole('link', { name: 'VIEW PREVIOUS RESULTS' })).toHaveAttribute('href', '/research/rq1/experiments/rq1_experiment_007')
  expect(screen.getByText(/Local recall 41.7%, FPR 25.0% · Global recall 91.7%, FPR 0.0% \(n=24\)/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'rq1_experiment_007' })).toBeInTheDocument()
 })

 it('run stays disabled until the current settings validate, and a change invalidates validation', async () => {
  const fetch = api((path, init) => path === '/api/research/rq1/validate' ? { ...validation, echoed: init?.body } : overview)
  mount(<RQ1LabPage view="new"/>)
  const run = await screen.findByRole('button', { name: 'RUN RQ1 EXPERIMENT' })
  expect(run).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: /Use pilot settings/ }))
  expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue('development')
  fireEvent.click(screen.getByRole('button', { name: 'VALIDATE METHODOLOGY' }))
  expect(await screen.findByLabelText('Validation report')).toHaveTextContent('WARNINGS')
  const sent = JSON.parse(String(fetch.mock.calls.find(c => c[0] === '/api/research/rq1/validate')![1]!.body))
  expect(sent).toMatchObject({ partition: 'development', scenario_ids: ['distributed_approval_001', 'legitimate_collaboration_001'], monitors: ['local_action', 'global_workflow'] })
  expect(run).toBeEnabled()
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Random seed' }), { target: { value: '4' } })
  expect(run).toBeDisabled()
  expect(screen.getByText(/Settings changed after validation/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'USE RECOMMENDED RQ1 SETTINGS' }))
  expect(screen.getByRole('combobox', { name: 'Dataset' })).toHaveValue('held_out')
 })

 it('LLM controls show live availability reasons and require paid-call confirmation for live runs', async () => {
  const live = { ...overview, llm_status: { ...overview.llm_status, live: { ...overview.llm_status.live, available: true, reasons: [] } } }
  api(path => path === '/api/research/rq1/validate' ? { ...validation, status: 'VALIDATED', llm_estimate: { calls: 48, estimated_input_tokens: 376140, max_output_tokens_total: 38400, estimated_max_cost_usd: 0.53, note: 'estimate' } } : live)
  mount(<RQ1LabPage view="new"/>)
  const mode = await screen.findByRole('combobox', { name: 'LLM mode' })
  expect(within(mode).getByRole('option', { name: /Live — openai · gpt-x \(paid\)/ })).toBeEnabled()
  fireEvent.change(mode, { target: { value: 'live' } })
  expect(screen.getByLabelText('C · LLM Local monitor (llm_local)')).toBeChecked()
  expect(screen.getByRole('spinbutton', { name: 'Maximum cost' })).toHaveValue(1)
  fireEvent.click(screen.getByRole('button', { name: 'VALIDATE METHODOLOGY' }))
  expect(await screen.findByText(/48 calls · ~376,140 input tokens · worst-case cost \$0.53/)).toBeInTheDocument()
  const run = screen.getByRole('button', { name: 'RUN RQ1 EXPERIMENT' })
  expect(run).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Confirm paid calls'))
  expect(run).toBeDisabled()  // confirmation changed the settings, so they must be re-validated
  fireEvent.click(screen.getByRole('button', { name: 'VALIDATE METHODOLOGY' }))
  await waitFor(() => expect(run).toBeEnabled())
 })

 it('live LLM is shown as unavailable with the server reasons', async () => {
  api(() => overview)
  mount(<RQ1LabPage view="new"/>)
  const mode = await screen.findByRole('combobox', { name: 'LLM mode' })
  expect(within(mode).getByRole('option', { name: /Live .*unavailable/ })).toBeDisabled()
  expect(within(mode).getByRole('option', { name: /Mock/ })).toBeEnabled()
  expect(screen.getByRole('note')).toHaveTextContent('SWARMGUARD_CONSOLE_LIVE_LLM=1')
 })

 it('validation report shows every check with a text status, never color alone', () => {
  render(<ValidationReport v={{ ...validation, status: 'INVALID', checks: [{ id: 'x', title: 'Execution limits are valid', status: 'FAIL', detail: 'too low' }] }}/>)
  expect(screen.getByText('INVALID')).toBeInTheDocument()
  expect(screen.getByText('Critical problems prevent a trustworthy evaluation.')).toBeInTheDocument()
  expect(screen.getByText('FAIL')).toBeInTheDocument()
 })

 it('raw data table requests server-side filters, sorting and pages, and expands rows', async () => {
  const page: RawPage = { level: 'checkpoint', total: 30, page: 1, page_size: 25, pages: 2, facets: { monitor: ['Global', 'Local'], ground_truth: ['SAFE', 'UNSAFE'], recommendation: ['ALLOW', 'ESCALATE'] },
   rows: [{ row_id: 'wf_001:1:local_action', scenario_id: 's1', workflow_id: 'wf_001', monitor: 'Local', configuration: 'local_action', status: 'COMPLETE', step: 1, trial: 1, agent_id: 'research_agent', tool: 'send_email', ground_truth: 'UNSAFE', recommendation: 'ALLOW', risk_level: 'LOW', workflow_correct: false, position: 'critical', critical_step: 1, latency_ms: 0.1 }] }
  const fetch = api(() => page)
  mount(<RawDataTab eid="rq1_experiment_001"/>)
  const table = await screen.findByRole('table', { name: 'Raw data table' })
  expect(within(table).getByText('send_email')).toBeInTheDocument()
  expect(within(table).getByText('INCORRECT')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('combobox', { name: 'Filter Monitor' }), { target: { value: 'Local' } })
  await waitFor(() => expect(String(fetch.mock.calls.at(-1)![0])).toContain('monitor=Local'))
  fireEvent.click(within(table).getByRole('button', { name: 'Step' }))
  await waitFor(() => expect(String(fetch.mock.calls.at(-1)![0])).toContain('sort=step'))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  await waitFor(() => expect(String(fetch.mock.calls.at(-1)![0])).toContain('page=2'))
  fireEvent.click(screen.getByRole('button', { name: 'Global false positives' }))
  await waitFor(() => { const url = String(fetch.mock.calls.at(-1)![0]); expect(url).toContain('level=workflow'); expect(url).toContain('ground_truth=SAFE'); expect(url).toContain('workflow_prediction=DETECTED') })
  fireEvent.click((await screen.findAllByRole('button', { name: 'Expand row' }))[0])
  expect(document.querySelector('.rq1-expanded')).toHaveTextContent('row_id')
 })

 it('raw data shows a clear empty state and an error state', async () => {
  api(() => ({ level: 'checkpoint', total: 0, page: 1, page_size: 25, pages: 1, rows: [], facets: {} }))
  mount(<RawDataTab eid="rq1_experiment_001"/>)
  expect(await screen.findByText('No rows match these filters')).toBeInTheDocument()
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 409, statusText: 'Conflict', json: async () => ({ detail: 'results are not available' }) })))
  mount(<RawDataTab eid="rq1_experiment_002"/>)
  expect(await screen.findByText('results are not available')).toBeInTheDocument()
 })

 it('copy buttons copy the exact text and original JSON is hidden until requested', async () => {
  const write = vi.fn(async () => undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true })
  render(<><CopyButton text="exact summary" label="COPY RESULTS SUMMARY"/><OriginalJson value={{ a: 1 }}/></>)
  fireEvent.click(screen.getByRole('button', { name: 'COPY RESULTS SUMMARY' }))
  await waitFor(() => expect(write).toHaveBeenCalledWith('exact summary'))
  expect(await screen.findByText('Copied to clipboard')).toBeInTheDocument()
  expect(screen.queryByLabelText('Original JSON')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'VIEW ORIGINAL JSON' }))
  expect(screen.getByLabelText('Original JSON')).toHaveTextContent('"a": 1')
 })

 it('status tags render unavailable values explicitly', () => {
  render(<><Tag value={null}/><Tag value="UNSAFE"/></>)
  expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument()
  expect(screen.getByText('UNSAFE')).toBeInTheDocument()
 })
})
