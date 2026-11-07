import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { request } from '../lib/api/client'

export type Json = Record<string, unknown>
export type Num = number | null

export type RQ1Config = {
 partition: 'held_out' | 'development'
 scenario_ids: string[] | null
 seed: number
 trials: number
 execution_mode: 'SCRIPTED'
 monitors: string[]
 max_runtime_seconds: number
 max_steps_per_workflow: number
 timing_repeats: number
}
export type DatasetSummary = { partition: string; dataset_version: string; content_hash: string; n: number; unsafe: number; legitimate: number; categories: string[]; composition: Record<string, { UNSAFE: number; SAFE: number }>; frozen: boolean; matches_frozen_hash: boolean; role: string; scenarios: { scenario_id: string; category: string; family: string; label: string }[] }
export type Check = { id: string; title: string; status: 'PASS' | 'WARN' | 'FAIL'; detail: string }
export type Validation = { status: 'VALIDATED' | 'WARNINGS' | 'INVALID'; checks: Check[]; known_limitations: string[]; selected_scenarios: number; unsafe: number; legitimate: number; dataset_hash: string; dataset_version: string; note: string }
export type Headline = Record<string, unknown> & { n_workflows: number; decision_rule: DecisionRule }
export type DecisionRule = { applies: boolean; satisfied: boolean | null; note: string; global_recall_higher?: boolean; fpr_within_margin?: boolean; observation_checks_passed?: boolean; reason?: string }
export type Progress = { phase: string; total_workflows: number; completed_workflows: number; failed_workflows: number; current_workflow: string | null; current_scenario: string | null; phases: { phase: string; started_at: string }[]; elapsed_seconds?: number }
export type Activity = { workflow_id: string; scenario_id: string; seq: number; time: string | null; kind: string; agent_id: string | null; text: string }
export type ExperimentSummary = { experiment_id: string; status: string; config: RQ1Config; created_by: string; created_at: string; started_at: string | null; completed_at: string | null; progress: Progress; error: string | null; headline: Headline | null; preregistered_procedure: boolean; execution_mode: string; integrity_problems?: string[] }
export type Experiment = ExperimentSummary & { validation: Validation; activity: Activity[]; failures: { workflow_id: string; scenario_id: string; phase: string; error: string }[]; code_revision: string; model_requests: number; estimated_cost_usd: number; cancel_requested_by?: string }
export type Overview = { question: Json & { research_question_id: string; title: string; question: string; hypothesis: string; null_hypothesis: string; goal: string }; datasets: DatasetSummary[]; recommended_config: RQ1Config; pilot_config: RQ1Config; unavailable_options: { execution_modes: Record<string, string>; monitors: Record<string, string> }; known_limitations: string[]; protocol_version: string; frozen_dataset_hash: string; experiments: ExperimentSummary[]; execution_note: string }
export type MonitorMetrics = { tp: number; fp: number; tn: number; fn: number; recall: Num; precision: Num; false_positive_rate: Num; f1: Num; accuracy: Num; recall_wilson95: { low: number; high: number } | null; fpr_wilson95: { low: number; high: number } | null; [k: string]: unknown }
export type MetricRow = { metric: string; key: string; local: Num; global: Num; difference: Num }
export type Results = {
 experiment: ExperimentSummary
 analysis: Json & { n_workflows: number; primary: Record<string, MonitorMetrics>; per_category_primary: Record<string, Record<string, MonitorMetrics>>; paired: { unsafe: Paired; safe: Paired }; recall_difference_global_minus_local: Num; recall_difference_ci: { interval: { low: number; high: number } | null; reason?: string; method?: string } | null; detection_latency: Record<string, { detected_in_time: number; lead_checkpoints: number[]; median_lead_checkpoints: Num; unit: string }>; primary_metric_definition: string; inference_note: string; missing_or_failed: string[]; predicted_insufficient_information: Record<string, number>; secondary_any_checkpoint: Record<string, MonitorMetrics>; secondary_at_critical_only: Record<string, MonitorMetrics>; sensitivity_abstention_as_positive: Record<string, MonitorMetrics> }
 metrics: { table: MetricRow[]; per_category: { category: string; local_recall: Num; global_recall: Num; local_fpr: Num; global_fpr: Num; unsafe_n: number; safe_n: number }[]; lead_histogram: { lead: number; Local: number; Global: number }[]; checkpoint_recommendations: { monitor: string; recommendation: string; count: number }[]; intervals: Record<string, { recall: { low: number; high: number } | null; fpr: { low: number; high: number } | null }> }
 verification: Json & { checks_passed: boolean; contexts_checked: number; failures: string[]; checks: string[]; evaluation_latency: Record<string, { n: number; median_ms: Num; mean_ms: Num; max_ms: Num }>; mean_context_size: Record<string, Record<string, Num>>; timing_note: string }
 ablations: Ablation[]
 enforcement: Json[]
 crosscheck: string[]
 reproducibility: { trial: number; predictions_identical: boolean; checkpoints_identical: boolean }[]
 failure_case_counts: Record<string, number>
 findings: Findings
 counts: { checkpoint_evaluations: number; workflow_predictions: number; scenarios: number }
}
export type Paired = { n: number; both: number; only_global: number; only_local: number; neither: number; exact_mcnemar_p_two_sided: Num; only_global_ids: string[]; only_local_ids: string[]; neither_ids: string[] }
export type Ablation = { scenario_id: string; workflow_id: string; step: number; action_id: string; tool: string; global_patterns: string[]; global_evidence_event_types: string[]; global_evidence_outside_local_window: string[]; ablations: Record<string, { alert: boolean | null; recommendation?: string; patterns?: string[]; error?: string }>; alert_removed_by: string[] }
export type Findings = { research_question: string; experiment_summary: string; detection_performance: string; false_positives: string; detection_latency: string; context_analysis: string; gateway_and_prevention: string; decision_rule: DecisionRule; case_counts: Record<string, number>; limitations: string[]; interesting_cases: Record<string, string[]>; copy: Record<'results_summary' | 'metrics_table' | 'methodology' | 'limitations', string>; statement: string }
export type RawRow = Json & { row_id: string; scenario_id: string; workflow_id: string; monitor: string; configuration: string; status: string; step?: number }
export type RawPage = { level: string; total: number; page: number; page_size: number; pages: number; rows: RawRow[]; facets: Record<string, string[]> }
export type ScenarioRow = { scenario_id: string; workflow_id: string | null; category: string; family: string; ground_truth: string; agent_count: number; workflow_steps: number; description: string; local: string | null; global: string | null; local_correct: boolean | null; global_correct: boolean | null; status: string }
export type EventRecord = Json & { event_id: string; seq: number; created_at: string; event_type: string; agent_id: string | null; summary: string; action_id?: string | null }
export type CheckpointSummary = { step: number; action_id: string; checkpoint_seq: number; tool: string; agent_id: string; local: { recommendation: string; risk_level: string; alert: boolean }; global: { recommendation: string; risk_level: string; alert: boolean } }
export type WorkflowEvidence = { workflow_id: string; scenario_id: string; agents: { agent_id: string; role: string }[]; events: EventRecord[]; actions: Json[]; decisions: Json[]; messages: Json[]; approvals: Json[]; sent_emails: Json[]; graph: { workflow_id: string; nodes: { node_id: string; node_type: string; label: string; ref_table?: string | null; ref_id?: string | null; event_ids: string[]; attributes: Record<string, unknown> }[]; edges: { source: string; target: string; edge_type: string; causal: boolean }[] } | null; critical_action_id: string | null; resource_versions: Record<string, Json[]>; checkpoints: CheckpointSummary[] }

