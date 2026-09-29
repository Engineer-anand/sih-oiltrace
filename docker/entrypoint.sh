#!/bin/sh
# OilTrace container entrypoint.
#
# The data volume may pre-date the non-root runtime user (root-owned volume
# created by an older image, or initialized outside Docker's image-copy
# path). Repair ownership as root, then drop privileges so the server itself
# still runs as the unprivileged `oiltrace` user (uid/gid 10001) — the M18
# non-root contract is preserved at runtime.
set -e
# The image default user is root; the server must see a writable HOME
# (matplotlib font cache for PDF reports, etc.) after the drop.
export HOME=/home/oiltrace
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/data
    chown oiltrace:oiltrace /app/data
    # Recursive repair for stale volume contents; individual files that
    # cannot be re-owned must not block boot.
    chown -R oiltrace:oiltrace /app/data || true
    exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
fi
exec "$@"
