# Stage 7 Deployment

Disposable, local, synthetic. Mock tools only; no real email. Never point these scripts at production data.

## Local development (no containers)

```bash
pip install -e ".[test,deploy]"           # psycopg only needed for PostgreSQL
python demo_stage7.py                     # gateway + executor + agent as separate local processes (SQLite)
python verify_stage7.py                   # same checks + audit self-test
python verify_stage7.py --audit --database path/to/stage7.db [--checkpoints DIR]
```

`demo_stage7.py --database postgresql://user@127.0.0.1:5432/db` runs against PostgreSQL
(`SWARMGUARD_DB_PASSWORD_FILE` supplies the password; it is never part of the URL).

## Hardened Docker Compose deployment

```bash
python deployment/scripts/verify_deployment.py          # generate secrets, build, start, verify, tear down
python deployment/scripts/verify_deployment.py --keep   # leave it running on http://127.0.0.1:8080
docker compose -f deployment/compose/compose.hardened.yml -p swarmguard-stage7 --profile agent down -v
```

Manual start (after generating secrets as below):

```bash
python stage7_cli.py init-secrets --dir deployment/secrets          # refuses to overwrite
python setup_console_operator.py --username operator_alice --role OPERATOR --file deployment/secrets/operators.json
python setup_console_operator.py --username admin_ada --role ADMINISTRATOR --file deployment/secrets/operators.json
python stage7_cli.py issue-token --issuer-key deployment/secrets/identity_issuer.key \
    --agent operations_agent --runtime runtime_alpha --ttl-seconds 3600 --out deployment/secrets/agent/token
chmod 0644 deployment/secrets/*.pub deployment/secrets/*.password deployment/secrets/approval_signing.key \
    deployment/secrets/executor_grant.key deployment/secrets/operators.json deployment/secrets/agent/token
docker compose -f deployment/compose/compose.hardened.yml -p swarmguard-stage7 up -d --build
docker compose -f deployment/compose/compose.hardened.yml -p swarmguard-stage7 --profile agent run --rm agent-runtime
```

Services, networks and secrets are described in [STAGE7_SECURITY_ARCHITECTURE.md](STAGE7_SECURITY_ARCHITECTURE.md).
Only the frontend publishes a port (`127.0.0.1:8080`). The `agent-runtime` service is in the `agent` profile
and is run on demand.

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `SWARMGUARD_DATABASE_URL` | gateway, executor, api | SQLite path or `postgresql://role@host:port/db` (no password) |
| `SWARMGUARD_DB_PASSWORD_FILE` | all PostgreSQL clients | secret file with the role password |
| `SWARMGUARD_APPROVAL_KEY_FILE` | gateway, executor, api | approval integrity key |
| `SWARMGUARD_APPROVER_IDS` | gateway, executor, api | trusted human approver IDs (must not be agent IDs) |
| `SWARMGUARD_IDENTITY_PUBLIC_KEY_FILE` | gateway | verifies agent credentials |
| `SWARMGUARD_GRANT_PRIVATE_KEY_FILE` / `..._PUBLIC_KEY_FILE` | gateway / executor | execution grants |
| `SWARMGUARD_EXECUTOR_URL`, `SWARMGUARD_EXECUTOR_TIMEOUT` | gateway | internal executor channel |
| `SWARMGUARD_OPERATORS_FILE`, `SWARMGUARD_CHECKPOINT_DIR`, `SWARMGUARD_ALLOWED_ORIGINS`, `SWARMGUARD_ALLOWED_HOSTS` | api | accounts, external checkpoints, browser origin/host allowlists |
| `SWARMGUARD_DB_BUSY_TIMEOUT_MS`, `SWARMGUARD_HEARTBEAT_SECONDS`, `SWARMGUARD_MIGRATE` | services | SQLite lock wait, heartbeat period, allow migrations (default false) |

Secrets: never in images, Git (`deployment/secrets/` is ignored), frontend bundles, audit events, metrics,
exception messages or agent containers. The agent container receives exactly one secret: its credential.

## Database migrations

Forward-only, idempotent (`swarmguard/storage/database.py: MIGRATIONS`), recorded in `schema_migrations`.
Services start with migrations disabled; only the `migrate` job (owner role) applies them and the role grants:

```bash
python stage7_cli.py migrate --database postgresql://swarmguard_owner@host/swarmguard --grants
```

**Back up before migrating** (`pg_dump -Fc swarmguard > backup.dump`; for SQLite copy the file while no
service is running). Migrations never drop or rewrite data. Do not run against production databases from
these scripts; verification uses disposable databases only.

## Health checks

`GET /health/live` (process responsive) and `GET /health/ready` (database reachable, migrations applied,
gateway: executor reachable). Responses list check names and ok/failed only. The control service reports
gateway/executor health from database heartbeats (`/api/v1/services/health`, authenticated).

## Shutdown, backup and recovery

`docker compose ... down` stops services (add `-v` to discard volumes). On restart the gateway closes
interrupted `PENDING`/`AUTHORIZED` operations as `FAILED` and the executor marks interrupted `EXECUTING`
operations `UNKNOWN`; nothing is re-executed. Back up the database and the external checkpoint directory
separately (checkpoints must not live in the same trust domain as the database they protect).

## Secret rotation

* Agent credentials: issue a new token, deploy it to the runtime, revoke the old credential ID (`cred_…`).
* Identity issuer key: generate a new pair, deploy the public key to the gateway, re-issue all credentials.
* Executor grant key: replace both halves and restart gateway and executor together (grants live 30 s).
* Approval key: rotate only with no outstanding approvals (existing signatures stop verifying).
* Database passwords: `ALTER ROLE … PASSWORD`, update the secret file, restart the service.
