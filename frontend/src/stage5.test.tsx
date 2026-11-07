import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Stage5FailuresPage, Stage5ResearchPage } from './pages/Stage5'
import type { ReactNode } from 'react'
function mount(node:ReactNode) {
 localStorage.setItem('sg-refresh','off')
 return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)
}
function mockApi(data:Record<string,unknown>) {
 const fetch = vi.fn(async (path:string,_options?:RequestInit) => ({ok:true,status:200,json:async() => data[path] ?? null}))
 vi.stubGlobal('fetch',fetch)
 return fetch
}
afterEach(() => vi.unstubAllGlobals())
describe('Stage 5 research controls',() => {
 it('requires authentication before research reads or launches',async() => {
  const fetch=mockApi({'/api/session':{authenticated:false,login_available:true}})
  mount(<Stage5ResearchPage kind="adaptive"/>)
  expect(await screen.findByText('Researcher login required')).toBeInTheDocument()
  expect(screen.getByRole('button',{name:'START RESEARCH RUN'})).toBeDisabled()
  expect(fetch.mock.calls.every(([path]) => !path.startsWith('/api/research/'))).toBe(true)
 })
 it('submits bounded mock data without privileged command fields',async() => {
  const fetch=mockApi({'/api/session':{authenticated:true,login_available:true},'/api/research/adversarial':[],
    '/api/research/adversarial/runs':{run_id:'research_001'}})
  mount(<Stage5ResearchPage kind="adaptive"/>)
  await waitFor(() => expect(screen.getByRole('button',{name:'START RESEARCH RUN'})).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Maximum research iterations'),{target:{value:'2'}})
  fireEvent.click(screen.getByRole('button',{name:'START RESEARCH RUN'}))
  await waitFor(() => expect(fetch.mock.calls.some(([path])=>path==='/api/research/adversarial/runs')).toBe(true))
  const options=fetch.mock.calls.find(([path])=>path==='/api/research/adversarial/runs')![1]!
  const body=JSON.parse(String(options.body))
  expect(body).toMatchObject({kind:'adaptive',max_iterations:2,max_steps:60,max_requests:100,max_cost_usd:0})
  expect(['policy','agent_id','approval_id','credentials','sql','command'].some(k=>k in body)).toBe(false)
 })
 it('shows undefined metrics without replacing them with zero',async() => {
  const record={run_id:'research_001',status:'COMPLETED',iteration:1,generated:1,validated:1,executed:1,rejected:0,failed:0,
   monitoring_failures:0,gateway_blocks:0,prohibited_outcomes:0,usage:{},manifest:{config:{partition:'adaptive_search'}},
   metrics:{detection:{GLOBAL_MONITOR:{n:0,precision:null,recall:null,f1:null,false_positive_rate:null}},comparison_count:0,note:'Synthetic'}}
  mockApi({'/api/session':{authenticated:true,login_available:true},'/api/research/adversarial':[record],'/api/research/runs/research_001':record,
   '/api/research/runs/research_001/candidates':[]})
  mount(<Stage5ResearchPage kind="adaptive"/>)
  expect((await screen.findAllByText('undefined')).length).toBeGreaterThanOrEqual(4)
  expect(screen.getByText('GLOBAL_MONITOR',{selector:'td'})).toBeInTheDocument()
 })
 it('renders untrusted failure text as inert content',async() => {
  const content='<img src=x onerror="window.stage5Injected=true">'
  mockApi({'/api/session':{authenticated:true,login_available:true},'/api/research/failures':[{failure_id:'f',run_id:'r',workflow_id:'w',monitor:content,
    expected_unsafe:true,prediction:false,category:'UNKNOWN_FAILURE'}]})
  mount(<Stage5FailuresPage/>)
  expect(await screen.findByText(content)).toBeInTheDocument()
  expect(document.querySelector('img[onerror]')).toBeNull()
 })
})
