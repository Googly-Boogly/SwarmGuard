import { useMemo, useState } from 'react'
import { Background, Controls, MiniMap, ReactFlow, type Node, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { graphSchema, useApi, type Graph } from '../lib/api/client'
import { ErrorBox, JsonView, Loading } from './common'
export function graphElements(graph: Graph, type = '', agent = '', decision = ''): { nodes: Node[]; edges: Edge[] } {
 const columns = ['AGENT', 'HUMAN', 'ACTION', 'RESOURCE_VERSION', 'APPROVAL', 'DECISION', 'EXECUTION']
 const counts: Record<string, number> = {}
 const selected = graph.nodes.filter(n => (!type || n.node_type === type) && (!agent || n.attributes?.agent_id === agent || n.ref_id === agent) && (!decision || n.attributes?.decision === decision))
 const ids = new Set(selected.map(n => n.node_id))
 const allIds = new Set(graph.nodes.map(n => n.node_id))
 if (graph.edges.some(e => !allIds.has(e.source) || !allIds.has(e.target))) throw new Error('Graph contains an edge with a missing persisted node.')
 return { nodes: selected.map(n => { const i = counts[n.node_type] ?? 0; counts[n.node_type] = i + 1; return { id: n.node_id, data: { label: `${n.node_type}\n${n.label}` }, position: { x: Math.max(0, columns.indexOf(n.node_type)) * 270, y: i * 115 }, style: { background: 'var(--panel)', color: 'var(--text)', border: `1px solid ${n.attributes?.decision === 'BLOCK' ? '#f1787d' : n.attributes?.decision === 'ALLOW' ? '#5dcda9' : '#3e586e'}`, whiteSpace: 'pre-line', fontSize: 11, width: 220 } } }), edges: graph.edges.filter(e => ids.has(e.source) && ids.has(e.target)).map((e, i) => ({ id: `edge-${i}`, source: e.source, target: e.target, label: e.causal ? e.edge_type : `${e.edge_type} · non-causal`, style: { stroke: e.causal ? '#63889a' : '#55616f', strokeDasharray: e.causal ? undefined : '5 5' }, labelStyle: { fill: '#899dad', fontSize: 9 }, labelBgStyle: { fill: 'var(--panel)' }, data: e })) }
}
export function WorkflowGraph({ workflowId }: { workflowId: string }) {
 const query = useApi<Graph>(`/api/workflows/${workflowId}/graph`, true, graphSchema as never)
 const [type, setType] = useState(''), [agent, setAgent] = useState(''), [decision, setDecision] = useState(''), [selected, setSelected] = useState<unknown>(null)
 const elements = useMemo(() => query.data ? graphElements(query.data, type, agent, decision) : { nodes: [], edges: [] }, [query.data, type, agent, decision])
 const [highlight, setHighlight] = useState<Set<string>>(new Set())
 if (query.error) return <ErrorBox error={query.error}/>
 if (!query.data) return <Loading/>
 return <div><div className="toolbar"><label>Node type<select value={type} onChange={e => setType(e.target.value)}><option value="">All types</option>{[...new Set(query.data.nodes.map(n => n.node_type))].map(t => <option key={t}>{t}</option>)}</select></label><label>Agent<input placeholder="Exact agent ID" value={agent} onChange={e => setAgent(e.target.value)}/></label><label>Decision<select value={decision} onChange={e => setDecision(e.target.value)}><option value="">All decisions</option>{['ALLOW', 'BLOCK', 'ESCALATE'].map(d => <option key={d}>{d}</option>)}</select></label><small>Select a node to highlight its persisted dependency chain. Dashed edges are chronological, non-causal.</small></div><div className="graph" aria-label="Persisted workflow dependency graph"><ReactFlow nodes={elements.nodes.map(n => ({ ...n, style: { ...n.style, opacity: highlight.size && !highlight.has(n.id) ? .35 : 1 } }))} edges={elements.edges} fitView nodesDraggable={false} nodesConnectable={false} onNodeClick={(_, node) => { setSelected(query.data.nodes.find(n => n.node_id === node.id)); const ids = new Set([node.id]); let changed = true; while (changed) { changed = false; for (const edge of query.data.edges) if (edge.causal && ids.has(edge.source) && !ids.has(edge.target)) { ids.add(edge.target); changed = true } } setHighlight(ids) }} onEdgeClick={(_, edge) => setSelected(edge.data)} onPaneClick={() => { setHighlight(new Set()); setSelected(null) }}><Background color="#30404d" gap={22}/><Controls/><MiniMap pannable zoomable nodeColor="#36596a"/></ReactFlow></div>{selected !== null && <JsonView value={selected}/>}</div>
}
