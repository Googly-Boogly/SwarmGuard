import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LaboratoryResults, type LaboratoryExperiment } from './components/LaboratoryResults'

const record: LaboratoryExperiment = {
 experiment_id: 'test', status: 'COMPLETED', config: { mode: 'SCRIPTED', question: 'RQ1' },
 metrics: {
  accounting: { planned: 2, recorded: 2, failed: 0, unrun: 0, missing_monitor_predictions: 0 },
  detection: {
   LOCAL_MONITOR: { n: 2, tp: 0, fn: 1, fp: 0, tn: 1, recall: 0, false_positive_rate: 0, not_evaluated: 0 },
   GLOBAL_MONITOR: { n: 2, tp: 1, fn: 0, fp: 0, tn: 1, recall: 1, false_positive_rate: 0, not_evaluated: 0 },
   GENERAL_COUNCIL: { n: 2 },
  },
  paired_effects: [{ evidence: 'advanced-evidence-marker' }],
 },
 rows: [
  { trial_id: 'one', evaluation_valid: true, ground_truth: { label: 'UNSAFE' }, predictions: { LOCAL_MONITOR: false, GLOBAL_MONITOR: true }, events: ['raw-trajectory-marker'] },
  { trial_id: 'two', evaluation_valid: true, ground_truth: { label: 'SAFE' }, predictions: { LOCAL_MONITOR: false, GLOBAL_MONITOR: false } },
 ], validation: { valid: true },
}

it('shows only the RQ1 comparison and two trial rows, with raw evidence unmounted', async () => {
 render(<LaboratoryResults record={record}/>)
 const comparison = within(screen.getByRole('table', { name: 'Monitor comparison' }))
 expect(comparison.getAllByRole('row')).toHaveLength(3)
 expect(comparison.getByText('Global')).toBeInTheDocument()
 expect(comparison.getByText('100.0%')).toBeInTheDocument()
 expect(within(screen.getByRole('table', { name: 'Trial outcomes' })).getAllByRole('row')).toHaveLength(3)
 expect(screen.queryByText(/advanced-evidence-marker/)).not.toBeInTheDocument()
 expect(screen.queryByText(/raw-trajectory-marker/)).not.toBeInTheDocument()
 fireEvent.click(screen.getByText('Advanced statistics — all monitors and paired comparisons'))
 expect(await screen.findByText(/advanced-evidence-marker/)).toBeInTheDocument()
 expect(screen.queryByText(/raw-trajectory-marker/)).not.toBeInTheDocument()
})

it('keeps failed assessments and undefined rates visible as unknown', () => {
 const failed = structuredClone(record)
 failed.metrics.detection!.GLOBAL_MONITOR = { n: 0, tp: 0, fn: 0, fp: 0, tn: 0, recall: null, false_positive_rate: null, not_evaluated: 2 }
 failed.rows[0].evaluation_valid = false
 failed.rows[1].predictions!.GLOBAL_MONITOR = null
 render(<LaboratoryResults record={failed}/>)
 expect(screen.getAllByText('Undefined')).toHaveLength(2)
 expect(screen.getByText('Unscored')).toBeInTheDocument()
 expect(within(screen.getByRole('table', { name: 'Trial outcomes' })).getAllByText('Unavailable')).toHaveLength(3)
})
