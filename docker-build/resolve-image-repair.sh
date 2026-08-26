#!/usr/bin/env bash
set -euo pipefail

image_name="${1:-}"
channel="${2:-}"
version="${3:-}"
explicit_digest="${4:-}"
curl_bin="${CURL_BIN:-curl}"

if [[ ! "${image_name}" =~ ^[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
  echo "invalid Docker Hub image name: '${image_name}'" >&2
  exit 1
fi
if [[ "${channel}" != 'stable' && "${channel}" != 'prerelease' ]]; then
  echo "channel must be stable or prerelease: '${channel}'" >&2
  exit 1
fi
if [[ ! "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "version must use vX.Y.Z form: '${version}'" >&2
  exit 1
fi
if [[ -n "${explicit_digest}" && ! "${explicit_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "explicit source digest is invalid: '${explicit_digest}'" >&2
  exit 1
fi

lookup_digest=''
lookup_tag() {
  local tag="$1"
  local response status body digest
  if ! response="$("${curl_bin}" --silent --show-error --location \
    --write-out $'\n%{http_code}' \
    "https://hub.docker.com/v2/repositories/${image_name}/tags/${tag}")"; then
    echo "failed to query Docker Hub tag ${tag}" >&2
    return 2
  fi
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [[ "${status}" == '404' ]]; then
    return 1
  fi
  if [[ "${status}" != '200' ]]; then
    echo "Docker Hub returned HTTP ${status} for tag ${tag}" >&2
    return 2
  fi
  digest="$(jq -r '.digest // empty' <<< "${body}")"
  if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Docker Hub tag ${tag} has an invalid digest" >&2
    return 2
  fi
  lookup_digest="${digest}"
}

read_optional_tag() {
  local tag="$1"
  local output_name="$2"
  local rc
  lookup_digest=''
  if lookup_tag "${tag}"; then
    printf -v "${output_name}" '%s' "${lookup_digest}"
    return
  else
    rc=$?
  fi
  if [[ "${rc}" -ne 1 ]]; then
    exit "${rc}"
  fi
  printf -v "${output_name}" '%s' ''
}

version_tag="${version}"
if [[ "${channel}" == 'prerelease' ]]; then
  version_tag="${version}-prerelease"
fi

version_digest=''
channel_digest=''
latest_digest=''
read_optional_tag "${version_tag}" version_digest
read_optional_tag "${channel}" channel_digest
if [[ "${channel}" == 'stable' ]]; then
  read_optional_tag latest latest_digest
fi

source_tag=''
source_digest=''
if [[ -n "${explicit_digest}" ]]; then
  source_tag='explicit-digest'
  source_digest="${explicit_digest}"
elif [[ -n "${version_digest}" ]]; then
  source_tag="${version_tag}"
  source_digest="${version_digest}"
elif [[ -n "${channel_digest}" ]]; then
  source_tag="${channel}"
  source_digest="${channel_digest}"
elif [[ "${channel}" == 'stable' && -n "${latest_digest}" ]]; then
  source_tag='latest'
  source_digest="${latest_digest}"
else
  echo "no existing ${version_tag} or ${channel} image can repair ${channel}" >&2
  exit 1
fi

previous_ref=''
if [[ "${channel}" == 'stable' && -n "${latest_digest}" && "${latest_digest}" != "${source_digest}" ]]; then
  previous_ref="${image_name}@${latest_digest}"
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "source_tag=${source_tag}"
    echo "source_digest=${source_digest}"
    echo "image_ref=${image_name}@${source_digest}"
    echo "previous_ref=${previous_ref}"
    echo "version_exists=$([[ -n "${version_digest}" ]] && echo true || echo false)"
  } >> "${GITHUB_OUTPUT}"
fi

echo "Repair source for ${channel} ${version_tag}: ${source_tag}@${source_digest}"
