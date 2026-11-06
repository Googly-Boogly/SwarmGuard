"""The shipped integration examples run offline against the real gateway."""
from __future__ import annotations
import asyncio
import importlib.util
import sys
from pathlib import Path
import pytest
from swarmguard.integrations.registry import framework_info

ROOT = Path(__file__).resolve().parents[1] / 'examples'
EXAMPLES = {'custom': 'custom_python', 'crewai': 'crewai_example', 'langgraph': 'langgraph_example', 'autogen': 'autogen_example'}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv('CREWAI_STORAGE_DIR', str(tmp_path / 'framework-data'))
    monkeypatch.setenv('OTEL_SDK_DISABLED', 'true')
    monkeypatch.setenv('CREWAI_TELEMETRY_ENABLED', 'false')
    monkeypatch.syspath_prepend(str(ROOT))


@pytest.mark.parametrize('framework', list(EXAMPLES))
def test_example_authorized_unauthorized_and_monitoring_cases(framework):
    if not framework_info(framework)['compatible_version_family']:
        pytest.skip(f'optional {framework} dependency unavailable')
    path = ROOT / EXAMPLES[framework] / 'example.py'
    spec = importlib.util.spec_from_file_location(f'example_{framework}', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    summary = asyncio.run(module.main())
    import _shared
    assert _shared.check(summary), summary
    assert summary['authorized_case']['executed'] and summary['simulated_emails_sent'] == 1
    assert summary['unauthorized_cases']['forged_approval']['decision'] == 'BLOCK'
    assert not any(c['executed'] for c in summary['unauthorized_cases'].values())
    if framework == 'langgraph':
        assert module.graph_check(summary)
    sys.modules.pop('_shared', None)
