#!/usr/bin/env python3
#
# Copyright (C) 2026  wifijt
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  This program is distributed WITHOUT ANY WARRANTY; see the GNU
# General Public License at <https://www.gnu.org/licenses/> for details.
#
"""What did photontune actually decide? Read its results off NetworkTables.

photontune does not write a results file.  Its log in the journal shows only
the boot baseline - the sweep's answer is PUBLISHED, to the /PhotonTune table,
and if nothing reads that table the answer is simply gone.  This reads it.

    python3 photontune_status.py --host rio        # on a robot
    python3 photontune_status.py --host pi         # on a bench, PV serving NT
    python3 photontune_status.py --host pi --watch # follow a tune as it runs

WHY A TOOL AND NOT A journalctl INCANTATION
    `journalctl -u photontune` is the right place to look for whether the
    SERVICE is healthy, and the wrong place to look for what it CHOSE.  The
    boot baseline logs a line per camera; a tune publishes its result as JSON
    to NT and logs a summary at best.  Asking the journal for the tune's answer
    gets you the baseline's answer instead, which looks like a plausible answer
    and is not the one you asked for.  That confusion is the reason this exists.

THE FAILURE THIS IS MOSTLY GOING TO REPORT
    "no NetworkTables server".  photontune NEVER creates one - it is a client,
    deliberately, because the server-management API in PhotonVision restarts
    the web server and drops every websocket, which would destroy the tool's
    own control channel.  So:

      - on a robot, the roboRIO is the server and there is nothing to do
      - on a bench with no roboRIO, PhotonVision's own NT server must be ON

    and that switch MUST be off again before the Pi meets a roboRIO, or two
    servers fight and both go quiet.  This tool only reads; it will tell you
    the server is missing and leave the decision to you.

WHAT 'never run' MEANS
    The daemon publishes summary="never run" at startup and only replaces it
    when a sweep finishes.  Seeing it means no tune has happened THIS BOOT -
    the keys are not persisted anywhere, so a reboot erases the last result.
    bootBaselineSummary is separate and does survive into the new boot's keys.

OPTIONS
    --host        the NetworkTables server: 'rio' on a robot, 'pi' on a bench
                  where PhotonVision is serving NT itself  (default rio)
    --table       NT table photontune publishes to  (default PhotonTune)
    --watch       keep printing until interrupted, so a tune can be followed
                  while it runs
    --interval    seconds between --watch samples  (default 2.0)
    --brief       skip the full result JSON, print its size instead
    --settle      seconds to let the table populate after connecting. NT
                  delivers values asynchronously, so reading immediately after
                  isConnected() gets defaults and reports 'never run' for a
                  table that is actually full.  (default 1.0)
    --team        FRC team number, so --host can be 'rio' or '.12'. Defaults to
                  $FRC_TEAM, then ~/.photonvision-tools.conf. Asked for once
                  and remembered if neither is set.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import preflight

import argparse
import json
import sys
import time

#: Everything the daemon publishes, in the order a human wants to read it.
#: (key, kind, label) - kind is what ntcore calls the getter.
TUNE = [
    ("ok",       "bool",   "ok"),
    ("summary",  "string", "summary"),
    ("warnings", "string", "warnings"),
    ("camera",   "string", "camera"),
]
BASELINE = [
    ("bootBaselineRan",     "bool",   "ran"),
    ("bootBaselineOk",      "bool",   "ok"),
    ("bootBaselineSummary", "string", "summary"),
]


def _need_nt():
    """Import ntcore here, naming the interpreter when it is missing.

    The same conda trap preflight exists for: the activated environment is not
    necessarily the one running this file.  Reporting sys.executable turns a
    confusing ModuleNotFoundError into an obvious one.
    """
    try:
        import ntcore                                    # noqa: F401
        return ntcore
    except ImportError:
        sys.exit("photontune_status needs ntcore, and this interpreter has none:\n"
                 "%s\n\n"
                 "    python -m pip install -r requirements-nt.txt\n\n"
                 "Use `python -m pip`, not bare `pip`: if those two disagree the\n"
                 "install lands in an environment this file is not running in."
                 % preflight.interpreter())


def read(table, inst):
    """One snapshot of the table, as a dict of label -> value."""
    def get(key, kind):
        e = table.getEntry(key)
        if kind == "bool":
            return e.getBoolean(False)
        if kind == "double":
            return e.getDouble(0.0)
        return e.getString("")
    out = {"connected": inst.isConnected(),
           "heartbeat": get("heartbeat", "double"),
           "busy": get("busy", "bool"),
           "status": get("status", "string"),
           "progress": get("progress", "double"),
           "result": get("result", "string")}
    for key, kind, label in TUNE:
        out["tune." + label] = get(key, kind)
    for key, kind, label in BASELINE:
        out["base." + label] = get(key, kind)
    return out


def show(s, full_json=True):
    print("heartbeat    %.0f  %s" % (
        s["heartbeat"],
        "alive" if s["heartbeat"] else "NO HEARTBEAT - is photontune.service running?"))
    print("busy         %s%s" % (
        s["busy"],
        "   progress %.0f%%" % (s["progress"] * 100) if s["busy"] else ""))
    print("status       %s" % (s["status"] or "-"))

    print("\nLAST TUNE")
    if s["tune.summary"] in ("", "never run"):
        print("    no tune has run this boot.  The daemon is waiting; set")
        print("    /PhotonTune/run true to start one.  Note the keys are not")
        print("    persisted, so a reboot erases the previous result.")
    for _k, _kind, label in TUNE:
        v = s["tune." + label]
        print("    %-10s %s" % (label, v if v != "" else "(none)"))

    print("\nBOOT BASELINE")
    for _k, _kind, label in BASELINE:
        print("    %-10s %s" % (label, s["base." + label]))

    raw = s["result"]
    if not raw:
        print("\nRESULT       empty - no tune has produced one")
        return
    if not full_json:
        print("\nRESULT       %d bytes of JSON (omit --brief to print it)" % len(raw))
        return
    print("\nRESULT")
    try:
        text = json.dumps(json.loads(raw), indent=2, sort_keys=True)
    except ValueError:
        text = raw                      # not JSON after all; show it verbatim
    for line in text.splitlines():
        print("    " + line)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="rio",
                   help="the NetworkTables server: 'rio' on a robot, 'pi' on a "
                        "bench where PhotonVision is serving NT itself")
    p.add_argument("--table", default="PhotonTune",
                   help="NT table photontune publishes to")
    p.add_argument("--watch", action="store_true",
                   help="keep printing until interrupted, so a tune can be "
                        "followed while it runs")
    p.add_argument("--interval", type=float, default=2.0,
                   help="seconds between --watch samples")
    p.add_argument("--brief", action="store_true",
                   help="skip the full result JSON, print its size instead")
    p.add_argument("--settle", type=float, default=1.0,
                   help="seconds to let the table populate after connecting. "
                        "NT delivers values asynchronously, so reading "
                        "immediately after isConnected() gets defaults and "
                        "reports 'never run' for a table that is actually full.")
    preflight.add_team_argument(p)
    a = p.parse_args()
    a.host = preflight.resolve_host(a.host, a.team)

    ntcore = _need_nt()
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("photontune-status")
    inst.setServer(a.host, ntcore.NetworkTableInstance.kDefaultPort4)
    preflight.require_nt(inst, a.host)
    time.sleep(a.settle)

    table = inst.getTable(a.table)
    print("photontune on %s, table /%s\n" % (a.host, a.table))
    if not a.watch:
        show(read(table, inst), full_json=not a.brief)
        return

    try:
        while True:
            s = read(table, inst)
            print("\033[2J\033[H", end="")        # clear, so it reads as a dashboard
            print("photontune on %s, table /%s   (ctrl-c to stop)\n" % (a.host, a.table))
            show(s, full_json=not a.brief)
            if not s["connected"]:
                print("\n*** NetworkTables connection LOST ***")
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("")


if __name__ == "__main__":
    main()
