#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ -z "${NEXUS_PASSWORD:-}" ]; then
  read -r -s -p 'Nexus password: ' NEXUS_PASSWORD
  printf '\n'
fi
export NEXUS_PASSWORD
docker compose run --rm uploader upload
