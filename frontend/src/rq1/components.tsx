import { useState, type ReactNode } from 'react'
import { CheckCircle2, Circle, Copy, ShieldAlert, XCircle } from 'lucide-react'
import { Button } from '../components/ui/button'
import { copyText } from './api'

const POSITIVE = ['PASS', 'VALIDATED', 'COMPLETED', 'COMPLETE', 'TN', 'TP', 'CORRECT', 'ALLOW', 'SAFE', 'TRUE', 'NOT_DETECTED_SAFE']
const DANGER = ['FAIL', 'INVALID', 'FAILED', 'ERROR', 'BLOCK', 'FN', 'FP', 'INCORRECT', 'UNSAFE']
const WARN = ['WARN', 'WARNINGS', 'INCONCLUSIVE', 'ESCALATE', 'RUNNING', 'QUEUED', 'DETECTED', 'HIGH', 'MEDIUM', 'CANCELLED']

/** Status badge with an icon and text, so state is never conveyed by color alone. */
export function Tag({ value, tone }: { value: string | boolean | null | undefined; tone?: 'positive' | 'danger' | 'warning' | 'neutral' }) {
 const text = value === null || value === undefined ? 'UNAVAILABLE' : typeof value === 'boolean' ? (value ? 'YES' : 'NO') : value
 const t = tone ?? (POSITIVE.includes(text) ? 'positive' : DANGER.includes(text) ? 'danger' : WARN.includes(text) ? 'warning' : 'neutral')
 const Icon = t === 'positive' ? CheckCircle2 : t === 'danger' ? XCircle : t === 'warning' ? ShieldAlert : Circle
 return <span className={`badge ${t}`}><Icon size={12}/>{text.replaceAll('_', ' ')}</span>
}

export function Correct({ value }: { value: boolean | null | undefined }) {
 return value === null || value === undefined ? <Tag value={null}/> : <Tag value={value ? 'CORRECT' : 'INCORRECT'}/>
}

export function CopyButton({ text, label = 'Copy', testId }: { text: string | (() => string); label?: string; testId?: string }) {
 const [state, setState] = useState<'' | 'copied' | 'failed'>('')
 return <span className="rq1-row-actions"><Button variant="outline" size="sm" data-testid={testId} onClick={async () => { const ok = await copyText(typeof text === 'function' ? text() : text); setState(ok ? 'copied' : 'failed'); window.setTimeout(() => setState(''), 2500) }}><Copy size={13}/>{label}</Button>{state && <span role="status" className="rq1-toast">{state === 'copied' ? 'Copied to clipboard' : 'Copy failed'}</span>}</span>
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
 return <dl className="rq1-kv">{rows.map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v === null || v === undefined || v === '' ? <span className="muted">Not recorded</span> : v}</dd></div>)}</dl>
}

export function Stat({ title, value, note }: { title: string; value: ReactNode; note?: ReactNode }) {
 return <div className="metric-card rq1-stat"><small>{title}</small><strong>{value}</strong>{note && <small>{note}</small>}</div>
}

function highlight(json: string): ReactNode[] {
 const out: ReactNode[] = []
 const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g
 let last = 0, m: RegExpExecArray | null, i = 0
 while ((m = re.exec(json))) {
  if (m.index > last) out.push(json.slice(last, m.index))
  if (m[1]) out.push(<span key={i++} className={m[2] ? 'k' : 's'}>{m[1]}</span>, m[2] ?? '')
  else if (m[3]) out.push(<span key={i++} className="b">{m[3]}</span>)
  else out.push(<span key={i++} className="n">{m[4]}</span>)
  last = re.lastIndex
 }
 out.push(json.slice(last))
 return out
}

/** Syntax-highlighted JSON, rendered only when the researcher asks for it. */
export function OriginalJson({ value, title = 'VIEW ORIGINAL JSON' }: { value: unknown; title?: string }) {
 const [open, setOpen] = useState(false)
 const text = open ? JSON.stringify(value, null, 2) : ''
 return <div className="rq1-section"><div className="rq1-row-actions"><Button variant="outline" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? 'HIDE ORIGINAL JSON' : title}</Button>{open && <CopyButton text={text} label="Copy JSON"/>}</div>{open && <pre className="rq1-json" aria-label="Original JSON">{highlight(text)}</pre>}</div>
}

export function Section({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
 return <section className="rq1-section"><div className="flex-between"><h3>{title}</h3>{action}</div>{children}</section>
}

export function Swatch({ color }: { color: string }) { return <span className="rq1-swatch" style={{ background: color }} aria-hidden="true"/> }
