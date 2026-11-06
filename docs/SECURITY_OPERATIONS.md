# Security Operations (Stage 7)

## Operator authentication and roles

Accounts are provisioned locally (`setup_console_operator.py --username U --role R`), stored as salted
scrypt hashes, and loaded by the control service. Sessions are server-side, random, HttpOnly,
`Secure`, `SameSite=Strict`, expire after one hour, and are rotated at login. Every mutation requires the
exact allowed Origin, JSON, the session CSRF token and a role. Login is rate limited globally and per
account; failed logins are recorded as `AUTHENTICATION_FAILED` in the chained `system_security` stream.
Administrators can revoke all sessions of an account (`POST /api/v1/security/sessions/revoke`).
MFA is not implemented.

| Role | May |
|---|---|
| RESEARCHER | launch synthetic evaluations (local development mode only) |
| OPERATOR | approve, reject and revoke approvals (as their own approver identity); read security status/operations |
| ADMINISTRATOR | revoke identities, emergency stop/recover, disable capabilities, pause workflows, revoke sessions |
| AUDITOR | read identities, audit verification, checkpoints, operations, events; write checkpoints |

No role implies another. The legacy single-password account has RESEARCHER and OPERATOR only. Agent
credentials are never accepted by console endpoints.

## Identity revocation

`POST /api/v1/security/identities/{id}/revoke` with `{"reason": "...", "confirm": true}` (ADMINISTRATOR).
`id` may be a runtime (revokes the runtime and all its agent bindings), an agent binding, or a credential ID.
Effective on the gateway's next request (checked per request). Bindings are never reassigned; register a
new agent ID. The Identity Management page (`/security/identities`) shows status and offers this action.

## Approval revocation

`POST /api/v1/approvals/{id}/revoke` (OPERATOR). Allowed from `PENDING` or `APPROVED`; consumed approvals
cannot be revoked. A revoked approval yields `BLOCK APPROVAL_REVOKED`.

## Emergency stop

`POST /api/v1/security/emergency-stop` (ADMINISTRATOR, reason + confirm). New protected operations are
refused at the gateway and at the executor. Recovery: `POST /api/v1/security/emergency-stop/recover` with
`confirmation_phrase: "RESUME PROTECTED OPERATIONS"`. Both events are chained. Operations already executing
complete; completed effects are not undone. Capability-level switch: `POST /api/v1/security/capabilities/{tool}`.

## Audit verification

* Console: `/security/audit-integrity` (AUDITOR/ADMINISTRATOR) recomputes the chain on each request.
* CLI: `python verify_stage7.py --audit --database URL --checkpoints DIR` → `Audit verification: PASS|FAIL`.
* Checkpoints: `python stage7_cli.py checkpoint --database URL --dir DIR` or the console button. Store the
  directory outside the database's trust domain and compare across copies.

## Security event review and service monitoring

`/security/operations` shows status counters (proposed violations = BLOCK/ESCALATE decisions; executed
effects = simulated sends), gateway operations with state and idempotency key, and the security event
stream. `/security/operations/{id}` shows transitions, decision, approval reference and audit events.
`/system/services` shows heartbeat-based health (STALE after 30 s) and per-service counters.
