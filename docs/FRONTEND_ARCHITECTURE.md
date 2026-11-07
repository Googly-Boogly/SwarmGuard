# SwarmGuard Research Console — Stage 4.5

This is a local research prototype. Existing Stage 1–3 components remain authoritative and the original `main:app` remains GET-only, with a read-only SQLite handle. The console uses a separate `console_main:app` entry point, a dedicated database by default, and the original API mounted at `/observe`.

## React architecture

`frontend/` uses React, TypeScript, Vite, Tailwind v4, shadcn-style source components built on Radix Dialog/Slot, React Router, TanStack Query, React Flow, Recharts, and Lucide icons. `components/ui` owns accessible buttons and dialogs. `pages/Operations.tsx` groups operational views; `pages/Research.tsx` groups approval and research views. This avoids empty components for each navigation entry. Shared tables, status labels, error handling, and JSON inspectors live in `components/common.tsx`.

The sidebar covers Overview, Agents, Workflows, Graph, Communications, Security, Gateway, Approvals, Ethics, Scenarios, Experiments, Results, Trajectories, Audit, System, and Settings. Detail routes are `/workflows/:workflowId`, `/workflows/:workflowId/graph`, and `/experiments/:experimentId`. The backend job ID is also the experiment detail ID. Missing routes and empty databases have explicit states.

TanStack Query owns server state; forms and selections use React state. Only theme, refresh preferences, and default research settings enter localStorage. No password, session token, API key, or trusted credential is stored there. Dark and light themes use CSS variables. Mobile navigation collapses; tables scroll; graphs retain pan/zoom. Radix dialogs manage focus, Escape, and focus restoration. Agent text is rendered as React text or escaped JSON, never HTML.

## API contract and requests

`src/lib/api/client.ts` centralizes requests, credentials, CSRF headers, HTTP errors, cancellation, and polling. Generated `schema.d.ts` comes from the actual console OpenAPI document. Important session, event, and graph shapes are also checked with Zod. Regenerate after API schema changes:

```bash
.venv/bin/python -m tools.export_console_schema
frontend/node_modules/.bin/openapi-typescript frontend/openapi.json -o frontend/src/lib/api/schema.d.ts
```

The Vite server binds loopback, uses a fixed port, and proxies `/api`, `/observe`, and `/ws` to `127.0.0.1:8000`. No wildcard CORS is enabled. The frontend and API share the browser origin through this proxy.

Main endpoint groups:

| Route | Service / source |
|---|---|
| `GET /api/health`, `/api/overview` | Non-secret diagnostics and persisted counts |
| `GET /api/agents`, `/api/workflows` | Trusted registry, workflow records, job runtime status |
| `GET /api/events` | Parameterized, filtered, bounded event query with sequence cursor and pagination |
| `GET /api/decisions`, `/api/gateway` | Persisted decisions and immutable policy capabilities |
| `GET /api/workflows/{id}/records`, `/graph` | Actions, decisions, messages, resources, agent decisions, assessments, simulated sends, graph service |
| `GET /api/approvals` | Exact-operation review packets without signing material |
| `POST /api/session`, `/api/session/logout` | Operator authentication / revocation |
| `POST /api/session/local` | Automatic session creation for the local synthetic console; no credentials accepted |
| `POST /api/workflows`, `/api/experiments` | Strict bounded configurations, authenticated job launch |
| `POST /api/jobs/{id}/pause`, `/resume`, `/stop` | Trusted cooperative control; stop waits for cleanup |
| `POST /api/approvals/{id}` | Exact reviewed hash and confirmation through existing ApprovalManager |
| `GET /api/jobs`, `/{id}`, `/{id}/results`, `/{id}/export` | Persistent progress, derived measurements, complete JSON export |
| `/observe/...` | Original read-only Stage 1–3 observability and evaluation archive |

GET requests never launch jobs or make LLM requests. All read queries use `mode=ro`. SQL stays in the existing storage modules. No endpoint accepts SQL, arbitrary shell commands, identities, tool proposals, or policy changes.

## WebSocket and recovery

`/ws/workflows/{workflow_id}?after_seq=N` authenticates an operator session cookie, checks the exact local Origin, validates workflow existence, and caps connections at eight. No secret appears in the URL. The server sends `{type: "event", event: WorkflowEvent}` and heartbeat envelopes. Event IDs and global sequence values are original persisted values. The database is authoritative.

`useWorkflowEvents` fetches missed events in pages, merges by event ID, sorts by sequence, reconnects, and retains a 1.5-second cursor-based recovery poll. Unauthenticated viewers use polling exclusively. This also recovers missed events after a connection interruption. Set Uvicorn `--ws-max-size 16384`; the application implements server delivery only, without any browser-to-agent message protocol. Normal query polling is configurable; active workflow recovery remains on for event continuity.

## Authentication and approval security

The local `console_main:app` entry point and development Compose stack default to password-free access (`SWARMGUARD_LOCAL_ACCESS=true`). The frontend reads `/api/session`, then automatically posts an empty JSON object to `/api/session/local` when needed. This creates the same bounded, expiring server-side session and HttpOnly cookie without showing login or sign-out controls. The server fixes the identity to `human_operator` with only RESEARCHER and OPERATOR roles; clients cannot choose identity or roles. Bootstrap requires an allowed Origin and JSON; subsequent mutations still require the session's CSRF token. Local access trusts access to the loopback browser console rather than authenticating an individual human. Keep the local stack bound to loopback. GETs do not create sessions or launch work.

