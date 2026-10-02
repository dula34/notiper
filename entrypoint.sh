#!/bin/sh
# Runs as root only long enough to make the data directory writable for PUID:PGID,
# then starts Notiper as that unprivileged user (e.g. Synology: PUID=1026, PGID=100).
set -e

DATA_DIR="${NOTIPER_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"
    mkdir -p "$DATA_DIR"
    if [ "$(stat -c %u:%g "$DATA_DIR")" != "$PUID:$PGID" ] || [ -n "$(find "$DATA_DIR" ! -user "$PUID" -print -quit)" ]; then
        echo "Setting owner of $DATA_DIR to $PUID:$PGID"
        chown -R "$PUID:$PGID" "$DATA_DIR"
    fi
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

# Already started as a non-root user (compose `user:`): the directory must be writable for it.
exec "$@"
