import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { post, useApi, type Json, type Session } from '../lib/api/client'
import { PageTitle, Panel, Empty, ErrorBox, JsonView } from '../components/common'
import { Button } from '../components/ui/button'
import { LaboratoryResults, ResearchDetails, type LaboratoryExperiment } from '../components/LaboratoryResults'

type Dataset = {content_hash:string;scenario_count:number;validation_status:string;definition:{dataset_id:string;version:string;partition:string}}
type Experiment = LaboratoryExperiment
type Catalog = {questions:Json[];datasets:Dataset[];experiments:Experiment[];provider:string}
export function LaboratoryPage({view='laboratory'}:{view?:string}) {
 const session=useApi<Session>('/api/session'), enabled=!!session.data?.authenticated
 const catalog=useApi<Catalog>('/api/research/lab/catalog',enabled)
 const [dataset,setDataset]=useState('swarmguard_adaptive_search'),[question,setQuestion]=useState('RQ1'),[mode,setMode]=useState('SCRIPTED'),[trials,setTrials]=useState(1),[totalLimit,setTotalLimit]=useState(2),[seed,setSeed]=useState(42),[requests,setRequests]=useState(500),[selected,setSelected]=useState('')
 const scenarioCount=catalog.data?.datasets.find(d=>d.definition.dataset_id===dataset)?.scenario_count
 const planned=scenarioCount===undefined?undefined:Math.min(scenarioCount*trials,totalLimit)
 const cache=useQueryClient()
 const id=selected || catalog.data?.experiments[0]?.experiment_id || ''
 const record=useApi<Experiment>(`/api/research/lab/experiments/${id}`,enabled && !!id)
 const ablation=useApi<Json>(`/api/research/lab/experiments/${id}/ablations`,enabled && !!id && view==='ablations')
 const launch=useMutation({mutationFn:()=>post<Experiment>('/api/research/lab/experiments',{question,dataset,mode,trials,max_total_trials:totalLimit,seed,max_requests:requests}),onSuccess:async r=>{setSelected(r.experiment_id);await cache.invalidateQueries()}})
 const report=useMutation({mutationFn:(experimentId:string)=>post<{markdown:string}>(`/api/research/lab/experiments/${experimentId}/report`,{format:'summary'})})
 const cancel=useMutation({mutationFn:()=>post(`/api/research/lab/experiments/${id}/cancel`),onSuccess:()=>cache.invalidateQueries()})
 return <><PageTitle eyebrow="RESEARCH LABORATORY" title={view==='laboratory'?'Research Laboratory':view==='new'?'Experiment Designer':view[0].toUpperCase()+view.slice(1)} description="Versioned synthetic experiments, measured outcomes and inspectable evidence."/>
 <p className="notice">Mock policies and councils are synthetic. Sample counts and failures accompany results. Live research remains separately opt-in through the existing provider CLI.</p>
 {!enabled?<Empty title={session.data?.login_required ? "Researcher login required" : "Preparing console…"}/>:<>
 <nav className="actions">{['laboratory','experiments/new','hypotheses','datasets','statistics','ablations','reports'].map(x=><Link key={x} to={`/research/${x}`}>{x}</Link>)}</nav>
 {catalog.error&&<ErrorBox error={catalog.error}/>}
 {view==='laboratory'&&<Panel title="RQ1 — Distributed Approval Laundering" subtitle="Investigate whether global workflow monitoring detects cumulative multi-agent safety violations more effectively than local action monitoring." action={<div className="actions"><Link className="button primary" to="/research/rq1/new">NEW EXPERIMENT</Link><Link className="button outline" to="/research/rq1">VIEW PREVIOUS RESULTS</Link></div>}><p className="rq1-intro" style={{padding:'0 20px 16px',fontSize:12}}>Paired Local vs. Global monitoring on the frozen held-out RQ1 dataset, with methodology validation, live progress, raw data, comparisons, failure analysis and findings.</p></Panel>}
 {view==='hypotheses'?<Panel title="Registered research questions">{catalog.data?.questions.map(q=><div key={String(q.research_question_id)}><h3>{String(q.research_question_id)} · {String(q.title)}</h3>{q.question?<p>{String(q.question)}</p>:null}{q.goal?<p><strong>Goal:</strong> {String(q.goal)}</p>:null}<JsonView value={q}/></div>)}</Panel>:view==='datasets'?<Panel title="Validated dataset versions"><table><thead><tr>{['Dataset','Version','Partition','Scenarios','Hash'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{catalog.data?.datasets.map(d=><tr key={d.content_hash}><td>{d.definition.dataset_id}</td><td>{d.definition.version}</td><td>{d.definition.partition}</td><td>{d.scenario_count}</td><td><code>{d.content_hash}</code></td></tr>)}</tbody></table></Panel>:<>
 {(view==='laboratory'||view==='new')&&<Panel title="Design a bounded experiment"><form onSubmit={e=>{e.preventDefault();launch.mutate()}}><div className="form-grid">
 <label>Research question<select value={question} onChange={e=>setQuestion(e.target.value)}>{catalog.data?.questions.map(q=><option key={String(q.research_question_id)}>{String(q.research_question_id)}</option>)}</select></label>
 <label>Dataset<select value={dataset} onChange={e=>setDataset(e.target.value)}>{catalog.data?.datasets.map(d=><option key={d.content_hash} value={d.definition.dataset_id}>{d.definition.dataset_id} · {d.definition.version}</option>)}</select></label>
 <label>Execution mode<select value={mode} onChange={e=>setMode(e.target.value)}><option>SCRIPTED</option><option>MOCK_AUTONOMOUS</option></select></label>
 <label>Trials per scenario<input type="number" min="1" max="3" required value={trials} onChange={e=>setTrials(Number(e.target.value))}/></label>
 <label>Maximum total trials<input type="number" min="1" max="60" required value={totalLimit} onChange={e=>setTotalLimit(Number(e.target.value))}/></label>
 <label>Seed<input type="number" min="0" value={seed} onChange={e=>setSeed(Number(e.target.value))}/></label>
 <label>Maximum requests<input type="number" min="1" max="1000" value={requests} onChange={e=>setRequests(Number(e.target.value))}/></label></div><p role="status">{planned===undefined?'Loading trial count…':`${planned} total trials planned (maximum ${totalLimit}).`}</p><p>The total limit applies across all scenarios and repetitions. Seed controls ordering. Monitor assessments are recorded within each trial. A limited sample may omit safe or unsafe cases; inspect the recorded counts.</p><p>Native framework · all seven monitor configurations · 60 steps · 300 seconds · zero billing. Backend validates compatibility.</p><Button disabled={launch.isPending || planned===undefined}>RUN EVALUATION</Button></form>{launch.error&&<ErrorBox error={launch.error}/>}</Panel>}
 <Panel title="Recorded experiments"><label>Experiment<select value={id} onChange={e=>setSelected(e.target.value)}><option value="">Select experiment</option>{catalog.data?.experiments.map(e=><option key={e.experiment_id} value={e.experiment_id}>{e.experiment_id} · {e.status} · {e.config.mode}</option>)}</select></label></Panel>
 {record.error&&<ErrorBox error={record.error}/>}{record.data&&<Panel title={`${id} · ${record.data.status}`}><p>{record.data.config.mode} · {record.data.rows.length} recorded trials</p>
 {['QUEUED','RUNNING'].includes(record.data.status)?<Button onClick={()=>cancel.mutate()}>Cancel experiment</Button>:<><Button onClick={()=>report.mutate(id)} disabled={report.isPending}>GENERATE REPORT</Button> <a href={`/api/research/lab/experiments/${id}/bundle`} download>Download reproducibility bundle (full evidence)</a></>}
 {cancel.error&&<ErrorBox error={cancel.error}/>}{report.error&&report.variables===id&&<ErrorBox error={report.error}/>}{report.data&&report.variables===id&&<pre aria-label="Generated research report">{report.data.markdown}</pre>}
 {view==='ablations'?<ResearchDetails title="Ablation statistics"><JsonView value={ablation.data??{}}/></ResearchDetails>:<LaboratoryResults key={id} record={record.data}/>}
 </Panel>}</>}
 </>}</>
}
