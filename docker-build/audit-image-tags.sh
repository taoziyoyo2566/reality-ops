#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image_name="${1:-taoziyoyo2566/xray_docker}"
output_file="$(mktemp /tmp/xray-tag-audit-discovery.XXXXXX)"
trap 'rm -f -- "${output_file}"' EXIT

GITHUB_OUTPUT="${output_file}" bash "${repo_root}/docker-build/discover-release-window.sh" \
  "${image_name}" "${repo_root}/docker-build/XRAY_IMAGE_REVISIONS.json" >/dev/null

read_output() {
  sed -n "s/^${1}=//p" "${output_file}"
}
expected_tags="$(read_output expected_tags)"
prerelease_versions="$(read_output prerelease_versions)"
registry_tags="$(read_output registry_tags)"

declare -A expected=()
while IFS= read -r tag; do
  expected["${tag}"]=1
done < <(jq -r '.[]' <<< "${expected_tags}")
expected[latest]=1

declare -A prerelease=()
while IFS= read -r version; do
  prerelease["${version}"]=1
done < <(jq -r '.[]' <<< "${prerelease_versions}")

declare -A seen=()
kept=()
cleanup=()
review=()
while IFS=$'\t' read -r tag digest; do
  seen["${tag}"]=1
  base_version="${tag%%-r[1-9]*}"
  if [[ -n "${prerelease[${base_version}]:-}" && "${tag}" != *-beta* ]]; then
    review+=("${tag}"$'\t'"${digest}")
  elif [[ -n "${expected[${tag}]:-}" ||
          "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-beta)?(-r[1-9][0-9]*)?$ ]]; then
    kept+=("${tag}"$'\t'"${digest}")
  elif [[ "${tag}" == build-* || "${tag}" =~ ^[0-9a-f]{40}$ ]]; then
    cleanup+=("${tag}"$'\t'"${digest}")
  else
    review+=("${tag}"$'\t'"${digest}")
  fi
done < <(jq -r '.[] | [.name, .digest] | @tsv' <<< "${registry_tags}" | sort)

missing=()
for tag in "${!expected[@]}"; do
  if [[ -z "${seen[${tag}]:-}" ]]; then
    missing+=("${tag}")
  fi
done
IFS=$'\n' missing=($(sort <<< "${missing[*]:-}"))
unset IFS
[[ "${#missing[@]}" -eq 1 && -z "${missing[0]}" ]] && missing=()

print_group() {
  local title="$1"
  shift
  echo "## ${title}"
  if [[ "$#" -eq 0 ]]; then echo '(none)'; else printf '%s\n' "$@"; fi
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
