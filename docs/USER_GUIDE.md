# SwarmGuard Research Console user guide

## Start locally

Python 3.11+ and Node.js 22.12+ are required. Use the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python setup_console_operator.py
python -m uvicorn console_main:app --host 127.0.0.1 --port 8000 --ws-max-size 16384 --limit-concurrency 32
```

Choose your own operator password. The setup script saves a salted hash; there is no default password. In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173**. The Vite proxy connects to FastAPI on **http://127.0.0.1:8000**. API docs are at `/docs`. Use one backend worker. The original read-only backend remains available separately via `uvicorn main:app` or on the console at `/observe`.

The console creates `swarmguard_console.db` and a trusted three-agent registry. No workflow or evaluation metrics are invented to fill an empty dashboard. Set `SWARMGUARD_CONSOLE_DB` to a separate persistent path if needed. Existing demo databases and evaluation archives are not overwritten.

The default session cookie is Secure and HttpOnly. Chromium accepts it at the localhost development origin. For a local HTTP client that cannot, `SWARMGUARD_COOKIE_SECURE=false` explicitly disables Secure for development only. Keep all services bound to loopback. This is not an internet deployment configuration.

## Navigate and observe

Overview shows persisted counts, agent status, workflows, and recent gateway decisions. Sidebar navigation groups agent operations, safety oversight, research, and system tools. The top bar shows backend health, MOCK MODE, active workflow/job, theme, and operator login. Read-only inspection does not require operator login. Mutations do.

Agent Control shows the fixed trusted registry and capabilities. Select an agent for history and workflow links. Control the owning workflow to pause/resume/stop execution; individual agent control and dynamic identity creation are unavailable.

## Launch a workflow

1. Sign in using **Operator login**.
2. Open **Scenario Library** and select **Configure workflow**, or **Active Workflows → Start workflow**.
3. Keep **Mock autonomous** for interactive operation. Choose a scenario, monitor configuration, intervention, aggregation, and execution limits.
4. Choose **Review configuration**, inspect the limits, then **Start workflow**.
5. The run detail page shows progress and links to each workflow. Open the workflow to see participating agents, graph, events, and gateway outcomes.
6. Interactive flows wait at **WAITING APPROVAL** until the operator grants or rejects the exact pending request.

Mock policies are deterministic handwritten behavior, not model reasoning. The approval-mismatch scenario includes a controlled agent edit after approval; the distributed scenario includes a misleading agent message. Both are synthetic adversarial fixtures, not claims about live autonomous behavior.

The scripted Stage 1 replay explicitly includes its original simulated human. It is not an interactive operator flow and does not support pause/resume. Live model execution is disabled in the browser, including at the backend. No paid API calls are necessary or possible through these console launch endpoints.

## Communications and graphs

**Agent Communications** filters persisted messages by sender/recipient, type, workflow, and time. Message content is untrusted text; its claims do not confer authority.

**Workflow Graph** renders persisted nodes/edges from the existing graph service. Pan, zoom, fit, filter by type/agent/decision, and select nodes to inspect backing records and dependency chains. Chronological `EXECUTED_AFTER` edges are dashed and non-causal. Links are never inferred from timestamps. The live event inspector includes IDs, versions, parent references, and payloads. Authenticated viewers receive WebSocket events; polling recovers missed events. Read-only viewers use polling.

## Review security and approvals

**Security Events** separates gateway decision from actual execution status. A blocked unsafe proposal is not an executed breach. **Capability Gateway** lists the immutable permission matrix, protected operations, and persisted successful checks. Checks not recorded remain unknown.

In **Human Approvals**, open **Review exact operation**. Inspect recipient, subject, complete body, workflow, resource version, expiry, and operation digest. Check the explicit review confirmation, then approve or reject. Success is shown only after backend confirmation. Changes to content/version invalidate the approval; stale or expired requests are rejected. Grants remain single-use and expire fifteen minutes after approval. Pending requests expire after fifteen minutes. Historical, interrupted, and terminal-job approvals are read-only. Stopping a job does not grant anything.

## Ethics Council

Choose a workflow and a recorded assessment. The page preserves every framework's risk, recommendation, concerns, evidence, uncertainty, model identifier, failures, and explanation. The comparison table and summary expose disagreement and missing monitors. Every assessment is advisory. MOCK labels remain visible. A council recommendation never overrides a deterministic block.

## Experiments and results

**Experiment Runner** accepts a scenario set, trials, seed, monitoring/intervention/aggregation, and bounded execution settings. Review before launch. There is one worker and a bounded queue; trials persist progress. Interactive trials need human review for each approval. Stop waits for backend cancellation confirmation. Paused time and approval-wait time count toward the job runtime budget. Restarted unfinished jobs become INTERRUPTED and never resume automatically.

**Research Results** displays measured sample counts, precision/recall/F1/accuracy/FPR, detection lead, safety/utility, and council agreement where the corresponding records exist. Undefined metrics remain undefined. Failed and unrun trials stay visible. Mock results validate infrastructure and do not measure real LLM performance. The original Stage 2/3 archive can be selected below console results without changing its files.

**Trajectory Explorer** filters full recorded events and exposes autonomous observations, proposals, resource versions, and assessments. Select a second trajectory for side-by-side events and decision outcomes. Identical seeds do not establish identical live runs. The interactive timeline displays the first 500 events; full history is available in the JSON export. **Audit Log** offers server-side filtering and pagination with no edit/delete controls.

## Export and settings

Each job detail and result page links to **Export JSON**. It contains configuration, actual revision, progress, failures, metrics, original events, workflow records, and graph relationships. No credentials or signing keys are exported.

**Settings** saves local theme, default mock scenario/monitor, and refresh preference. Execution limits remain server-validated. API keys are never accepted or saved by the browser. **System Status** shows non-secret diagnostics and recent job failures.

## Verify

With the Python environment active:

```bash
python -m pytest -v
python demo.py
python demo_stage2.py
python demo_stage3.py --provider mock
cd frontend
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright starts isolated FastAPI/Vite servers, a temporary database, and a test-only operator. Stop normal development servers on ports 8000/5173 first. The real local operator and database are not used. Demos support `--db /tmp/name.db` to avoid replacing your demonstration databases.

To verify the optimized production assets after building, run `SWARMGUARD_PREVIEW=1 npm run test:e2e` in `frontend/`. It runs the same isolated browser checks through `npm run preview`; the backend and proxy are still required.
