#!/usr/bin/env bash
# Runner only: disposable app/Redis/TLS gateway, no production data or credentials.
set -euo pipefail
: "${SHA:?Set SHA to the image commit}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
tmp="$(mktemp -d)"
cleanup() {
  # Allowlisted state only: raw logs can contain prompts, cookies or credentials.
  if (( $? != 0 )); then
    for name in smoke-backend smoke-redis smoke-gateway; do
      docker inspect --format '{{.Name}} status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} {{if .State.Health}}health={{.State.Health.Status}}{{end}}' "$name" 2>/dev/null || true
    done
    echo 'Isolated release smoke failed; no release should be published.' >&2
  fi
  docker rm -f smoke-gateway smoke-backend smoke-redis >/dev/null 2>&1 || true
  docker network rm release-smoke >/dev/null 2>&1 || true
  rm -rf -- "$tmp"
}
trap cleanup EXIT
# Pull before creating an internal-only network; runtime cannot reach paid APIs.
docker pull redis:7-alpine
docker pull nginx:alpine
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$tmp/key.pem" -out "$tmp/fullchain.pem" \
  -subj '/CN=www.myzyitzj.cn' -addext 'subjectAltName=DNS:www.myzyitzj.cn' >/dev/null 2>&1
# Only the listen port differs; route handling/security headers use production config.
sed 's/listen 443 ssl;/listen 19443 ssl;/' deploy/tls/nginx.conf > "$tmp/nginx.conf"
docker network create --internal release-smoke >/dev/null
docker run -d --name smoke-redis --network release-smoke redis:7-alpine >/dev/null
docker run -d --name smoke-backend --network release-smoke --network-alias resume-backend \
  -e REDIS_URL=redis://smoke-redis:6379/0 \
  -e DEEPSEEK_API_KEY=smoke-not-a-real-key -e TAVILY_API_KEY=smoke-not-a-real-key \
  -e SESSION_COOKIE_SECURE=true -e REQUIRE_HTTPS=true -e APP_VERSION="$SHA" \
  -e LANGSMITH_TRACING=false -e LANGCHAIN_TRACING_V2=false \
  "resume-backend:$SHA" >/dev/null
for attempt in $(seq 1 40); do
  state=$(docker inspect -f '{{.State.Health.Status}}' smoke-backend)
  if [[ "$state" == healthy ]]; then break; fi
  sleep 3
done
test "$(docker inspect -f '{{.State.Health.Status}}' smoke-backend)" = healthy
docker run -d --name smoke-gateway --network release-smoke --network-alias www.myzyitzj.cn \
  -v "$tmp/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$tmp:/etc/nginx/resume-certs:ro" \
  -v "$repo_root/frontend/dist:/usr/share/nginx/resume:ro" nginx:alpine >/dev/null
docker cp "$repo_root/frontend/dist" smoke-backend:/tmp/smoke-frontend >/dev/null
docker cp "$tmp/fullchain.pem" smoke-backend:/tmp/smoke-ca.pem >/dev/null
docker exec -i -e SHA="$SHA" -e SMOKE_CA=/tmp/smoke-ca.pem -e SMOKE_FRONTEND=/tmp/smoke-frontend smoke-backend \
  python - < scripts/cd/smoke-release.py
