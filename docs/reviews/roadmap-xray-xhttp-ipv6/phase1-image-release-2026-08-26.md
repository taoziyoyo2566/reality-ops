# Phase 1 Image Release Evidence

Date: 2026-08-26 JST

## Scope

- Repository: `reality-ops`
- Branch: `feat/xray-modernization`
- Base: `origin/ops@68ce3e0574d3f30f871060e8e52b06e5b0bf5607`
- Purpose: establish stable/prerelease Xray image inputs, tags, verification,
  and automatic alias promotion without building, pushing, or deploying.

## Implemented locally

- `docker-build/dockerfile` now uses a digest-pinned Alpine base, explicit Xray
  version and architecture checksum build arguments, checksum-before-unzip,
  a contained-version assertion, and no investigation-history comments.
- Stable and prerelease versions and checksums are held in separate files.
- `docker-build/check-inputs.sh` validates the version syntax, required asset
  set, checksum syntax, duplicates, omissions, and unexpected assets.
- `.github/workflows/build-image.yml` validates official release state and
  asset digests before registry login, then defines multi-platform version and
  `build-<Git-SHA>-xray-<version>` tags. Verification and handoff use the
  resulting digest rather than treating those tags as immutable.
- `docker-build/verify-image.sh` accepts only a digest-pinned image reference,
  requires exactly `linux/amd64` and `linux/arm64`, runs the contained Xray
  binary on both platforms, and requires their versions to agree with each
  other and the expected version when supplied.
- Build jobs move `stable`, `latest`, or `prerelease` only after the pushed
  digest passes that verifier. A stable build additionally resolves and
  verifies the pre-update `latest` digest as its rollback candidate.
- Build-driven and scheduled promotions share the
  `xray-image-alias-update` concurrency group, preventing overlapping alias
  mutations across the two workflows.
- `.github/workflows/promote-xray-stable.yml` resolves the official latest
  stable release daily, verifies both the already-built target and the prior
  `latest` rollback image when they differ, then promotes the target digest to
  `stable` and `latest`; it does not rebuild that version.
- `.github/workflows/quality.yml` runs both stable and prerelease input checks,
  shell syntax checks, and the focused image-verifier test suite.
- The broad `.github` ignore rule was removed so new workflow files are
  trackable; unrelated ignore rules remain unchanged.

## Verified results

Official GitHub release API, `X-GitHub-Api-Version: 2026-03-10`:

| Pin | Release state | amd64 SHA256 | arm64 SHA256 |
|---|---|---|---|
| `v26.3.27` | latest stable | `23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae` | `4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c` |
| `v26.7.28` | prerelease | `8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40` | `f5698bb218ada3b4022db26fafc39601c5f53b46b19eb76c9616325985807501` |

All four downloaded archives matched the API and repository SHA256 values.
Python standard-library ZIP tests returned `Done testing` for all four files.
The downloaded amd64 binaries reported:

```text
Xray 26.3.27 (Xray, Penetrates Everything.) d2758a0 (go1.26.1 linux/amd64)
Xray 26.7.28 (Xray, Penetrates Everything.) 5ca6f4b (go1.26.5 linux/amd64)
```

Docker Hub returned the same digest used by the Dockerfile for `alpine:3.24`:

```text
sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
```

Repository-local checks passed:

- `bash -n docker-build/check-inputs.sh docker-build/verify-image.sh
  tests/test_xray_image_verify.sh`
- stable and prerelease positive validation
- invalid-version and missing-checksum rejection cases
- output generation for version, amd64 SHA256, and arm64 SHA256
- digest-only verifier acceptance and rejection coverage for a missing arm64
  manifest, an extra Linux architecture, cross-platform version mismatch, and
  a mutable tag
- verifier output generation for version, platforms, and immutable image
  reference
- PyYAML parsing of all three changed workflow files
- `bash -n` for every embedded workflow run block
- `git diff --check`

## Current registry state

The read-only Docker Hub query returned five existing tags: `latest` and four
full Git commit hashes. It returned no human-readable Xray version, `stable`,
or `prerelease` tag. The current `latest` top-level digest is
`sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3`.
Both the Docker Hub tag API and `docker buildx imagetools inspect --raw`
reported active `linux/amd64` and `linux/arm64` manifests plus the two expected
`unknown/unknown` provenance manifests. This establishes the platform shape of
the rollback candidate, but not its contained Xray version. No alias was
changed, so automatic promotion is not claimed as executed or effective.

## Gaps and next protected actions

- No Docker image was built locally or in GitHub Actions.
- No newly built stable or prerelease digest exists to verify.
- The current `latest` rollback manifest and image configuration were inspected
  read-only, but its contained Xray binary could not be executed because this
  user cannot access `/var/run/docker.sock`.
- No Docker Hub login, push, tag mutation, or stable/latest promotion ran.
- No production or staging deployment reference changed.
- The existing `latest` digest is the configured rollback candidate, but its
  runtime behavior is still unverified.
- GitHub CLI authentication is invalid for both configured accounts, so
  `gh`-based PR and workflow operations are unavailable until the operator
  reauthenticates. Git push authentication was not tested because no
  publication action was authorized.
- The project guidance names `scripts/check-project-memory.sh` as a freshness
  gate, but that path is absent from the current branch and repository file
  list, so the named check could not run. The required memory update was made
  manually; the missing checker remains a project-governance gap.
- Git staging, commit, push, PR creation, and merge remain separate actions.

The next gate is a reviewed Git publication and PR so the repository quality
workflow can run. After review and integration to `ops`, the push-triggered
multi-platform build can publish and verify the two version digests before
moving aliases. Git publication, registry writes, and any later deployment each
require their own authorization and evidence.

Executable operator procedure:
[`xray-image-release.md`](../../runbooks/xray-image-release.md).
