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
"""What did photontune actually decide? Reads the journal, and NT if it is up.

    photontune-status                     # on the Pi, once installed
    python3 photontune_status.py          # from a checkout, on the Pi
    python3 photontune_status.py --ssh photon@10.54.59.12   # from a laptop
    python3 photontune_status.py --watch  # follow a tune live (needs NT)

TWO SOURCES, AND THE JOURNAL IS THE RECORD
    This tool first read only NetworkTables. That was backwards, and it is
    worth writing down so it does not come back.

    NT holds the CURRENT state: summary, ok, warnings, result (full JSON),
    progress, and the bootBaseline* keys. It is the only way to watch a tune
    while it runs. But those keys are not persisted - when the daemon restarts
    they are gone - and on a bench with no roboRIO there may be no NT server at
    all, in which case NT holds nothing and never did.

    The journal holds the HISTORY, across boots, with far more detail: the gain
    walk trial by trial, the reference frame, the gain chosen and the margin
    added, the applied-and-confirmed line, and every refusal - too dark, tags
    falling as gain rises, a camera that reports back something other than what
    it was asked for. That is the material you want when a tune produced a
    number you do not believe.

    So the journal is authoritative for what HAPPENED and NT for what is
    happening NOW. This reads the journal always, and NT when it can.

NT BEING ABSENT IS NOT AN ERROR
    photontune NEVER creates an NT server - it is a client, deliberately,
    because PhotonVision's server-management API restarts the web server and
    drops every websocket, which would destroy the tool's own control channel.
    On a robot the roboRIO is the server. On a bench, PhotonVision's own NT
    server must be on - and OFF again before the box meets a roboRIO, or two
    servers fight and both go quiet.

    An earlier version of this file called require_nt(), which EXITS. With no
    server it died before printing a journal it had never read. A missing
    server is now one line of output, and the journal is reported regardless.

TIMESTAMPS
    The Pi has no RTC battery, and an isolated robot network has no NTP, so the
    clock can be days out and is not corrected across resets. Ordering within a
    boot is reliable; absolute times are not. Boot boundaries are printed so a
    reading can be placed against a power cycle.

OPTIONS
    --host             the NetworkTables server for the LIVE view: 'rio' on a
                       robot, or leave it for this box. The journal is read
                       either way  (default 127.0.0.1)
    --ssh USER@HOST    read the journal from this machine over ssh, for running
                       from a laptop. Needs passwordless sudo there
    --ssh-key FILE     identity for --ssh, when the host is not in
                       ~/.ssh/config and no agent is loaded
    --all              print every journal line, not just the decisions and the
                       most recent tune
    --boots N          restrict the journal to boot N, journalctl's numbering:
                       0 is this boot, -1 the one before
    --lines            most journal lines to read  (default 4000)
    --table            NT table photontune publishes to  (default PhotonTune)
    --watch            follow a tune live. This one DOES need NetworkTables,
                       because progress is only published there
    --interval         seconds between --watch samples  (default 2.0)
    --brief            skip the full result JSON, print its size instead
    --no-journal       skip the journal and report NetworkTables only
    --settle           seconds to let the NT table populate after connecting.
                       NT delivers values asynchronously, so reading
                       immediately after isConnected() gets defaults and
                       reports 'never run' for a table that is full  (default
                       1.0)
    --team             FRC team number, so --host can be 'rio' or '.12'.
                       Defaults to $FRC_TEAM, then ~/.photonvision-tools.conf.
                       Asked for once and remembered if neither is set.
"""
import os as _os, sys as _sys
# The repo layout puts preflight.py one directory up. This file is ALSO
# installed flat on the Pi, in /opt/photontune, where preflight sits beside it
# - so look in both, own directory first.
_here = _os.path.dirname(_os.path.abspath(__file__))
for _d in (_here, _os.path.dirname(_here)):
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
import preflight

import argparse
import json
import shutil
import subprocess
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


