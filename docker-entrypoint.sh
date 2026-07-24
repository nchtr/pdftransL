#!/bin/sh
set -eu

# Named Docker volumes are created as root.  Fix only the explicitly mounted
# data directory, then drop privileges before Django, Celery, bot and parsers
# see untrusted files.
mkdir -p /data
chown -R appuser:appuser /data
exec su -s /bin/sh appuser -c 'exec "$0" "$@"' "$@"
