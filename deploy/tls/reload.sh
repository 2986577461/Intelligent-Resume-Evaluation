#!/usr/bin/env bash
set -euo pipefail
if [[ "$(docker inspect --format '{{.State.Running}}' resume-https 2>/dev/null || true)" == true ]]; then
  docker exec resume-https nginx -t
  docker exec resume-https nginx -s reload
fi
