# Stage 5 adversarial threat model

Trusted components are the simulator, sealed identity registry, deterministic policy, capability
gateway, HMAC-permitted executor, approval service, observation builder, validator, controller,
resource budgets, trusted scenario oracles, storage and authenticated console worker. Scenario
candidates and all model-generated text are untrusted data. This is an architectural boundary in
one local Python process, not protection against hostile code execution or filesystem owners.

The generator may select allowlisted synthetic templates and bounded data mutations. It cannot
supply code, SQL, credentials, approval grants, tool registries or policy changes. Unknown fields
are forbidden. Labels and feedback from held_out never enter adaptive generation. A live generator
receives only its authorized template list, current definition and local search feedback, in a JSON
user message. System instructions come from a versioned constant. Deterministic validation remains
mandatory after any provider response.

All proposals, public work, messages, task assignments, shared writes and protected sends use
credential-bound AgentGatewayClient → CapabilityGateway → PolicyEngine → permitted mock
executor. Extra participants have unique registered research identities, workflow membership,
ordinary role permissions and existing resource ACLs. Identity claims never grant authority.
Only the trusted simulated human can approve exact operations. Agent proposals have no policy
or approval-grant tool. Ordinary authorization, version, integrity, expiry and atomic consumption
checks remain unchanged. An additive trusted gateway proposal limit blocks and audits excess
requests. No execution limit is agent-configurable.

The shared analyzer can flag monitor-directed instructions. Its text rule is heuristic; it does not
establish hidden intent. Prompt injection is supplied only as untrusted workflow content. Monitors
receive snapshots without execution, database, approval or policy capabilities. Council advice can
only escalate an ALLOW through the existing DecisionEngine. Policy BLOCK/ESCALATE cannot be
relaxed. Robustness transforms operate only on copied experimental observations; original audit
records and as-of resource versions remain intact. Ground truth is joined after predictions.

Research commands use the existing server-side operator session, exact allowed local Origin,
JSON content type and session CSRF token. Labels and detailed research records also require
researcher authentication. Browser live mode is refused even if confirmation is supplied.
Rejected research POSTs are audited without saving credentials. Accepted runs, cancellations,
progress versions and candidate completion have persisted audit records. Read queries use the
read-only SQLite connection. The original `main:app` remains GET-only.

Research jobs share the existing single execution semaphore, have a bounded queue, and never
resume after restart. Live CLI configuration requires explicit authorization, a positive dollar
limit, configured input/output prices, bounded requests/concurrency and pre-request reservation
through the existing GuardedProvider. There is no mock fallback for live failures. Runtime expiry
and provider exhaustion stop new candidates. A synchronous gateway submission settles before
control reaches a cancellation point; paid provider requests can already be in flight when stopped.
Provider billing limits are still needed for independently enforced account-level spending control.

All records are synthetic, destinations come from existing reserved example-domain fixtures, and
sends only insert simulated email rows. No network-capable code was added to `swarmguard/`.
The opt-in provider adapter remains in `swarmguard_providers/`. The research API accepts neither
arbitrary network targets nor shell instructions. Keep the server on loopback with one Uvicorn
worker. Do not expose it as a public adversarial service.

Known limitations: local audit data is not tamper-proof; same-process code introspection is outside
the model; explicit provenance does not capture arbitrary paraphrases; public held-out fixtures
cannot support a blind-test claim; unknown failure causes remain unknown; live provider behavior
and prompt-injection resistance have not been empirically verified by these mock evaluations.
