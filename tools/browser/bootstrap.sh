#!/bin/bash
# Rebuild a working headless Chromium inside the BRIDGE pod (Bash tool), from nothing.
# Why this exists: the bridge pod has node+npx and a route to nova-site:8083, but no root,
# no apt lists, no X/GTK libraries and no fonts. Each of those is solvable without root.
# Takes ~3 minutes and ~1.5GB under /data/workspace/nova-browser (which survives cycles).
set -euo pipefail
R=/data/workspace/nova-browser
mkdir -p "$R"; cd "$R"

# 1. playwright-core + the chromium build it pins
npm init -y >/dev/null 2>&1 || true
npm i playwright-core@1.49.1 --no-audit --no-fund >/dev/null
PLAYWRIGHT_BROWSERS_PATH=$R/browsers node node_modules/playwright-core/cli.js install chromium || true
# ^ exits non-zero on the host-deps check; the download itself has already succeeded.

# 2. apt without root. Two traps: deb.debian.org has no working IPv6 route from this pod,
#    and plain HTTP is blocked outbound — only 443 gets out. So force IPv4 and https URIs.
mkdir -p apt/lists/partial apt/cache/archives/partial apt/state
cat > apt/sources.list <<'EOF'
deb https://deb.debian.org/debian trixie main
deb https://deb.debian.org/debian trixie-updates main
deb https://deb.debian.org/debian-security trixie-security main
EOF
APT=(-o Dir::State::Lists=$R/apt/lists -o Dir::State=$R/apt/state -o Dir::Cache=$R/apt/cache
     -o Dir::Cache::archives=$R/apt/cache/archives -o Dir::Etc::SourceList=$R/apt/sources.list
     -o Dir::Etc::SourceParts=/dev/null -o Debug::NoLocking=1 -o Acquire::ForceIPv4=true)
apt-get "${APT[@]}" update
apt-get "${APT[@]}" install --download-only -y --no-install-recommends \
  libnss3 libnspr4 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
  libasound2t64 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libdbus-1-3 \
  libdrm2 libgbm1 libglib2.0-0t64 libxcb1 libxkbcommon0 libexpat1 \
  fontconfig fonts-dejavu-core libfreetype6 libfontconfig1 fonts-noto-color-emoji

# 3. unpack into a private sysroot and point the loader at it
mkdir -p sysroot
for d in apt/cache/archives/*.deb; do dpkg -x "$d" sysroot; done
find sysroot -name '*.so*' -printf '%h\n' | sort -u | tr '\n' ':' > libdirs.txt

# 4. fonts. dejavu-core is why any text draws at all; noto-color-emoji is why the
#    status and priority pills draw as 🟡 / 🟠 rather than as empty boxes. Cycle 196
#    rendered the issues board without it and every pill on the page was tofu — which
#    reads exactly like a broken stylesheet, and is a standing invitation to "fix" an
#    app that was never wrong. A screenshot that misrepresents the page is worse than
#    no screenshot, because it is acted on.
#    Without this chromium launches, lays the page out, and draws NO text at all —
#    innerText comes back empty and the screenshot looks like an empty wireframe.
mkdir -p fontconf
sed "s|<dir>/usr/share/fonts</dir>|<dir>$R/sysroot/usr/share/fonts</dir>|" \
  sysroot/etc/fonts/fonts.conf > fontconf/fonts.conf

# 5. verify, don't assume: a run that renders no text must fail loudly.
./run.sh / > /tmp/nova-browser-verify.json
python3 - <<'PY'
import json,sys
r=json.load(open('/tmp/nova-browser-verify.json'))
assert r['status']==200, r
assert r['textLen']>500, f"page rendered {r['textLen']} chars of text — fonts are probably missing"
print("OK", r['textLen'], "chars,", len(r['consoleErrors']), "console errors")
PY
