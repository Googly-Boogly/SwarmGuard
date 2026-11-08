"""Post-run checks of immutable stored data, with no simulator/monitor executions."""
import copy
import json
from pathlib import Path

import pytest

from tools.verify_rq1_followup import verify


@pytest.fixture(scope='module')
def snapshot():
    return json.loads(Path('research/rq1/experiment_006_snapshot.json').read_text())


def test_persisted_60_workflow_accounting_and_independent_metric_recomputation(snapshot):
    report=verify(snapshot)
    assert report['valid']
    assert (report['workflow_trials'],report['paired_workflow_outcomes'])==(60,120)
    assert (report['legitimate'],report['unsafe'])==(48,12)
    assert report['monitor_checkpoint_evaluations']==2*report['primary_action_checkpoints']
    assert report['baseline_preservation']['all_unchanged']


def test_changed_summary_is_rejected(snapshot):
    altered=copy.deepcopy(snapshot)
    altered['result']['analysis']['primary']['global_workflow']['tp']+=1
    with pytest.raises(AssertionError):verify(altered)


def test_missing_workflow_is_rejected(snapshot):
    altered=copy.deepcopy(snapshot);altered['evidence'].pop()
    with pytest.raises(AssertionError):verify(altered)


def test_corrupted_evidence_is_rejected(snapshot):
    altered=copy.deepcopy(snapshot);altered['evidence'][0]['sha256']='wrong'
    with pytest.raises(AssertionError):verify(altered)


def test_baseline_change_is_not_reported_as_preserved(snapshot):
    altered=copy.deepcopy(snapshot);altered['baseline_preservation']['all_unchanged']=False
    with pytest.raises(AssertionError):verify(altered)
