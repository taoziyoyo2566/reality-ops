# Xray Modernization Roadmap

> **SUPERSEDED 2026-08-27.** This roadmap covered delivery-layer work only and
> did not intersect the config-layer plan in `todo.md`. Both were merged into
> [`roadmap-unified-2026-08-27.md`](roadmap-unified-2026-08-27.md), which is now
> the single planning source. Phases 1–4 below are carried forward there as
> P2 and P4; unchecked Phase 1 items are tracked in its P2. Retained for the
> branch contract and historical decisions.

Last reviewed: 2026-08-26 JST

## Branch contract

- **Problem/outcome:** Modernize the project Xray stack without carrying old
  investigation notes, intermediate implementations, or unrelated operational
  changes into `ops`.
- **Approach/scope:** Work in ordered phases: reproducible Xray stable and
  prerelease images, XHTTP + REALITY, client-to-VPS IPv4/IPv6 automatic
  selection, then directly supporting engineering improvements. Each phase
  preserves the previously verified default behavior.
- **Prerequisites:** Recheck upstream Xray releases and the relevant repository
  or live baseline at the start of each phase. Live, registry, DNS, Gist, and
  deployment writes require separate authorization.
- **Excluded:** Historical 2026-08-23/24 investigations, the former roadmap and
  changelog series, monitor/setup/wrapper work, node-lifecycle work, and the
  `tmp`/`todo` branch contents are not migrated as part of this branch.
- **Acceptance:** Only phase-scoped paths differ from `ops`; native static and
  behavior checks pass; unavailable live or Docker checks remain explicit gaps.
- **Publication:** Use phase-sized logical commits. Staging, commit, push, and
  PR publication require a separate result review and authorization. Phase 1
  used `feat/xray-modernization` plus `fix/xray-image-verifier`; later phases
  use new branches from current `origin/ops`.
- **Integration/closeout:** Integrate only a verified phase. Record remaining
  gaps and the next phase owner before continuing.
- **Retirement:** Retain the branch until all accepted phases are integrated or
  cancelled. Removal of local or remote branch state requires a separate
  reviewed action.
- **Branch action:** Created additively from
  `origin/ops@68ce3e0574d3f30f871060e8e52b06e5b0bf5607` as
  `feat/xray-modernization`. It is now checked out directly in
  `/home/saberu/workspace/projects/reality-ops`; the temporary isolated
  worktree has been removed, and the pre-modernization workspace remains
  preserved on `feat/roadmap-2026-08`.

### Phase 1 release hardening

- **Problem/outcome:** Prevent failed candidate builds from leaving public
  tags, prevent any published version tag from moving to different content,
  and prevent prerelease content from being relabeled as a final release.
- **Approach/scope:** Push candidates only by digest; verify the candidate and
  then create one immutable version tag. Discover the newest upstream stable
  and every newer prerelease from GitHub, build only tags missing from Docker
  Hub through a dynamic matrix, use repository-owned `-rN` overrides, map
  prereleases to `-beta`, and retain `latest` as the only moving tag. Remove
  fixed pins and old channel/rollback aliases. No deployment change is included.
- **Acceptance:** Local workflow and shell checks pass; no build step contains
  a public tag; failed verification cannot create a public tag; a prerelease
  release assets and states are validated; a dry-run identifies the complete
  window; a post-merge manual sync confirms all intended public tags.
- **Publication:** One phase-1 hardening commit and PR from the current branch
  to `ops`, subject to separate authorization.
- **Integration/closeout:** Close Phase 1 only after the hardened workflow has
  passed in GitHub Actions and the resulting registry state has been checked.
- **Retirement:** Retain the branch until its PR is integrated; cleanup requires
  separate review.
- **Branch action:** Direct checkout in the primary workspace from
  `origin/ops@890b16f23c9979edfd53eea97b701c2bdca674da`; the merged verifier branch
  remains preserved.

## Confirmed direction

1. Upgrade Xray Core and make image releases reproducible and identifiable.
2. Add declarative XHTTP + REALITY support while retaining TCP + REALITY +
   Vision as the default.
3. Add client-to-VPS address-family selection: `auto` uses a verified
   dual-stack FQDN and client-native fallback; explicit `ipv4`, missing VPS
   IPv6, or missing client IPv6 uses IPv4.
4. Apply only improvements that materially support the first three goals.

## Phase 1 — Xray image release model (hardening in progress)

Current discovered window, subject to an official-release recheck at execution time:

- stable: `v26.3.27`
- prerelease range: `v26.4.13` through `v26.7.28` (11 releases)
- Docker Hub repository: `taoziyoyo2566/xray_docker`

Implementation checklist:

- [x] Discover the stable-to-latest-prerelease window from official GitHub
  Release state and keep the computed matrix auditable.
- [x] Verify official release assets with architecture-specific SHA256 values
  before unpacking them.
- [x] Configure `linux/amd64` and `linux/arm64` builds with version, build
  channel, source, and project-revision labels. Both architectures were built
  and verified in GitHub Actions run `32914861142`.
- [ ] Publish no public tag until the candidate digest passes multi-platform
  runtime verification; then create only the immutable version tag and, for
  stable, move `latest`. Public
  `build-*` tags are not part of the release contract.
