#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_dir="$(mktemp -d /tmp/xray-image-tag-audit-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT

tags_json="${fixture_dir}/tags.json"
keep_file="${fixture_dir}/keep-tags"
output_file="${fixture_dir}/output"
github_output="${fixture_dir}/github-output"

cat > "${tags_json}" <<'JSON'
{"results":[
  {"name":"latest","digest":"sha256:a"},
  {"name":"stable","digest":"sha256:a"},
  {"name":"prerelease","digest":"sha256:g"},
  {"name":"stable-previous","digest":"sha256:b"},
  {"name":"v26.3.27","digest":"sha256:a"},
  {"name":"v26.7.28-prerelease","digest":"sha256:g"},
  {"name":"v26.7.28","digest":"sha256:g"},
  {"name":"build-deadbeef-xray-v26.3.27","digest":"sha256:c"},
  {"name":"1111111111111111111111111111111111111111","digest":"sha256:d"},
  {"name":"2222222222222222222222222222222222222222","digest":"sha256:e"},
  {"name":"unexpected","digest":"sha256:f"}
]}
JSON
printf '%s\n' '2222222222222222222222222222222222222222' > "${keep_file}"

TAG_JSON_FILE="${tags_json}" GITHUB_OUTPUT="${github_output}" \
  bash "${repo_root}/docker-build/audit-image-tags.sh" \
    taoziyoyo2566/xray_docker "${keep_file}" > "${output_file}"

grep -F $'build-deadbeef-xray-v26.3.27\tsha256:c' "${output_file}" >/dev/null
grep -F $'1111111111111111111111111111111111111111\tsha256:d' "${output_file}" >/dev/null
grep -F $'2222222222222222222222222222222222222222\tsha256:e' "${output_file}" >/dev/null
grep -F $'unexpected\tsha256:f' "${output_file}" >/dev/null
grep -Fx 'retain_count=7' "${github_output}" >/dev/null
grep -Fx 'cleanup_count=2' "${github_output}" >/dev/null
grep -Fx 'review_count=2' "${github_output}" >/dev/null
grep -Fx 'missing_count=0' "${github_output}" >/dev/null

cat > "${tags_json}" <<'JSON'
{"results":[
  {"name":"prerelease","digest":"sha256:g"},
  {"name":"v26.7.28-prerelease","digest":"sha256:g"},
  {"name":"2222222222222222222222222222222222222222","digest":"sha256:e"}
]}
JSON
: > "${github_output}"
TAG_JSON_FILE="${tags_json}" GITHUB_OUTPUT="${github_output}" \
  bash "${repo_root}/docker-build/audit-image-tags.sh" \
    taoziyoyo2566/xray_docker "${keep_file}" > "${output_file}"
grep -Fx 'latest' "${output_file}" >/dev/null
grep -Fx 'stable' "${output_file}" >/dev/null
grep -Fx 'v26.3.27' "${output_file}" >/dev/null
grep -Fx 'missing_count=3' "${github_output}" >/dev/null

echo 'Xray image tag audit tests passed'