Password-free access is rejected when a control plane is supplied or in-process execution is disabled. The separated control service retains its account and role checks. Password-based local configuration remains available with `SWARMGUARD_LOCAL_ACCESS=false`; the following provisioning/login details apply to that mode.

`setup_console_operator.py` reads a hidden password twice, requires at least twelve characters, and writes only a salted scrypt hash into `.console/operator.json` with mode 0600. No default operator password exists. Provisioning requires local filesystem authority and is never an HTTP endpoint.

Successful login creates a random, one-hour session held only by the server. The browser cookie is HttpOnly, Secure by default, SameSite=Strict, and scoped to `/`. The session identifier is never returned as JSON. The session endpoint returns a separate CSRF token for the request header. Every mutation requires a valid session, JSON content type, an allowed local Origin, and a matching CSRF token. Login checks Origin and JSON, rate-limits failures, and rotates sessions. Logout revokes the server-side session. Restart revokes all sessions. The operator identity is the fixed trusted registry identity `human_operator`; request bodies cannot choose it. Host checks reject unknown hosts. Bodies are bounded to 32 KiB.

Approvals expose recipient, subject, body, workflow, resource version, deadline, status, and operation digest. An operator must explicitly confirm this exact packet. The control service rechecks status, pending-request age (15 minutes), workflow ownership, current version, protected content, and reviewed digest inside the simulator transaction. It then calls the existing credential-bearing simulated operator and ApprovalManager. The grant event records the trusted human identity. Execution still passes the original gateway, HMAC permit, version/content/approval revalidation, and atomic approval consumption. Historical and interrupted requests are read-only because their owning runtime credentials are gone. A rejection cannot execute an operation. Agents cannot obtain a browser operator session from their agent credentials.

Secure cookies work on the localhost development origin in the tested Chromium flow. `SWARMGUARD_COOKIE_SECURE=false` is an explicit compatibility escape hatch for a local HTTP client that cannot handle localhost Secure cookies. It must not be used for public hosting; public deployment is outside this prototype's scope.

## Jobs and execution

`console/jobs.py` manages one cooperative async worker and at most four active/queued jobs. Each immutable configuration contains allowlisted scenarios/modes, monitoring, intervention, aggregation, seed, trial count, step cap, runtime cap, provider request cap, and spending ceiling. Limits are validated server-side. Maximum concurrency is one, at most fifty trials per job, at most 200 steps per workflow, 900 seconds per job, and 2,000 provider requests per job.

The existing autonomous agent, private memory, task manager, mock policy responder, and gateway are reused. Interactive approvals never auto-grant. Local/global assessment contexts come exclusively from ObservationBuilder at proposal cutoffs. Councils use the existing synthetic responder and evidence validator. All provider calls share one GuardedProvider request budget. Monitoring is post-proposal advisory in observation mode; HUMAN_OVERSIGHT uses the existing monotone gateway guard/council path; ENFORCEMENT selects the fixed synthetic recipient scope in existing deterministic policy. Monitor advice cannot alter policy.

Three interactive scenarios are enabled: legitimate collaboration, approval mismatch, and misleading communication / distributed approval laundering. Controlled adversarial gateway proposals augment deterministic mock policies and are disclosed in run metadata. Two original Stage 1 acceptance scripts support labelled SCRIPTED replay; their human is scripted. Their action sequences are finite and known to fit the required minimum forty-step bound; pause/resume are not offered. The original fourteen-case Stage 2 benchmark remains a CLI operation and its stored results remain readable through `/observe`.

Jobs and progress use compare-and-set versions plus immutable `console_job_versions` snapshots with responsible principal attribution. Original workflow events remain the agent-operation audit record. Startup marks unfinished jobs INTERRUPTED and halts their workflows. It never schedules recovery. Graceful shutdown cancels jobs and persists terminal state. Pause takes effect at cooperative boundaries; an already submitted action may finish. Cancellation waits for task cleanup before reporting CANCELLED. Run only one Uvicorn worker; no multi-process scheduler or persistent agent-credential recovery exists.

## Research integrity and errors

Ground-truth outcome specifications are joined after predictions are frozen. Neutral workflow IDs, titles, and objectives reach agents/monitors. Run metadata records the actual Git revision (including dirty state) obtained by the entry point, or `unavailable` when it cannot be verified. No files under existing `results/` are changed.

Completed trial metrics derive from recorded predictions and independent synthetic outcome specifications. Failed/unrun trial counts are explicit. Zero denominators stay null/undefined. Graphs preserve causal flags; chronology is dashed and labelled non-causal. Gateway traces display only `checks_passed`, final reason, and execution status; unrecorded intermediate checks stay unknown. Council disagreement is displayed without a claim of improved safety. Detection lead uses agent-decision steps before the final attempted send, not wall-clock latency. Mock usage tokens remain unavailable; billing is zero because no billable provider is invoked. All console results keep `is_research_result=False`.

Browser live execution is unavailable and refused server-side even with confirmation. This prevents an incomplete browser budget path from invoking paid providers. Existing opt-in Stage 3 live CLI code is preserved. Advanced live autonomy and automated repeated human approvals are intentionally not claimed.

Cancellation shielding lets any already-submitted gateway request finish its persisted decision before worker teardown, including during a council await. This preserves the submission/decision audit invariant. The cancellation regression test exercises a delayed council and verifies every proposed action has its decision. Full immutable resource-version histories are included in workflow records/export; decision inspection loads the original persisted action alongside the final gateway record.
