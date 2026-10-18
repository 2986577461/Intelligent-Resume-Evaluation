#!/usr/bin/env bash
# Read-only: no env files, database queries, image pulls, or service changes.
set -euo pipefail

for tool in bash docker tar gzip sha256sum flock; do
  if ! command -v "$tool" >/dev/null; then
    printf 'Missing deployment prerequisite: %s\n' "$tool" >&2
    exit 1
  fi
done

printf 'SSH authentication: OK\n'
printf 'Docker server version: '
docker version --format '{{.Server.Version}}'

if docker compose version --short 2>/dev/null; then
  printf 'Compose v2: available\n'
elif command -v docker-compose >/dev/null; then
  printf 'WARNING: only legacy docker-compose is installed. Upgrade to Compose v2 before deployment.\n'
else
  printf 'WARNING: Compose is not installed. Install Compose v2 before deployment.\n'
fi

printf 'SSH_PROBE_OK (read-only check; no deployment performed)\n'
