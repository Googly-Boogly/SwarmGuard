import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
async function login(page: Page) {
 await page.goto('/integrations')
 await page.getByRole('button', { name: 'Operator login' }).click()
 await page.getByLabel('Operator password').fill('browser-test-operator-password')
 await page.getByRole('button', { name: 'Sign in as operator' }).click()
 await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
}
async function latestJob(request: APIRequestContext, kind: string) {
 const jobs = await (await request.get('/api/platform/jobs')).json()
 return jobs.filter((j: { kind: string }) => j.kind === kind).at(-1)
}
async function settled(request: APIRequestContext, kind: string) {
 await expect.poll(async () => (await latestJob(request, kind))?.status, { timeout: 30000 }).toMatch(/COMPLETED|FAILED|TIMED_OUT/)
 return latestJob(request, kind)
}

test('framework integration, gateway block, and mock safety regression match backend records', async ({ page }) => {
 const errors: string[] = []; page.on('pageerror', e => errors.push(e.message))
 await page.goto('/integrations')
 await expect(page.getByText('Researcher login required')).toBeVisible()
 for (const [path, data] of [['/api/integrations/runs', { case: 'service' }], ['/api/regression/runs', { baseline_id: 'baseline_001' }], ['/api/regression/baselines', { experiment_id: 'x' }]] as const)
  expect((await page.request.post(path, { data })).status()).toBe(401)
 await login(page)
 // Integrations: installed frameworks and declared capabilities from the backend.
 const catalog = await (await page.request.get('/api/integrations')).json()
 for (const f of catalog.available) await expect(page.getByRole('heading', { name: f.framework, exact: true })).toBeVisible()
 // Run a forged-approval workflow through the custom adapter with the synthetic replay human.
 await page.getByLabel('Framework').selectOption('custom')
 await page.getByLabel('Integration scenario').selectOption('approval_attack')
 await page.getByLabel('Use existing operator approval queue').uncheck()
 await page.getByRole('button', { name: 'Run framework workflow' }).click()
 const job = await settled(page.request, 'integration')
 expect(job.status).toBe('COMPLETED')
 const finalDecision = job.result.decisions.at(-1)
 expect(finalDecision).toMatchObject({ tool: 'send_email', decision: 'BLOCK', executed: false })
 expect(job.result.prohibited_outcome).toBe(false)
 // Integration detail: normalized events, gateway-verified block, workflow graph.
 await page.goto(`/integrations/${job.result.integration_id}`)
 await expect(page.getByRole('heading', { name: 'Event coverage' })).toBeVisible()
 await expect(page.getByText('GATEWAY_VERIFIED').first()).toBeVisible()
 const events = await (await page.request.get(`/api/integrations/${job.result.integration_id}/events`)).json()
 expect(events.some((e: { event_type: string; metadata: { decision?: string } }) => e.event_type === 'AUTHORIZATION_DECISION' && e.metadata.decision === 'BLOCK')).toBe(true)
 await expect(page.locator('.react-flow__node').first()).toBeVisible()
 // Runtime monitoring shows the same persisted events.
 await page.getByRole('link', { name: 'Runtime Monitoring', exact: true }).click()
 await expect(page.getByText(events[0].event_id ? 'AUTHORIZATION_DECISION' : '', { exact: true }).first()).toBeVisible()
 // Safety regression: evaluate, designate baseline, run controlled regression, inspect report.
 await page.goto('/regression')
 await page.getByRole('button', { name: 'Evaluate application' }).click()
 const evaluation = await settled(page.request, 'evaluation')
 expect(evaluation.result.status).toBe('COMPLETED')
 await page.reload()
 await page.getByRole('button', { name: 'Designate baseline' }).first().click()
 await expect.poll(async () => (await (await page.request.get('/api/regression/baselines')).json()).length).toBe(1)
 await page.getByLabel('Controlled application profile').selectOption('redirected')
 await expect(page.getByRole('button', { name: 'Start safety regression' })).toBeEnabled()
 await page.getByRole('button', { name: 'Start safety regression' }).click()
 const regression = await settled(page.request, 'regression')
 const runId = regression.result.run_id
 await page.goto(`/regression/${runId}`)
 const record = await (await page.request.get(`/api/regression/runs/${runId}`)).json()
 expect(record.classification).toBe('CONFIRMED_THRESHOLD_VIOLATION')
 await expect(page.getByText('CONFIRMED THRESHOLD VIOLATION').first()).toBeVisible()
 await expect(page.getByText(`Exit code: ${record.exit_code} · Gate passed: ${record.ci_passed}`)).toBeVisible()
 await expect(page.getByText(`n baseline ${record.sample_counts.baseline}, candidate ${record.sample_counts.candidate}`, { exact: false })).toBeVisible()
 expect(errors).toEqual([])
})
