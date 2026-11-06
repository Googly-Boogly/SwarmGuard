"""Documented CI exit codes of run_regression.py: 0 pass, 1 violation, 2 incomplete, 3 invalid config."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / '.ci' / 'swarmguard-baseline.json'


def regression(tmp_path, *args):
    out = tmp_path / f'out_{len(list(tmp_path.iterdir()))}'
    proc = subprocess.run([sys.executable, '-W', 'ignore', str(ROOT / 'run_regression.py'), '--provider', 'mock',
                           '--output', str(out), *args], cwd=ROOT, capture_output=True, text=True, timeout=240,
                          env={'PATH': '', 'OTEL_SDK_DISABLED': 'true', 'CREWAI_TELEMETRY_ENABLED': 'false',
                               'HOME': str(tmp_path), 'PYTHONPATH': str(ROOT)})
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture(scope='module')
def committed_baseline():
    if not BASELINE.is_file():
        pytest.skip('committed CI baseline missing')
    return json.loads(BASELINE.read_text())


def test_safe_candidate_passes(tmp_path, committed_baseline):
    code, out = regression(tmp_path, '--baseline', str(BASELINE), '--application-version', 'ci-safe')
    assert code == 0, out
    assert '"classification": "NO_REGRESSION_DETECTED"' in out or '"classification": "IMPROVEMENT_OBSERVED"' in out


def test_controlled_regression_fails_gate(tmp_path, committed_baseline):
    code, out = regression(tmp_path, '--baseline', str(BASELINE), '--profile', 'redirected', '--application-version', 'ci-bad')
    assert code == 1, out
    assert '"mandatory_violation": true' in out


def test_incomparable_baseline_is_incomplete_not_pass(tmp_path, committed_baseline):
    other = dict(committed_baseline)
    other['evaluation'] = {**committed_baseline['evaluation'], 'suite_version': 'different-suite'}
    path = tmp_path / 'other.json'
    path.write_text(json.dumps(other))
    code, out = regression(tmp_path, '--baseline', str(path))
    assert code == 2, out
    assert 'INCONCLUSIVE' in out


@pytest.mark.parametrize('args', [['--profile', 'unrestricted'], ['--provider', 'openai'], ['--baseline', 'baseline_missing'],
                                  ['--policy', 'missing-policy.json']])
def test_invalid_configuration_exit_code(tmp_path, args):
    code, out = regression(tmp_path, *args)
    assert code == 3, out


def test_weakened_policy_file_is_invalid_configuration(tmp_path, committed_baseline):
    policy = tmp_path / 'lenient.json'
    policy.write_text(json.dumps({'maximum_prohibited_outcomes': 5}))
    code, out = regression(tmp_path, '--baseline', str(BASELINE), '--policy', str(policy), '--profile', 'redirected')
    assert code == 3, out
