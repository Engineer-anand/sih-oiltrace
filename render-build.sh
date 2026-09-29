#!/usr/bin/env bash
# Plan B build for a NATIVE (non-Docker) Render Python service.
#
# Use this only if the Docker build fails. It performs exactly the same
# pip installs as Dockerfile.demo, so the application is identical — there
# is simply no image layer in between.
#
# Render dashboard settings:
#   Runtime            : Python   (NOT Docker)
#   Root Directory     : (repo root, leave empty)
#   Build Command      : bash render-build.sh
#   Start Command      : bash render-start.sh
#   Health Check Path  : /health
#
# Invoke these with an explicit `bash` prefix: git does not reliably preserve
# the executable bit, so `./render-build.sh` can fail with permission denied.
#
# Requires Node on the build image for the frontend bundle. If your Render
# build image has no Node, the backend still starts — the API and /docs work
# fully; only the /app dashboard pages will show the "bundle unavailable"
# status page. In that case serve the frontend from Netlify instead (see
# DEPLOY_TODAY.md Plan B2).

set -e

echo "==> Installing CPU-only PyTorch"
pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1

echo "==> Installing demo Python requirements"
pip install --no-cache-dir -r backend/requirements-demo.txt

echo "==> Building the frontend bundle"
if command -v npm >/dev/null 2>&1; then
  cd frontend
  npm install --no-audit --no-fund
  npm run build
  cd ..
else
  echo "!! npm not found on this build image — skipping the frontend bundle."
  echo "!! The API will still run; /app will serve the status page."
fi

echo "==> Build complete"
