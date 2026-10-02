#!/usr/bin/env bash
# Build a candidate first. Switch latest only after the offline smoke check.
set -euo pipefail
cd "$(dirname "$0")/.."
candidate="${SANDBOX_CANDIDATE:-ctf-sandbox:re-tools-20261003}"
proxy="${SANDBOX_BUILD_PROXY:-}"
args=(--network=host --progress=plain)
if [ -n "$proxy" ]; then
  args+=(--build-arg "HTTP_PROXY=$proxy" --build-arg "HTTPS_PROXY=$proxy" --build-arg "http_proxy=$proxy" --build-arg "https_proxy=$proxy")
fi
docker build "${args[@]}" -f devtools/sandbox.Dockerfile -t "$candidate" devtools/
docker run --rm --network=none --memory=8g --cpus=2 "$candidate" python3 /opt/ctf-tools/smoke.py
if docker image inspect ctf-sandbox:latest >/dev/null 2>&1; then
  docker tag ctf-sandbox:latest "ctf-sandbox:before-re-tools-$(date +%Y%m%d-%H%M%S)"
fi
docker tag "$candidate" ctf-sandbox:latest
printf 'Sandbox verified and activated: %s\n' "$candidate"
