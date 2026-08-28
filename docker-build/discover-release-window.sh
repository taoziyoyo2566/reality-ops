#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image_name="${1:-taoziyoyo2566/xray_docker}"
revisions_file="${2:-${repo_root}/docker-build/XRAY_IMAGE_REVISIONS.json}"
curl_bin="${CURL_BIN:-curl}"

if [[ ! "${image_name}" =~ ^[a-z0-9._-]+/[a-z0-9._-]+$ ]]; then
  echo "invalid Docker Hub image name: '${image_name}'" >&2
  exit 1
fi
if ! revisions_json="$(jq -ce '
  if type == "object"
     and all(keys[]; test("^v[0-9]+\\.[0-9]+\\.[0-9]+$"))
     and all(.[]; type == "number" and . >= 0 and floor == .)
  then . else error("invalid revisions ledger") end
' "${revisions_file}")"; then
  echo "invalid image revisions ledger: ${revisions_file}" >&2
  exit 1
fi

if [[ -n "${RELEASES_JSON_FILE:-}" ]]; then
  releases_json="$(< "${RELEASES_JSON_FILE}")"
else
  releases_json='[]'
  page=1
  github_auth=()
  if [[ -n "${GH_TOKEN:-}" ]]; then
    github_auth=(-H "Authorization: Bearer ${GH_TOKEN}")
  fi
  while true; do
    page_json="$("${curl_bin}" --fail --silent --show-error --location \
      -H 'Accept: application/vnd.github+json' \
      "${github_auth[@]}" \
      -H 'X-GitHub-Api-Version: 2026-03-10' \
      "https://api.github.com/repos/XTLS/Xray-core/releases?per_page=100&page=${page}")"
    if ! jq -e 'type == "array"' <<< "${page_json}" >/dev/null; then
      echo 'GitHub releases response is not an array' >&2
      exit 1
    fi
    releases_json="$(jq -sc '.[0] + .[1]' \
      <(printf '%s\n' "${releases_json}") <(printf '%s\n' "${page_json}"))"
    if jq -e 'map(select(.draft == false and .prerelease == false)) | length > 0' <<< "${releases_json}" >/dev/null; then
      break
    fi
    if [[ "$(jq 'length' <<< "${page_json}")" -eq 0 || "${page}" -ge 10 ]]; then
      echo 'no non-draft stable GitHub release found' >&2
      exit 1
    fi
    page=$((page + 1))
  done
fi

window_json="$(jq -ce --argjson revisions "${revisions_json}" '
  if type != "array" then error("releases response is not an array") else . end
  | (to_entries | map(select(.value.draft == false and .value.prerelease == false)) | first | .key) as $stable_index
  | if $stable_index == null then error("no non-draft stable release found") else . end
  | .[0:($stable_index + 1)]
  | map(select(.draft == false))
  | map(
      . as $release
      | .tag_name as $version
      | ($revisions[$version] // 0) as $revision
      | ([.assets[]? | select(.name == "Xray-linux-64.zip") | .digest] | first) as $amd64
      | ([.assets[]? | select(.name == "Xray-linux-arm64-v8a.zip") | .digest] | first) as $arm64
      | if ($version | test("^v[0-9]+\\.[0-9]+\\.[0-9]+$") | not) then error("invalid release tag: " + $version)
        elif (($amd64 // "") | test("^sha256:[0-9a-f]{64}$") | not) then error("invalid amd64 digest for " + $version)
        elif (($arm64 // "") | test("^sha256:[0-9a-f]{64}$") | not) then error("invalid arm64 digest for " + $version)
        else {
          version: $version,
          prerelease: .prerelease,
          channel: (if .prerelease then "beta" else "stable" end),
          revision: $revision,
          image_tag: ($version + (if .prerelease then "-beta" else "" end) + (if $revision == 0 then "" else "-r" + ($revision | tostring) end)),
          amd64_sha: ($amd64 | sub("^sha256:"; "")),
          arm64_sha: ($arm64 | sub("^sha256:"; "")),
          published_at: .published_at
        } end
    )
' <<< "${releases_json}")"

if [[ -n "${TAG_JSON_FILE:-}" ]]; then
  tags_json="$(< "${TAG_JSON_FILE}")"
else
  tags_json='{"results":[]}'
  next_url="https://hub.docker.com/v2/repositories/${image_name}/tags?page_size=100"
  while [[ -n "${next_url}" ]]; do
    page_json="$("${curl_bin}" --fail --silent --show-error --location "${next_url}")"
    tags_json="$(jq -sc '{results: (.[0].results + .[1].results)}' \
      <(printf '%s\n' "${tags_json}") <(printf '%s\n' "${page_json}"))"
    next_url="$(jq -r '.next // empty' <<< "${page_json}")"
  done
fi
if ! jq -e '.results | type == "array"' <<< "${tags_json}" >/dev/null; then
  echo 'Docker Hub tag response does not contain a results array' >&2
  exit 1
fi

registry_names="$(jq -c '[.results[].name]' <<< "${tags_json}")"
missing_json="$(jq -c --argjson names "${registry_names}" '
  [.[] | select(.image_tag as $tag | ($names | index($tag) | not))]
  | sort_by(.published_at)
  | (map(select(.prerelease)) + map(select(.prerelease | not)))
' <<< "${window_json}")"
matrix="$(jq -cn --argjson include "${missing_json}" '{include: $include}')"
expected_tags="$(jq -c '[.[].image_tag]' <<< "${window_json}")"
prerelease_versions="$(jq -c '[.[] | select(.prerelease) | .version]' <<< "${window_json}")"
stable_version="$(jq -r '[.[] | select(.prerelease == false)] | first | .version' <<< "${window_json}")"
stable_tag="$(jq -r '[.[] | select(.prerelease == false)] | first | .image_tag' <<< "${window_json}")"
missing_count="$(jq 'length' <<< "${missing_json}")"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  {
    echo "matrix=${matrix}"
    echo "expected_tags=${expected_tags}"
    echo "prerelease_versions=${prerelease_versions}"
    echo "registry_tags=$(jq -c '.results' <<< "${tags_json}")"
    echo "missing_count=${missing_count}"
    echo "stable_version=${stable_version}"
    echo "stable_tag=${stable_tag}"
  } >> "${GITHUB_OUTPUT}"
fi

echo "Release window: ${stable_version} through $(jq -r 'first.version' <<< "${window_json}")"
jq -r '.[] | [.image_tag, .channel, ("r" + (.revision | tostring))] | @tsv' <<< "${window_json}"
echo "Missing immutable tags: ${missing_count}"
