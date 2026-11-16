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
    -v, --verbose      the full report: every decision, the most recent run in
                       full, and the NetworkTables table. Without this you get
                       one line
    --all              implies -v, and prints EVERY journal line with the boot
                       boundaries
    --pv-host          the box running PhotonVision, checked for a stray NT
                       server. Not the same question as --host: on a robot
                       --host is the roboRIO and serving NT is correct, while
                       PhotonVision serving it at the same time is the silent
                       failure being looked for  (default 127.0.0.1)
    --boots N          restrict the journal to boot N, journalctl's numbering:
                       0 is this boot, -1 the one before
    --lines            most journal lines to read  (default 4000)
    --table            NT table photontune publishes to  (default PhotonTune)
    --tune             ask the running SERVICE to tune, and follow it. With a
                       shell on the box, `python3
                       /opt/photontune/photontune.py` is simpler and shows
                       more. Use this when the result must be RECORDED: a
                       direct run prints to your terminal and is logged
                       nowhere, so this tool cannot see it afterwards. WRITES
                       /PhotonTune/run. Needs NetworkTables
    --watch            follow a tune live. This one DOES need NetworkTables,
                       because progress is only published there
    --interval         seconds between --watch samples  (default 2.0)
    --brief            skip the full result JSON, print its size instead
    --log              the `photontune` wrapper's local log, which is the only
                       record of a tune run by hand - journald sees the service
                       and nothing else
    --no-journal       skip the systemd journal (the service's runs). The local
                       log is still read
    --no-local         skip the local log (runs by hand). The journal is still
                       read
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


#: Where the `photontune` wrapper appends a timestamped copy of every run.
LOCAL_LOG = "/var/log/photontune/photontune.log"


def local_log(path=LOCAL_LOG, lines=4000, ssh=None, key=None):
    """The wrapper's log as [(stamp, message)], oldest first.

    The journal only ever holds the SERVICE's runs. This file holds the ones
    somebody typed, which were previously recorded nowhere at all. Both are
    read and merged, because either can contain the most recent tune and
    picking one source means being wrong half the time.
    """
    cmd = ["tail", "-n", str(lines), path]
    if ssh:
        remote = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if key:
            remote += ["-i", key]
        cmd = remote + [ssh, " ".join(cmd)]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = proc.communicate(timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return [], "could not read %s" % path
    if proc.returncode != 0:
        detail = (err or b"").decode("utf-8", "replace").strip()
        if "No such file" in detail:
            # NOT an empty string. An absent log means "no tune has ever been
            # recorded here", which is completely different from "I read the
            # log and it was empty". Returning "" made local_seen True, which
            # turned off the cautious wording and let this tool assert "No tune
            # since boot" about a tune that had just run and FAILED.
            return [], "no log at %s yet" % path
        return [], detail.splitlines()[-1] if detail else "tail failed"
    return parse_local(out.decode("utf-8", "replace")), ""


def parse_local(text):
    """"<iso> <message>" per line, which is what the wrapper writes."""
    rows = []
    for raw in text.splitlines():
        raw = raw.rstrip()
        if not raw:
            continue
        parts = raw.split(None, 1)
        if len(parts) == 2 and "T" in parts[0] and parts[0][:2] == "20":
            rows.append((parts[0], parts[1]))
        else:
            rows.append(("", raw))
    return rows


def merge(journal_rows, local_rows):
    """Both sources in one timeline, oldest first.

    Sorted on the stamp, which is safe because both come off the same box and
    therefore share its clock - including when that clock is wrong. Rows with
    no stamp keep their position relative to the row above.
    """
    if not local_rows:
        return journal_rows
    if not journal_rows:
        return local_rows
    tagged = []
    for src, rows in (("j", journal_rows), ("l", local_rows)):
        last = ""
        for i, (stamp, msg) in enumerate(rows):
            if stamp:
                last = stamp
            tagged.append((last, src, i, msg))
    tagged.sort(key=lambda r: (r[0], r[1], r[2]))
    return [(stamp, msg) for stamp, _src, _i, msg in tagged]


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
               if m.startswith("@@BOOT")
               or "Started photontune.service" in m
               or "photontune run started" in m          # the tuner's own banner
               or "photontune manual run started" in m]   # the old wrapper's
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



# ---------------------------------------------------------------------------
# The one line that actually gets read.
#
# A teenager in a pit is not going to read 70 lines of journal. They need one
# sentence that says whether vision is ready, and the one safety item that
# will silently kill a match. Everything below builds that sentence from the
# journal, and every phrase it can produce is grounded in a real log string
# from photontune - nothing here is inferred from a metric that does not exist.
# ---------------------------------------------------------------------------

#: Applied gain is the light measure. The walk visits six points from 0 to
#: --max-gain (default 100), so the steps are 20. Low gain means the scene had
#: light to spare; the top of the range means it barely worked.
GAIN_GOOD = 20.0        # at or below: plenty of light
GAIN_MARGINAL = 80.0    # at or above: running out of range

#: What this tool can and cannot conclude from a silent journal.
#:
#: journald captures the SERVICE's output. photontune writes no logfile, so a
#: tune somebody ran by hand - `python3 /opt/photontune/photontune.py`, which
#: is the obvious thing to do with a shell - prints to their terminal and is
#: recorded NOWHERE this tool can see. "NO TUNE since last boot" was therefore
#: a claim about the world made from one of the two places a tune can happen.
#: It is now a claim about the record, which is all the record can support.
NOT_RECORDED = ("No tune RECORDED since boot - and there is no local log here, "
                "so a tune somebody ran by hand would be INVISIBLE to this. "
                "Do not read this as 'no tune happened'")
#: ...but once the wrapper's log exists, hand runs ARE recorded, and the caveat
#: above would be stale reassurance in the wrong direction - telling someone a
#: tune might have happened invisibly when it could not have.
NOT_RUN = "No tune since boot - run `photontune` to do one"

#: Phrases photontune logs when the scene defeats it outright.
TOO_DARK = ("cannot see the tags", "cannot work at", "scene is too")

#: The whole sweep saw nothing: every gain AND a shorter and a longer exposure.
#:
#: This is NOT the same as "too dark" and must not be reported as it. Tags that
#: are present but dim give FALLING tag counts as light runs out; a flat 0.00
#: across every gain from 0 to 100 and every exposure either side means nothing
#: was in frame to see. The tuner's own line says "a LIGHTING or CONFIG problem"
#: without separating them, and a run on a bench with no tag in view was
#: reported as "light levels TOO LOW for competition" - which sends somebody to
#: add lights when the answer is to point a camera at a tag.
NOTHING_SEEN = ("and neither a shorter nor a longer exposure did",
                "nothing worked at any gain or exposure")
#: ...and when it cannot find what it was told to tune.
NO_CAMERA = ("no cameras reported by photonvision", "no camera matched",
             "no cameras", "no camerasettings")


def _age(stamp, now=None):
    """(seconds, text) for a short-iso stamp. (None, reason) if it cannot."""
    import datetime
    if not stamp:
        return None, "time unknown"
    try:
        # "2026-11-16T14:23:01+0000" - %z wants no colon, which short-iso gives
        when = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None, "time unreadable"
    now = now or datetime.datetime.now(datetime.timezone.utc)
    secs = (now - when).total_seconds()
    if secs < -60:
        # The Pi has no RTC battery and no NTP on an isolated network, so a
        # stamp in the future means the clock is wrong, not that time moved.
        return secs, "clock is wrong (log is ahead of now)"
    if secs < 90:
        return secs, "just now"
    if secs < 5400:
        return secs, "%d min ago" % round(secs / 60.0)
    if secs < 172800:
        return secs, "%d h ago" % round(secs / 3600.0)
    return secs, "%d days ago" % round(secs / 86400.0)


def uptime_seconds(ssh=None, key=None):
    """How long this box has been up, or None.

    Used to catch a lying clock. The Pi has no RTC battery and an isolated
    robot network has no NTP, so entries written before someone fixes the date
    carry a timestamp that is days out - and "applied 18 days ago" about a
    baseline that ran forty minutes ago reads as stale vision when it is not.
    An entry from the CURRENT boot cannot be older than the uptime, which
    settles it without guessing.
    """
    if ssh:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if key:
            cmd += ["-i", key]
        cmd += [ssh, "cat /proc/uptime"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, _err = proc.communicate(timeout=20)
            if proc.returncode != 0:
                return None
            return float(out.split()[0])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return None
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except (IOError, OSError, ValueError, IndexError):
        return None                      # not Linux, so no claim either way


def _cameras_applied(run):
    """Camera names that got a gain/exposure APPLIED in this run, in order."""
    names, current = [], None
    for _t, m in run:
        txt = m.strip()
        if txt.startswith("\u2500\u2500 "):               # "── Left ──  current exposure..."
            parts = txt.split("\u2500\u2500")
            current = parts[1].strip() if len(parts) > 2 else None
        elif current and "applied" in txt.lower() and "confirmed" in txt.lower():
            if current not in names:
                names.append(current)
    return names


def _applied_gain(run):
    """The highest gain applied in this run, which is the worst-lit camera."""
    import re
    best = None
    for _t, m in run:
        hit = re.search(r"applied gain\s+(\d+(?:\.\d+)?)", m, re.I)
        if hit:
            g = float(hit.group(1))
            best = g if best is None else max(best, g)
    return best


def _light_phrase(gain):
    if gain is None:
        return None
    if gain <= GAIN_GOOD:
        return "light levels optimal"
    if gain >= GAIN_MARGINAL:
        return "light levels MARGINAL (gain %g of %g - almost out of range)" % (
            gain, GAIN_MARGINAL + 20)
    return "light levels workable (gain %g)" % gain


def _join(names):
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def verdict(rows, note="", nt_local_on=False, now=None, uptime=None,
            local_seen=False):
    """One line. What a teenager reads, and nothing else.

    Returns (text, ready) - ready is False whenever someone should look before
    trusting vision, so a caller can colour or exit on it.
    """
    danger = "  *** DANGER: local NT server is ON - turn it OFF before the robot ***" \
             if nt_local_on else ""

    if note:
        return ("cannot tell - no photontune log here (%s)" % note) + danger, False
    if not rows:
        return "photontune has NEVER run on this box" + danger, False

    run, _decisive = last_run(rows)
    raw = " ".join(m for _t, m in run)      # case preserved: camera names
    text = raw.lower()                      # case folded: keyword matching
    stamp = next((t for t, m in reversed(run) if t), "")
    secs, when = _age(stamp, now)
    # last_run anchors on the current boot's start, so anything in it happened
    # during this uptime. A bigger age than that is the clock, not the event.
    if (uptime is not None and secs is not None
            and secs > uptime + 300):
        when = "earlier this boot (the log's clock was wrong)"

    # Order matters: the worst thing true is the thing to say.
    if any(k in text for k in NO_CAMERA):
        # "no camera matched 'Left'. Available: Right" - naming the one that is
        # missing is the difference between a fixable problem and a mystery.
        import re
        named = re.search(r"(?i)no camera matched ['\"]?([^'\".]+)", raw)
        if named:
            return ("ERROR %s camera NOT FOUND (%s)"
                    % (named.group(1).strip(), when)) + danger, False
        return ("ERROR cameras not found (%s)" % when) + danger, False

    if any(k in text for k in NOTHING_SEEN):
        return ("tune FAILED %s - NO TAGS SEEN at any gain or exposure. Is a "
                "tag actually in view? If it is, the scene is too dark for any "
                "setting" % when) + danger, False

    if any(k in text for k in TOO_DARK):
        return ("tune FAILED %s - light levels TOO LOW for competition" % when) + danger, False

    if "mismatch" in text:
        return ("tune ran %s but the camera did NOT take the settings - check it by hand"
                % when) + danger, False

    if "could not restore" in text:
        return ("tune ABORTED %s and could not restore the exposure - check it by hand"
                % when) + danger, False

    applied = _cameras_applied(run)
    if applied:
        light = _light_phrase(_applied_gain(run))
        bits = ["tune ran %s" % when]
        if light:
            bits.append(light)
        bits.append("applied to %s" % _join(applied))
        return ", ".join(bits) + danger, not nt_local_on

    if "not ready within" in text:
        return ("PhotonVision never came up %s - photontune gave up, nothing was set"
                % when) + danger, False

    if "baseline" in text:
        ok = "ok=true" in text
        cams = []
        for _t, m in run:
            if m.strip().lower().startswith("baseline for"):
                cams = [c.strip() for c in m.split(":", 1)[-1].split(",") if c.strip()]
        where = " on %s" % _join(cams) if cams else ""
        if not ok:
            return ("baseline FAILED %s%s" % (when, where)) + danger, False

        # "already correct" is the baseline RUNNING and finding nothing to do.
        # Reporting that as if nothing happened is wrong: it ran, it checked,
        # and the settings were right. Saying "no tune" about it also overstates
        # what this tool knows - see NOT_RECORDED below.
        unchanged = "already correct" in text
        what = ("baseline checked%s %s - settings already correct, nothing "
                "needed changing" % (where, when)) if unchanged else (
               "baseline applied%s %s" % (where, when))
        # local_seen means the wrapper's log was readable, so a hand-run tune
        # WOULD be in it. Only without that can this tool be blind to one.
        return (what + ". " + (NOT_RUN if local_seen else NOT_RECORDED)) + danger, False

    return ("photontune started %s; nothing else recorded since. %s"
            % (when, NOT_RUN if local_seen else NOT_RECORDED)) + danger, False


def start_tune(table, inst, timeout=240.0, interval=2.0):
    """Ask the RUNNING SERVICE to tune, by setting /PhotonTune/run true.

    If you have a shell on the box, this is NOT the easy way to tune. Just run
    the tuner:

        python3 /opt/photontune/photontune.py        # a real tune, ~45 s

    The run lock is taken inside photontune's run() and released in finally,
    so an idle daemon does not hold it and a direct run is not blocked. The
    lock only stops two tunes colliding over the same cameras, which is what
    it is for.

    This flag exists for ONE reason, and it is not convenience: a direct run
    prints to a terminal and is recorded nowhere. journald captures the
    SERVICE's output, and photontune writes no logfile - so a tune you run by
    hand is invisible to this tool afterwards, and `photontune-status` will
    still say "NO TUNE since last boot" because as far as the journal is
    concerned there was not one. Going through the service puts the result in
    the journal and in NT, where it can be read back later and by someone
    else.

    So: tuning at the bench, run photontune directly and watch it. Tuning
    something that needs to be ACCOUNTABLE afterwards - before a match, or for
    a student who will be asked what the cameras are set to - go through the
    service.

    photontune clears the flag itself when it finishes, and refuses outright
    while the robot is enabled. That interlock is its own and is not worked
    around from here.
    """
    import ntcore
    pub = table.getBooleanTopic("run").publish()
    pub.set(True, int(ntcore._now()))
    print("asked photontune to tune (/PhotonTune/run = true)")
    print("it refuses while the robot is enabled; disable it if nothing happens.\n")

    started = time.time()
    seen_busy = False
    last = ""
    while time.time() - started < timeout:
        snap = read(table, inst)
        if snap["busy"]:
            seen_busy = True
        line = "%-6s %3.0f%%  %s" % ("busy" if snap["busy"] else "idle",
                                     snap["progress"] * 100,
                                     (snap["status"] or "")[:70])
        if line != last:
            print("   " + line)
            last = line
        if seen_busy and not snap["busy"]:
            break
        time.sleep(interval)
    else:
        print("   gave up waiting after %.0f s - the tune may still be running"
              % timeout)
        return False

    snap = read(table, inst)
    print("")
    show(snap, full_json=False)
    return bool(snap["ok"])


def nt_server_here(host, port=5810, timeout=2.0):
    """Is something serving NetworkTables on `host`? A stray one loses matches.

    PhotonVision's own NT server must be OFF whenever a roboRIO is present, or
    the two fight and both go quiet - with no error anywhere. That failure is
    silent, costs a match, and is one TCP connect to detect, so it is checked
    on every run and shouted about rather than mentioned.
    """
    ok, _detail = preflight.reachable(host, port, timeout)
    return ok


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
    p.add_argument("-v", "--verbose", action="store_true",
                   help="the full report: every decision, the most recent run "
                        "in full, and the NetworkTables table. Without this "
                        "you get one line")
    p.add_argument("--all", action="store_true",
                   help="implies -v, and prints EVERY journal line with the "
                        "boot boundaries")
    p.add_argument("--pv-host", default="127.0.0.1",
                   help="the box running PhotonVision, checked for a stray NT "
                        "server. Not the same question as --host: on a robot "
                        "--host is the roboRIO and serving NT is correct, "
                        "while PhotonVision serving it at the same time is the "
                        "silent failure being looked for")
    p.add_argument("--boots", type=int, default=None, metavar="N",
                   help="restrict the journal to boot N, journalctl's "
                        "numbering: 0 is this boot, -1 the one before")
    p.add_argument("--lines", type=int, default=4000,
                   help="most journal lines to read")
    p.add_argument("--table", default="PhotonTune",
                   help="NT table photontune publishes to")
    p.add_argument("--tune", action="store_true",
                   help="ask the running SERVICE to tune, and follow it. With "
                        "a shell on the box, `python3 "
                        "/opt/photontune/photontune.py` is simpler and shows "
                        "more. Use this when the result must be RECORDED: a "
                        "direct run prints to your terminal and is logged "
                        "nowhere, so this tool cannot see it afterwards. "
                        "WRITES /PhotonTune/run. Needs NetworkTables")
    p.add_argument("--watch", action="store_true",
                   help="follow a tune live. This one DOES need NetworkTables, "
                        "because progress is only published there")
    p.add_argument("--interval", type=float, default=2.0,
                   help="seconds between --watch samples")
    p.add_argument("--brief", action="store_true",
                   help="skip the full result JSON, print its size instead")
    p.add_argument("--log", default=LOCAL_LOG,
                   help="the `photontune` wrapper's local log, which is the "
                        "only record of a tune run by hand - journald sees "
                        "the service and nothing else")
    p.add_argument("--no-journal", action="store_true",
                   help="skip the systemd journal (the service's runs). The "
                        "local log is still read")
    p.add_argument("--no-local", action="store_true",
                   help="skip the local log (runs by hand). The journal is "
                        "still read")
    p.add_argument("--settle", type=float, default=1.0,
                   help="seconds to let the NT table populate after "
                        "connecting. NT delivers values asynchronously, so "
                        "reading immediately after isConnected() gets defaults "
                        "and reports 'never run' for a table that is full")
    preflight.add_team_argument(p)
    a = p.parse_args()
    a.host = preflight.resolve_host(a.host, a.team)

    if a.watch or a.tune:
        # These two need NT, so these two are the only ones that exit without it.
        ntcore = _need_nt()
        inst = ntcore.NetworkTableInstance.getDefault()
        inst.startClient4("photontune-status")
        inst.setServer(a.host, ntcore.NetworkTableInstance.kDefaultPort4)
        preflight.require_nt(inst, a.host)
        time.sleep(a.settle)
        table = inst.getTable(a.table)
        if a.tune:
            sys.exit(0 if start_tune(table, inst) else 1)
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

    # --ssh means the Pi is elsewhere, so that is the box to check for a stray
    # NT server, not this laptop.
    pv_host = a.pv_host
    if a.ssh and pv_host == "127.0.0.1":
        pv_host = a.ssh.split("@")[-1]
    nt_local_on = nt_server_here(pv_host)

    # Two independent sources, two independent switches. --no-journal used to
    # skip BOTH, which its name does not promise and which silently discarded
    # the only record of a hand-run tune.
    rows, note = ([], "skipped (--no-journal)")
    if not a.no_journal:
        rows, note = journal(lines=a.lines, ssh=a.ssh, boots=a.boots,
                             key=a.ssh_key)
    local, local_note = ([], "skipped (--no-local)")
    if not a.no_local:
        # The journal holds the service's runs; the wrapper's log holds the
        # ones somebody typed. Either can be the most recent, so read both.
        local, local_note = local_log(a.log, lines=a.lines, ssh=a.ssh,
                                      key=a.ssh_key)
    if local:
        rows = merge(rows, local)
        note = ""                         # a local log alone is enough
    up = uptime_seconds(ssh=a.ssh, key=a.ssh_key)

    if not (a.verbose or a.all):
        line, ready = verdict(rows, note, nt_local_on, uptime=up,
                              local_seen=not local_note)
        print("Photon tune status: %s" % line)
        sys.exit(0 if ready else 1)

    where = a.ssh or "this machine"
    print("Photon tune status: %s\n"
          % verdict(rows, note, nt_local_on, uptime=up,
                    local_seen=not local_note)[0])
    sources = ["systemd journal (the service)"]
    if local:
        sources.append("%s (%d lines, runs by hand)" % (a.log, len(local)))
    else:
        sources.append(local_note or "%s empty" % a.log)
    print("on %s: %s" % (where, ", ".join(sources)))
    print("NetworkTables at %s\n" % a.host)

    if not a.no_journal:
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
