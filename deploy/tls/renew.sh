#!/usr/bin/env bash
# Root-only credentials; do not enable xtrace or print the ACME account config.
set -euo pipefail
umask 077
exec 9>/run/lock/resume-acme.lock
flock -n 9 || exit 0
set -a
source /root/.config/resume-acme/aliyun.env
set +a
export http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890
export HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"
export no_proxy=localhost,127.0.0.1,::1,.aliyuncs.com
export NO_PROXY="$no_proxy"
# Keep all ACME output private; an API error might contain signed request details.
if ! /opt/resume-acme/acme.sh --cron --home /opt/resume-acme \
  --config-home /root/.config/resume-acme/account \
  --cert-home /root/.config/resume-acme/certs \
  > /root/.config/resume-acme/renew.log 2>&1; then
  echo 'Certificate renewal failed; inspect the root-only renewal log.' >&2
  exit 1
fi
