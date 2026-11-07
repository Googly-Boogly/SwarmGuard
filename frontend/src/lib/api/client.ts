import { z } from 'zod'
import { useQuery } from '@tanstack/react-query'
import type { components } from './schema'
export type Job = Required<components['schemas']['JobRecord']>
export type Agent = components['schemas']['AgentView']
export type Workflow = components['schemas']['WorkflowView']
export type Decision = components['schemas']['DecisionRecord']
export type Scenario = components['schemas']['ScenarioView']
export type Approval = components['schemas']['ApprovalView']
export type RunConfig = components['schemas']['RunConfig']
export type Overview = components['schemas']['Overview']
export type Graph = components['schemas']['WorkflowGraph']
export type Event = components['schemas']['WorkflowEvent']
export type Session = components['schemas']['SessionView']
export type Json = Record<string, unknown>
export type CouncilRecord = { evaluation_id: string; action_id: string; created_at: string; report: { council_recommendation: string; aggregation_method: string; disagreement_detected: boolean; contains_mock_assessments: boolean; assessments: Assessment[]; failed_monitors: string[]; missing_frameworks: string[]; failures: Json[]; summary: string; disagreement?: Json } }
export type Assessment = { framework: string; risk_level: string; recommendation: string; explanation: string; concerns: unknown[]; evidence_event_ids: string[]; uncertainty: string; is_mock: boolean; model?: string; status?: string; [key: string]: unknown }
export type WorkflowRecords = { actions: Json[]; decisions: Decision[]; messages: { message_id: string; sender_id: string; recipient_id: string; message_type: string; workflow_id: string; text: string; created_at: string; action_id?: string }[]; tasks: Json[]; resource_versions: Record<string, Json[]>; agent_decisions: Json[]; assessments: Json[]; ethics: CouncilRecord[]; sent_emails: Json[] }
export type Results = { job_id: string; mode: string; note: string; sample_count: number; detection_lead: Record<string, { mean_lead_steps: number | null; detected: number; undetected: number }>; council_assessment_count: number; council_disagreement_count: number; council_disagreement_rate: number | null; failed_trials: number; unrun_trials: number; metrics: Record<string, Record<string, number | null>>; prevention_rate: number | null; legitimate_task_completion_rate: number | null; average_steps: number | null; unsafe_n: number; legitimate_n: number; outcomes: Json[]; metadata: Json; config: RunConfig }
export type Health = { status: string; stage: string; provider: string; model: string; live_available: boolean; ethics_available: boolean; transport: string; secure_cookie: boolean; scope: string; active_jobs: number; recent_errors: string[] }
export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }
let csrf: string | null = null
export function setCsrf(value: string | null | undefined) { csrf = value ?? null }
export async function request<T>(path: string, options: RequestInit = {}, schema?: z.ZodType<T>): Promise<T> {
 const response = await fetch(path, { ...options, credentials: 'same-origin', headers: { Accept: 'application/json', ...(options.method && options.method !== 'GET' ? { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}) } : {}), ...options.headers } })
 const raw: unknown = await response.json().catch(() => null)
 if (!response.ok) { const detail = raw && typeof raw === 'object' && 'detail' in raw ? raw.detail : response.statusText; throw new ApiError(response.status, typeof detail === 'string' ? detail : JSON.stringify(detail)) }
 if (schema) { const parsed = schema.safeParse(raw); if (!parsed.success) throw new Error('Backend returned an invalid response. Refresh or inspect System Status.'); return parsed.data }
 return raw as T
}
export const sessionSchema = z.object({ authenticated: z.boolean(), operator_id: z.string().nullish(), csrf_token: z.string().nullish(), login_available: z.boolean(), login_required: z.boolean().default(true), roles: z.array(z.string()).optional() })
async function consoleSession(signal: AbortSignal): Promise<Session> {
 let session = await request<Session>('/api/session', { signal }, sessionSchema)
 if (session.login_required === false && !session.authenticated) {
  session = await request<Session>('/api/session/local', { method: 'POST', body: '{}', signal }, sessionSchema)
 }
 setCsrf(session.csrf_token)
 return session
}
export const graphSchema = z.object({ workflow_id: z.string(), nodes: z.array(z.object({ node_id: z.string(), node_type: z.string(), label: z.string(), ref_table: z.string().nullish(), ref_id: z.string().nullish(), event_ids: z.array(z.string()), attributes: z.record(z.string(), z.unknown()) })), edges: z.array(z.object({ source: z.string(), target: z.string(), edge_type: z.string(), causal: z.boolean(), evidence_event_ids: z.array(z.string()) })) })
export const jobSchema = z.object({ job_id: z.string(), status: z.string(), workflow_ids: z.array(z.string()) }).passthrough()
export const eventSchema = z.object({ event_id: z.string(), seq: z.number().int().positive(), created_at: z.string(), workflow_id: z.string(), event_type: z.string(), summary: z.string() }).passthrough()
export function useApi<T>(path: string, enabled = true, schema?: z.ZodType<T>) {
 return useQuery<T>({ queryKey: [path], queryFn: ({ signal }) => path === '/api/session' ? consoleSession(signal) as Promise<T> : request<T>(path, { signal }, schema), enabled, refetchInterval: localStorage.getItem('sg-refresh') === 'off' ? false : 2000 })
}
export const post = <T>(path: string, body: unknown = {}) => request<T>(path, { method: 'POST', body: JSON.stringify(body) })
