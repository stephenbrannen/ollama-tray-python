#!/bin/sh
# Builds the test image, runs the end-to-end tests once, then removes the containers.
# The named volume with the pulled model is kept, so later runs skip the download.
set -u
compose_file="$(dirname "$0")/compose.yaml"
docker compose -f "$compose_file" run --rm --build tray "$@"
status=$?
docker compose -f "$compose_file" down
exit "$status"
