#!/bin/sh
# AIM Data Backend — Container Entrypoint
# Runs as root to guarantee /data is writable (including legacy root-owned
# volumes left by pre-1001 images), then drops to the unprivileged aim_data
# user via gosu before exec'ing the app. App + migrations run as aim_data.

set -e

APP_USER=aim_data

# Ensure data dirs exist and are owned by the app user. On a fresh volume
# these are created here; on upgrade from a legacy root-owned volume this
# reclaims ownership so the app can write.
mkdir -p /data/uploads /data/processed /data/temp /data/keystore
chown -R "${APP_USER}:${APP_USER}" /data

START="uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WORKERS:-1} --log-level info"

# Drop privileges only if still root (defensive: allow envs that pin the user).
if [ "$(id -u)" = "0" ]; then
    exec gosu "${APP_USER}" sh -c "exec ${START}"
else
    exec sh -c "exec ${START}"
fi
