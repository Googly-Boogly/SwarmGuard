import { ErrorBox, Loading } from '../components/common'
import { useRQ1, type HypothesisReport, type HypothesisVerdict } from './api'
import { Section, Tag } from './components'

const TONE: Record<HypothesisVerdict['status'], 'positive' | 'danger' | 'warning' | 'neutral'> = {
 SUPPORTED: 'positive', H0_RETAINED: 'danger', INCONCLUSIVE: 'warning', NOT_APPLICABLE: 'neutral',
}
const CONDITION_TONE = { PASS: 'positive', FAIL: 'danger', UNDEFINED: 'neutral' } as const

/** Pre-registered hypothesis verdicts, recomputed by the server from this experiment's stored records. */
export function HypothesisVerdicts({ eid, compact = false }: { eid: string; compact?: boolean }) {
 const q = useRQ1<HypothesisReport>(`/api/research/rq1/experiments/${eid}/hypotheses`)
 if (q.error) return <ErrorBox error={q.error}/>
 if (!q.data) return <Loading/>
 const r = q.data
 const shown = compact ? r.verdicts.filter(v => v.applicable) : r.verdicts
 return <Section title="Pre-registered hypothesis verdicts">
  <p className="muted">Computed from this experiment's stored records against the registered decision rules ({r.registry.version}, {r.engine_version}). Nothing is rerun. A verdict is descriptive support on this constructed dataset only.</p>
  {!shown.length && <p>{r.primary_summary}</p>}
  {shown.map(v => <div key={v.hypothesis_id} className="rq1-verdict" aria-label={`Verdict ${v.hypothesis_id}`}>
   <p><Tag value={v.status} tone={TONE[v.status]}/> <b>{v.hypothesis_id}</b> · {v.role}{v.role_note && v.role === 'secondary' ? ` (${v.role_note})` : ''}</p>
   <p>{v.statement} {v.note}</p>
   {v.applicability_reasons.length > 0 && <ul className="rq1-list">{v.applicability_reasons.map(x => <li key={x}>Not the registered procedure: {x}</li>)}</ul>}
   <ul className="rq1-list">{v.conditions.map(c => <li key={c.id}><Tag value={c.status} tone={CONDITION_TONE[c.status]}/> ({c.id}) {c.text}: <span className="mono">{c.detail}</span></li>)}</ul>
   {!compact && <p className="muted">Source: {v.source.document} · {v.source.section}{v.source_document_unchanged ? '' : ' · WARNING: the protocol document changed after transcription'}. Transcribed {v.transcribed_at}.</p>}
  </div>)}
 </Section>
}
