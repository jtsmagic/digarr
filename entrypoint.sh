#!/bin/sh
# Fix /data ownership in case the volume was created by a previous root-run container
chown -R digarr:digarr /data 2>/dev/null || true
# Allow nginx (running as digarr) to write to docker's log streams
chmod 666 /dev/stdout /dev/stderr 2>/dev/null || true
exec gosu digarr "$@"
