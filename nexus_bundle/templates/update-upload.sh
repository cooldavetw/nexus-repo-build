#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
usage() {
  echo 'Usage: upload-wheels.sh --url URL [--repository NAME] [--username USER] [--network NETWORK] [--image IMAGE]'
  echo 'Credentials: NEXUS_PASSWORD environment variable or an interactive prompt.'
}
NEXUS_URL="${NEXUS_URL:-}"
NEXUS_REPOSITORY="${NEXUS_REPOSITORY:-$(cat repository.txt)}"
NEXUS_USERNAME="${NEXUS_USERNAME:-admin}"
network="${NEXUS_NETWORK:-host}"
image="${UPLOADER_IMAGE:-$(cat uploader-image.txt)}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --url|--repository|--username|--network|--image)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then usage >&2; exit 1; fi
      case "$1" in
        --url) NEXUS_URL="$2" ;;
        --repository) NEXUS_REPOSITORY="$2" ;;
        --username) NEXUS_USERNAME="$2" ;;
        --network) network="$2" ;;
        --image) image="$2" ;;
      esac
      shift 2 ;;
    *) usage >&2; exit 1 ;;
  esac
done
if [ -z "$NEXUS_URL" ]; then echo '--url or NEXUS_URL is required' >&2; exit 1; fi
sha256sum --check SHA256SUMS
if [ -f images/uploader.tar ]; then
  docker image load --input images/uploader.tar
fi
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "Uploader image is missing: $image. Load it from the original bundle or build with --include-uploader." >&2
  exit 1
fi
if [ -z "${NEXUS_PASSWORD:-}" ]; then
  read -r -s -p 'Nexus password: ' NEXUS_PASSWORD
  printf '\n'
fi
if [ -z "$NEXUS_PASSWORD" ]; then echo 'Password cannot be empty' >&2; exit 1; fi
export NEXUS_URL NEXUS_REPOSITORY NEXUS_USERNAME NEXUS_PASSWORD
docker run --rm --pull never --platform "$(cat container-platform.txt)" \
  --network "$network" \
  -e NEXUS_URL -e NEXUS_REPOSITORY -e NEXUS_USERNAME -e NEXUS_PASSWORD \
  -v "$PWD/wheels:/wheels:ro" -v "$PWD/scripts:/scripts:ro" \
  --entrypoint python "$image" /scripts/nexus_admin.py upload
