#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_dir="$(mktemp -d /tmp/xray-image-verify-test.XXXXXX)"
trap 'rm -rf -- "${fixture_dir}"' EXIT

mock_docker="${fixture_dir}/docker"
cat > "${mock_docker}" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail

if [[ "$1 $2 $3" == 'buildx imagetools inspect' && "$4" == '--raw' ]]; then
  case "${MOCK_MANIFEST_MODE:-complete}" in
    missing-arm64)
      printf '%s\n' '{"manifests":[{"platform":{"os":"linux","architecture":"amd64"}}]}'
      ;;
    extra-linux-platform)
      printf '%s\n' '{"manifests":[{"platform":{"os":"linux","architecture":"amd64"}},{"platform":{"os":"linux","architecture":"arm64"}},{"platform":{"os":"linux","architecture":"riscv64"}}]}'
      ;;
    complete)
      printf '%s\n' '{"manifests":[{"platform":{"os":"linux","architecture":"amd64"}},{"platform":{"os":"linux","architecture":"arm64"}},{"platform":{"os":"unknown","architecture":"unknown"}}]}'
      ;;
    *) exit 2 ;;
  esac
  exit 0
fi

if [[ "$1" == 'run' ]]; then
  platform=''
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == '--platform' ]]; then
      platform="$2"
      break
    fi
    shift
  done
  case "${platform}" in
    linux/amd64) version="${MOCK_VERSION_AMD64:-26.3.27}" ;;
    linux/arm64) version="${MOCK_VERSION_ARM64:-26.3.27}" ;;
    *) exit 2 ;;
  esac
  printf 'Xray %s (mock)\n' "${version}"
  exit 0
fi

exit 2
MOCK
chmod 755 "${mock_docker}"

image_ref='taoziyoyo2566/xray_docker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
github_output="${fixture_dir}/github-output"

GITHUB_OUTPUT="${github_output}" DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" v26.3.27

grep -Fx "version=v26.3.27" "${github_output}" >/dev/null
grep -Fx "platforms=linux/amd64,linux/arm64" "${github_output}" >/dev/null
grep -Fx "image_ref=${image_ref}" "${github_output}" >/dev/null

if MOCK_MANIFEST_MODE=missing-arm64 DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" v26.3.27 >/dev/null 2>&1; then
  echo 'missing arm64 manifest was accepted' >&2
  exit 1
fi

if MOCK_MANIFEST_MODE=extra-linux-platform DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" v26.3.27 >/dev/null 2>&1; then
  echo 'unexpected Linux platform was accepted' >&2
  exit 1
fi

if MOCK_VERSION_ARM64=26.7.28 DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" v26.3.27 >/dev/null 2>&1; then
  echo 'mismatched platform versions were accepted' >&2
  exit 1
fi

if DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" 'taoziyoyo2566/xray_docker:latest' v26.3.27 >/dev/null 2>&1; then
  echo 'mutable image reference was accepted' >&2
  exit 1
fi

echo 'Xray image verification tests passed'
