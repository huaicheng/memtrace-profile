#!/bin/bash
# Per-node bootstrap for the Google-trace CloudLab cluster (Ubuntu 24.04, c220g5).
# CloudLab runs this as a startup service from /local/repository/setup-host.sh on
# every node. It is idempotent (safe to re-run). It does NOT start any heavy work --
# it only prepares the node; the campaign driver launches transform/analysis later.
#
# Prepares:
#   - local fast scratch at /mnt/nvme (the chunked transform's transient scratch)
#   - the 2TB persistent dataset at /pdata (durable raw zips + text + results)
#   - DynamoRIO prebuilt release at ~/dynamorio-rel (Ubuntu 24.04 has a recent
#     libstdc++, so no GLIBCXX shim is needed, unlike Rocky 9 on ARC)
#   - a Python venv ~/gtrace-venv with numpy + numba + matplotlib
#   - the analysis repo checked out (CloudLab already clones it to /local/repository)
set -euo pipefail

# Run as the experiment user even though startup services run as root.
WHO="${SUDO_USER:-$USER}"
HOME_DIR="$(getent passwd "$WHO" | cut -d: -f6)"
log(){ echo "$(date '+%F %T') [setup-host] $*"; }

# DynamoRIO prebuilt: releases are weekly "cronbuild" tags. The asset is
# DynamoRIO-Linux-<VER>.tar.gz under tag cronbuild-<VER>. Pin a known-good build.
DR_VER="${DR_VER:-11.91.20623}"
DR_TARBALL="DynamoRIO-Linux-${DR_VER}.tar.gz"
DR_URL="https://github.com/DynamoRIO/dynamorio/releases/download/cronbuild-${DR_VER}/${DR_TARBALL}"

# The experiment user's PRIMARY GROUP (e.g. nestfarm-PG0 on CloudLab) -- NOT "$WHO":
# CloudLab has no per-user group named after the login, so chown "$WHO":"$WHO" fails
# ("invalid group") and, under set -e, aborts the whole setup before DR/venv install.
WHO_GRP="$(id -gn "$WHO" 2>/dev/null || echo "$WHO")"

log "node $(hostname): user=$WHO group=$WHO_GRP home=$HOME_DIR"

# ---- 1. base packages ----
export DEBIAN_FRONTEND=noninteractive
log "installing base packages"
apt-get update -qq
# libsnappy1v5 is REQUIRED: DynamoRIO's drmemtrace links libsnappy.so.1 for trace
# decompression and won't even print -version without it (Ubuntu 24.04 omits it).
apt-get install -y -qq python3-venv python3-dev build-essential unzip curl \
    pigz zstd util-linux coreutils libsnappy1v5 >/dev/null

# ---- 2. local scratch /mnt/nvme (CloudLab blockstore mounts it; ensure perms) ----
if mountpoint -q /mnt/nvme; then
  log "/mnt/nvme mounted"
else
  log "WARN: /mnt/nvme not a mountpoint (blockstore may still be attaching)"
  mkdir -p /mnt/nvme
fi
mkdir -p /mnt/nvme/scratch
chown -R "$WHO":"$WHO_GRP" /mnt/nvme

# ---- 3. dataset /pdata (RemoteBlockstore mounts it rw; ensure dirs + perms) ----
if mountpoint -q /pdata; then
  log "/pdata (dataset) mounted"
  mkdir -p /pdata/raw /pdata/text /pdata/results /pdata/figures
  chown -R "$WHO":"$WHO_GRP" /pdata
else
  log "WARN: /pdata not mounted yet (dataset link may still be coming up)"
fi

# ---- 4. DynamoRIO prebuilt release -> ~/dynamorio-rel ----
if [ -x "$HOME_DIR/dynamorio-rel/tools/bin64/drmemtrace_launcher" ]; then
  log "DynamoRIO already present"
else
  log "fetching DynamoRIO $DR_VER"
  sudo -u "$WHO" bash -c "
    cd '$HOME_DIR' &&
    curl -fsSL '$DR_URL' -o '/tmp/$DR_TARBALL' &&
    tar xzf '/tmp/$DR_TARBALL' &&
    rm -rf dynamorio-rel &&
    mv 'DynamoRIO-Linux-${DR_VER}' dynamorio-rel &&
    rm -f '/tmp/$DR_TARBALL'
  "
  if [ -x "$HOME_DIR/dynamorio-rel/tools/bin64/drmemtrace_launcher" ]; then
    log "DynamoRIO installed"
  else
    log "ERROR: DynamoRIO launcher missing after install"
  fi
fi

# ---- 5. Python venv ~/gtrace-venv (numpy + numba + matplotlib) ----
if [ -x "$HOME_DIR/gtrace-venv/bin/python" ] && \
   sudo -u "$WHO" "$HOME_DIR/gtrace-venv/bin/python" -c "import numpy,numba,matplotlib" 2>/dev/null; then
  log "venv already complete"
else
  log "building venv"
  sudo -u "$WHO" bash -c "
    python3 -m venv '$HOME_DIR/gtrace-venv' &&
    '$HOME_DIR/gtrace-venv/bin/pip' install -q --upgrade pip &&
    '$HOME_DIR/gtrace-venv/bin/pip' install -q numpy numba matplotlib
  "
  log "venv built"
fi

# ---- 6. analysis code: prefer the dataset-independent repo clone ----
# CloudLab already cloned the profile's repo to /local/repository. Symlink it to a
# stable path the campaign scripts expect, and copy nothing (read from the clone).
REPO=/local/repository
if [ -d "$REPO/scripts" ]; then
  ln -sfn "$REPO" "$HOME_DIR/google-trace"
  chown -h "$WHO":"$WHO_GRP" "$HOME_DIR/google-trace"
  log "analysis repo linked: ~/google-trace -> $REPO"
else
  log "WARN: $REPO has no scripts/ (profile repo not the analysis repo?)"
fi

log "node $(hostname) bootstrap complete"
