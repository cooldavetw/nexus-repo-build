#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
sha256sum --check SHA256SUMS
docker image load --input images/nexus.tar
docker image load --input images/uploader.tar
docker compose up --detach nexus
if ! docker compose run --rm uploader wait --timeout 300; then
  echo 'Nexus failed to become ready. Inspect: docker compose logs nexus' >&2
  exit 1
fi
