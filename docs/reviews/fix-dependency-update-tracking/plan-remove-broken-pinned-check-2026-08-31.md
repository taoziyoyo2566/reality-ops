# Remove the broken pinned dependency check

Date: 2026-08-31
Status: locally implemented; integration pending

## Problem and outcome

The dependency-tracking change added both Dependabot and a scheduled pinned
runtime dependency workflow. Dependabot is active and has already raised
dependency update pull requests, but the scheduled workflow invokes
`scripts/check_pinned_updates.py`, which is excluded from the repository. The
local ignored copy also expects a digest-pinned `xray_image` and an
`xray_image_update_source` variable, while the repository deliberately consumes
the floating `latest` channel and defines no update-source variable.

Remove the unusable scheduled workflow while preserving Dependabot as the
repository's dependency-update owner.

## Approach and scope

- Delete `.github/workflows/check-pinned-updates.yml`.
- Keep `.github/dependabot.yml` unchanged.
- Do not change dependency versions, the Xray image policy, ignored local
  scripts, deployment configuration, or live systems.
- Do not stage, commit, push, publish a pull request, merge, or retire the
  branch without a separate reviewed authorization.

## Acceptance

- The branch contains this contract and the deletion of the scheduled workflow
  only.
- No tracked workflow invokes `scripts/check_pinned_updates.py`.
- `.github/dependabot.yml` remains parseable and unchanged from the base.
- `git diff --check` passes.
- Any check that cannot run is reported as a gap rather than passed.

## Publication

The intended publication unit is one repair commit containing the contract and
workflow deletion. Publication requires a separate review and authorization,
then a pull request from `fix/remove-broken-pinned-check` to `ops`.

## Integration, closeout, and retirement

The task is locally implemented when the acceptance checks pass. It is not
integrated until the reviewed pull request is merged into `ops`. After
integration, branch and worktree retirement require separate authorization.

## Branch action

- Branch: `fix/remove-broken-pinned-check`
- Exact base: `82aed094908d09e4def46fabb8cc88a9af57f64d`
- Base ref at review time: `origin/ops`
- Worktree: `/tmp/reality-ops-fix-remove-broken-pinned-check`
- Existing-change treatment: leave the original clean checkout and its ignored
  local `scripts/` directory untouched; carry no local changes into this
  worktree.

Approved creation command:

```bash
git worktree add -b fix/remove-broken-pinned-check /tmp/reality-ops-fix-remove-broken-pinned-check 82aed094908d09e4def46fabb8cc88a9af57f64d
```

## Local implementation record

On 2026-08-31 the approved additive worktree was created from the exact base
above and `.github/workflows/check-pinned-updates.yml` was removed. Dependabot
configuration and all runtime/deployment configuration remained unchanged.

Local verification:

- the working tree contains only this contract and the workflow deletion;
- no remaining tracked file references the removed workflow or
  `scripts/check_pinned_updates.py`;
- `.github/dependabot.yml` is unchanged from the base and parses as YAML with
  the expected two update entries;
- `git diff --check` passes.

At this initial local verification checkpoint, no files had been staged or
committed, no branch had been pushed, and no pull request had been created. No
live or external system was changed.
