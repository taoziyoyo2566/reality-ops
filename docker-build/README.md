# Xray Docker Image

Multi-platform Xray images built from official Xray release assets.

## Supported platforms

| OCI platform | CPU family | Release verification |
|---|---|---|
| `linux/amd64` | 64-bit x86 (`x86_64`), including Intel 64 and AMD64 processors | Manifest and Xray runtime verified |
| `linux/arm64` | 64-bit ARM (`AArch64`/ARMv8) | Manifest and Xray runtime verified |

`amd64` is the standard Docker/OCI name for the 64-bit x86 platform; it is not
limited to AMD processors. Docker automatically selects the matching platform
variant when an image is pulled. This image does not publish 32-bit `386` or
32-bit ARM variants.

## Release synchronization

The image repository follows the official non-draft
[Xray-core releases](https://github.com/XTLS/Xray-core/releases). Each sync
selects the newest stable release and every newer prerelease, verifies the
official amd64 and arm64 asset digests, and builds only immutable tags that are
missing from Docker Hub.

Synchronization runs daily and can also be started manually. Previously
published immutable version tags remain available when the active upstream
window advances; the sync never deletes historical tags.

### How automatic discovery works

The repository's GitHub Actions workflow runs on a daily UTC schedule. At the
start of every run it reads the live GitHub Releases API rather than a pinned
version file, stops at the newest non-draft stable release, and includes every
newer non-draft prerelease. It then compares the resulting immutable tags with
the live Docker Hub tag list.

Only missing tags enter the build matrix. If an upstream asset digest is
missing, an API request fails, or any image fails verification, the run fails
without moving `latest`. Images that were already published successfully keep
their immutable tags, so a later run can continue with only the tags that are
still missing. GitHub Actions scheduled runs are best-effort and may start
later than the configured minute; the weekly read-only audit provides a
separate drift check.

The schedule is not an availability guarantee: GitHub may delay or drop a
scheduled run, and scheduled workflows in inactive public repositories may be
disabled by GitHub. Repository maintainers must monitor Actions run history
and use the manual trigger when a scheduled run is absent.

## Tag overview

All versioned tags are immutable. After a versioned tag is published, it is
never overwritten or moved to another image digest.

`latest` is the only moving tag. It points to the newest recommended and
verified stable image. Beta images are never assigned to `latest`.

### Stable images

| Tag | Meaning |
|---|---|
| `vX.Y.Z` | Initial image build for an upstream Xray release; implicitly revision `r0` |
| `vX.Y.Z-rN` | Image revision `N` for the same upstream Xray release |
| `latest` | Current recommended stable image; the only moving tag |

For example, after two image-level improvements to Xray `v26.3.27`:

```text
latest      -> v26.3.27-r2
v26.3.27-r2    immutable
v26.3.27-r1    immutable
v26.3.27       immutable (implicit r0)
```

An image revision changes the image packaging, base image, entrypoint, or
other repository-owned content without changing the bundled Xray version.
Per-release revision overrides are recorded in
`docker-build/XRAY_IMAGE_REVISIONS.json`; releases not listed there use `r0`.

### Beta images

An upstream GitHub release with `prerelease=true` is published with a `-beta`
suffix in this repository. Here, `beta` is the repository's concise name for
the GitHub prerelease state; it does not claim a more specific upstream release
stage.

| Tag | Meaning |
|---|---|
| `vX.Y.Z-beta` | Initial image build for an upstream GitHub prerelease; implicitly revision `r0` |
| `vX.Y.Z-beta-rN` | Image revision `N` of that beta image |

Beta tags are immutable and never receive a moving alias.

## Pulling images

Use `latest` when automatic stable updates are desired:

```bash
docker pull taoziyoyo2566/xray_docker:latest
```

Use an immutable version tag for controlled upgrades:

```bash
docker pull taoziyoyo2566/xray_docker:v26.3.27-r2
```

For strictly reproducible deployments, pin the verified top-level image
digest. A digest remains the authoritative image identity even if a tag is
deleted.

## Publication and verification

Images are pushed without a public tag, verified on both supported platforms,
and only then assigned their immutable version tag. Stable publication also
moves `latest` to the verified digest. Publication refuses to continue when
the target version tag already exists.

Missing version tags may be restored only from their recorded, independently
verified digest; `latest` is never used to guess a missing revision. An
existing version tag is never repaired by moving it to different content.

## Source

Build workflow, Dockerfile, checksums, and release policy:
[taoziyoyo2566/reality-ops](https://github.com/taoziyoyo2566/reality-ops).
