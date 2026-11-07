import { useState, type ReactNode } from 'react'
import { JsonView } from './common'
import { Button } from './ui/button'
import { metric } from '../lib/utils'
import type { Json } from '../lib/api/client'

type Detection = Record<string, number | null>
type Trial = Json & { trial_id?: string; evaluation_valid?: boolean; ground_truth?: { label: string }; predictions?: Record<string, boolean | null> }
export type LaboratoryExperiment = {
 experiment_id: string; status: string; config: { mode: string; question?: string }
 metrics: Json & { detection?: Record<string, Detection>; accounting?: Record<string, number> }
 rows: Trial[]; validation: Json | null; error?: string | null
}
const labels: Record<string, string> = { LOCAL_MONITOR: 'Local', GLOBAL_MONITOR: 'Global', GENERAL_COUNCIL: 'General council', SPECIALIZED_ETHICS_COUNCIL: 'Specialized council' }
const rate = (value: number | null | undefined) => value === null || value === undefined ? 'Undefined' : `${(value * 100).toFixed(1)}%`
const countRatio = (count: number | null | undefined, other: number | null | undefined) => typeof count === 'number' && typeof other === 'number' ? `${count} / ${count + other}` : 'Unavailable'
function prediction(row: Trial, monitor: string) {
 const value = row.predictions?.[monitor]
 return !row.evaluation_valid || value === null || value === undefined ? 'Unavailable' : value ? 'Alert' : 'No alert'
}
export function ResearchDetails({ title, children }: { title: string; children: ReactNode }) {
 const [open, setOpen] = useState(false)
 return <details onToggle={e => setOpen(e.currentTarget.open)}><summary>{title}</summary>{open && children}</details>
}
export function LaboratoryResults({ record }: { record: LaboratoryExperiment }) {
 const [page, setPage] = useState(0)
 const detection = record.metrics.detection ?? {}, accounting = record.metrics.accounting
 const monitors = record.config.question === 'RQ1' ? ['LOCAL_MONITOR', 'GLOBAL_MONITOR']
  : record.config.question === 'RQ2' ? ['GENERAL_COUNCIL', 'SPECIALIZED_ETHICS_COUNCIL'] : Object.keys(detection).sort()
 const visibleRows = record.rows.slice(page * 10, (page + 1) * 10)
 return <section aria-label="Experiment summary">
 <h3>Measured statistics</h3>
 <div className="small-metrics">{['planned', 'recorded', 'failed', 'unrun'].map(name => <div key={name}><small>{name}</small><strong>{metric(accounting?.[name])}</strong></div>)}</div>
 <p className="muted">Missing monitor predictions: {metric(accounting?.missing_monitor_predictions)}. Unavailable assessments are unknown.</p>
 <div className="table-wrap"><table aria-label="Monitor comparison"><thead><tr><th>Monitor</th><th>Evaluated</th><th>Detected / unsafe</th><th>False alerts / safe</th><th>Recall</th><th>False-positive rate</th><th>Not evaluated</th></tr></thead>
 <tbody>{monitors.map(name => { const m = detection[name]; return <tr key={name}><td>{labels[name] ?? name}</td><td>{metric(m?.n)}</td><td>{countRatio(m?.tp, m?.fn)}</td><td>{countRatio(m?.fp, m?.tn)}</td><td>{rate(m?.recall)}</td><td>{rate(m?.false_positive_rate)}</td><td>{metric(m?.not_evaluated)}</td></tr> })}</tbody></table></div>
 <p className="muted">{record.config.question === 'RQ1' ? 'RQ1 compares local and global monitoring on the same trials. ' : ''}These synthetic samples are descriptive. Detection is separate from gateway prevention; a small sample cannot establish a low population false-positive rate.</p>
 <h3>Trial outcomes</h3>
 <div className="table-wrap"><table aria-label="Trial outcomes"><thead><tr><th>Trial</th><th>Label</th>{monitors.map(name => <th key={name}>{labels[name] ?? name}</th>)}</tr></thead>
 <tbody>{visibleRows.map((row, index) => <tr key={row.trial_id ?? index}><td>{row.trial_id ?? 'Unavailable'}</td><td>{row.evaluation_valid ? row.ground_truth?.label ?? 'Unscored' : 'Unscored'}</td>{monitors.map(name => <td key={name}>{prediction(row, name)}</td>)}</tr>)}</tbody></table></div>
 {record.rows.length > 10 && <div className="actions"><Button variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous trials</Button><span>Page {page + 1} of {Math.ceil(record.rows.length / 10)}</span><Button variant="outline" disabled={(page + 1) * 10 >= record.rows.length} onClick={() => setPage(page + 1)}>Next trials</Button></div>}
 <p>Evidence validation: {record.validation ? record.validation.valid ? 'Passed' : 'Failed — inspect validation details' : 'Pending'}.</p>
 {record.error && <p className="warning-text">Run error: {record.error}</p>}
 <ResearchDetails title="Advanced statistics — all monitors and paired comparisons"><JsonView value={record.metrics}/></ResearchDetails>
 <ResearchDetails title="Trajectories, graph, assessments and authorization decisions"><JsonView value={record.rows}/></ResearchDetails>
 <ResearchDetails title="Validation details"><JsonView value={record.validation}/></ResearchDetails>
 </section>
}
