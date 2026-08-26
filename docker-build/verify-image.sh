#!/usr/bin/env bash
set -euo pipefail

image_ref="${1:-}"
expected_version="${2:-}"
docker_bin="${DOCKER_BIN:-docker}"

if [[ ! "${image_ref}" =~ ^[^@[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "image reference must use a sha256 digest: '${image_ref}'" >&2
  exit 1
fi

if [[ -n "${expected_version}" && ! "${expected_version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "expected version must use vX.Y.Z form: '${expected_version}'" >&2
  exit 1
fi

manifest_json="$("${docker_bin}" buildx imagetools inspect --raw "${image_ref}")"
manifest_rows="$(jq -r '
  [.manifests[]?
   | select(.platform.os == "linux")
   | [(.platform.os + "/" + .platform.architecture), .digest]]
  | sort_by(.[0])
  | .[]
  | @tsv
' <<< "${manifest_json}")"
platforms="$(cut -f1 <<< "${manifest_rows}" | paste -sd, -)"

if [[ "${platforms}" != 'linux/amd64,linux/arm64' ]]; then
  echo "image must contain exactly the required Linux architectures; found '${platforms}'" >&2
  exit 1
fi

image_repository="${image_ref%@sha256:*}"
verified_version=""
while IFS=$'\t' read -r platform manifest_digest; do
  if [[ ! "${manifest_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "${platform} manifest has an invalid digest: '${manifest_digest}'" >&2
    exit 1
  fi

  platform_ref="${image_repository}@${manifest_digest}"
  first_line="$("${docker_bin}" run --rm \
    --platform "${platform}" \
    --entrypoint /usr/bin/xray \
    "${platform_ref}" -version | sed -n '1p')"
  actual_version="$(awk '$1 == "Xray" { print "v" $2; exit }' <<< "${first_line}")"

  if [[ ! "${actual_version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "could not read Xray version for ${platform}: '${first_line}'" >&2
    exit 1
  fi
  if [[ -n "${expected_version}" && "${actual_version}" != "${expected_version}" ]]; then
    echo "${platform} contains ${actual_version}, expected ${expected_version}" >&2
    exit 1
  fi
  if [[ -n "${verified_version}" && "${actual_version}" != "${verified_version}" ]]; then
    echo "platform versions differ: ${verified_version} vs ${actual_version}" >&2
    exit 1
  fi
  verified_version="${actual_version}"
done <<< "${manifest_rows}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "version=${verified_version}"
    echo "platforms=${platforms}"
    echo "image_ref=${image_ref}"
  } >> "${GITHUB_OUTPUT}"
fi

echo "Verified ${image_ref}: ${verified_version} (${platforms})"
