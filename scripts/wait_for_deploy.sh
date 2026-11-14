#!/bin/sh
# Waits until a site serves the viewer of this checkout, so that a test run
# after a merge tests that merge and not the release before it
# (.github/workflows/ui-tests.yml, docs/environments.md).
#
#   wait_for_deploy.sh https://dev.vanillasphere.com
#   BEFORE=<commit> PUSHED_AT=<time> MESSAGE=<commit message> \
#       wait_for_deploy.sh https://vanillasphere.com engine
#
# 1. With "engine" (production): if the push changed something the service
#    is built from, wait until the server has started after the push. The
#    time a server started is the second half of `instance` in /api/health.
#    A push that deploys nothing (docs, tests, scripts, .github, Markdown,
#    or "[skip render]" in the message: render.yaml and docs/deployment.md)
#    is not waited for. If no restart is seen in time the run goes on with a
#    warning: the comparison below still catches a viewer that is not there.
# 2. Wait until every script and style sheet under /static/ is byte for byte
#    the one of this checkout. Not there in time: exit 1, because a test
#    run against the previous release would say nothing about this one.
# 3. Wait until /api/health answers three times in a row.
#
# RESTART_LIMIT_S (default 420) and STATIC_LIMIT_S (default 600) bound the
# two waits.
set -eu
cd "$(dirname "$0")/.."
site="${1%/}"
kind="${2:-static}"
restart_limit="${RESTART_LIMIT_S:-420}"
static_limit="${STATIC_LIMIT_S:-600}"
stamp="$(git rev-parse --short=12 HEAD 2>/dev/null || date +%s)"

get() { curl -fsS -m 20 --compressed -H "Cache-Control: no-cache" "$1" 2>/dev/null; }
now() { date +%s; }

# ------------------------------------------------------------ 1. the engine
since=0
if [ "$kind" = engine ]; then
  case "${MESSAGE:-}" in
    *"[skip render]"*|*"[render skip]"*|*"[skip deploy]"*|*"[skip cd]"*)
      echo "The commit message skips the deploy: no restart is awaited." ;;
    *)
      # What render.yaml leaves out of a deploy (buildFilter.ignoredPaths).
      if changed=$(git diff --name-only "${BEFORE:-}" HEAD 2>/dev/null); then
        built=$(printf '%s\n' "$changed" | grep -Ev '^(docs|tests|scripts|\.github)/|\.md$|^render\.dev\.yaml$' || true)
      else
        built="unknown (the commit before the push is not in this checkout)"
      fi
      if [ -n "$built" ]; then
        case "${PUSHED_AT:-}" in
          ""|*[!0-9]*) since=$(date -d "${PUSHED_AT:-now}" +%s 2>/dev/null || now) ;;
          *) since=$PUSHED_AT ;;
        esac
        echo "The push changes what the service is built from:"
        printf '%s\n' "$built" | head -20 | sed 's/^/  /'
      else
        echo "The push changes nothing the service is built from: no restart is awaited."
      fi ;;
  esac
fi
if [ "$since" -gt 0 ]; then
  echo "Waiting for a server started after $(date -u -d "@$since" +%H:%M:%S) UTC at $site"
  start=$(now)
  while :; do
    started=$(get "$site/api/health" | sed -n 's/.*"instance" *: *"[0-9]*-\([0-9]*\)".*/\1/p' || true)
    if [ -n "$started" ] && [ "$started" -ge "$since" ]; then
      echo "The server started at $(date -u -d "@$started" +%H:%M:%S) UTC, $((started - since)) s after the push."
      break
    fi
    if [ $(($(now) - start)) -ge "$restart_limit" ]; then
      echo "::warning title=No restart seen::$site did not restart within $restart_limit s of the push. The tests run against the server that is there."
      break
    fi
    sleep 10
  done
fi

# ------------------------------------------------------------ 2. the viewer
echo "Waiting for $site to serve the viewer of this commit"
start=$(now)
while :; do
  missing=""
  for path in web/*.js web/*.css; do
    name=${path#web/}
    want=$(sha256sum < "$path")
    got=$(get "$site/static/$name?ci=$stamp" | sha256sum || true)
    if [ "$want" != "$got" ]; then missing="$missing $name"; fi
  done
  if [ -z "$missing" ]; then
    echo "Every script and style sheet is the one of this commit ($(($(now) - start)) s)."
    break
  fi
  if [ $(($(now) - start)) -ge "$static_limit" ]; then
    echo "::error title=The site does not serve this commit::After $static_limit s these files at $site/static/ still differ from the commit:$missing. The deploy failed or has not run."
    exit 1
  fi
  echo "  not yet:$missing"
  sleep 10
done

# ------------------------------------------------------------ 3. answering
ok=0
start=$(now)
while [ "$ok" -lt 3 ]; do
  if get "$site/api/health" | grep -q '"ok" *: *true'; then ok=$((ok + 1)); else ok=0; fi
  if [ $(($(now) - start)) -ge 180 ]; then
    echo "::error title=The engine does not answer::$site/api/health did not answer three times in a row within 180 s."
    exit 1
  fi
  if [ "$ok" -lt 3 ]; then sleep 3; fi
done
echo "$site answers. Ready for the tests."
