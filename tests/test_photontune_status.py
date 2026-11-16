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
"""Tests for photontune_status's journal reading.

    python3 tests/test_photontune_status.py

Stdlib only, and no Pi required: the parser is fed recorded journalctl output,
which is how a log reader should be tested. The fixture below is real output
from a Pi running the photontune service, including the two boots where
PhotonVision never came up.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitor"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import photontune_status as pts

FAILED = []


def check(name, got, want):
    ok = got == want
    print("  %-52s %s" % (name, "ok" if ok else "FAIL  got=%r want=%r" % (got, want)))
    if not ok:
        FAILED.append(name)


#: Recorded from the real Pi. Two cameras, so it is the shape that exposed the
#: anchoring bug: a run whose last "── " marker is the SECOND camera.
FIXTURE = """-- Logs begin at Thu 2026-10-29 21:00:00 UTC. --
2026-10-29T22:45:50+0000 photonvision systemd[1]: Started photontune.service - PhotonTune - boot baseline + NetworkTables-triggered tuner.
2026-10-29T22:47:22+0000 photonvision python3[1077]: boot baseline skipped: PhotonVision not ready within 90s
-- Boot 3db6c1d72b674e69aa1c5303725c453b --
2026-10-29T23:42:10+0000 photonvision systemd[1]: Started photontune.service - PhotonTune - boot baseline + NetworkTables-triggered tuner.
2026-10-29T23:42:12+0000 photonvision python3[935]: daemon up. table /PhotonTune  - set 'run' true to tune.
2026-10-29T23:42:16+0000 photonvision python3[935]: baseline for 2 camera(s): Left, Right
2026-10-29T23:42:16+0000 photonvision python3[935]: ── Left ──  current exposure 3453.99, gain 60
2026-10-29T23:42:16+0000 photonvision python3[935]:    baseline: already correct
2026-10-29T23:42:17+0000 photonvision python3[935]: ── Right ──  current exposure 3438.85, gain 40
2026-10-29T23:42:17+0000 photonvision python3[935]:    baseline: already correct
2026-10-29T23:42:18+0000 photonvision python3[935]: boot baseline done in 1 s: ok=True Left: already correct; Right: already correct
"""


def test_parse():
    print("parse_journal")
    rows = pts.parse_journal(FIXTURE)
    check("drops the 'Logs begin' header",
          any("Logs begin" in m for _t, m in rows), False)
    check("keeps the boot boundary",
          sum(1 for _t, m in rows if m.startswith("@@BOOT")), 1)
    check("row count (10 messages + 1 boundary)", len(rows), 11)
    check("stamp is parsed off the front", rows[0][0], "2026-10-29T22:45:50+0000")
    check("message has host and unit stripped",
          rows[0][1].startswith("Started photontune.service"), True)
    check("indented continuation keeps its text",
          rows[7][1].strip(), "baseline: already correct")
    check("the camera header survives the unicode rule",
          rows[6][1].strip().startswith("\u2500\u2500 Left"), True)
    check("empty input is empty, not a crash", pts.parse_journal(""), [])
    check("'no entries' is empty",
          pts.parse_journal("-- No entries --\n"), [])


def test_last_run_anchors_on_the_boundary():
    """The regression. Anchoring on the last camera header dropped a camera.

    This fixture's final run logs Left then Right. Anchoring on the last "── "
    marker starts at Right, so the report looked complete and silently omitted
    Left's exposure - the worse kind of wrong, because nothing about the output
    says anything is missing.
    """
    print("\nlast_run - the run must start at the boundary, not the last camera")
    rows = pts.parse_journal(FIXTURE)
    recent, decisive = pts.last_run(rows)
    text = " ".join(m for _t, m in recent)
    check("includes Left",  "Left" in text and "3453.99" in text, True)
    check("includes Right", "Right" in text and "3438.85" in text, True)
    check("starts at the service start",
          recent[0][1].startswith("Started photontune.service"), True)
    check("does not reach back into the earlier boot",
          "not ready within 90s" in text, False)


def test_decisive():
    print("\nlast_run - which lines count as decisions")
    rows = pts.parse_journal(FIXTURE)
    _recent, decisive = pts.last_run(rows)
    text = " | ".join(m for _t, m in decisive)
    check("catches the 90s give-up",   "not ready within 90s" in text, True)
    check("catches the baseline outcome", "boot baseline done" in text, True)
    check("no synthetic boot rows leak in",
          any(m.startswith("@@BOOT") for _t, m in decisive), False)
    check("progress chatter is excluded",
          "daemon up" in text, False)


def test_no_boundary_means_one_run():
    print("\nlast_run - a journal with no boundary at all")
    rows = pts.parse_journal(
        "2026-11-16T10:00:00+0000 h p[1]: ── Left ──  current exposure 1, gain 2\n"
        "2026-11-16T10:00:01+0000 h p[1]:    applied gain 2 / exposure 1 - confirmed\n")
    recent, decisive = pts.last_run(rows)
    check("everything is the run", len(recent), 2)
    check("the applied line is decisive", len(decisive), 1)


def test_journal_without_journalctl():
    """On a laptop this must be an ordinary outcome, not an exception."""
    print("\njournal() - no journalctl present")
    real = pts.shutil.which
    pts.shutil.which = lambda _name: None
    try:
        rows, note = pts.journal()
    finally:
        pts.shutil.which = real
    check("returns no rows", rows, [])
    check("says the journal is missing, not that sudo failed",
          "no systemd journal" in note, True)
    check("does not blame a password", "password" in note.lower(), False)


# --- the one line a teenager reads -----------------------------------------
# Most of these cannot be produced on real hardware on demand - nobody is
# going to unplug a camera to see the camera-missing line - so they are driven
# from synthetic journals built out of photontune's real log strings.

import datetime

NOW = datetime.datetime(2026, 11, 16, 15, 0, 0, tzinfo=datetime.timezone.utc)
STAMP = "2026-11-16T14:47:00+0000"       # 13 minutes before NOW


def mkjournal(*messages):
    head = "%s h p[1]: Started photontune.service - PhotonTune\n" % STAMP
    return pts.parse_journal(head + "".join(
        "%s h p[1]: %s\n" % (STAMP, m) for m in messages))


CAM_L = "\u2500\u2500 Left \u2500\u2500  current exposure 3453, gain 60"
CAM_R = "\u2500\u2500 Right \u2500\u2500  current exposure 3438, gain 40"


def test_verdict_cases():
    print("\nverdict - the one line, per situation")

    def line(rows, note="", nt=False):
        return pts.verdict(rows, note, nt_local_on=nt, now=NOW)[0]

    def ready(rows, note="", nt=False):
        return pts.verdict(rows, note, nt_local_on=nt, now=NOW)[1]

    v = line([], "no systemd journal on this machine (darwin)")
    check("no log: says it cannot tell", "cannot tell" in v, True)
    check("no log: not reported as ready",
          ready([], "no systemd journal"), False)

    v = line([])
    check("never ran", "NEVER run" in v, True)

    v = line(mkjournal("baseline for 2 camera(s): Left, Right", CAM_L,
                       "   baseline: already correct",
                       "boot baseline done in 1 s: ok=True Left: already correct"))
    check("baseline ran: says it CHECKED, not that nothing happened",
          "baseline checked" in v, True)
    check("baseline ran: says the settings were already correct",
          "already correct" in v, True)
    check("baseline ran: names both cameras", "Left and Right" in v, True)
    check("baseline ran: does NOT look like a shell command",
          "/PhotonTune/run true" in v, False)
    check("baseline ran: claims only that nothing was RECORDED",
          "No tune RECORDED" in v, True)
    check("baseline ran: does NOT assert no tune happened",
          "NO TUNE since last boot" in v, False)
    check("baseline ran: says a hand-run tune is invisible",
          "run by hand is not" in v, True)
    check("baseline only: not ready", ready(mkjournal(
        "baseline for 2 camera(s): Left, Right", CAM_L,
        "boot baseline done in 1 s: ok=True")), False)

    v = line(mkjournal("boot baseline skipped: PhotonVision not ready within 90s"))
    check("PV never came up: says so", "never came up" in v, True)
    check("PV never came up: says nothing was set", "nothing was set" in v, True)

    v = line(mkjournal("no cameras reported by PhotonVision at 127.0.0.1"))
    check("no cameras: ERROR", v.startswith("ERROR"), True)
    check("no cameras: says not found", "not found" in v, True)

    v = line(mkjournal("no camera matched 'Left'. Available: Right"))
    check("named camera: names it", "Left camera NOT FOUND" in v, True)
    check("named camera: keeps its capital L", "left camera" in v, False)

    v = line(mkjournal(CAM_L,
                       "   even gain 100 cannot see the tags at 3453 us - the scene is too dark"))
    check("too dark: FAILED", "FAILED" in v, True)
    check("too dark: the phrase he asked for",
          "light levels TOO LOW for competition" in v, True)

    v = line(mkjournal(CAM_L,
                       "   *** MISMATCH *** wanted gain 40 / exposure 3000, camera reports 60"))
    check("mismatch: says the camera refused",
          "did NOT take the settings" in v, True)

    good = mkjournal(
        CAM_L, "   lowest gain that sees every tag: 0. Applying 20 - one step",
        "   applied gain 20 / exposure 3453 - confirmed. 5.8 px of smear",
        CAM_R, "   applied gain 20 / exposure 3438 - confirmed. 5.6 px of smear")
    v = line(good)
    check("good tune: says when", "13 min ago" in v, True)
    check("good tune: light optimal", "light levels optimal" in v, True)
    check("good tune: names both cameras", "Left and Right" in v, True)
    check("good tune: IS ready", ready(good), True)

    v = line(mkjournal(CAM_L,
                       "   applied gain 100 / exposure 3453 - confirmed. 5.8 px of smear"))
    check("max gain: light called MARGINAL", "MARGINAL" in v, True)
    check("max gain: NOT called optimal", "optimal" in v, False)

    v = line(mkjournal(CAM_L,
                       "   applied gain 60 / exposure 3453 - confirmed. 5.8 px of smear"))
    check("mid gain: workable", "workable" in v, True)


def test_danger_is_always_appended():
    """A stray local NT server is silent and loses matches. Never omit it."""
    print("\nverdict - DANGER rides on every outcome")
    cases = {
        "no log":        ([], "no systemd journal"),
        "never ran":     ([], ""),
        "baseline only": (mkjournal("baseline for 1 camera(s): Left", CAM_L,
                                    "boot baseline done in 1 s: ok=True"), ""),
        "too dark":      (mkjournal(CAM_L, "   even gain 100 cannot see the tags"), ""),
        "good tune":     (mkjournal(CAM_L, "   applied gain 20 / exposure 3453 - confirmed"), ""),
        "no cameras":    (mkjournal("no cameras reported by PhotonVision"), ""),
    }
    for label, (rows, note) in sorted(cases.items()):
        text, ready = pts.verdict(rows, note, nt_local_on=True, now=NOW)
        check("%-14s carries DANGER" % label, "DANGER" in text, True)
        check("%-14s never ready with NT on" % label, ready, False)
    for label, (rows, note) in sorted(cases.items()):
        text, _r = pts.verdict(rows, note, nt_local_on=False, now=NOW)
        check("%-14s silent when NT is off" % label, "DANGER" in text, False)


def test_age():
    print("\n_age - relative time a human reads")
    secs, text = pts._age(STAMP, NOW)
    check("13 minutes", text, "13 min ago")
    check("seconds returned too", round(secs), 780)
    _s, t = pts._age("2026-11-16T14:59:30+0000", NOW)
    check("under 90s is 'just now'", t, "just now")
    _s, t = pts._age("2026-11-16T11:00:00+0000", NOW)
    check("hours", t, "4 h ago")
    _s, t = pts._age("2026-11-13T15:00:00+0000", NOW)
    check("days", t, "3 days ago")
    _s, t = pts._age("", NOW)
    check("no stamp", t, "time unknown")
    _s, t = pts._age("not a date", NOW)
    check("garbage stamp", t, "time unreadable")
    _s, t = pts._age("2026-11-17T15:00:00+0000", NOW)
    check("a stamp in the future blames the clock", "clock is wrong" in t, True)


def test_uptime_overrides_a_lying_clock():
    """An entry from THIS boot cannot be older than the uptime.

    The Pi has no RTC battery and no NTP on an isolated network. Its journal
    said a baseline that ran forty minutes earlier was "18 days ago", which a
    teenager reads as stale vision. Uptime settles it without guessing.
    """
    print("\nverdict - uptime beats a lying clock")
    stale = pts.parse_journal(
        "2026-10-29T23:42:10+0000 h p[1]: Started photontune.service - PhotonTune\n"
        "2026-10-29T23:42:16+0000 h p[1]: baseline for 1 camera(s): Left\n"
        "2026-10-29T23:42:16+0000 h p[1]: " + CAM_L + "\n"
        "2026-10-29T23:42:18+0000 h p[1]: boot baseline done in 1 s: ok=True\n")
    loud, _r = pts.verdict(stale, "", now=NOW)
    check("without uptime it reports the bogus age", "18 days ago" in loud, True)
    fixed, _r = pts.verdict(stale, "", now=NOW, uptime=1860)   # up 31 min
    check("with uptime it says this boot",
          "earlier this boot" in fixed, True)
    check("and names the clock as the reason",
          "clock was wrong" in fixed, True)
    check("a real age survives when uptime is long enough",
          "13 min ago" in pts.verdict(
              mkjournal(CAM_L, "   applied gain 20 / exposure 1 - confirmed"),
              "", now=NOW, uptime=86400)[0], True)


def test_parse_local():
    print("\nparse_local - the wrapper's own log format")
    rows = pts.parse_local(
        "2026-11-16T14:52:35+0000 photontune manual run started:\n"
        "2026-11-16T14:52:35+0000 \u2500\u2500 Left \u2500\u2500  current exposure 3453, gain 60\n"
        "2026-11-16T14:52:36+0000    applied gain 20 / exposure 3453 - confirmed\n"
        "2026-11-16T14:52:37+0000 photontune manual run finished: exit=0\n")
    check("row count", len(rows), 4)
    check("stamp split off", rows[0][0], "2026-11-16T14:52:35+0000")
    check("banner kept as the anchor",
          "manual run started" in rows[0][1], True)
    check("exit line kept", "exit=0" in rows[3][1], True)
    check("empty is empty", pts.parse_local(""), [])
    odd = pts.parse_local("no timestamp here\n")
    check("an unstamped line is kept, not dropped", len(odd), 1)
    check("...with an empty stamp", odd[0][0], "")


def test_local_log_absent_is_not_an_error():
    """Nothing run by hand yet is the NORMAL state, not a failure."""
    print("\nlocal_log - a missing file")
    rows, note = pts.local_log("/nonexistent/photontune.log")
    check("no rows", rows, [])
    check("and no complaint", note, "")


def test_merge():
    print("\nmerge - one timeline from two sources")
    j = pts.parse_journal(
        "2026-11-16T10:00:00+0000 h p[1]: Started photontune.service\n"
        "2026-11-16T10:00:01+0000 h p[1]: boot baseline done in 1 s: ok=True\n")
    l = pts.parse_local(
        "2026-11-16T11:00:00+0000 photontune manual run started:\n"
        "2026-11-16T11:00:30+0000    applied gain 20 / exposure 3453 - confirmed\n")
    both = pts.merge(j, l)
    check("every row survives", len(both), len(j) + len(l))
    stamps = [t for t, _m in both if t]
    check("in time order", stamps, sorted(stamps))
    check("the manual run is last",
          "confirmed" in both[-1][1], True)
    check("one empty source returns the other", pts.merge(j, []), j)
    check("the other empty source too", pts.merge([], l), l)


def test_manual_run_is_an_anchor():
    """last_run must find a hand-run tune's start, which has no service line."""
    print("\nlast_run - a manual run is its own boundary")
    rows = pts.merge(
        pts.parse_journal(
            "2026-11-16T10:00:00+0000 h p[1]: Started photontune.service\n"
            "2026-11-16T10:00:01+0000 h p[1]: baseline for 1 camera(s): Left\n"
            "2026-11-16T10:00:02+0000 h p[1]: boot baseline done in 1 s: ok=True\n"),
        pts.parse_local(
            "2026-11-16T11:00:00+0000 photontune manual run started:\n"
            "2026-11-16T11:00:10+0000 " + CAM_L + "\n"
            "2026-11-16T11:00:30+0000    applied gain 20 / exposure 3453 - confirmed\n"))
    recent, _d = pts.last_run(rows)
    text = " ".join(m for _t, m in recent)
    check("starts at the manual banner",
          "manual run started" in recent[0][1], True)
    check("does not reach back to the boot baseline",
          "boot baseline done" in text, False)
    line, ready = pts.verdict(rows, "", local_seen=True,
                              now=datetime.datetime(2026, 11, 16, 11, 1, 0,
                                                    tzinfo=datetime.timezone.utc))
    check("the hand-run tune is what gets reported",
          "applied to Left" in line, True)
    check("and it reads as ready", ready, True)