#: Lines that carry a DECISION, as opposed to progress chatter. Matched against
#: the message text; keep in step with photontune's own log() calls.
DECISIVE = (
    "lowest gain that sees every tag",   # the answer the walk produced
    "applied",                           # what was actually written, confirmed
    "MISMATCH",                          # camera reports something else - bad
    "could not restore",                 # left in an unknown state - worse
    "cannot see the tags",               # refused: too dark
    "cannot work at",                    # refused: no gain works
    "tags FALL as gain rises",           # refused: the scene is wrong
    "boot baseline",                     # baseline outcome
    "done:",                             # the daemon's end-of-tune line
    "no NetworkTables server",           # why it fell back to the websocket
    "not ready within",                  # PhotonVision never came up
    "publishes NOTHING",                 # the dead-pipeline warning
)

#: Lines that name a camera and its settings. These are the per-camera headers
#: and the trial rows, which are the detail behind a decision.
DETAIL = ("current exposure", "reference  gain", "gain ", "px of smear",
          "baseline:", "clamped to the camera")


def journal(unit="photontune", lines=4000, ssh=None, boots=None, key=None):
    """photontune's journal as [(stamp, text)], oldest first.

    Returns ([], reason) rather than raising: on a laptop there is no journal
    and that is an ordinary outcome, not a failure. `ssh` runs journalctl on
    another machine, which is how the laptop copy is useful at all.

    `sudo -n` so a box without passwordless sudo says so instead of hanging on
    a password prompt nobody is watching.
    """
    if not ssh and not shutil.which("journalctl"):
        # Without this the attempt reaches `sudo -n` first, which fails with
        # "a password is required" - true, and nothing to do with the actual
        # reason, which is that this machine has no systemd journal at all.
        return [], "no systemd journal on this machine (%s)" % sys.platform
    cmd = ["sudo", "-n", "journalctl", "-u", unit, "--no-pager",
           "-o", "short-iso", "-n", str(lines)]
    if boots is not None:
        cmd += ["-b", str(boots)]
    if ssh:
        remote = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if key:
            remote += ["-i", key]
        cmd = remote + [ssh, " ".join(cmd)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = proc.communicate(timeout=30)
    except OSError as e:
        return [], "could not run journalctl (%s)" % e
    except subprocess.TimeoutExpired:
        proc.kill()
        return [], "journalctl timed out"
    if proc.returncode != 0:
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        return [], (detail[-1] if detail else "journalctl exited %d" % proc.returncode)

    return parse_journal(out.decode("utf-8", "replace")), ""


def parse_journal(text):
    """journalctl short-iso output -> [(stamp, message)], oldest first.

    Boot boundaries become a synthetic "@@BOOT <id>" row rather than being
    dropped, because where the power was cut is part of the story - two of
    this Pi's boots show photontune giving up on PhotonVision entirely, and
    that only reads as a crash loop if the reboots either side are visible.

    Split out from journal() so it can be tested without a journal.
    """
    rows = []
    for raw in text.splitlines():
        raw = raw.rstrip()
        if not raw or raw.startswith("-- No entries"):
            continue
        if raw.startswith("-- Boot "):
            rows.append(("", "@@BOOT " + raw[8:].split()[0][:12]))
            continue
        if raw.startswith("--"):            # "-- Logs begin at ... --"
            continue
        parts = raw.split(None, 3)
        # short-iso: "2026-11-16T14:23:01+0000 host unit[pid]: message"
        if len(parts) >= 4 and "T" in parts[0]:
            rows.append((parts[0], parts[3]))
        else:
            rows.append(("", raw))
    return rows


def last_run(rows):
    """The most recent RUN in full, plus every decisive line in the journal.

    "Run" is anchored on the last service start or boot boundary, NOT on the
    last per-camera header. Anchoring on the header truncated a two-camera
    baseline to its second camera: the run reads

        -- Boot x --
        ── Left ──  current exposure ..., gain 60
           baseline: already correct
        ── Right ── current exposure ..., gain 40
           baseline: already correct
        boot baseline done

    and starting at the last "── " marker drops Left entirely while looking
    complete. Starting at the boundary keeps whatever the run contained,
    however many cameras that is.
    """
    anchors = [i for i, (_t, m) in enumerate(rows)
               if m.startswith("@@BOOT") or "Started photontune.service" in m]
    decisive = [(t, m) for t, m in rows
                if not m.startswith("@@BOOT")
                and any(k.lower() in m.lower() for k in DECISIVE)]
    if not anchors:
        return rows, decisive          # no boundary: everything is one run
    return rows[anchors[-1]:], decisive


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


def show_journal(rows, note, show_all=False, advice=()):
    """The journal half of the report. This is the part that survives a reboot."""
    print("JOURNAL  (what happened - persists across boots)")
    if note:
        print("    unavailable: %s" % note)
        for line in advice:
            print("    " + line)
        return
    if not rows:
        print("    no photontune entries at all - has the service ever started?")
        return

    marks = sum(1 for _t, m in rows if m.startswith("@@BOOT"))
    # journalctl prints a boundary BETWEEN boots, so the number of boots
    # represented is one more than the number of markers - unless --lines cut
    # the oldest one off mid-boot, which is why this says "at least".
    print("    %d entries across at least %d boot%s"
          % (len(rows) - marks, marks + 1, "" if marks == 0 else "s"))

    recent, decisive = last_run(rows)
    if not decisive:
        print("\n    nothing decisive logged - no tune and no baseline outcome.")
    else:
        print("\n    DECISIONS, oldest first:")
        for t, m in decisive:
            if m.startswith("@@BOOT"):
                continue
            print("      %-25s %s" % (t or "-", m.strip()))

    if show_all:
        print("\n    EVERY LINE:")
        for t, m in rows:
            if m.startswith("@@BOOT"):
                print("      ---- reboot (%s) ----" % m.split(None, 1)[1])
            else:
                print("      %-25s %s" % (t or "-", m.strip()))
    elif recent:
        print("\n    MOST RECENT RUN, in full:")
        for t, m in recent:
            if m.startswith("@@BOOT"):
                print("      ---- reboot (%s) ----" % m.split(None, 1)[1])
            else:
                print("      %-25s %s" % (t or "-", m.strip()))
        print("\n    (--all for the whole journal)")


def try_nt(host, settle, seconds=4.0):
    """(table, inst, note). A missing server is a note, never an exit.

    require_nt() exits, which is right for a tool that cannot work without NT
    and wrong for this one: it would kill the process before the journal - the
    source that actually persists - was ever printed.
    """
    try:
        import ntcore
    except ImportError:
        return None, None, ("no ntcore in this interpreter (%s) - journal only"
                            % sys.executable)
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("photontune-status")
    inst.setServer(host, ntcore.NetworkTableInstance.kDefaultPort4)
    deadline = time.time() + seconds
    while time.time() < deadline and not inst.isConnected():
        time.sleep(0.1)
    if not inst.isConnected():
        if preflight.is_loopback(host):
            why = ("nothing is serving NetworkTables on this box - "
                   "PhotonVision's NT server is off")
        else:
            why = "no NetworkTables server at %s" % host
        return None, inst, why
    time.sleep(settle)
    return inst.getTable("PhotonTune"), inst, ""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1",
                   help="the NetworkTables server for the LIVE view: 'rio' on "
                        "a robot, or leave it for this box. The journal is "
                        "read either way")
    p.add_argument("--ssh", default=None, metavar="USER@HOST",
                   help="read the journal from this machine over ssh, for "
                        "running from a laptop. Needs passwordless sudo there")
    p.add_argument("--ssh-key", default=None, metavar="FILE",
                   help="identity for --ssh, when the host is not in "
                        "~/.ssh/config and no agent is loaded")
    p.add_argument("--all", action="store_true",
                   help="print every journal line, not just the decisions and "
                        "the most recent tune")
    p.add_argument("--boots", type=int, default=None, metavar="N",
                   help="restrict the journal to boot N, journalctl's "
                        "numbering: 0 is this boot, -1 the one before")
    p.add_argument("--lines", type=int, default=4000,
                   help="most journal lines to read")
    p.add_argument("--table", default="PhotonTune",
                   help="NT table photontune publishes to")
    p.add_argument("--watch", action="store_true",
                   help="follow a tune live. This one DOES need NetworkTables, "
                        "because progress is only published there")
    p.add_argument("--interval", type=float, default=2.0,
                   help="seconds between --watch samples")
    p.add_argument("--brief", action="store_true",
                   help="skip the full result JSON, print its size instead")
    p.add_argument("--no-journal", action="store_true",
                   help="skip the journal and report NetworkTables only")
    p.add_argument("--settle", type=float, default=1.0,
                   help="seconds to let the NT table populate after "
                        "connecting. NT delivers values asynchronously, so "
                        "reading immediately after isConnected() gets defaults "
                        "and reports 'never run' for a table that is full")
    preflight.add_team_argument(p)
    a = p.parse_args()
    a.host = preflight.resolve_host(a.host, a.team)

    if a.watch:
        # Only here is NT mandatory, so only here does a missing one exit.
        ntcore = _need_nt()
        inst = ntcore.NetworkTableInstance.getDefault()
        inst.startClient4("photontune-status")
        inst.setServer(a.host, ntcore.NetworkTableInstance.kDefaultPort4)
        preflight.require_nt(inst, a.host)
        time.sleep(a.settle)
        table = inst.getTable(a.table)
        try:
            while True:
                snap = read(table, inst)
                print("\033[2J\033[H", end="")
                print("photontune on %s, table /%s   (ctrl-c to stop)\n"
                      % (a.host, a.table))
                show(snap, full_json=not a.brief)
                if not snap["connected"]:
                    print("\n*** NetworkTables connection LOST ***")
                time.sleep(a.interval)
        except KeyboardInterrupt:
            print("")
        return

    where = a.ssh or "this machine"
    print("photontune: journal from %s, NetworkTables at %s\n" % (where, a.host))

    if not a.no_journal:
        rows, note = journal(lines=a.lines, ssh=a.ssh, boots=a.boots,
                             key=a.ssh_key)
        # Advice has to match what was actually tried. Telling someone to use
        # --ssh when --ssh is the thing that just failed is worse than silence.
        if a.ssh and "denied" in note.lower():
            advice = ("ssh could not authenticate. Pass the key:",
                      "    --ssh %s --ssh-key ~/.ssh/photonvision" % a.ssh)
        elif a.ssh and note:
            advice = ("The journal is on the Pi, and that ssh did not work.",
                      "Check the host is up, and that sudo there needs no password.")
        else:
            advice = ("This is normal off the Pi. Read the Pi's journal from here:",
                      "    --ssh photon@<pi> --ssh-key ~/.ssh/photonvision",
                      "or run the tool on the Pi, where it needs no arguments.")
        show_journal(rows, note, show_all=a.all, advice=advice)
        print("")

    table, _inst, nt_note = try_nt(a.host, a.settle)
    print("NETWORKTABLES  (what is happening now - erased by a restart)")
    if table is None:
        print("    %s" % nt_note)
        if preflight.is_loopback(a.host):
            print("    Turn it on in the UI (Settings -> Networking -> 'Run")
            print("    NetworkTables Server'), and OFF again before this box")
            print("    meets a roboRIO or the two servers fight.")
        else:
            print("    On a robot the server is the roboRIO: --host rio.")
        print("\n    The journal above is unaffected - it is the real record.")
        return
    show(read(table, _inst), full_json=not a.brief)


if __name__ == "__main__":
    main()
