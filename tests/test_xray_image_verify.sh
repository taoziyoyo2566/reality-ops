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
      printf '%s\n' '{"manifests":[{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platform":{"os":"linux","architecture":"amd64"}}]}'
      ;;
    extra-linux-platform)
      printf '%s\n' '{"manifests":[{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platform":{"os":"linux","architecture":"amd64"}},{"digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","platform":{"os":"linux","architecture":"arm64"}},{"digest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","platform":{"os":"linux","architecture":"riscv64"}}]}'
      ;;
    malformed-child-digest)
      printf '%s\n' '{"manifests":[{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platform":{"os":"linux","architecture":"amd64"}},{"digest":"sha256:invalid","platform":{"os":"linux","architecture":"arm64"}}]}'
      ;;
    complete)
      printf '%s\n' '{"manifests":[{"digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platform":{"os":"linux","architecture":"amd64"}},{"digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","platform":{"os":"linux","architecture":"arm64"}},{"digest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","platform":{"os":"unknown","architecture":"unknown"}}]}'
      ;;
    *) exit 2 ;;
  esac
  exit 0
fi

if [[ "$1" == 'run' ]]; then
  platform=''
  run_ref=''
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --platform) platform="$2"; shift ;;
      *@sha256:*) run_ref="$1" ;;
    esac
    shift
  done
  case "${platform}" in
    linux/amd64)
      expected_ref='taoziyoyo2566/xray_docker@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
      version="${MOCK_VERSION_AMD64:-26.3.27}"
      ;;
    linux/arm64)
      expected_ref='taoziyoyo2566/xray_docker@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
      version="${MOCK_VERSION_ARM64:-26.3.27}"
      ;;
    *) exit 2 ;;
  esac
  if [[ "${run_ref}" != "${expected_ref}" ]]; then
    echo "docker: cannot overwrite digest ${run_ref##*@}" >&2
    exit 125
  fi
  if [[ -n "${MOCK_RUN_LOG:-}" ]]; then
    printf '%s\t%s\n' "${platform}" "${run_ref}" >> "${MOCK_RUN_LOG}"
  fi
  printf 'Xray %s (mock)\n' "${version}"
  exit 0
fi

exit 2
MOCK
chmod 755 "${mock_docker}"

image_ref='taoziyoyo2566/xray_docker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
github_output="${fixture_dir}/github-output"
run_log="${fixture_dir}/run-log"

GITHUB_OUTPUT="${github_output}" MOCK_RUN_LOG="${run_log}" DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" v26.3.27

grep -Fx "version=v26.3.27" "${github_output}" >/dev/null
grep -Fx "platforms=linux/amd64,linux/arm64" "${github_output}" >/dev/null
grep -Fx "image_ref=${image_ref}" "${github_output}" >/dev/null
grep -Fx $'linux/amd64\ttaoziyoyo2566/xray_docker@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' "${run_log}" >/dev/null
grep -Fx $'linux/arm64\ttaoziyoyo2566/xray_docker@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc' "${run_log}" >/dev/null

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

if MOCK_MANIFEST_MODE=malformed-child-digest DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" v26.3.27 >/dev/null 2>&1; then
  echo 'malformed child manifest digest was accepted' >&2
  exit 1
fi

if MOCK_VERSION_ARM64=26.7.28 DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" "${image_ref}" >/dev/null 2>&1; then
  echo 'mismatched platform versions were accepted' >&2
  exit 1
fi

if DOCKER_BIN="${mock_docker}" \
  bash "${repo_root}/docker-build/verify-image.sh" 'taoziyoyo2566/xray_docker:latest' v26.3.27 >/dev/null 2>&1; then
  echo 'mutable image reference was accepted' >&2
  exit 1
fi

echo 'Xray image verification tests passed'
