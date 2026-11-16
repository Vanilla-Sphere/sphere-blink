#!/bin/sh
# Starts the service of this checkout on this machine, in the background, and
# returns when it is ready to be tested: the UI tests before a merge run
# against it instead of a deployed site, so that a test run costs the live
# service no traffic (.github/workflows/ui-tests.yml, docs/environments.md).
#
#   sh scripts/start_local_site.sh          # http://127.0.0.1:8000
#   PORT=8010 sh scripts/start_local_site.sh
#   SITE_HTTPS=1 sh scripts/start_local_site.sh   # https://localhost:8000
#   SITE_HTTPS=1 SITE_NAME=vanillasphere.com PORT=443 sh scripts/start_local_site.sh
#   sh scripts/start_local_site.sh stop     # stop the one started here
#
# 1. Start one server process (the service is always one process, see
#    render.yaml). Its output goes to SITE_LOG (default local-site.log), its
#    process id to SITE_PID (default local-site.pid).
# 2. Wait until /api/health answers three times in a row (START_LIMIT_S,
#    default 120).
# 3. Wait until the featured targets are on disk, as they are on the live
#    service: the server fetches them by itself after a start, and a test
#    that opens one should not have to wait for the archive (WARM_LIMIT_S,
#    default 600; not ready in time is a warning, the tests then wait
#    themselves). A cache directory kept from an earlier run
#    (SPHEREX_CACHE_DIR) makes this a matter of seconds.
#
# With SITE_HTTPS=1 the site is served as https://SITE_NAME:PORT, as the
# deployed sites are (the page builds its links from its own address, and
# browsers treat http and https pages differently). A certificate authority
# is made for this run only, in SITE_CERTS (default local-site-certs), and
# added to the certificates Chromium trusts for this user
# (~/.pki/nssdb, needs certutil of libnss3-tools); its key never leaves the
# machine and it is good for two days.
#
# SITE_NAME (default localhost) is the name the site is reached under, and
# the name in its certificate. The UI tests are written for the address of
# the live site: they compare the links of the page with it and leave out
# the cases that start heavy jobs there. So the job of the workflow gives its
# own server that name (SITE_NAME=vanillasphere.com PORT=443, and a line in
# its /etc/hosts that sends the name to 127.0.0.1). The line in /etc/hosts
# and the right to listen on port 443 are the caller's to arrange; this
# script itself always talks to 127.0.0.1, whatever the name resolves to.
#
# The server has no measurement database here (no DATABASE_URL) and keeps
# the settings of sphereblink/config.py.
set -eu
cd "$(dirname "$0")/.."
port="${PORT:-8000}"
certs="${SITE_CERTS:-local-site-certs}"
name="${SITE_NAME:-localhost}"
if [ "${SITE_HTTPS:-0}" != 1 ]; then site="http://127.0.0.1:$port"
elif [ "$port" = 443 ]; then site="https://$name"
else site="https://$name:$port"; fi
log="${SITE_LOG:-local-site.log}"
pid_file="${SITE_PID:-local-site.pid}"
start_limit="${START_LIMIT_S:-120}"
warm_limit="${WARM_LIMIT_S:-600}"

get() {
  if [ "${SITE_HTTPS:-0}" = 1 ]; then curl -fsS -m 20 --cacert "$certs/ca.pem" --resolve "$name:$port:127.0.0.1" "$1" 2>/dev/null
  else curl -fsS -m 20 "$1" 2>/dev/null; fi
}
now() { date +%s; }

if [ "${1:-}" = stop ]; then
  if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    kill "$pid" 2>/dev/null || true
    n=0
    while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 20 ]; do sleep 0.5; n=$((n + 1)); done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$pid_file"
    echo "The local site is stopped."
  fi
  exit 0
fi

# ------------------------------------------------------------ 1. start
tls=""
if [ "${SITE_HTTPS:-0}" = 1 ]; then
  mkdir -p "$certs"
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=sphere-blink local test authority" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" \
    -keyout "$certs/ca-key.pem" -out "$certs/ca.pem" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=$name" \
    -keyout "$certs/key.pem" -out "$certs/site.csr" 2>/dev/null
  printf 'subjectAltName=DNS:%s,DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' "$name" > "$certs/site.ext"
  openssl x509 -req -in "$certs/site.csr" -CA "$certs/ca.pem" -CAkey "$certs/ca-key.pem" -CAcreateserial \
    -days 2 -extfile "$certs/site.ext" -out "$certs/cert.pem" 2>/dev/null
  rm -f "$certs/ca-key.pem" "$certs/site.csr" "$certs/site.ext"   # nothing more is signed with it
  db="sql:$HOME/.pki/nssdb"
  mkdir -p "$HOME/.pki/nssdb"
  certutil -d "$db" -L >/dev/null 2>&1 || certutil -d "$db" -N --empty-password
  certutil -d "$db" -D -n sphere-blink-local-test 2>/dev/null || true
  certutil -d "$db" -A -t "C,," -n sphere-blink-local-test -i "$certs/ca.pem"
  tls="--ssl-keyfile $certs/key.pem --ssl-certfile $certs/cert.pem"
  echo "Made a certificate for $site and added its authority to the ones Chromium trusts."
fi
# shellcheck disable=SC2086  # $tls is two options or nothing
nohup python -m uvicorn sphereblink.main:app --host 127.0.0.1 --port "$port" --workers 1 \
  --timeout-graceful-shutdown 3 $tls > "$log" 2>&1 &
echo $! > "$pid_file"
echo "Started the service of $(git rev-parse --short=12 HEAD 2>/dev/null || echo this checkout) at $site (process $(cat "$pid_file"), log $log)"

# ------------------------------------------------------------ 2. answering
ok=0
start=$(now)
while [ "$ok" -lt 3 ]; do
  if get "$site/api/health" | grep -q '"ok" *: *true'; then ok=$((ok + 1)); else ok=0; fi
  if ! kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "::error title=The local site stopped::The server ended while starting. The end of its log:"
    tail -40 "$log" || true
    exit 1
  fi
  if [ $(($(now) - start)) -ge "$start_limit" ]; then
    echo "::error title=The local site does not answer::$site/api/health did not answer three times in a row within $start_limit s. The end of its log:"
    tail -40 "$log" || true
    exit 1
  fi
  if [ "$ok" -lt 3 ]; then sleep 1; fi
done
echo "$site answers ($(($(now) - start)) s)."

# ------------------------------------------------------------ 3. featured targets
keys=$(get "$site/api/featured" | tr ',' '\n' | sed -n 's/.*"key" *: *"\([^"]*\)".*/\1/p' || true)
start=$(now)
for key in $keys; do
  while :; do
    status=$(get "$site/api/view?target=$key" | sed -n 's/.*"status" *: *"\([a-z]*\)".*/\1/p' || true)
    case "$status" in
      ready) echo "  $key is ready ($(($(now) - start)) s)"; break ;;
      error) echo "::warning title=A featured target did not load::$key could not be fetched from the archive. The tests go on."; break ;;
    esac
    if [ $(($(now) - start)) -ge "$warm_limit" ]; then
      echo "::warning title=Featured targets not ready::Not all featured targets were on disk after $warm_limit s. The tests go on and wait for them themselves."
      break 2
    fi
    sleep 3
  done
done
echo "$site is ready for the tests."