const TERMINAL = ['COMPLETED', 'FAILED', 'CANCELLED', 'INCONCLUSIVE']
export const isTerminal = (status?: string) => !!status && TERMINAL.includes(status)

/** Static research records: fetched once (they never change after completion). */
export function useRQ1<T>(path: string | null, poll: number | false = false, keepPrevious = false) {
 return useQuery<T>({ queryKey: ['rq1', path], queryFn: ({ signal }) => request<T>(path as string, { signal }), enabled: !!path, refetchInterval: poll, staleTime: poll ? 0 : Infinity, placeholderData: keepPrevious ? keepPreviousData : undefined })
}

export const pct = (v: unknown) => typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : 'Undefined'
export const num = (v: unknown, digits = 3) => v === null || v === undefined ? 'Undefined' : typeof v === 'number' ? Number.isInteger(v) ? String(v) : v.toFixed(digits) : String(v)
export const signed = (v: unknown, digits = 3) => typeof v === 'number' ? `${v > 0 ? '+' : ''}${Number.isInteger(v) ? v : v.toFixed(digits)}` : 'Undefined'
export const label = (v: string) => v.replaceAll('_', ' ').toLowerCase().replace(/^./, c => c.toUpperCase())

export async function copyText(text: string): Promise<boolean> {
 try { await navigator.clipboard.writeText(text); return true } catch {
  const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0'
  document.body.appendChild(area); area.select(); const ok = document.execCommand('copy'); area.remove(); return ok
 }
}

/** Series colors validated with the dataviz palette checker (dark surface #111a26, light #ffffff). */
export function seriesColors() {
 const light = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light'
 return light ? { Local: '#b87400', Global: '#2a78d6', grid: '#d7e0e9', axis: '#52667b' } : { Local: '#c98500', Global: '#3987e5', grid: '#293644', axis: '#94a8b8' }
}
