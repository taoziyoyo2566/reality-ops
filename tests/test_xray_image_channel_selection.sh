#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
selector="${repo_root}/docker-build/select-image-channels.sh"
fixture_dir="$(mktemp -d /tmp/xray-image-channel-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT
output="${fixture_dir}/output"

GITHUB_OUTPUT="${output}" bash "${selector}" workflow_dispatch stable >/dev/null
grep -Fx 'stable=true' "${output}" >/dev/null
grep -Fx 'prerelease=false' "${output}" >/dev/null

: > "${output}"
GITHUB_OUTPUT="${output}" bash "${selector}" workflow_dispatch all >/dev/null
grep -Fx 'stable=true' "${output}" >/dev/null
grep -Fx 'prerelease=true' "${output}" >/dev/null

: > "${output}"
printf '%s\n' docker-build/XRAY_PRERELEASE_VERSION \
  | GITHUB_OUTPUT="${output}" bash "${selector}" push >/dev/null
grep -Fx 'stable=false' "${output}" >/dev/null
grep -Fx 'prerelease=true' "${output}" >/dev/null

: > "${output}"
printf '%s\n' docker-build/dockerfile \
  | GITHUB_OUTPUT="${output}" bash "${selector}" push >/dev/null
grep -Fx 'stable=true' "${output}" >/dev/null
grep -Fx 'prerelease=true' "${output}" >/dev/null

if bash "${selector}" workflow_dispatch invalid >/dev/null 2>&1; then
  echo 'invalid manual channel was accepted' >&2
  exit 1
fi

echo 'Xray image channel selection tests passed'
