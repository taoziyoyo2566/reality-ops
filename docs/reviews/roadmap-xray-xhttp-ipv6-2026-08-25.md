# Xray Modernization Roadmap

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
  version or build-revision tags, and prevent prerelease content from being
  relabeled as a final release without a fresh build.
- **Approach/scope:** Push candidates only by digest; verify the candidate and
  rollback digest; then create only version and channel tags. Remove public
  `build-*` generation; select only the changed or manually requested channel;
  add no-build tag repair, durable `stable-previous`, ordered rollback
  resolution, and read-only garbage-tag auditing; remove the registry-writing
  login test; and synchronize the roadmap, evidence, runbook, and project
  memory. No deployment change is included.
- **Acceptance:** Local workflow and shell checks pass; no build step contains
  a public tag; failed verification cannot create a public tag; a prerelease
  pin that became stable is rejected; the scheduled stable check is read-only;
  a post-merge run confirms the selected channel and intended public tags.
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

Current target pins, subject to an official-release recheck at execution time:

- stable: `v26.3.27`
- prerelease: `v26.7.28`
- Docker Hub repository: `taoziyoyo2566/xray_docker`

Implementation checklist:

- [x] Keep the stable and prerelease versions explicit and independently
  auditable.
- [x] Verify official release assets with architecture-specific SHA256 values
  before unpacking them.
- [x] Configure `linux/amd64` and `linux/arm64` builds with version, build
  channel, source, and project-revision labels. Both architectures were built
  and verified in GitHub Actions run `32914861142`.
- [ ] Publish no public tag until the candidate digest passes multi-platform
  runtime verification; then create only the version and channel tags. Public
  `build-*` tags are not part of the release contract.
- [x] The integrated baseline moved `stable` and `latest` from an already-built
  same-version image after runtime verification. This behavior is retained as
  historical evidence but is rejected as the future release contract.
- [ ] Always build a final stable release from its final official assets, even
  when the same upstream version was previously built as prerelease. Never
  relabel the prerelease digest as stable.
- [x] Gate `stable`, `latest`, and `prerelease` alias movement on a
  digest-pinned manifest/runtime verifier. Stable updates also require the
  current `latest` digest to pass the same verifier as a rollback candidate.
- [x] Serialize build and repair alias updates through one GitHub Actions
  concurrency group so they cannot race each other. The scheduled stable check
  is read-only and never joins the registry writer group.
- [x] Reject a prerelease presented as stable, a stale stable pin, missing
  checksums, unsupported architectures, and malformed versions.
- [ ] Publish prerelease version tags as `vX.Y.Z-prerelease`, and reject a
  prerelease pin that is no longer marked prerelease. Update it independently
  rather than silently skipping or promoting its old image.
- [ ] On manual runs build only `stable`, `prerelease`, or explicitly `all`;
  on version-input pushes build only the changed channel. Workflow/tooling
  changes must not cause an implicit registry build.
- [ ] Repair missing version or channel tags from an existing verified digest
  without rebuilding. Reject repair when every source tag and explicit digest
  is absent or inaccessible.
- [ ] Preserve the prior verified stable digest as `stable-previous`, with an
  explicit bootstrap keep-list fallback when all stable aliases are absent.
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
public. The current hardening work removes public `build-*` tags and delays all
remaining public tags until verification. A later registry cleanup also removed
all stable tags and their digest is no longer readable, while prerelease remains
intact. Phase 1 remains open until the hardening is integrated and a manual
stable-only rebuild restores and verifies `v26.3.27/stable/latest` without
rebuilding prerelease. A separate verified no-build repair then adds
`v26.7.28-prerelease` before the ambiguous old `v26.7.28` tag is considered for
cleanup. Deployment remains a separate later action.

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
