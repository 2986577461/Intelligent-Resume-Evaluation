#!/usr/bin/env bash
# Run locally or in Actions. Never enable xtrace: inputs include a private key.
set -euo pipefail

fail() {
  printf 'Connection check failed: %s\n' "$1" >&2
  exit 1
}

for name in CD_SSH_HOST CD_SSH_PORT CD_SSH_USER CD_SSH_PRIVATE_KEY CD_SSH_KNOWN_HOSTS; do
  [[ -n "${!name:-}" ]] || fail "missing $name"
done

# No URLs, SSH aliases, options, or shell fragments in connection parameters.
[[ "$CD_SSH_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || fail 'invalid hostname (use a DNS name or IPv4 address)'
[[ "$CD_SSH_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || fail 'invalid SSH user'
[[ "$CD_SSH_PORT" =~ ^[0-9]{1,5}$ ]] || fail 'invalid SSH port'
port=$((10#$CD_SSH_PORT))
(( port >= 1 && port <= 65535 )) || fail 'SSH port must be between 1 and 65535'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
umask 077
credentials_dir="$(mktemp -d)"
trap 'rm -rf -- "$credentials_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$CD_SSH_PRIVATE_KEY" > "$credentials_dir/key"
printf '%s\n' "$CD_SSH_KNOWN_HOSTS" > "$credentials_dir/known_hosts"
# SSH needs the file only; don't forward the private key in subprocess environments.
unset CD_SSH_PRIVATE_KEY CD_SSH_KNOWN_HOSTS

ssh-keygen -y -P '' -f "$credentials_dir/key" >/dev/null 2>&1 \
  || fail 'private key is invalid or passphrase-protected; use a dedicated CI key'
known_host="$CD_SSH_HOST"
if (( port != 22 )); then
  known_host="[$CD_SSH_HOST]:$port"
fi
ssh-keygen -F "$known_host" -f "$credentials_dir/known_hosts" >/dev/null \
  || fail 'known_hosts has no entry for this host and port; verify the host key through a trusted connection'

# Ignore user SSH config, agent keys, password prompts, and interactive host-key trust.
ssh -F /dev/null \
  -i "$credentials_dir/key" \
  -p "$port" -l "$CD_SSH_USER" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o IdentityAgent=none \
  -o PreferredAuthentications=publickey \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$credentials_dir/known_hosts" \
  -o GlobalKnownHostsFile=/dev/null \
  -o UpdateHostKeys=no \
  -o ConnectTimeout=15 \
  -o ConnectionAttempts=1 \
  -o ServerAliveInterval=10 \
  -o ServerAliveCountMax=2 \
  -o ForwardAgent=no \
  -o ClearAllForwardings=yes \
  -T "$CD_SSH_HOST" 'bash -se' < "$script_dir/remote-probe.sh"
