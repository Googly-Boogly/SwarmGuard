import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { post, useApi, type Job, type RunConfig, type Scenario, type Session } from '../lib/api/client'
import { Button } from './ui/button'
import { ErrorBox, Mode } from './common'
export function LaunchForm({ kind = 'workflow', initialScenario }: { kind?: 'workflow' | 'experiment'; initialScenario?: string }) {
 const defaults = JSON.parse(localStorage.getItem('sg-defaults') ?? '{}') as Record<string, string>
 const [config, setConfig] = useState<RunConfig>({ scenario_ids: [initialScenario ?? defaults.scenario ?? 'legitimate_collaboration'], execution_mode: initialScenario === 'authorized_email' ? 'SCRIPTED' : 'MOCK_AUTONOMOUS', monitoring: (defaults.monitoring ?? 'GLOBAL') as RunConfig['monitoring'], intervention: 'OBSERVATION', aggregation: 'MAJORITY_WITH_ESCALATION', trials: 1, seed: 0, max_steps: 80, max_runtime_seconds: 300, max_llm_requests: 400, max_cost_usd: 0, max_concurrency: 1, live_confirmed: false })
 const [review, setReview] = useState(false)
 const scenarios = useApi<Scenario[]>('/api/scenarios'), session = useApi<Session>('/api/session')
 const navigate = useNavigate(), cache = useQueryClient()
 const mutation = useMutation({ mutationFn: () => post<Job>(kind === 'workflow' ? '/api/workflows' : '/api/experiments', config), onSuccess: async job => { await cache.invalidateQueries(); navigate(`/experiments/${job.job_id}`) } })
 const set = <K extends keyof RunConfig>(key: K, value: RunConfig[K]) => { setReview(false); setConfig(old => ({ ...old, [key]: value })) }
 const available = scenarios.data?.filter(s => s.execution_modes.includes(config.execution_mode ?? 'MOCK_AUTONOMOUS')) ?? []
 return <form className="launch-form" onSubmit={e => { e.preventDefault(); if (!review) setReview(true); else mutation.mutate() }}>
 <div className="notice"><Mode value={config.execution_mode}/> Synthetic records and simulated tools. Interactive trials wait for operator approval. No API key required.</div>
 <div className="form-grid"><label>Execution mode<select value={config.execution_mode} onChange={e => { const mode = e.target.value as RunConfig['execution_mode']; set('execution_mode', mode); set('scenario_ids', [mode === 'SCRIPTED' ? 'authorized_email' : 'legitimate_collaboration']); if (mode === 'SCRIPTED') set('monitoring', 'GLOBAL') }}><option value="MOCK_AUTONOMOUS">Mock autonomous</option><option value="SCRIPTED">Scripted acceptance replay</option><option value="LIVE_AUTONOMOUS" disabled>Live autonomous — unavailable</option></select></label>
 <label>Scenario{kind === 'experiment' ? ' set (Ctrl / ⌘ to select)' : ''}<select multiple={kind === 'experiment'} value={kind === 'experiment' ? config.scenario_ids : config.scenario_ids[0]} onChange={e => set('scenario_ids', [...e.target.selectedOptions].map(o => o.value))}>{available.map(s => <option key={s.scenario_id} value={s.scenario_id}>{s.name}</option>)}</select></label>
 <label>Monitoring<select value={config.monitoring} onChange={e => set('monitoring', e.target.value as RunConfig['monitoring'])}>{(config.execution_mode === 'SCRIPTED' ? ['NONE', 'GLOBAL'] : ['NONE', 'LOCAL', 'GLOBAL', 'GENERAL', 'ETHICS', 'COMBINED']).map(v => <option key={v}>{v}</option>)}</select></label>
 <label>Intervention<select value={config.intervention} onChange={e => set('intervention', e.target.value as RunConfig['intervention'])}>{(config.execution_mode === 'SCRIPTED' ? ['OBSERVATION'] : ['OBSERVATION', 'WARNING', 'HUMAN_OVERSIGHT', 'ENFORCEMENT']).map(v => <option key={v}>{v}</option>)}</select></label>
 <label>Aggregation<select value={config.aggregation} onChange={e => set('aggregation', e.target.value as RunConfig['aggregation'])}>{['MAJORITY', 'CONSERVATIVE', 'MAJORITY_WITH_ESCALATION'].map(v => <option key={v}>{v}</option>)}</select></label>
 {kind === 'experiment' && <label>Trials per scenario<input type="number" min="1" max="10" value={config.trials} onChange={e => set('trials', e.target.valueAsNumber)}/></label>}
 {([['seed','Random seed',0,2147483647],['max_steps','Maximum workflow steps',config.execution_mode === 'SCRIPTED' ? 40 : 1,200],['max_runtime_seconds','Maximum job runtime (seconds)',1,900],['max_llm_requests','Maximum provider requests (job)',1,2000]] as const).map(([key,label,min,max]) => <label key={key}>{label}<input type="number" required min={min} max={max} value={config[key]} onChange={e => set(key, e.target.valueAsNumber)}/></label>)}
 <label>Maximum spending (USD)<input type="number" min="0" max="0" value="0" readOnly/><small>Mock mode: no billable requests. One worker; concurrency 1.</small></label></div>
 <p className="muted small-text">HUMAN_OVERSIGHT uses the existing guard to escalate risk. ENFORCEMENT sets the fixed synthetic recipient scope in deterministic policy. Council recommendations remain advisory. Scripted replay contains its own simulated human.</p>
 {review && <div className="notice"><strong>Configuration ready for review</strong><p>{config.scenario_ids.join(', ')} · {config.trials} trial(s) · {config.monitoring} · {config.intervention} · at most {config.max_steps} steps per workflow and {config.max_llm_requests} provider requests per job.</p></div>}
 {session.data && session.data.login_required !== false && !session.data.authenticated && <p className="warning-text">Sign in as the local operator using the button in the top bar to launch runs.</p>}
 {mutation.error && <ErrorBox error={mutation.error}/>}
 <Button type="submit" disabled={!session.data?.authenticated || mutation.isPending || !config.scenario_ids.length}><Play size={15}/>{mutation.isPending ? 'Starting…' : review ? `Start ${kind}` : 'Review configuration'}</Button>
 </form>
}
