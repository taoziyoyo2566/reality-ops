#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_dir="$(mktemp -d /tmp/xray-image-repair-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT

mock_curl="${fixture_dir}/curl"
cat > "${mock_curl}" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
url="${!#}"
tag="${url##*/}"
digest_for() {
  local char="$1"
  printf 'sha256:%064d' "${char}"
}
case "${MOCK_MODE:-complete}:${tag}" in
  complete:v26.3.27) printf '{"digest":"%s"}\n200' "$(digest_for 1)" ;;
  complete:stable) printf '{"digest":"%s"}\n200' "$(digest_for 1)" ;;
  complete:latest) printf '{"digest":"%s"}\n200' "$(digest_for 2)" ;;
  missing-version:v26.3.27) printf '{}\n404' ;;
  missing-version:stable|missing-version:latest) printf '{"digest":"%s"}\n200' "$(digest_for 3)" ;;
  prerelease:v26.7.28-prerelease) printf '{}\n404' ;;
  prerelease:prerelease) printf '{"digest":"%s"}\n200' "$(digest_for 4)" ;;
  invalid:v26.3.27) printf '{"digest":"bad"}\n200' ;;
  invalid:stable|invalid:latest|empty:*) printf '{}\n404' ;;
  *) printf '{}\n404' ;;
esac
MOCK
chmod 755 "${mock_curl}"

output="${fixture_dir}/output"
GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=complete \
  bash "${repo_root}/docker-build/resolve-image-repair.sh" \
    taoziyoyo2566/xray_docker stable v26.3.27 >/dev/null
grep -Fx 'source_tag=v26.3.27' "${output}" >/dev/null
grep -Fx 'previous_ref=taoziyoyo2566/xray_docker@sha256:0000000000000000000000000000000000000000000000000000000000000002' "${output}" >/dev/null

: > "${output}"
GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=missing-version \
  bash "${repo_root}/docker-build/resolve-image-repair.sh" \
    taoziyoyo2566/xray_docker stable v26.3.27 >/dev/null
grep -Fx 'source_tag=stable' "${output}" >/dev/null
grep -Fx 'version_exists=false' "${output}" >/dev/null

: > "${output}"
GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=prerelease \
  bash "${repo_root}/docker-build/resolve-image-repair.sh" \
    taoziyoyo2566/xray_docker prerelease v26.7.28 >/dev/null
grep -Fx 'source_tag=prerelease' "${output}" >/dev/null

: > "${output}"
GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=complete \
  bash "${repo_root}/docker-build/resolve-image-repair.sh" \
    taoziyoyo2566/xray_docker prerelease v26.7.28 \
    sha256:6666666666666666666666666666666666666666666666666666666666666666 >/dev/null
grep -Fx 'source_tag=explicit-digest' "${output}" >/dev/null

: > "${output}"
GITHUB_OUTPUT="${output}" CURL_BIN="${mock_curl}" MOCK_MODE=empty \
  bash "${repo_root}/docker-build/resolve-image-repair.sh" \
    taoziyoyo2566/xray_docker stable v26.3.27 \
    sha256:5555555555555555555555555555555555555555555555555555555555555555 >/dev/null
grep -Fx 'source_tag=explicit-digest' "${output}" >/dev/null
grep -Fx 'source_digest=sha256:5555555555555555555555555555555555555555555555555555555555555555' "${output}" >/dev/null

for mode in invalid empty; do
  if CURL_BIN="${mock_curl}" MOCK_MODE="${mode}" \
    bash "${repo_root}/docker-build/resolve-image-repair.sh" \
      taoziyoyo2566/xray_docker stable v26.3.27 >/dev/null 2>&1; then
    echo "invalid repair source was accepted: ${mode}" >&2
    exit 1
  fi
done

echo 'Xray image repair resolution tests passed'
