#!/bin/sh
# Deploy Laser to Cloudflare Pages.
#
# Copies ONLY the three public files to a temp dir and deploys that, so a stray
# .gcal_token.json in the source directory can never be uploaded. Substitutes the
# Google client id from .gcalid (gitignored) so it stays out of the repo.
set -e
cd "$(dirname "$0")"
[ -f .gcalid ] || { echo "missing .gcalid -- put your Google browser client id in it" >&2; exit 1; }
ID=$(tr -d ' \n' < .gcalid)
D=$(mktemp -d)
cp privacy.html icon.png "$D"/
sed "s|__GCAL_ID__|$ID|" index.html > "$D/index.html"
grep -q "__GCAL_ID__" "$D/index.html" && { echo "client id substitution failed" >&2; exit 1; }
echo "deploying: $(ls "$D" | tr '\n' ' ')"
npx --yes wrangler@latest pages deploy "$D" --project-name=todo-focus --commit-dirty=true
