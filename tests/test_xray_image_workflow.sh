#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="${repo_root}/.github/workflows/build-image.yml"

grep -F 'name: Sync Xray Release Images' "${workflow}" >/dev/null
grep -F 'bash docker-build/discover-release-window.sh' "${workflow}" >/dev/null
grep -F 'matrix: ${{ fromJSON(needs.discover.outputs.matrix) }}' "${workflow}" >/dev/null
grep -F 'max-parallel: 2' "${workflow}" >/dev/null
grep -F 'schedule:' "${workflow}" >/dev/null
grep -F 'workflow_dispatch:' "${workflow}" >/dev/null

if grep -F 'push:' "${workflow}" >/dev/null; then
  echo 'merging workflow changes triggers an implicit registry synchronization' >&2
  exit 1
fi
if grep -Eq 'type=raw,value=build-|--tag .*build-' "${workflow}"; then
  echo 'workflow publishes a public build-revision tag' >&2
  exit 1
fi

build_line="$(grep -nF -- '- name: Build and push image candidate' "${workflow}" | cut -d: -f1)"
verify_line="$(grep -nF -- '- name: Verify pushed multi-platform image' "${workflow}" | cut -d: -f1)"
publish_line="$(grep -nF -- '- name: Publish immutable version tag' "${workflow}" | cut -d: -f1)"
if (( build_line >= verify_line || verify_line >= publish_line )); then
  echo 'image publication steps are not ordered build, verify, publish' >&2
  exit 1
fi

build_block="$(sed -n "${build_line},$((verify_line - 1))p" "${workflow}")"
if grep -Eq '^[[:space:]]+tags:' <<< "${build_block}"; then
  echo 'candidate build publishes tags before verification' >&2
  exit 1
fi

grep -F 'bash docker-build/check-image-tag-available.sh' "${workflow}" >/dev/null
# shellcheck disable=SC2016
grep -F -- '--tag "${IMAGE_NAME}:${IMAGE_TAG}"' "${workflow}" >/dev/null
# shellcheck disable=SC2016
grep -F -- '--tag "${IMAGE_NAME}:latest"' "${workflow}" >/dev/null
for removed_tag in stable prerelease stable-previous; do
  # shellcheck disable=SC2016
  if grep -F -- "--tag \"\${IMAGE_NAME}:${removed_tag}\"" "${workflow}" >/dev/null; then
    echo "workflow publishes removed floating tag: ${removed_tag}" >&2
    exit 1
  fi
done

if [[ -e "${repo_root}/.github/workflows/promote-xray-stable.yml" ]]; then
  echo 'unsafe alias-only stable promotion workflow still exists' >&2
  exit 1
fi
if [[ -e "${repo_root}/.github/workflows/test.yml" ]]; then
  echo 'obsolete Docker Hub push-test workflow still exists' >&2
  exit 1
fi

echo 'Xray release synchronization workflow tests passed'
