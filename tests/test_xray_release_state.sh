#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
checker="${repo_root}/docker-build/check-release-state.sh"
fixture_dir="$(mktemp -d /tmp/xray-release-state-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT

github_output="${fixture_dir}/github-output"

GITHUB_OUTPUT="${github_output}" bash "${checker}" \
  stable v26.3.27 false false v26.3.27 >/dev/null
grep -Fx 'publish=true' "${github_output}" >/dev/null

: > "${github_output}"
GITHUB_OUTPUT="${github_output}" bash "${checker}" \
  prerelease v26.7.28 true false '' >/dev/null
grep -Fx 'publish=true' "${github_output}" >/dev/null

for invalid_case in \
  'stable v26.3.27 true false v26.3.27' \
  'stable v26.3.27 false true v26.3.27' \
  'stable v26.3.27 false false v26.4.1' \
  'prerelease v26.7.28 false false empty' \
  'prerelease invalid true false empty'; do
  read -r -a args <<< "${invalid_case}"
  if [[ "${args[4]}" == 'empty' ]]; then
    args[4]=''
  fi
  if bash "${checker}" "${args[@]}" >/dev/null 2>&1; then
    echo "invalid release state was accepted: ${invalid_case}" >&2
    exit 1
  fi
done

echo 'Xray release state tests passed'
