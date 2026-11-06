# Incident Response (Stage 7)

General rule: **contain first** (emergency stop or revocation), **preserve evidence** (do not edit the
database; export events and write an audit checkpoint), then investigate. Nothing here undoes completed
external effects.

## Suspected agent compromise
1. Revoke the agent binding or the whole runtime (`/security/identities` → Revoke).
2. If scope is unclear, activate the emergency stop.
3. Revoke outstanding approvals for affected workflows; pause affected workflows.
4. Review `/security/operations` filtered by the agent; inspect UNKNOWN or FAILED operations.
5. Write an audit checkpoint; export evidence (`GET /api/v1/security/events`, operation details, and a read-only `pg_dump`).
6. Re-issue credentials for a new agent ID only after the runtime is rebuilt.

## Unauthorized execution attempts
Blocked attempts appear as BLOCK decisions and `SECURITY_ALERT` / `AUTHENTICATION_FAILED` events. Confirm
`executed=false` and the simulated outbox count. Repeated attempts from one identity: revoke it. Attempts
against the executor (`EXECUTION_GRANT_INVALID` alerts) indicate a network-boundary problem: verify that the
agent container is attached only to `agent_net`.

## Credential exposure
* Agent credential: revoke its credential ID immediately; issue a new one.
* Identity issuer key: rotate the key pair, redeploy the gateway public key, re-issue all credentials,
  revoke all runtimes until done.
* Executor grant key or approval key: emergency stop, rotate, restart gateway/executor/control, re-request
  approvals.
* Database password: `ALTER ROLE`, update the secret, restart the service.

## Approval misuse
Revoke the approval (or all outstanding approvals of the workflow). Check the approval's events (requested,
granted by which approver, consumed by which action). Approvals are bound to exact parameters and versions;
a mismatch is blocked, so misuse usually means an approver approved the wrong operation: review approver
practice and revoke the operator's sessions if the account is suspect.

## Database integrity failure
1. `verify_stage7.py --audit` with all checkpoints; note the first failing sequence.
2. Activate the emergency stop. Snapshot the database read-only for forensics.
3. Compare with backups and external checkpoints to bound the tampered range.
4. Rotate database credentials; investigate which role could write (only owner can bypass grants).
5. Restore from the last verified backup if required; never "repair" chain rows in place.

## Service outages
Protected operations fail closed. Executor down: `EXECUTOR_UNAVAILABLE`. Database down:
`AUTHORIZATION_SERVICE_UNAVAILABLE` (decisions cannot be persisted; gateway logs record the refusals). Gateway
down: agents cannot proceed. After recovery, review operations left `UNKNOWN`; they are never retried
automatically. Do not re-submit an UNKNOWN operation until its effect has been established.
