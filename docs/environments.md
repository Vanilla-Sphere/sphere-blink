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

`scripts/build_dev_site.sh` builds the site: the first page
(`web/landing.html`) at `/` and the viewer (`web/index.html`) as `sky.html`,
which a rewrite answers `/sky` with, as production does (both with `[dev]` in
front of the title, the only difference in the pages), the rest of
`web/` under `/static/` as the server mounts it, and a `robots.txt` that
disallows everything. `render.dev.yaml` adds four things:

- rewrites that pass `/api/*`, `/docs` and `/openapi.json` on to the live
  site, so the viewer keeps calling its own origin;
- `X-Robots-Tag: noindex, nofollow` on every answer;
- the content security policy the server gives the page (`guard.CSP`), on
  `/` and on `/index.html`, which a static site also answers with the page,
  and on `/sky` and `/sky.html` for the viewer.
  A test keeps the two equal, so a script or request the policy would block
  in production is blocked on dev as well;
- `X-Frame-Options: SAMEORIGIN` on every path, so that no other site can
  show the copy inside a frame ([security](security.md#framing-by-other-sites)).

These headers go on the files of the static site only. What the rewrites
pass on arrives with the headers production gave it.

The dev site redeploys only when `web/`, the build script or
`render.dev.yaml` changes on `develop`.

## The flow

1. **Branch from `develop`.** Name the branch after the kind of change:
   `feat/...` for something new, `fix/...` for a repair, `chore/...` for
   everything else (documentation, configuration, upkeep).
2. **Open the pull request against `develop`.** The checks
   (`.github/workflows/checks.yml`) run the test suite and refuse a branch
   with another kind of name, and the [UI tests](#ui-tests-before-every-merge)
   run against the commit of the pull request, on GitHub.
3. **Merge to `develop`.** If the viewer changed, the dev site shows it a few
   seconds later.
4. **Look at the dev site.**
5. **Promote.** Open a pull request from `develop` to `main`; the UI tests
   run on it once more. Have it approved
   ([what GitHub enforces](#what-github-enforces-on-main)) and merge it with
   a merge commit, not a squash: `main` and `develop` then keep one history
   and the next promotion contains only what is new. Production deploys by
   itself ([release process](deployment.md#release-process)).
6. **Check production** by hand with the
   [post-deploy checks](deployment.md#post-deploy-checks). No automated test
   runs after a deploy.

`main` takes nothing but `develop`: a pull request to `main` from any other
branch fails the `branch` check. An urgent repair takes the same path, which
costs little since the dev site deploys in seconds.

### What GitHub enforces on `main`

The repository ruleset "Protect main" (Settings, Rules, Rulesets) holds
`main` to this flow:

- the branch cannot be deleted or force-pushed;
- a change reaches it only through a pull request, merged with a merge
  commit (squash and rebase are refused);
- the checks `tests` and `branch` must have passed on the pull request. The
  UI tests run on the pull request too but are not required: an outage of
  the archive can turn them red;
- the pull request needs one approving review from somebody with write
  access to the repository, and GitHub does not count the author's own. An
  approval is dismissed when new commits are pushed after it.

One account, the repository owner's, is on the bypass list, for pull
requests only: its owner may merge a pull request that has no approval yet,
and still cannot push to `main` directly. `develop` has no ruleset: there
the checks only show red.

`[skip render]` in the commit message and in the merge title still skips the
production deploy for a change that cannot affect the running service
([details](deployment.md#merging-without-a-deploy)).

## UI tests before every merge

**Rule (2026-11-16): tests run before a deploy, never after it, and never
against production or any site hosted on Render. They run on GitHub, against
a server started inside the job.**

The browser tests of the viewer are a repository of their own,
[sphere-blink-ui-tests](https://github.com/Vanilla-Sphere/sphere-blink-ui-tests)
(Playwright, a desktop and a phone project). `.github/workflows/ui-tests.yml`
runs them on every pull request to `develop` or `main`:

| Pull request to | Site under test | Check on the pull request |
| --- | --- | --- |
| `develop` | the service of the pull request's commit, started inside the job | `ui-tests / before merge` |
| `main` (a promotion) | the same | `ui-tests / before merge` |

Nothing runs on a push: a push to `main` is the deploy, and a test after it
would only find out what is already live. The tests never go through the
deployed sites. A run opens hundreds of pages and downloads about a third of
a gigabyte of pictures, and Render charges for every byte a site sends: in
the first day the runs after merges were about half of the outgoing traffic
of the whole product (a run against the dev site was paid for twice, because
dev passes `/api/` on to production), and on 2026-11-16 runs of the test
repository against production were 3.6 of 4.3 GB in a day.

1. **Start the site** (`scripts/start_local_site.sh`). One server process
   of the commit, with the settings of `sphereblink/config.py` and no
   measurement database. Inside the job, and only there, it is reached as
   `https://vanillasphere.com`: the tests are written for the address of
   the live site (they compare the links of the page with it, and leave out
   the cases that start heavy jobs there), so the runner's `/etc/hosts`
   sends that name, and every other name of our deployed sites
   (`www.` and `dev.vanillasphere.com`, both `onrender.com` names), to
   `127.0.0.1`. The server listens on port 443 with a certificate made for
   that run, which the browser and Node of the tests are told to trust. A
   test that names a deployed site therefore reaches this server or fails;
   it cannot reach Render. The step fails if the name does not lead to the
   server it started. The run goes on when the server answers and the
   featured targets are on disk, as they are on the live service. What the
   server fetches from the archive is kept between runs (a cache of the
   workflow), so later runs start in seconds.
2. **Run the tests** with the action of the other repository
   (`uses: Vanilla-Sphere/sphere-blink-ui-tests@main`). Which tests there
   are, and how they are run, is decided there: a new or changed test needs
   no change here. The run page shows the counts and the failed tests, and
   keeps the HTML report with the traces as an artifact for 14 days. Its
   summary names `https://vanillasphere.com` as the site under test; a line
   above it says that this was the server of the job. The end of the job
   prints the warnings and errors of the server.

This tests the engine of the commit as well as its viewer. What it does not
see is anything that lives only in production: the measurement database
(tracks, discoveries), the frames production already holds, and the deploy
itself; those are the [post-deploy checks](deployment.md#post-deploy-checks),
done by hand.

The tests only read. The cases that start a 7 × 7 field or a measurement
stay off in CI. A newer push to the same pull request cancels the run before
it. The workflow can also be started by hand (Actions > ui-tests > Run
workflow) for a branch; it then runs the same way, against the server of
that branch inside the job. There is no way to point it at a deployed site.

A red check blocks no merge: it says that the commit fails a test, and the
summary of the run says which. Whether the viewer or the test is wrong is
decided from there.

The other repository is private. It lets this one use its action through
its setting Settings > Actions > General > Access, "Accessible from
repositories in the 'Vanilla-Sphere' organization". Its own workflow must
follow the same rule: test against a server started in its job, never
against `vanillasphere.com` or the dev site.

## Asset versions

The page asks for its scripts and styles as `file.js?v=N`, and the live
server lets browsers keep those for a week. The dev site does not repeat
that rule, so it always shows the newest files. A viewer change therefore
still needs `N` raised in `web/index.html` before it is promoted: dev will
look right either way, and returning visitors of the live site would keep
the old files.
