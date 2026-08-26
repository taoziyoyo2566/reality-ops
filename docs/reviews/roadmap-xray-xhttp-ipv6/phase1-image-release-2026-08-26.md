# Phase 1 Image Release Evidence

Date: 2026-08-26 JST

## Scope

- Repository: `reality-ops`
- Initial branch: `feat/xray-modernization`
- Remediation branch: `fix/xray-image-verifier`
- Initial integration: PR #1 at
  `46c97f65a6e1755c4fa71bd92757931876418bda`
- Verifier remediation integration: PR #2 at
  `890b16f23c9979edfd53eea97b701c2bdca674da`
- Purpose: establish stable/prerelease Xray image inputs, tags, verification,
  and automatic alias promotion, and retain actual release evidence.

## Implemented

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
- The integrated baseline serialized build-driven and scheduled promotions
  through `xray-image-alias-update`. That scheduled alias-only promotion is now
  treated as an invalid prerelease-to-final assumption and is being removed.
- `.github/workflows/promote-xray-stable.yml` previously moved an already-built
  same-version image to `stable/latest`. It is replaced by
  `.github/workflows/check-xray-stable.yml`, which only compares the repository
  stable pin and asset digests with the upstream final release; it never logs
  in to or writes Docker Hub.
- `.github/workflows/quality.yml` runs both stable and prerelease input checks,
  shell syntax checks, and the focused image-verifier test suite.
- The broad `.github` ignore rule was removed so new workflow files are
  trackable; unrelated ignore rules remain unchanged.

## Pending release hardening

The Docker Hub audit found that the integrated workflow creates version and
`build-*` tags before runtime verification. This made both failed candidates
from run `32911966721` publicly visible. The current local hardening changes:

- push the candidate manifest by digest without a public tag;
- create the version and channel tags only after target and rollback gates pass;
- stop creating public `build-*` tags;
- publish prerelease versions as `vX.Y.Z-prerelease` and reject a pin that is
  no longer an upstream prerelease;
- require every final stable release to use its final official assets and a
  fresh stable build, even when the upstream version number is unchanged;
- add focused release-state and workflow publication-policy tests.

The expanded local lifecycle hardening also:

- selects only the manually requested or input-file-changed channel;
- keeps workflow/tooling changes from implicitly triggering a registry build;
- repairs tags from an existing verified digest without rebuilding;
- preserves the displaced stable digest as `stable-previous` and resolves
  rollback candidates through an explicit ordered policy;
- audits every Docker Hub tag page read-only and reports missing required tags;
- removes the disposable registry-writing login-test workflow.

Read-only inspection also found the old workflow's independent
`taoziyoyo2566/dockerhub-test:test` image still present. Removing that test
repository is a separate destructive Docker Hub action; it is not part of the
`xray_docker` tag repair.

These changes have local evidence only. They are not integrated and have not
yet been exercised by a registry-writing GitHub Actions run.

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

## Successful release verification

[PR #2](https://github.com/taoziyoyo2566/reality-ops/pull/2) merged the child
manifest verifier as
`890b16f23c9979edfd53eea97b701c2bdca674da`. The resulting
[Build and Push Xray Images run](https://github.com/taoziyoyo2566/reality-ops/actions/runs/32914861142)
completed successfully on 2026-08-26 JST. Both matrix jobs passed release
validation, multi-platform build/push, target runtime verification, alias
promotion, and reporting. The stable job also passed rollback resolution and
runtime verification.

Runtime and registry evidence:

| Channel | Runtime result | Top-level digest | Published tags and aliases |
|---|---|---|---|
| stable | `v26.3.27` on `linux/amd64,linux/arm64` | `sha256:5b905e8ff49804690109f74e305611869513a803d5bacf9d1f24d5fa4b1e40ce` | `v26.3.27`, `stable`, `latest` |
| prerelease | `v26.7.28` on `linux/amd64,linux/arm64` | `sha256:53cb9d8730738744a2dbe8c73502e5cd1d8667b14012fbd38a4a38e13495c3f8` | `v26.7.28`, `prerelease` |

Before moving `stable/latest`, the stable job ran the prior `latest` digest
`sha256:433d7302cddb336cb3b4d06f543798a850991a662cd136b5a6b7fa43274599a3`
on both required architectures and identified its contained version as
`v25.12.8`.

The old workflow also published these build-revision tags. They are historical
registry clutter rather than part of the intended release contract:

- `build-890b16f23c9979edfd53eea97b701c2bdca674da-xray-v26.3.27`;
- `build-890b16f23c9979edfd53eea97b701c2bdca674da-xray-v26.7.28`.

## Current registry state

Read-only Docker Hub inspection after the successful run confirmed:

- `v26.3.27`, `stable`, and `latest` resolve to
  `sha256:5b905e8ff49804690109f74e305611869513a803d5bacf9d1f24d5fa4b1e40ce`;
- `v26.7.28` and `prerelease` resolve to
  `sha256:53cb9d8730738744a2dbe8c73502e5cd1d8667b14012fbd38a4a38e13495c3f8`;
- both digests expose exactly `linux/amd64` and `linux/arm64` as Linux
  runtime platforms.

A tag inventory initially found 13 public tags. Under the then-active release
contract, the intended retained set was:

- `v26.3.27`, `stable`, and `latest` for the then-current stable digest;
- `v26.7.28` and `prerelease` for the current prerelease digest;
- `276dbacafbe703cde6e4a03bdff09f1ec7e45aee` for the verified `v25.12.8`
  rollback digest.

Seven tags were cleanup candidates: all four existing `build-*` tags and the
unreferenced legacy SHA tags `4d5f99fe...`, `6593b674...`, and `7033c408...`.
Repository search found no deployment reference to them; external consumers
cannot be proven from repository state.

A later read-only Docker Hub API query returned only three tags:

- `v26.7.28` and `prerelease` at the verified prerelease digest;
- `276dbacafbe703cde6e4a03bdff09f1ec7e45aee` at the verified `v25.12.8`
  rollback digest.

The cleanup candidates are gone, but `v26.3.27`, `stable`, and `latest` were
also removed. The previously verified stable digest now returns `not found`,
so it cannot be repaired by re-tagging. The ordered rollback resolver selects
the retained `276d...` tag successfully. After the hardening is integrated,
recovery therefore requires a manual stable-only rebuild; prerelease must not
be rebuilt. The hardened tag contract uses `v26.7.28-prerelease`, so a separate
no-build prerelease repair must validate the existing digest and add that tag.
The old unsuffixed `v26.7.28` is then an explicit manual-review cleanup target,
not a final-release image. Because deployment still defaults to `latest`,
new-node or forced pull operations must stop until recovery completes. No
registry write was performed as part of this read-only audit.

No deployment reference changed.

## Completion and remaining boundary

- The integrated baseline verified official inputs, multi-platform builds,
  contained versions, rollback behavior, immutable digests, and floating
  aliases. Phase 1 remains open while the no-public-candidate-tag hardening is
  unpublished and lacks a merge-triggered registry run.
- The failed first attempt remains recorded because it explains the verifier
  regression test and why its earlier local mock was insufficient.
- No production or staging deployment was performed. Choosing or rolling out a
  new deployment digest remains a separately reviewed action.
- Removing obsolete Docker Hub tags is a separate destructive registry action;
  it does not substitute for fixing and verifying the publishing workflow.
- Current stable recovery is a separate registry-writing action after the
  hardening is integrated. Until then, the required stable tags are absent.
- Publication of the hardening changes and all Phase 2 work remain separate
  actions.

Executable operator procedure:
[`xray-image-release.md`](../../runbooks/xray-image-release.md).
