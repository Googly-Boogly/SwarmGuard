import { useEffect, useState } from 'react'
import { eventSchema, request, useApi, type Event, type Session } from '../lib/api/client'
export function mergeEvents(before: Event[], next: Event[]) { return [...new Map([...before, ...next].map(e => [e.event_id, e])).values()].sort((a, b) => a.seq - b.seq) }
export function useWorkflowEvents(workflowId: string) {
 const [events, setEvents] = useState<Event[]>([])
 const [transport, setTransport] = useState('Polling')
 const [error, setError] = useState<unknown>(null)
 const session = useApi<Session>('/api/session')
 useEffect(() => {
  let alive = true, cursor = 0, socket: WebSocket | undefined, reconnect: ReturnType<typeof setTimeout> | undefined
  const controller = new AbortController()
  setEvents([]); setError(null)
  function append(rows: Event[]) { if (!alive) return; for (const row of rows) eventSchema.parse(row); cursor = Math.max(cursor, ...rows.map(e => e.seq)); setEvents(old => mergeEvents(old, rows)) }
  async function recover() { try { let more = true; while (alive && more) { const rows = await request<Event[]>(`/api/events?workflow_id=${encodeURIComponent(workflowId)}&after_seq=${cursor}&limit=500`, { signal: controller.signal }); append(rows); more = rows.length === 500 } if (alive) setError(null) } catch (e) { if (alive) setError(e) } }
  function connect() { if (!alive || !session.data?.authenticated) return; socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/workflows/${encodeURIComponent(workflowId)}?after_seq=${cursor}`)
   socket.onopen = () => { if (alive) setTransport('WebSocket + recovery polling') }
   socket.onmessage = message => { try { const data = JSON.parse(message.data); if (data.type === 'event') append([data.event]) } catch (e) { setError(e) } }
   socket.onclose = () => { if (alive) { setTransport('Polling · reconnecting'); reconnect = setTimeout(connect, 2500) } }
  }
  void recover().then(connect)
  const timer = setInterval(() => { void recover() }, 1500)
  return () => { alive = false; controller.abort(); clearInterval(timer); clearTimeout(reconnect); socket?.close() }
 }, [workflowId, session.data?.authenticated])
 return { events, transport, error }
}
