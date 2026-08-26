# Phase 1 Image Release Evidence

Date: 2026-08-26 JST

## Scope

- Repository: `reality-ops`
- Initial branch: `feat/xray-modernization`
- Remediation branch: `fix/xray-image-verifier`
- Current remediation base:
  `origin/ops@46c97f65a6e1755c4fa71bd92757931876418bda`
- Purpose: establish stable/prerelease Xray image inputs, tags, verification,
  and automatic alias promotion, and retain actual release evidence.

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
  requires exactly `linux/amd64` and `linux/arm64`, resolves each platform's
  child manifest digest, runs the contained Xray binary through those distinct
  child references, and requires their versions to agree with each other and
  the expected version when supplied.
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
- regression coverage that rejects reuse of the top-level multi-platform
  digest for both platform runs and requires the matching child manifest
  digest for each architecture
- rejection coverage for a malformed child manifest digest
- verifier output generation for version, platforms, and immutable image
  reference
- PyYAML parsing of all four workflow files
- `bash -n` for every embedded workflow run block
- `git diff --check`

## First GitHub Actions release attempt

Merge commit
`46c97f65a6e1755c4fa71bd92757931876418bda` triggered
[run 32911966721](https://github.com/taoziyoyo2566/reality-ops/actions/runs/32911966721)
on 2026-08-26 JST. Both matrix jobs validated inputs, logged in, built, and
pushed successfully, then failed in `Verify pushed multi-platform image`:

```text
docker: cannot overwrite digest sha256:...
Process completed with exit code 125.
```

The original verifier ran the amd64 and arm64 variants through the same
top-level index digest. Docker resolved the first platform into its local image
store, then refused to overwrite that digest with the other platform. The
local mock had returned versions without modeling this Docker image-store
collision, so its prior success was insufficient integration evidence.

Published target digests from the failed run:

| Channel | Version | Top-level digest |
|---|---|---|
| stable | `v26.3.27` | `sha256:168290fdc51724f35b60f2b60d4b043816145bf7ac572af538d79897b3cf7a7d` |
| prerelease | `v26.7.28` | `sha256:f4220a4d33e935574cb1f892677885805acc35d84b596d2b23177b0507c7f095` |

The remediated verifier was then exercised read-only against those real
top-level manifests. Its runtime command was stubbed because the local user
cannot access the Docker daemon; manifest retrieval, parsing, platform
selection, digest validation, and child-reference construction used the real
Docker Hub data:

| Channel | Platform | Selected child manifest digest |
|---|---|---|
| stable | `linux/amd64` | `sha256:190a55263202695497973921aa976a8f3e9530eca1dfb4dedfa88d0bfb55a740` |
| stable | `linux/arm64` | `sha256:1cb0b3b7a7308fdf8491af09e3e1d1a0e4488ba7e3598a11f31c39b3a4f225e1` |
| prerelease | `linux/amd64` | `sha256:e1a8ef19972e0af994612058804b775baba2edfdf4d1c822b8a8114be1f58ebd` |
| prerelease | `linux/arm64` | `sha256:3c0760b6559532f1e6d8adf0520493db185dd9ffb1d424bb5d7c07600871f71c` |

This confirms that the corrected path no longer passes the same top-level
digest to both platform runs. It is not evidence that either container binary
actually ran; that remains a GitHub Actions gate.

The version tags and their `build-46c97f65...` tags exist. Target
verification failed before rollback verification or alias promotion, so the
old `latest` remained unchanged and neither `stable` nor `prerelease` was
created.

## Current registry state

Read-only Docker Hub inspection after the failed run found `v26.3.27`,
`v26.7.28`, and both `build-46c97f65...` tags. Each top-level digest contains
`linux/amd64` and `linux/arm64`. The existing `latest` remains at
`sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3`.
There is still no `stable` or `prerelease` alias. No deployment reference
changed.

## Gaps and next protected actions

- Both target images were built and pushed, but contained-version verification
  did not complete because of the verifier defect described above.
- The current `latest` rollback manifest and image configuration were inspected
  read-only, but its contained Xray binary could not be executed because this
  user cannot access `/var/run/docker.sock`.
- Version and build tags were written. No stable/latest/prerelease promotion
  ran.
- No production or staging deployment reference changed.
- The existing `latest` digest is the configured rollback candidate, but its
  runtime behavior is still unverified.
- GitHub CLI authentication was available for the failure investigation;
  authenticated job logs confirmed both jobs failed for the same reason.
- The project guidance names `scripts/check-project-memory.sh` as a freshness
  gate, but that path is absent from the current branch and repository file
  list, so the named check could not run. The required memory update was made
  manually; the missing checker remains a project-governance gap.
- Git staging, commit, push, PR creation, and merge remain separate actions.

The next gate is a reviewed verifier-fix publication and PR. Its integration to
`ops` will trigger a new multi-platform build; rerunning the old workflow cannot
pick up the fix. Actual child-digest runtime verification, rollback
verification, and alias promotion remain required evidence. Git publication,
the new registry write, and any later deployment each require their own
authorization.

Executable operator procedure:
[`xray-image-release.md`](../../runbooks/xray-image-release.md).
