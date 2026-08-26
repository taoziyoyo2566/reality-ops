#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
availability="${repo_root}/docker-build/check-image-tag-available.sh"
overview="${repo_root}/docker-build/README.md"
fixture_dir="$(mktemp -d /tmp/xray-image-tags-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT

grep -F 'All versioned tags are immutable.' "${overview}" >/dev/null
grep -F '`latest` is the only moving tag.' "${overview}" >/dev/null
grep -F '`vX.Y.Z-beta-rN`' "${overview}" >/dev/null

mock_curl="${fixture_dir}/curl"
cat > "${mock_curl}" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
case "${MOCK_STATUS:-404}" in
  200) printf '{}\n200' ;;
  404) printf '{}\n404' ;;
  *) printf '{}\n500' ;;
esac
MOCK
chmod 755 "${mock_curl}"

CURL_BIN="${mock_curl}" MOCK_STATUS=404 \
  bash "${availability}" taoziyoyo2566/xray_docker v26.3.27-r1 >/dev/null
for status in 200 500; do
  if CURL_BIN="${mock_curl}" MOCK_STATUS="${status}" \
    bash "${availability}" taoziyoyo2566/xray_docker v26.3.27-r1 \
      >/dev/null 2>&1; then
    echo "unavailable immutable tag was accepted for HTTP ${status}" >&2
    exit 1
  fi
done

echo 'Xray immutable image tag tests passed'
