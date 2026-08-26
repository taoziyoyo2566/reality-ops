#!/usr/bin/env bash
set -euo pipefail

image_name="${1:-}"
image_tag="${2:-}"
curl_bin="${CURL_BIN:-curl}"

if [[ ! "${image_name}" =~ ^[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
  echo "invalid Docker Hub image name: '${image_name}'" >&2
  exit 1
fi
if [[ ! "${image_tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-beta)?(-r[1-9][0-9]*)?$ ]]; then
  echo "invalid immutable image tag: '${image_tag}'" >&2
  exit 1
fi

response="$("${curl_bin}" --silent --show-error --location \
  --write-out $'\n%{http_code}' \
  "https://hub.docker.com/v2/repositories/${image_name}/tags/${image_tag}")"
status="${response##*$'\n'}"

case "${status}" in
  404)
    echo "Immutable image tag is available: ${image_name}:${image_tag}"
    ;;
  200)
    echo "immutable image tag already exists and will not be overwritten: ${image_name}:${image_tag}" >&2
    exit 1
    ;;
  *)
    echo "Docker Hub returned HTTP ${status} while checking ${image_name}:${image_tag}" >&2
    exit 1
    ;;
esac
