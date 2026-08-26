#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image_name="${1:-taoziyoyo2566/xray_docker}"
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

if [[ -n "${TAG_JSON_FILE:-}" ]]; then
  tags_json="$(< "${TAG_JSON_FILE}")"
else
  tags_json='{"results":[]}'
  next_url="https://hub.docker.com/v2/repositories/${image_name}/tags?page_size=100"
  while [[ -n "${next_url}" ]]; do
    page_json="$("${curl_bin}" --fail --silent --show-error --location "${next_url}")"
    if ! jq -e '.results | type == "array"' <<< "${page_json}" >/dev/null; then
      echo 'Docker Hub tag page does not contain a results array' >&2
      exit 1
    fi
    tags_json="$(jq -cn --argjson current "${tags_json}" --argjson page "${page_json}" \
      '{results: ($current.results + $page.results)}')"
    next_url="$(jq -r '.next // empty' <<< "${page_json}")"
  done
fi

if ! jq -e '.results | type == "array"' <<< "${tags_json}" >/dev/null; then
  echo 'Docker Hub tag response does not contain a results array' >&2
  exit 1
fi

declare -A explicit_keep=()
while IFS= read -r tag; do
  [[ -z "${tag}" || "${tag}" == \#* ]] && continue
  explicit_keep["${tag}"]=1
done < "${keep_file}"

stable_version="$(tr -d '[:space:]' < "${repo_root}/docker-build/XRAY_VERSION")"
prerelease_version="$(tr -d '[:space:]' < "${repo_root}/docker-build/XRAY_PRERELEASE_VERSION")"
prerelease_tag="${prerelease_version}-prerelease"
required_tags=(latest stable prerelease "${stable_version}" "${prerelease_tag}")
declare -A seen_tags=()
kept=()
cleanup=()
review=()
while IFS=$'\t' read -r tag digest; do
  seen_tags["${tag}"]=1
  if [[ "${tag}" == "${prerelease_version}" &&
        "${tag}" != "${stable_version}" ]]; then
    review+=("${tag}"$'\t'"${digest}")
  elif [[ "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-prerelease)?$ ||
        "${tag}" == 'stable' || "${tag}" == 'latest' ||
        "${tag}" == 'prerelease' || "${tag}" == 'stable-previous' ||
        -n "${explicit_keep[${tag}]:-}" ]]; then
    kept+=("${tag}"$'\t'"${digest}")
  elif [[ "${tag}" == build-* || "${tag}" =~ ^[0-9a-f]{40}$ ]]; then
    cleanup+=("${tag}"$'\t'"${digest}")
  else
    review+=("${tag}"$'\t'"${digest}")
  fi
done < <(jq -r '.results[] | [.name, .digest] | @tsv' <<< "${tags_json}" | sort)

missing=()
for tag in "${required_tags[@]}"; do
  if [[ -z "${seen_tags[${tag}]:-}" ]]; then
    missing+=("${tag}")
  fi
done

print_group() {
  local title="$1"
  shift
  echo "## ${title}"
  if [[ "$#" -eq 0 ]]; then
    echo '(none)'
  else
    printf '%s\n' "$@"
  fi
  echo
}

print_group 'Retain' "${kept[@]}"
print_group 'Cleanup candidates' "${cleanup[@]}"
print_group 'Manual review' "${review[@]}"
print_group 'Missing required tags' "${missing[@]}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "retain_count=${#kept[@]}"
    echo "cleanup_count=${#cleanup[@]}"
    echo "review_count=${#review[@]}"
    echo "missing_count=${#missing[@]}"
  } >> "${GITHUB_OUTPUT}"
fi