- [x] The integrated baseline moved `stable` and `latest` from an already-built
  same-version image after runtime verification. This behavior is retained as
  historical evidence but is rejected as the future release contract.
- [ ] Always build a final stable release from its final official assets, even
  when the same upstream version was previously built as prerelease. Never
  relabel the prerelease digest as stable.
- [x] Gate `latest` movement on a digest-pinned manifest/runtime verifier.
  Version tags are immutable and publication rejects an existing target tag.
- [x] Serialize manual and scheduled synchronization through one GitHub Actions
  concurrency group so registry writers cannot race each other.
- [x] Reject malformed release state/tags, missing official asset digests,
  unsupported architectures, and incomplete API responses.
- [ ] Publish prerelease version tags as `vX.Y.Z-beta[-rN]`, and reject a
  prerelease pin that is no longer marked prerelease. Update it independently
  rather than silently skipping or promoting its old image.
- [ ] On manual or scheduled runs build only immutable tags missing from the
  discovered window. Merge and push events must not cause an implicit registry
  build; successful prior matrix entries must be skipped on retry.
- [ ] Remove `stable`, `prerelease`, and `stable-previous`; roll back by moving
  only `latest` to a verified immutable stable version or recorded digest.
- [ ] Audit all Docker Hub tag pages weekly without registry credentials or
  deletes; report retained, cleanup-candidate, unknown, and required-but-missing
  tags.
- [x] Remove the obsolete Docker Hub login-test workflow that published a
  disposable test image.
- [x] Cover verifier success, missing/extra Linux architectures, cross-platform
  version mismatch, mutable references, and GitHub output generation with
  focused local tests.
- [x] Run the verifier against newly built stable and prerelease digests and
  retain the resulting manifest architectures, contained Xray versions, and
  top-level digests as registry evidence.
- [x] Keep the production deployment reference unchanged until the new target
  and its verified rollback digest have passed the actual registry/runtime
  gate. The project still defaults to the mutable `latest` tag; neither
  implementation PR changed deployment configuration or ran a deployment.

Required local checks:

- workflow YAML parses;
- shell scripts pass syntax and focused behavior tests;
- version/checksum files agree with the release assets;
- Dockerfile contains build logic and essential comments only;
- the diff against `ops` contains no unrelated paths.

External gaps do not count as passed checks. Building or pushing images,
changing Docker Hub tags, and deploying nodes are separate authorized actions.

Local verification evidence:
[`phase1-image-release-2026-08-26.md`](roadmap-xray-xhttp-ipv6/phase1-image-release-2026-08-26.md).

Operator procedure:
[`xray-image-release.md`](../runbooks/xray-image-release.md).

Execution note, 2026-08-26: the first merged build exposed a verifier defect
after pushing both candidates, without moving aliases. PR #2 corrected the
verifier to run each platform through its child manifest digest. Post-merge
run `32914861142` then passed stable and prerelease target verification, stable
rollback verification, and all expected alias promotions. A later registry
audit found that pre-verification version/build tags made failed candidates
public. The current hardening work removes public `build-*` tags, delays public
tags until verification, makes every version tag immutable with explicit image
revisions, maps GitHub prereleases to `-beta`, and leaves `latest` as the only
moving tag. The operator-reported registry still contains old aliases; their
inventory and cleanup remain separate registry actions. Phase 1 remains open
until this contract is integrated and exercised by a verified publication.
Deployment remains a separate later action.

## Phase 2 — XHTTP + REALITY

- [ ] Add one validated `tcp|xhttp` node selector; default to `tcp`.
- [ ] Keep TCP on REALITY + Vision with no semantic output change.
- [ ] For XHTTP, use REALITY, an Ansible-owned stable path, and `mode=auto`;
  omit `xtls-rprx-vision` flow.
- [ ] Update single and multi server templates, subscription output, validation,
  and regression tests together.
- [ ] Validate offline first, then staging single/multi nodes and the actual
  client compatibility matrix before any production canary.

## Phase 3 — IPv4/IPv6 automatic selection

- [ ] Separate VPS IPv6 capability, DNS A/AAAA correctness, and client fallback
  as independently verified conditions.
- [ ] Implement an explicit `auto|ipv4` policy with IPv4 as the safe fallback.
- [ ] Use a verified dual-stack FQDN for `auto`; do not claim that an IPv6
  literal or detected interface alone proves end-to-end usability.
- [ ] Validate IPv6-preferred, IPv4-only, broken-IPv6, and no-AAAA client paths
  in staging before DNS or production rollout.
- [ ] Treat active periodic quality testing as an optional controlled-client
  feature, not as a capability of a generic VLESS URI.

## Phase 4 — Supporting improvements

- [ ] Remove invalid or obsolete Xray fields with generated-config regression
  coverage.
- [ ] Consolidate duplicated subscription generation only after outputs are
  characterized.
- [ ] Strengthen deployment evidence around running version, manifest digest,
  real proxy behavior, and rollback.
- [ ] Add multi-role Docker IPv6 handling only if phase 3 proves it is needed.

## Stop conditions

Stop the affected phase when repository or upstream evidence contradicts its
assumptions, a required check cannot run, a default TCP/subscription behavior
changes unexpectedly, or a requested action crosses into registry, DNS, Gist,
node, production, or Git publication mutation without separate authorization.
