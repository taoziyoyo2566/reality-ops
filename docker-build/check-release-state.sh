#!/usr/bin/env bash
set -euo pipefail

channel="${1:-}"
version="${2:-}"
actual_prerelease="${3:-}"
actual_draft="${4:-}"
latest_version="${5:-}"

if [[ "${channel}" != 'stable' && "${channel}" != 'prerelease' ]]; then
  echo "channel must be stable or prerelease: '${channel}'" >&2
  exit 1
fi
if [[ ! "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "version must use vX.Y.Z form: '${version}'" >&2
  exit 1
fi
if [[ "${actual_prerelease}" != 'true' && "${actual_prerelease}" != 'false' ]]; then
  echo "release prerelease state must be true or false: '${actual_prerelease}'" >&2
  exit 1
fi
if [[ "${actual_draft}" != 'true' && "${actual_draft}" != 'false' ]]; then
  echo "release draft state must be true or false: '${actual_draft}'" >&2
  exit 1
fi
if [[ "${actual_draft}" == 'true' ]]; then
  echo "${version} is a draft release" >&2
  exit 1
fi

if [[ "${channel}" == 'stable' ]]; then
  if [[ "${actual_prerelease}" != 'false' ]]; then
    echo "stable pin ${version} is a prerelease" >&2
    exit 1
  fi
  if [[ "${version}" != "${latest_version}" ]]; then
    echo "stable pin ${version} is not upstream latest ${latest_version}" >&2
    exit 1
  fi
elif [[ "${actual_prerelease}" != 'true' ]]; then
  echo "prerelease pin ${version} is no longer an upstream prerelease; select a current prerelease" >&2
  exit 1
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo 'publish=true' >> "${GITHUB_OUTPUT}"
fi

echo "Release state accepted for ${channel} ${version}; publish=true"
