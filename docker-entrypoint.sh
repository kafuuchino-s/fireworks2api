#!/bin/sh
set -eu

APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
APP_DATA_DIR="${DATA_DIR:-/app/data}"

mkdir -p "$APP_DATA_DIR"
chown -R "$APP_UID:$APP_GID" "$APP_DATA_DIR"

exec gosu appuser "$@"
