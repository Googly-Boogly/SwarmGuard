# Reproducing Stage 5

Use Python 3.11+ and the repository virtual environment. Tests and default demonstrations require
no API key or external service. The network block in tests remains enabled.

```bash
source .venv/bin/activate
python demo_stage5.py --provider mock
python run_adaptive_evaluation.py --provider mock
python run_benchmark.py --provider mock
python run_benchmark.py --provider mock --suite held_out
```

The default destination is `results/stage5/research.db`. Each execution gets an ID from
Database.next_id and an exclusive `results/stage5/<run_id>/` export containing `manifest.json`
(run configuration, actual metadata, counts, status and metrics) and `candidates.json` (full
trajectories, exact observations, assessments, labels, feedback, mutation, graph, interventions
and robustness pairs). Existing result directories are never overwritten. The database preserves
CAS run versions, responsible actors and research audit events. Interrupted and failed candidates
are not scored as negative predictions. Check requested versus executed and unrun counts.

The manifest records actual start/end times, Python version, git revision including `-dirty`, a
source-tree digest, dataset/template/ruleset/prompt versions and hashes, provider model, sampling,
aggregation, checkpoint policy, partition and all configured limits. The source digest covers core,
provider and entry-point Python plus frontend TS/TSX; it is a fingerprint, not an archived checkout.
Keep a copy of a dirty source tree if exact historical reconstruction is required. Browser backend
revision is captured by `console_main.py`; unavailable revisions are explicitly marked unavailable.

Deterministic mock replay means rerunning the same configuration and source. Compare candidate
selection, actions, event contents, predictions and feedback. Real execution timestamps, elapsed
latencies, experiment IDs in an existing database and source metadata may differ. Repeated
trajectories are not independent statistical samples.

Recorded live trajectory replay means examining the exact stored observations, assessments and
authoritative trajectory without issuing new model requests. Fresh live model execution is a new
experiment: seeds may be unsupported, provider implementations can change, and identical prompts
do not ensure identical results. Do not claim perfect live reproducibility.

Live commands require explicit opt-in and configured prices, model and key:

```bash
# Set OPENAI_API_KEY, SWARMGUARD_OPENAI_MODEL and both per-million-token
# pricing environment variables described in .env.example, without committing secrets.
python run_adaptive_evaluation.py --provider openai --authorize-live --max-cost 1 --max-requests 20
```

Stage 5 live execution is implemented but unverified. It was not run during implementation.
Never interpret mock response agreement as evidence for ethical specialization. Adaptive results
are exploratory local feedback; inherited public held-out cases are only operationally separated.
Use independently authored, sealed fixtures before drawing generalization conclusions.

Console records live in the configured console SQLite database and use separate ID allocation.
Use the research page export link or authenticated GET `/api/research/runs/<id>/export` to inspect
the exact backend records. CLI and browser data stores are intentionally separate; CLI exports
are not automatically imported into the console. No credentials are stored in exports.

Verification commands:

```bash
python -m pytest -v
python demo.py
python demo_stage2.py
python demo_stage3.py --provider mock
python demo_stage5.py --provider mock
cd frontend
npm run lint
npm test
npm run build
npx playwright test
```

Playwright starts an isolated temporary database and test-only operator, not the normal console
operator. Sandboxed execution may need permission to start local browser/server processes;
backend FastAPI tests also stalled under the restricted sandbox during implementation and were
successfully run with the offline network-blocking test fixture outside it.
