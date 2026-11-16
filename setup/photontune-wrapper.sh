#!/bin/sh
# photontune - run the tuner and print one line about it.
#
# Installed by photonvision-tools (setup/install_photontune_status.sh).
#
# This does NOT do the logging. photontune.py logs itself, to
# /var/log/photontune/photontune.log, on every invocation - including
# `python3 /opt/photontune/photontune.py`, which is what people actually type
# and which no wrapper can intercept. Logging here as well would duplicate
# every line.
#
# All this adds is the quiet default: the tuner prints ~100 lines of gain-walk
# detail, which is correct and which nobody in a pit reads. So by default this
# shows the SAME one line photontune-status shows - by calling
# photontune-status, not by reimplementing it, so the two cannot disagree.
#
#     photontune           # tune, then one line
#     photontune -v        # ...and the tuner's output as it goes
#     photontune -vvv      # ...and the full status report afterwards
#     photontune --help    # the tuner's own options
#
# Everything that is not -v/-vv/-vvv goes straight through to the tuner. The
# log is written either way.
set -e

TUNER="${PHOTONTUNE_TUNER:-/opt/photontune/photontune.py}"
STATUS="${PHOTONTUNE_STATUS:-/opt/photontune/photontune_status.py}"

if [ ! -f "$TUNER" ]; then
    echo "photontune is not installed at $TUNER" >&2
    exit 1
fi

V=0
ARGS=""
for a in "$@"; do
    case "$a" in
        -h|--help|--explain) exec /usr/bin/python3 "$TUNER" "$@" ;;
        -v)   V=1 ;;
        -vv)  V=2 ;;
        -vvv) V=3 ;;
        *)    ARGS="$ARGS $a" ;;
    esac
done

set +e
if [ "$V" -ge 1 ]; then
    /usr/bin/python3 -u "$TUNER" $ARGS
else
    echo "tuning... (~45 s; -v to watch)"
    # Swallowed, not discarded: photontune.py has already written every line of
    # this to its log, so nothing is lost and photontune-status reads it back
    # in a moment.
    /usr/bin/python3 -u "$TUNER" $ARGS >/dev/null 2>&1
fi
TUNE_EXIT=$?
set -e

echo ""
if [ ! -f "$STATUS" ]; then
    echo "photontune exit=$TUNE_EXIT  (photontune_status not installed)"
    exit "$TUNE_EXIT"
fi

set +e
if [ "$V" -ge 3 ]; then
    /usr/bin/python3 "$STATUS" -v
else
    /usr/bin/python3 "$STATUS"
fi
STATUS_EXIT=$?
set -e

# A tuner that failed is the louder fact; otherwise defer to the verdict.
[ "$TUNE_EXIT" -ne 0 ] && exit "$TUNE_EXIT"
exit "$STATUS_EXIT"
