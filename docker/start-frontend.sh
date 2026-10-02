#!/bin/sh
# Startup for the frontend container (see compose.yaml).
set -e

# node_modules lives in a Docker volume that outlives rebuilds, so reinstall
# whenever package-lock.json has changed since it was last installed.
if ! cmp -s package-lock.json node_modules/.lock-stamp; then
  npm ci
  cp package-lock.json node_modules/.lock-stamp
fi

exec npm run dev -- --host 0.0.0.0
