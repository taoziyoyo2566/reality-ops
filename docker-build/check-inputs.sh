#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version_file="${1:-${repo_root}/docker-build/XRAY_VERSION}"
checksums_file="${2:-${repo_root}/docker-build/XRAY_SHA256SUMS}"

version="$(tr -d '[:space:]' < "${version_file}")"
if [[ ! "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "invalid Xray version in ${version_file}: '${version}'" >&2
  exit 1
fi

required_assets=(
  Xray-linux-64.zip
  Xray-linux-arm64-v8a.zip
)

declare -A seen=()
line_count=0
while read -r digest asset extra; do
  [[ -z "${digest}${asset}${extra}" ]] && continue
  line_count=$((line_count + 1))

  if [[ -n "${extra}" || ! "${digest}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "invalid SHA256 line in ${checksums_file}: '${digest} ${asset} ${extra}'" >&2
    exit 1
  fi

  case "${asset}" in
    Xray-linux-64.zip|Xray-linux-arm64-v8a.zip) ;;
    *)
      echo "unexpected Xray asset in ${checksums_file}: '${asset}'" >&2
      exit 1
      ;;
  esac

  if [[ -n "${seen[${asset}]:-}" ]]; then
    echo "duplicate Xray asset in ${checksums_file}: '${asset}'" >&2
    exit 1
  fi
  seen["${asset}"]="${digest}"
done < "${checksums_file}"

if [[ "${line_count}" -ne "${#required_assets[@]}" ]]; then
  echo "expected ${#required_assets[@]} checksum lines in ${checksums_file}, found ${line_count}" >&2
  exit 1
fi

for asset in "${required_assets[@]}"; do
  if [[ -z "${seen[${asset}]:-}" ]]; then
    echo "missing checksum for ${asset} in ${checksums_file}" >&2
    exit 1
  fi
done

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "version=${version}"
    echo "amd64_sha=${seen[Xray-linux-64.zip]}"
    echo "arm64_sha=${seen[Xray-linux-arm64-v8a.zip]}"
  } >> "${GITHUB_OUTPUT}"
fi

echo "Xray image inputs valid: ${version} (${required_assets[*]})"
