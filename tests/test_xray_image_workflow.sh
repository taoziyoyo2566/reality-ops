#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="${repo_root}/.github/workflows/build-image.yml"

# shellcheck disable=SC2016 # Match the literal GitHub expression.
grep -F 'outputs: type=image,name=${{ env.IMAGE_NAME }},push-by-digest=true,name-canonical=true,push=true' \
  "${workflow}" >/dev/null

if grep -Eq 'type=raw,value=build-|--tag .*build-' "${workflow}"; then
  echo 'workflow publishes a public build-revision tag' >&2
  exit 1
fi

build_line="$(grep -nF -- '- name: Build and push image' "${workflow}" | cut -d: -f1)"
verify_line="$(grep -nF -- '- name: Verify pushed multi-platform image' "${workflow}" | cut -d: -f1)"
publish_line="$(grep -nF -- '- name: Preserve rollback and publish verified tags' "${workflow}" | cut -d: -f1)"

if (( build_line >= verify_line || verify_line >= publish_line )); then
  echo 'image publication steps are not ordered build, verify, publish' >&2
  exit 1
fi

build_block="$(sed -n "${build_line},$((verify_line - 1))p" "${workflow}")"
if grep -Eq '^[[:space:]]+tags:' <<< "${build_block}"; then
  echo 'candidate build publishes tags before verification' >&2
  exit 1
fi

# shellcheck disable=SC2016 # Match literal stable and prerelease workflow tags.
grep -F -- '--tag "${IMAGE_NAME}:${VERSION}"' "${workflow}" >/dev/null
# shellcheck disable=SC2016
grep -F -- '--tag "${IMAGE_NAME}:${VERSION}-prerelease"' "${workflow}" >/dev/null
# shellcheck disable=SC2016
grep -F 'echo "${IMAGE_NAME}:${VERSION}-prerelease"' "${workflow}" >/dev/null

grep -F 'steps.select.outputs.selected' "${workflow}" >/dev/null
grep -F 'bash docker-build/select-image-channels.sh' "${workflow}" >/dev/null
grep -F 'default: stable' "${workflow}" >/dev/null
if grep -F -- "- '.github/workflows/build-image.yml'" "${workflow}" >/dev/null; then
  echo 'workflow implementation changes trigger a registry build automatically' >&2
  exit 1
fi
# shellcheck disable=SC2016 # Match the literal workflow shell expression.
grep -F -- '--tag "${IMAGE_NAME}:stable-previous"' "${workflow}" >/dev/null

repair_workflow="${repo_root}/.github/workflows/repair-xray-image-tags.yml"
audit_workflow="${repo_root}/.github/workflows/audit-xray-image-tags.yml"
stable_check_workflow="${repo_root}/.github/workflows/check-xray-stable.yml"

if [[ -e "${repo_root}/.github/workflows/promote-xray-stable.yml" ]]; then
  echo 'unsafe alias-only stable promotion workflow still exists' >&2
  exit 1
fi

if grep -F 'docker/build-push-action' "${repair_workflow}" >/dev/null; then
  echo 'tag repair workflow rebuilds an image' >&2
  exit 1
fi
grep -F 'bash docker-build/verify-image.sh' "${repair_workflow}" >/dev/null

if grep -E 'docker/login-action|imagetools create|build-push-action' "${audit_workflow}" >/dev/null; then
  echo 'read-only tag audit workflow contains a registry write action' >&2
  exit 1
fi

if grep -E 'docker/login-action|imagetools create|build-push-action' "${stable_check_workflow}" >/dev/null; then
  echo 'read-only stable check workflow contains a registry write action' >&2
  exit 1
fi
grep -F 'A final release must be rebuilt from its final official assets.' \
  "${stable_check_workflow}" >/dev/null

if [[ -e "${repo_root}/.github/workflows/test.yml" ]]; then
  echo 'obsolete Docker Hub push-test workflow still exists' >&2
  exit 1
fi

echo 'Xray image workflow publication-policy tests passed'
