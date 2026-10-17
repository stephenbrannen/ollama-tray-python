#!/bin/sh
set -eu
# `xvfb-run` waits until the X server accepts connections, and `dbus-run-session` gives the run
# its own session bus. Both stop when pytest exits.
exec xvfb-run --auto-servernum --server-args="-screen 0 640x480x24 -nolisten tcp" \
    dbus-run-session -- /opt/driver/bin/pytest -p no:cacheprovider -v -rA /src/e2e "$@"
