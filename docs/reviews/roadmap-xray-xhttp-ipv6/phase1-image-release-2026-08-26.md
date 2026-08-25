# Phase 1 Image Release Evidence

Date: 2026-08-26 JST

## Scope

- Repository: `reality-ops`
- Branch: `feat/xray-modernization`
- Base: `origin/ops@68ce3e0574d3f30f871060e8e52b06e5b0bf5607`
- Purpose: establish stable/prerelease Xray image inputs, tags, validation, and
  automatic stable alias promotion without building, pushing, or deploying.

## Implemented locally

- `docker-build/dockerfile` now uses a digest-pinned Alpine base, explicit Xray
  version and architecture checksum build arguments, checksum-before-unzip,
  a contained-version assertion, and no investigation-history comments.
- Stable and prerelease versions and checksums are held in separate files.
- `docker-build/check-inputs.sh` validates the version syntax, required asset
  set, checksum syntax, duplicates, omissions, and unexpected assets.
- `.github/workflows/build-image.yml` validates official release state and
  asset digests before registry login, then defines multi-platform version,
  channel, and `build-<Git-SHA>-xray-<version>` tags.
- `.github/workflows/promote-xray-stable.yml` resolves the official latest
  stable release daily and promotes its already-built version digest to
  `stable` and `latest`; it does not rebuild that version.
- `.github/workflows/quality.yml` runs both stable and prerelease input checks.
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

- `bash -n docker-build/check-inputs.sh`
- stable and prerelease positive validation
- invalid-version and missing-checksum rejection cases
- output generation for version, amd64 SHA256, and arm64 SHA256
- PyYAML parsing of all three changed workflow files
- `bash -n` for every embedded workflow run block
- `git diff --check`

## Current registry state

The read-only Docker Hub query returned five existing tags: `latest` and four
full Git commit hashes. It returned no human-readable Xray version, `stable`,
or `prerelease` tag. Therefore automatic promotion is correctly not claimed as
executed or effective yet.

## Gaps and next protected actions

- No Docker image was built locally or in GitHub Actions.
- No amd64/arm64 manifest list, image label, contained binary, or top-level
  project digest has been inspected.
- No Docker Hub login, push, tag mutation, or stable/latest promotion ran.
- No production or staging deployment reference changed.
- No rollback digest was selected or behavior-tested.
- Git staging, commit, push, PR creation, and merge remain separate actions.

The next implementation gate is a reviewed publication of this branch so the
multi-platform GitHub Actions build can run. Registry credentials and writes,
followed by manifest and runtime verification, require their own authorization
and evidence.
