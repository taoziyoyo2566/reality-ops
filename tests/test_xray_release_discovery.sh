#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_dir="$(mktemp -d /tmp/xray-release-discovery-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT
releases="${fixture_dir}/releases.json"
tags="${fixture_dir}/tags.json"
revisions="${fixture_dir}/revisions.json"
output="${fixture_dir}/output"

jq -n '
  def asset($name; $char): {name: $name, digest: ("sha256:" + ($char * 64))};
  def release($tag; $pre; $char): {
    tag_name: $tag, prerelease: $pre, draft: false, published_at: "2026-01-01T00:00:00Z",
    assets: [asset("Xray-linux-64.zip"; $char), asset("Xray-linux-arm64-v8a.zip"; $char)]
  };
  [release("v26.4.2"; true; "c"), release("v26.4.1"; true; "b"),
   release("v26.3.27"; false; "a"), release("v26.3.23"; true; "d")]
' > "${releases}"
printf '%s\n' '{"v26.4.1":1}' > "${revisions}"
printf '%s\n' '{"results":[{"name":"v26.3.27","digest":"sha256:a"},{"name":"v26.4.1-beta-r1","digest":"sha256:b"}]}' > "${tags}"

RELEASES_JSON_FILE="${releases}" TAG_JSON_FILE="${tags}" GITHUB_OUTPUT="${output}" \
  bash "${repo_root}/docker-build/discover-release-window.sh" \
    taoziyoyo2566/xray_docker "${revisions}" >/dev/null

grep -Fx 'stable_version=v26.3.27' "${output}" >/dev/null
grep -Fx 'stable_tag=v26.3.27' "${output}" >/dev/null
grep -Fx 'expected_tags=["v26.4.2-beta","v26.4.1-beta-r1","v26.3.27"]' "${output}" >/dev/null
grep -Fx 'prerelease_versions=["v26.4.2","v26.4.1"]' "${output}" >/dev/null
grep -Fx 'missing_count=1' "${output}" >/dev/null
jq -e '.include[0].image_tag == "v26.4.2-beta" and .include[0].amd64_sha == ("c" * 64)' \
  <<< "$(sed -n 's/^matrix=//p' "${output}")" >/dev/null

printf '%s\n' '{"invalid":-1}' > "${revisions}"
if RELEASES_JSON_FILE="${releases}" TAG_JSON_FILE="${tags}" \
  bash "${repo_root}/docker-build/discover-release-window.sh" \
    taoziyoyo2566/xray_docker "${revisions}" >/dev/null 2>&1; then
  echo 'invalid image revisions ledger was accepted' >&2
  exit 1
fi

echo 'Xray release discovery tests passed'
