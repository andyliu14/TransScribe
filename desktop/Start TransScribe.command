#!/bin/sh
set -eu
cd "$(dirname "$0")"
echo 'Starting TransScribe. The first launch downloads Python and the score tools.'
exec ./runtime/uv run --locked --no-dev --python 3.12 transscribe ui --port 0 "$@"
