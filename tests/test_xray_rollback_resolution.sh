#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_dir="$(mktemp -d /tmp/xray-rollback-resolution-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT

mock_curl="${fixture_dir}/curl"
cat > "${mock_curl}" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
tag="${!#}"
tag="${tag##*/}"
case "${MOCK_MODE:-latest}:${tag}" in
  latest:latest) printf '{"digest":"sha256:%064d"}\n200' 1 ;;
  fallback:latest|fallback:stable|fallback:stable-previous) printf '{}\n404' ;;
  fallback:keep-me) printf '{"digest":"sha256:%064d"}\n200' 2 ;;
  invalid:latest) printf '{"digest":"bad"}\n200' ;;
  *) printf '{}\n404' ;;
esac
MOCK
chmod 755 "${mock_curl}"

keep_file="${fixture_dir}/keep"
printf '%s\n' keep-me > "${keep_file}"
output="${fixture_dir}/output"

GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=latest \
  bash "${repo_root}/docker-build/resolve-rollback-image.sh" \
    taoziyoyo2566/xray_docker "${keep_file}" >/dev/null
grep -Fx 'source_tag=latest' "${output}" >/dev/null

: > "${output}"
GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=fallback \
  bash "${repo_root}/docker-build/resolve-rollback-image.sh" \
    taoziyoyo2566/xray_docker "${keep_file}" >/dev/null
grep -Fx 'source_tag=keep-me' "${output}" >/dev/null

for mode in invalid empty; do
  if CURL_BIN="${mock_curl}" MOCK_MODE="${mode}" \
    bash "${repo_root}/docker-build/resolve-rollback-image.sh" \
      taoziyoyo2566/xray_docker "${keep_file}" >/dev/null 2>&1; then
    echo "invalid rollback state was accepted: ${mode}" >&2
    exit 1
  fi
done

echo 'Xray rollback resolution tests passed'
