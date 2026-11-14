# Environments and the path to production

Vanilla Sphere has two addresses: the live site, and a dev site that shows
the viewer of the next release before the public sees it.

| | Production | Dev |
| --- | --- | --- |
| Purpose | The public product | Looking at a change to the viewer (`web/`) first |
| Branch | `main` | `develop` |
| Blueprint file | `render.yaml`, read from `main` | `render.dev.yaml`, read from `develop` |
| Address | <https://vanillasphere.com> | <https://dev.vanillasphere.com> |
| What runs | The web service: engine, measurement pipeline, viewer | A static site: the viewer only |
| Engine, disk, database | Its own | Production's: `/api/` is passed on to the live service |
| Search engines | Pages may be indexed, the API may not | Closed entirely |
| Deploy | About 70 s, with a short interruption | A few seconds, no interruption |

## What dev is, and what it is not

The dev site is the page and its scripts, styles, fonts and data files from
`develop`, in front of the engine of production. A view opened on dev is
gathered, cached and measured by the live service, so dev shows the same
pictures and the same measurements at the same speed, and costs nothing to
run.

Two consequences follow:

- **Dev tries the viewer, not the engine.** A change under `sphereblink/`
  that sits on `develop` does not run anywhere until it is promoted; what
  stands behind it is the test suite. A viewer change that needs an engine
  change of the same release shows its real behaviour only after the
  promotion.
- **Dev acts on production.** "Measure motion and brightness" on dev starts a real
  measurement and stores a real result. There is no test data to spoil
  because there is no second data set.

`scripts/build_dev_site.sh` builds the site: `web/index.html` at `/` (with
`[dev]` in front of the title, the only difference in the page), the rest of
`web/` under `/static/` as the server mounts it, and a `robots.txt` that
disallows everything. `render.dev.yaml` adds three things:

- rewrites that pass `/api/*`, `/docs` and `/openapi.json` on to the live
  site, so the viewer keeps calling its own origin;
- `X-Robots-Tag: noindex, nofollow` on every answer;
- the content security policy the server gives the page (`guard.CSP`). A
  test keeps the two equal, so a script or request the policy would block in
  production is blocked on dev as well.

The dev site redeploys only when `web/`, the build script or
`render.dev.yaml` changes on `develop`.

## The flow

1. **Branch from `develop`.** Name the branch after the kind of change:
   `feat/...` for something new, `fix/...` for a repair, `chore/...` for
   everything else (documentation, configuration, upkeep).
2. **Open the pull request against `develop`.** The checks
   (`.github/workflows/checks.yml`) run the test suite and refuse a branch
   with another kind of name.
3. **Merge to `develop`.** If the viewer changed, the dev site shows it a few
   seconds later, and the [UI tests](#ui-tests-after-every-merge) run
   against it.
4. **Look at the dev site**, and at the `ui-tests` check on the merge commit.
5. **Promote.** Open a pull request from `develop` to `main` and merge it
   with a merge commit, not a squash: `main` and `develop` then keep one
   history and the next promotion contains only what is new. Production
   deploys by itself ([release process](deployment.md#release-process)).
6. **Check production**: the UI tests run against it once the deploy is
   through, next to the
   [post-deploy checks](deployment.md#post-deploy-checks).

`main` takes nothing but `develop`: a pull request to `main` from any other
branch fails the `branch` check. An urgent repair takes the same path, which
costs little since the dev site deploys in seconds.

`[skip render]` in the commit message and in the merge title still skips the
production deploy for a change that cannot affect the running service
([details](deployment.md#merging-without-a-deploy)).

## UI tests after every merge

The browser tests of the viewer are a repository of their own,
[sphere-blink-ui-tests](https://github.com/Vanilla-Sphere/sphere-blink-ui-tests)
(Playwright, a desktop and a phone project). `.github/workflows/ui-tests.yml`
runs them after every merge:

| Merge to | Site under test | Check on the merge commit |
| --- | --- | --- |
| `develop` | <https://dev.vanillasphere.com> | `ui-tests / dev` |
| `main` | <https://vanillasphere.com> | `ui-tests / production` |

A run has two steps.

1. **Wait for the deploy** (`scripts/wait_for_deploy.sh`). The run goes on
   when every script and style sheet the site serves under `/static/` is byte
   for byte the one of the merged commit; if that has not happened after ten
   minutes the run fails, because the deploy failed or did not start. On
   `main` it first waits for the engine to restart when the merge changed
   something the service is built from: the second half of `instance` in
   `/api/health` is the time the server started, and it has to be later than
   the push. A merge that deploys nothing
   ([merging without a deploy](deployment.md#merging-without-a-deploy)) is
   not waited for.
2. **Run the tests** with the action of the other repository
   (`uses: Vanilla-Sphere/sphere-blink-ui-tests@main`). Which tests there
   are, and how they are run, is decided there: a new or changed test needs
   no change here. The run page shows the counts and the failed tests, and
   keeps the HTML report with the traces as an artifact for 14 days.

The tests only read. The cases that start a 7 × 7 field or a measurement
stay off in CI, on dev as well, because dev acts on production. A newer
merge to the same branch cancels the run of the one before it. The workflow
can also be started by hand (Actions > ui-tests > Run workflow) against
either site as it is.

A red check does not undo a merge and does not stop a deploy: it says that
the site now fails a test, and the summary of the run says which. Whether
the viewer or the test is wrong is decided from there.

The other repository is private. It lets this one use its action through
its setting Settings > Actions > General > Access, "Accessible from
repositories in the 'Vanilla-Sphere' organization".

## Asset versions

The page asks for its scripts and styles as `file.js?v=N`, and the live
server lets browsers keep those for a week. The dev site does not repeat
that rule, so it always shows the newest files. A viewer change therefore
still needs `N` raised in `web/index.html` before it is promoted: dev will
look right either way, and returning visitors of the live site would keep
the old files.
