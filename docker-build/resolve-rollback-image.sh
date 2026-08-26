#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image_name="${1:-}"
keep_file="${2:-${repo_root}/docker-build/XRAY_IMAGE_KEEP_TAGS}"
curl_bin="${CURL_BIN:-curl}"

if [[ ! "${image_name}" =~ ^[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
  echo "invalid Docker Hub image name: '${image_name}'" >&2
  exit 1
fi
if [[ ! -f "${keep_file}" ]]; then
  echo "keep-tag file does not exist: ${keep_file}" >&2
  exit 1
fi

candidates=(latest stable stable-previous)
while IFS= read -r tag; do
  [[ -z "${tag}" || "${tag}" == \#* ]] && continue
  candidates+=("${tag}")
done < "${keep_file}"

for tag in "${candidates[@]}"; do
  response="$("${curl_bin}" --silent --show-error --location \
    --write-out $'\n%{http_code}' \
    "https://hub.docker.com/v2/repositories/${image_name}/tags/${tag}")"
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [[ "${status}" == '404' ]]; then
    continue
  fi
  if [[ "${status}" != '200' ]]; then
    echo "Docker Hub returned HTTP ${status} for rollback tag ${tag}" >&2
    exit 1
  fi
  digest="$(jq -r '.digest // empty' <<< "${body}")"
  if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "rollback tag ${tag} has an invalid digest" >&2
    exit 1
  fi
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      echo "source_tag=${tag}"
      echo "digest=${digest}"
      echo "image_ref=${image_name}@${digest}"
    } >> "${GITHUB_OUTPUT}"
  fi
  echo "Rollback source: ${tag}@${digest}"
  exit 0
done

echo 'no verified rollback tag candidate exists' >&2
exit 1
