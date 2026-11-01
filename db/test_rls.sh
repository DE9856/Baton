#!/bin/sh
# Runs db/test_rls.sql against a throwaway Postgres in Docker.
set -e
cd "$(dirname "$0")"
NAME=baton-rls-test
docker rm -f "$NAME" >/dev/null 2>&1 || true
MSYS_NO_PATHCONV=1 docker run -d --name "$NAME" -e POSTGRES_PASSWORD=test -v "$PWD:/sql:ro" postgres:16-alpine >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1' EXIT
i=0
until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && { echo "Postgres did not start"; exit 1; }
  sleep 1
done
sleep 2
MSYS_NO_PATHCONV=1 docker exec "$NAME" psql -U postgres -v ON_ERROR_STOP=1 -q -f /sql/test_rls.sql 2>&1 | grep -E "ok:|FAILED|ERROR|ALL RLS"