def test_caveat_depends_on_the_local_log():
    """Do not warn that a tune might be invisible when it could not be."""
    print("\nverdict - the invisibility caveat is conditional")
    base = mkjournal("baseline for 1 camera(s): Left", CAM_L,
                     "   baseline: already correct",
                     "boot baseline done in 1 s: ok=True Left: already correct")
    blind, _r = pts.verdict(base, "", now=NOW, local_seen=False)
    check("no local log: warns a hand run is unseen",
          "run by hand is not" in blind, True)
    seen, _r = pts.verdict(base, "", now=NOW, local_seen=True)
    check("local log present: does NOT warn",
          "run by hand is not" in seen, False)
    check("local log present: says to run photontune",
          "run `photontune` to do one" in seen, True)


if __name__ == "__main__":
    for fn in (test_parse, test_last_run_anchors_on_the_boundary, test_decisive,
               test_no_boundary_means_one_run, test_journal_without_journalctl,
               test_verdict_cases, test_danger_is_always_appended, test_age,
               test_uptime_overrides_a_lying_clock, test_parse_local,
               test_local_log_absent_is_not_an_error, test_merge,
               test_manual_run_is_an_anchor,
               test_caveat_depends_on_the_local_log):
        fn()
    print("")
    if FAILED:
        print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
        sys.exit(1)
    print("all photontune_status tests pass")
