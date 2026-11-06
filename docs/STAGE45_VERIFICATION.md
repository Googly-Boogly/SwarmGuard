# Stage 4.5 verification record — 2026-11-06

The application was implemented and exercised locally against actual services and persisted records. These checks validate infrastructure, not RQ1–RQ3 or real LLM performance.

| Check | Actual result |
|---|---|
| Baseline `.venv/bin/python -m pytest -v` | 268 passed, 15.38 s |
| Final full offline Python suite | 315 passed, 41.96 s (47 added console tests) |
| `npm run lint` | TypeScript check passed |
| `npm test` | 16 passed |
| `npm run build` | Production bundle generated successfully |
| `npx playwright test` (development server) | 4 Chromium tests passed, 39.1 s |
| `SWARMGUARD_PREVIEW=1 npx playwright test` (production bundle) | Same 4 Chromium tests passed, 24.5 s |
| Stage 1 demo | Acceptance check: PASS |
| Stage 2 demo | Stage 2 demonstration check: PASS |
| Stage 3 mock demo | Stage 3 demonstration check: PASS |

Demos used `--db /tmp/swarmguard45-stage{1,2,3}.db` to preserve existing demonstration databases. Backend tests used isolated temporary databases and the existing socket block. FastAPI TestClient and local browser server binding required running outside the execution sandbox; the application's test socket block remained active. Browser tests used isolated temporary state and a test-only provisioned operator; no normal operator password/database was changed.

## Browser evidence

The Playwright suite starts loopback FastAPI and Vite and exercises real API requests, without route interception or scenario fixtures in the browser:

1. Login with the default Secure/HttpOnly session cookie; select legitimate collaboration; configure COMBINED mock monitoring; review and launch; wait for human approval; inspect exact parameters and explicitly approve; verify completed workflow, persisted WebSocket events, React Flow nodes/edges, council assessments, charts, one simulated send to the approved recipient, and ALLOW decisions.
2. Launch the controlled approval-laundering workflow; approve the original operation; verify a persisted `APPROVAL_MISMATCH`, no simulated send, the original proposal in the decision inspector, the graph's `INVALIDATED_BY` relationship, and the audit decision.
3. Configure an authenticated mock experiment; observe progress; pause, resume, and stop; confirm CANCELLED from the backend; exercise mobile navigation and horizontal overflow check.
4. Visit agent, workflow, graph, communications, gateway, ethics, trajectories, audit, system, and settings pages with persisted data; check for browser exceptions and visible API errors.

Screenshots: `frontend/test-results/overview-desktop.png` and `frontend/test-results/system-mobile.png` (ignored generated artifacts). Test traces/screenshots on failure are also kept there.

## Security and research bounds

Existing authorization tests remain unchanged and passing. New tests cover session rotation/expiry/logout/rate limits, CSRF and Origin rejection, unauthenticated and agent-token rejection, exact reviewed hash/confirmation, expiry and resource races, approval audit attribution, worker/queue/step/request/runtime limits, cost/live-mode rejection, ordered WebSocket replay, cursor recovery, cancellation audit integrity, and restart interruption with no resume. All protected agent operations retain the original gateway/HMAC execution path.

No real email or paid API calls occurred. Original evaluation artifacts were not rewritten. Synthetic console results remain non-research results. Unknown provider token usage remains unavailable. Undefined metric denominators remain null. Policy traces display only persisted successful checks and final outcomes. Graph chronology retains `causal=False`.

The browser's live LLM mode is unavailable; confirming a live request does not unlock it. Three interactive mock scenarios and two finite scripted replays are enabled. This remains a single-worker local prototype; multi-user authorization, public hosting, persistent agent credential recovery, a distributed job service, live autonomy, and broad hold-out research are not verified or claimed.

The final optimized production bundle was also served with `npm run preview` and passed the same four browser tests, including WebSocket and API proxying. Build chunks use explicit manual splitting with no circular-chunk warning. The build emits harmless upstream Zod comment-annotation warnings; generation succeeds.
