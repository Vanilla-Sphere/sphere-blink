#!/bin/sh
# Lays the viewer (web/) out as the static site of the dev environment
# (render.dev.yaml, docs/environments.md): the page at /, its files under
# /static/ as the server of production mounts them, and a robots.txt that
# closes the copy to search engines. Usage: build_dev_site.sh [output dir]
set -eu
cd "$(dirname "$0")/.."
out="${1:-dev-site}"
rm -rf "$out"
mkdir -p "$out"
cp -R web "$out/static"
rm "$out/static/index.html"
# The tab says which site it is; nothing else of the page differs.
sed 's|<title>|<title>[dev] |' web/index.html > "$out/index.html"
cp web/apple-touch-icon.png "$out/favicon.ico"
printf 'User-agent: *\nDisallow: /\n' > "$out/robots.txt"
echo "dev site written to $out: $(find "$out" -type f | wc -l) files"
