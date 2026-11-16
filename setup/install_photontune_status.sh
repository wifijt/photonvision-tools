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
# A second argument is an ssh key, for when the target is not already set up in
# ~/.ssh/config and no agent is loaded - which is the common case on a laptop
# that reaches the Pi by address:
#
#     sh setup/install_photontune_status.sh photon@10.54.59.12 ~/.ssh/photonvision
#
# It needs passwordless sudo on the target (the PhotonVision image has it).
# Re-running it is how you upgrade; it overwrites the three files and nothing
# else, touches no service, and restarts nothing.
set -e

TARGET="$1"
KEY="$2"
if [ -z "$TARGET" ]; then
    echo "usage: sh setup/install_photontune_status.sh user@host [ssh-key]" >&2
    echo "   eg: sh setup/install_photontune_status.sh photon@10.54.59.12" >&2
    echo "       sh setup/install_photontune_status.sh photon@10.54.59.12 ~/.ssh/photonvision" >&2
    exit 2
fi
if [ -n "$KEY" ] && [ ! -f "$KEY" ]; then
    echo "no such key: $KEY" >&2
    exit 2
fi

# Built once and reused, so scp and ssh cannot disagree about how to connect.
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"
if [ -n "$KEY" ]; then
    SSH_OPTS="$SSH_OPTS -i $KEY"
fi

HERE=$(cd "$(dirname "$0")/.." && pwd)
for f in preflight.py monitor/photontune_status.py setup/photontune-wrapper.sh; do
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
# shellcheck disable=SC2086  # SSH_OPTS must word-split
scp -q $SSH_OPTS "$HERE/preflight.py" "$HERE/monitor/photontune_status.py" \
    "$HERE/setup/photontune-wrapper.sh" "$WRAP" "$TARGET:/tmp/" || {
        echo "" >&2
        echo "scp to $TARGET failed. Usually one of:" >&2
        echo "  - the host is unreachable          ping it" >&2
        echo "  - no key for it                    pass one as the 2nd argument" >&2
        echo "  - ~/.ssh/config names a stale address for that name" >&2
        exit 1
    }
WRAPNAME=$(basename "$WRAP")

echo "installing ..."
ssh $SSH_OPTS "$TARGET" "set -e
    sudo mkdir -p /opt/photontune
    sudo cp /tmp/preflight.py /opt/photontune/preflight.py
    sudo cp /tmp/photontune_status.py /opt/photontune/photontune_status.py
    sudo cp /tmp/$WRAPNAME /usr/local/bin/photontune-status
    sudo cp /tmp/photontune-wrapper.sh /usr/local/bin/photontune
    sudo chmod 644 /opt/photontune/preflight.py
    sudo chmod 755 /opt/photontune/photontune_status.py \
                   /usr/local/bin/photontune-status /usr/local/bin/photontune
    # The log has to be writable by whoever runs a tune, not only by root, or
    # the wrapper falls back to sudo on every single run.
    sudo mkdir -p /var/log/photontune
    sudo chown \$(id -un) /var/log/photontune
    # And it must not fill the SD card. An unrotated log on a box that is
    # power-cycled by yanking the plug is how a card dies.
    printf '%s\n' \
      '/var/log/photontune/photontune.log {' \
      '    weekly' '    rotate 4' '    size 2M' '    missingok' \
      '    notifempty' '    compress' '    copytruncate' '}' \
      | sudo tee /etc/logrotate.d/photontune >/dev/null
    rm -f /tmp/$WRAPNAME /tmp/photontune-wrapper.sh"

echo "checking it runs ..."
ssh $SSH_OPTS "$TARGET" 'photontune-status --help >/dev/null' || {
    echo "installed, but it will not start. Does the target have ntcore?" >&2
    echo "  python3 -c 'import ntcore'" >&2
    exit 1
}

echo ""
echo "done. On $TARGET:"
echo "    photontune                     # tune, logged, one line of output"
echo "    photontune -v                  # ...and watch the gain walk"
echo "    photontune-status              # one line: is vision ready?"
echo "    photontune-status -v           # the full report"
echo "    photontune-status --host rio   # on a robot"
echo ""
echo "If it reports no NetworkTables server, that is the expected answer on a"
echo "bench: PhotonVision's NT server is off. It must be off before the box"
echo "meets a roboRIO, so this tool never turns it on."
