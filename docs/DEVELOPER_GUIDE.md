# Developer guide

Use Python 3.11+ and the existing frontend toolchain. Install with `python -m pip install -e ".[test]"`; install frontend dependencies with `cd frontend && npm ci`.

Read AGENTS.md before changes. SQL belongs only in storage modules. Preserve credential-derived identity, resource CAS versions, approval binding, fail-closed authorization and advisory-only monitors. Optional framework dependencies must remain optional. Research imports accept data only. Add regression tests for allowed and blocked authorization paths.

Run `python -m pytest -v`, the three original demonstrations, `python demo_final.py`, `python run_research.py --provider mock`, and frontend lint/test/build plus Playwright. Use disposable databases. Never edit historical results to make verification pass.
