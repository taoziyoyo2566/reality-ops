#!/usr/bin/env bash
set -euo pipefail

event_name="${1:-}"
requested_channel="${2:-}"
stable=false
prerelease=false

case "${event_name}" in
  workflow_dispatch)
    case "${requested_channel}" in
      all) stable=true; prerelease=true ;;
      stable) stable=true ;;
      prerelease) prerelease=true ;;
      *) echo "invalid requested channel: ${requested_channel}" >&2; exit 1 ;;
    esac
    ;;
  push)
    while IFS= read -r changed_path; do
      case "${changed_path}" in
        docker-build/dockerfile) stable=true; prerelease=true ;;
        docker-build/XRAY_VERSION|docker-build/XRAY_SHA256SUMS) stable=true ;;
        docker-build/XRAY_PRERELEASE_VERSION|docker-build/XRAY_PRERELEASE_SHA256SUMS) prerelease=true ;;
      esac
    done
    ;;
  *)
    echo "unsupported event: ${event_name}" >&2
    exit 1
    ;;
esac

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "stable=${stable}"
    echo "prerelease=${prerelease}"
  } >> "${GITHUB_OUTPUT}"
fi

echo "Selected image channels: stable=${stable}, prerelease=${prerelease}"
