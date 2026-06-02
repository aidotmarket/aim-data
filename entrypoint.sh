#!/bin/sh
# AIM Data Backend — Container Entrypoint
# When started as root (default), repair /data ownership — this reclaims
# legacy root-owned volumes left by pre-1001 images — then drop to the
# unprivileged aim_data user via gosu. When started already non-root
# (pinned --user / k8s runAsUser), run directly without chown.
# App + migrations always run as the aim_data uid (1001).

set -e

APP_USER=aim_data
DIRS="/data/uploads /data/processed /data/temp /data/keystore"

if [ "$(id -u)" = "0" ]; then
    mkdir -p $DIRS
    chown -R "${APP_USER}:${APP_USER}" /data
    exec gosu "${APP_USER}" uvicorn app.main:app \
        --host 0.0.0.0 \
        --port "${PORT:-8000}" \
        --workers "${WORKERS:-1}" \
        --log-level info
fi

# Already non-root: cannot chown; best-effort dir create, then run.
mkdir -p $DIRS 2>/dev/null || true
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${WORKERS:-1}" \
    --log-level info
