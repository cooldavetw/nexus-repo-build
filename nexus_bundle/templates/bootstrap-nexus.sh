#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--accept-eula' ]; }; then
  echo 'Usage: bootstrap-nexus.sh [--accept-eula]' >&2; exit 1
fi
accept_eula="${1:-}"
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ -z "${NEXUS_PASSWORD:-}" ]; then
  read -r -s -p 'Admin password to set (or existing password): ' NEXUS_PASSWORD
  printf '\n'
fi
if [ -z "$NEXUS_PASSWORD" ]; then echo 'Password cannot be empty' >&2; exit 1; fi
export NEXUS_PASSWORD
# The file disappears after the initial administrator password is changed.
NEXUS_INITIAL_PASSWORD="$(docker compose exec -T nexus sh -c 'if [ -f /nexus-data/admin.password ]; then cat /nexus-data/admin.password; fi')"
export NEXUS_INITIAL_PASSWORD
docker compose run --rm uploader bootstrap

if docker compose run --rm uploader eula-status; then
  exit 0
else
  status="$?"
  if [ "$status" -ne 2 ]; then exit "$status"; fi
fi
if [ "$accept_eula" != '--accept-eula' ]; then
  echo 'Review the EULA at https://links.sonatype.com/products/nxrm/ce-eula'
  read -r -p 'Accept the Sonatype Nexus Community Edition EULA? [y/N] ' consent
  if [ "$consent" != 'y' ] && [ "$consent" != 'Y' ]; then
    echo 'EULA not accepted; Nexus blocks package uploads and downloads.' >&2
    exit 1
  fi
fi
docker compose run --rm uploader accept-eula
