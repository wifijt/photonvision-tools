#!/bin/sh
#
# Copyright (C) 2026  wifijt
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  This program is distributed WITHOUT ANY WARRANTY; see the GNU
# General Public License at <https://www.gnu.org/licenses/> for details.
#
# Install `photontune-status` on the coprocessor, so the question "what did
# photontune decide?" can be answered from the Pi's own shell with no laptop
# and no checkout.
#
# Run it FROM A LAPTOP, from the root of this repo:
#
#     sh setup/install_photontune_status.sh photon@10.54.59.12
#     sh setup/install_photontune_status.sh photon@photonvision.local
#
# It needs passwordless sudo on the target (the PhotonVision image has it).
# Re-running it is how you upgrade; it overwrites the three files and nothing
# else, touches no service, and restarts nothing.
set -e

TARGET="$1"
if [ -z "$TARGET" ]; then
    echo "usage: sh setup/install_photontune_status.sh user@host" >&2
    echo "   eg: sh setup/install_photontune_status.sh photon@10.54.59.12" >&2
    exit 2
fi

HERE=$(cd "$(dirname "$0")/.." && pwd)
for f in preflight.py monitor/photontune_status.py; do
    if [ ! -f "$HERE/$f" ]; then
        echo "missing $f - run this from the repo root" >&2
        exit 1
    fi
done

# The wrapper is generated here rather than kept as a file, so it can never
# drift out of step with where this script installs things.
WRAP=$(mktemp)
trap 'rm -f "$WRAP"' EXIT
cat > "$WRAP" <<'WRAPPER'
#!/bin/sh
# photontune-status - print what photontune last decided.
#
# Installed by photonvision-tools (setup/install_photontune_status.sh).
#
# The results live in the /PhotonTune NetworkTables table, NOT in the journal:
# `journalctl -u photontune` shows the boot baseline only, which looks like an
# answer and is a different one.
#
# Defaults to the NT server on this box, which is where photontune's own daemon
# publishes. On a robot, pass --host rio.
for a in "$@"; do
    case "$a" in
        --host|--host=*) exec /usr/bin/python3 /opt/photontune/photontune_status.py "$@" ;;
    esac
done
exec /usr/bin/python3 /opt/photontune/photontune_status.py --host 127.0.0.1 "$@"
WRAPPER

echo "copying to $TARGET:/tmp ..."
scp -q "$HERE/preflight.py" "$HERE/monitor/photontune_status.py" "$WRAP" \
    "$TARGET:/tmp/" || {
        echo "scp failed - is the host reachable, and the key loaded?" >&2
        exit 1
    }
WRAPNAME=$(basename "$WRAP")

echo "installing ..."
ssh "$TARGET" "set -e
    sudo mkdir -p /opt/photontune
    sudo cp /tmp/preflight.py /opt/photontune/preflight.py
    sudo cp /tmp/photontune_status.py /opt/photontune/photontune_status.py
    sudo cp /tmp/$WRAPNAME /usr/local/bin/photontune-status
    sudo chmod 644 /opt/photontune/preflight.py
    sudo chmod 755 /opt/photontune/photontune_status.py /usr/local/bin/photontune-status
    rm -f /tmp/$WRAPNAME"

echo "checking it runs ..."
ssh "$TARGET" 'photontune-status --help >/dev/null' || {
    echo "installed, but it will not start. Does the target have ntcore?" >&2
    echo "  python3 -c 'import ntcore'" >&2
    exit 1
}

echo ""
echo "done. On $TARGET:"
echo "    photontune-status              # the last tune and the boot baseline"
echo "    photontune-status --watch      # follow a tune while it runs"
echo "    photontune-status --host rio   # on a robot"
echo ""
echo "If it reports no NetworkTables server, that is the expected answer on a"
echo "bench: PhotonVision's NT server is off. It must be off before the box"
echo "meets a roboRIO, so this tool never turns it on."
