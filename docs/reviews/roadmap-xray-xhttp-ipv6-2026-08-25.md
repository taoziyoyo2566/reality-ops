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
  PR publication require a separate result review and authorization. PR base is
  `ops`; head is `feat/xray-modernization`.
- **Integration/closeout:** Integrate only a verified phase. Record remaining
  gaps and the next phase owner before continuing.
- **Retirement:** Retain the branch/worktree until all accepted phases are
  integrated or cancelled. Removal of either local or remote state requires a
  separate reviewed action.
- **Branch action:** Created additively from
  `origin/ops@68ce3e0574d3f30f871060e8e52b06e5b0bf5607` as
  `feat/xray-modernization`. Its initial isolated checkout was
  `/home/saberu/workspace/projects/reality-ops-xray-modernization`; any later
  handoff to the primary workspace must first preserve the original dirty state
  in a visible recovery commit.

## Confirmed direction

1. Upgrade Xray Core and make image releases reproducible and identifiable.
2. Add declarative XHTTP + REALITY support while retaining TCP + REALITY +
   Vision as the default.
3. Add client-to-VPS address-family selection: `auto` uses a verified
   dual-stack FQDN and client-native fallback; explicit `ipv4`, missing VPS
   IPv6, or missing client IPv6 uses IPv4.
4. Apply only improvements that materially support the first three goals.

## Phase 1 — Xray image release model

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
  channel, source, and project-revision labels. Actual image builds remain a
  separate verification gap.
- [x] Configure immutable version and build-revision tags while retaining old
  version tags. Registry publication has not run.
- [x] Configure `stable` and `latest` promotion from an already-built official
  latest-stable version digest; never rebuild during promotion. Registry
  promotion has not run.
- [x] Reject a prerelease presented as stable, a stale stable pin, missing
  checksums, unsupported architectures, and malformed versions.
- [ ] Verify the built manifest architectures, contained Xray version, and
  top-level digest before any deployment reference changes.
- [ ] Preserve the current production deployment reference until a target and
  rollback digest have both been verified.

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
