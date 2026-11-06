#!/bin/sh
# Runs once at database initialization (as the owner). Creates login roles for each trusted service with
# passwords from Docker secrets. Table grants are applied by the migrate job (stage7_cli.py migrate --grants).
set -eu
for role in gateway executor control; do
  pw=$(cat "/run/secrets/db_${role}_password")
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
       -v role="sg_${role}" -v pw="$pw" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 20',
              :'role', :'pw') \gexec
SQL
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
REVOKE ALL ON DATABASE swarmguard FROM PUBLIC;
GRANT CONNECT ON DATABASE swarmguard TO sg_gateway, sg_executor, sg_control;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
