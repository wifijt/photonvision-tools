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
    check("never ran", "never run" in v, True)

    v = line(mkjournal("baseline for 2 camera(s): Left, Right", CAM_L,
                       "   baseline: already correct",
                       "boot baseline done in 1 s: ok=True Left: already correct"))
    check("baseline ran: says the baseline ran, not that nothing happened",
          "baseline ran" in v, True)
    check("baseline ran: says the settings were already correct",
          "already correct" in v, True)
    check("baseline ran: names both cameras", "Left and Right" in v, True)
    check("baseline ran: does NOT look like a shell command",
          "/PhotonTune/run true" in v, False)
    check("baseline ran: leads with the answer, which is no",
          v.startswith("not tuned since"), True)
    check("baseline ran: does not claim it was tuned",
          "tuned since" in v and "applied to" not in v, True)
    check("baseline ran: says a hand run would not show up",
          "would not show up" in v, True)
    check("baseline only: not ready", ready(mkjournal(
        "baseline for 2 camera(s): Left, Right", CAM_L,
        "boot baseline done in 1 s: ok=True")), False)

    v = line(mkjournal("boot baseline skipped: PhotonVision not ready within 90s"))
    check("PV never came up: says so", "never came up" in v, True)
    check("PV never came up: says photontune gave up", "gave up" in v, True)

    v = line(mkjournal("no cameras reported by PhotonVision at 127.0.0.1"))
    check("no cameras: error", v.startswith("error"), True)
    check("no cameras: says not found", "not found" in v, True)

    v = line(mkjournal("no camera matched 'Left'. Available: Right"))
    check("named camera: names it", "Left camera not found" in v, True)
    check("named camera: keeps its capital L", "left camera" in v, False)

    v = line(mkjournal(CAM_L,
                       "   even gain 100 cannot see the tags at 3453 us - the scene is too dark"))
    check("too dark: says not tuned", v.startswith("not tuned"), True)
    check("too dark: the phrase he asked for",
          "light levels too low for competition" in v, True)

    v = line(mkjournal(CAM_L,
                       "   *** MISMATCH *** wanted gain 40 / exposure 3000, camera reports 60"))
    check("mismatch: says the camera refused",
          "did not take the settings" in v, True)

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
    check("max gain: light called marginal", "light levels marginal" in v, True)
    check("max gain: NOT called optimal", "optimal" in v, False)

    v = line(mkjournal(CAM_L,
                       "   applied gain 60 / exposure 3453 - confirmed. 5.8 px of smear"))
    check("mid gain: ok, with the number", "light levels ok, gain 60" in v, True)


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
    # Deliberately NOT "": an absent log is not a successful read of an empty
    # one. Conflating the two let the tool claim no tune had happened when it
    # simply had no way to know. That was the lie.
    check("says there is no log yet", "no log at" in note, True)
    check("and therefore is NOT counted as seeing hand runs",
          note == "", False)


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
    check("no local log: says a hand run would not show up",
          "would not show up" in blind, True)
    seen, _r = pts.verdict(base, "", now=NOW, local_seen=True)
    check("local log present: does not say that",
          "would not show up" in seen, False)
    check("local log present: says to run photontune",
          "run photontune" in seen, True)


#: A real failing run, 2026-11-16, abridged. Kept verbatim because this is the
#: run that exposed the lie: it happened, it FAILED, and the status tool said
#: "No tune since boot - run `photontune` to do one".
REAL_FAILED_RUN = [
    "photontune run started: ",
    "tuning 2 camera(s): Left, Right",
    "   no NetworkTables server at photonvision.local or 127.0.0.1 (8.3 s spent looking)",
    "\u2500\u2500 Left \u2500\u2500  current exposure 3453.9974841568746, gain 60",
    "   baseline: already correct",
    "   reference  gain 100  exposure    863  tags 0.00  ambiguity n/a  (no multitag)",
    "       gain 100  exposure   1727  tags 0.00  ambiguity n/a  (no multitag)",
    "   even gain 100 cannot see the tags at 863 us - the scene is too dark for the blur budget.",
    "   !! no gain from [0, 20, 40, 60, 80, 100] saw the tags at 863 us, and neither a "
    "shorter nor a longer exposure did. That is a LIGHTING or CONFIG problem.",
    "\u2500\u2500 Right \u2500\u2500  current exposure 3438.8577861772997, gain 40",
    "   baseline: already correct",
    "   reference  gain 100  exposure    860  tags 0.00  ambiguity n/a  (no multitag)",
    "       gain 100  exposure   1719  tags 0.00  ambiguity n/a  (no multitag)",
    "   even gain 100 cannot see the tags at 860 us - the scene is too dark for the blur budget.",
    "   !! no gain from [0, 20, 40, 60, 80, 100] saw the tags at 860 us, and neither a "
    "shorter nor a longer exposure did. That is a LIGHTING or CONFIG problem.",
    "completed in 114 s",
    "  Left           unchanged (nothing worked at any gain or exposure)",
    "  Right          unchanged (nothing worked at any gain or exposure)",
    "FAILED Left: no gain from [0, 20, 40, 60, 80, 100] saw the tags at 863 us",
    "FAILED Right: no gain from [0, 20, 40, 60, 80, 100] saw the tags at 860 us",
    "photontune run finished",
]


def test_the_run_that_caught_the_lie():
    """A failed tune must be reported as a failed tune.

    The tuner is run as `python3 /opt/photontune/photontune.py`, which is what
    the docs show and what people type. It now logs itself, so this is what
    the status tool sees. Before, it saw nothing and claimed no tune had
    happened - the single worst answer available, because a student would read
    it as "nothing to worry about" when both cameras had just failed to see a
    single tag at any gain or exposure.
    """
    print("\nverdict - the real failed run from 2026-11-16")
    rows = pts.parse_local(
        "".join("%s %s\n" % (STAMP, m) for m in REAL_FAILED_RUN))
    check("the whole run parses", len(rows), len(REAL_FAILED_RUN))
    check("the tuner's own banner anchors the run",
          "photontune run started" in pts.last_run(rows)[0][0][1], True)

    line, ready = pts.verdict(rows, "", local_seen=True, now=NOW)
    check("reported as not tuned", line.startswith("not tuned"), True)
    # The bench had no tag in view, which the user confirmed. A flat 0.00 at
    # every gain AND either exposure is "nothing to see", not "too dark" -
    # reporting it as light sends somebody to fetch lamps.
    check("says no tags seen, not 'too dark'",
          "no tags seen at any gain or exposure" in line, True)
    check("does NOT blame the light outright",
          "light levels too low" in line, False)
    check("points at the thing that actually resolves it",
          "check a tag is in front of it" in line, True)
    check("says when", "13 min ago" in line, True)
    check("NOT reported as ready", ready, False)
    check("does NOT claim no tune happened",
          "not tuned since" in line, False)
    check("does NOT claim it was only a baseline",
          "baseline" in line.lower(), False)


def test_a_missing_log_never_claims_no_tune():
    """The actual bug, as a test. 'No error' is not 'I have the data'.

    local_log returned ("", []) for a file that did not exist, main turned
    that into local_seen=True, and verdict took that as licence to assert "No
    tune since boot". Three states - log read with runs, log read empty, log
    absent - were collapsed into two.
    """
    print("\nverdict - a missing local log must not license a confident negative")
    rows, note = pts.local_log("/nonexistent/dir/photontune.log")
    check("absent log yields a note, not silence", bool(note), True)
    seen = (note == "")
    check("so local_seen is False", seen, False)

    base = mkjournal("baseline for 2 camera(s): Left, Right", CAM_L,
                     "   baseline: already correct",
                     "boot baseline done in 1 s: ok=True Left: already correct")
    line, _r = pts.verdict(base, "", now=NOW, local_seen=seen)
    check("the line does NOT assert no tune happened",
          "No tune since boot" in line, False)
    check("it says a hand run would not show up",
          "would not show up" in line, True)


def test_dim_tags_are_not_the_same_as_no_tags():
    """Falling tag counts mean dim. A flat zero everywhere means absent."""
    print("\nverdict - dim tags vs no tags")
    # Dim: the walk gave up at the top of the gain range, but the exposure
    # walk was never reported as also failing.
    dim = mkjournal(CAM_L,
                    "   even gain 100 cannot see the tags at 863 us - the scene is too dark")
    v, _r = pts.verdict(dim, "", local_seen=True, now=NOW)
    check("dim: reported as light levels too low",
          "light levels too low for competition" in v, True)
    check("dim: does not ask about tags being in view",
          "Is a tag actually in view?" in v, False)

    # Absent: the full sweep failed, both directions.
    gone = mkjournal(CAM_L,
                     "   even gain 100 cannot see the tags at 863 us",
                     "   !! no gain from [0, 20, 40, 60, 80, 100] saw the tags at 863 us, "
                     "and neither a shorter nor a longer exposure did.")
    v, _r = pts.verdict(gone, "", local_seen=True, now=NOW)
    check("absent: reported as no tags seen", "no tags seen" in v, True)
    check("absent: points at the camera, not the lamps",
          "check a tag is in front of it" in v, True)


#: The run that proved one verdict cannot describe two cameras. Verbatim from
#: /var/log/photontune/photontune.log, 2026-11-16 18:02:26, trimmed only in the
#: middle of Left's gain walk - every line that decides anything is here.
MIXED_RUN = [
    "photontune run started: ",
    "tuning 2 camera(s): Left, Right",
    "\u2500\u2500 Left \u2500\u2500  current exposure 3453.9974841568746, gain 60",
    "   baseline: already correct",
    "   reference  gain 100  exposure    863  tags 0.89  ambiguity 0.937  (no multitag)",
    "   !! the reference does not pass its own test (no multi-tag solve, and only 0.89 "
    "tags), so it cannot be a bar. Walking without the tag-count floor; THIS RUN WILL "
    "NOT REPORT SUCCESS.",
    "       gain 20   exposure    863  tags 1.00  ambiguity 0.800  (no multitag)",
    "       gain 40   exposure    863  tags 1.00  ambiguity 0.429  (no multitag)",
    "       gain 60   exposure    863  tags 1.00  ambiguity 0.429  (no multitag)",
    "       gain 80   exposure    863  tags 1.00  ambiguity 0.460  (no multitag)",
    "       gain 100  exposure    863  tags 0.89  ambiguity 0.937  (no multitag)",
    "   even gain 100 cannot see the tags at 863 us - the scene is too dark for the blur budget.",
    "   !! no gain from [0, 20, 40, 60, 80, 100] saw the tags at 863 us, and neither a "
    "shorter nor a longer exposure did. That is a LIGHTING or CONFIG problem, not a "
    "tuning one: check cameraBrightness, check the camera is pointed at tags, add light.",
    "\u2500\u2500 Right \u2500\u2500  current exposure 3438.8577861772997, gain 40",
    "   baseline: already correct",
    "   reference  gain 100  exposure    860  tags 1.00  ambiguity 0.000  (no multitag)",
    "   only one tag is visible to this camera (1.00 tags at ambiguity 0.000, 35 frames).",
    "   ok  gain 0    exposure    860  tags 1.00  ambiguity 0.000  (no multitag)",
    "   lowest gain that sees every tag: 0. Applying 20 - one grid step of margin.",
    "   applied gain 20 / exposure 860 - confirmed. 6 px of smear at 360 deg/s (budget 6)",
    "   ok  applied   gain 20   exposure    860  tags 1.00  ambiguity 0.000  (no multitag)",
    "completed in 78 s",
    "  Left           unchanged (nothing worked at any gain or exposure)",
    "  Right          exposure -> 860, gain 20",
    "FAILED Left: no gain from [0, 20, 40, 60, 80, 100] saw the tags at 863 us, and "
    "neither a shorter nor a longer exposure did. That is a LIGHTING or CONFIG problem; "
    "the reference measurement did not itself pass (no multi-tag solve, and only 0.89 "
    "tags), so it is not a bar anything can be held to.",
    "photontune run finished",
]


def test_one_camera_tuned_the_other_did_not():
    """Two cameras, two outcomes, one line that has to carry both.

    Reported at the bench as:

        tune FAILED just now - NO TAGS SEEN at any gain or exposure

    Right had just been tuned and applied at gain 20. Left had seen 1.00 tags
    at four separate gains. Both halves of that sentence were false, and the
    half that was readable as advice - go and find some light - pointed at the
    one thing that was not the problem.

    Two independent causes, so two sets of checks. The whole-run text scan
    could only return one answer, and NOTHING_SEEN sorted ahead of "applied",
    so one camera's failure erased the other's success. And the failure itself
    was misattributed: what failed on Left was the REFERENCE, taken at max
    gain, where that camera happens to read WORSE (0.89 tags, ambiguity 0.937)
    than at every gain from 20 to 80 (1.00 tags, ambiguity 0.43).
    """
    print("\nverdict - one camera tuned, one failed")
    rows = pts.parse_local("".join("%s %s\n" % (STAMP, m) for m in MIXED_RUN))
    cams = {c["name"]: c for c in pts.per_camera(rows)}

    check("both cameras are accounted for", sorted(cams), ["Left", "Right"])
    check("Right is recorded as applied", cams["Right"]["applied"], True)
    check("Right's gain is read back", cams["Right"]["gain"], 20.0)
    check("Left is recorded as failed", cams["Left"]["failed"], True)
    check("Left is NOT recorded as applied", cams["Left"]["applied"], False)
    check("Left's best tag count is kept", cams["Left"]["max_tags"], 1.0)

    line, ready = pts.verdict(rows, "", local_seen=True, now=NOW)
    check("names the camera that worked", line.startswith("tuned Right"), True)
    check("names the camera that did not", "Left not tuned" in line, True)
    check("does NOT claim nothing was seen",
          "no tags seen" in line, False)
    check("does NOT blame the light",
          "light levels TOO LOW" in line, False)
    check("blames the reading it measures against, which is what failed",
          "the reading it measures against was bad" in line, True)
    check("says what to do about it", "run it again" in line, True)
    check("NOT reported as ready", ready, False)


def test_a_failure_cannot_erase_a_success():
    """The ordering bug on its own, independent of any one message.

    Whatever a failed camera's reason turns out to be, a camera that was
    tuned and confirmed must still be named. This fails if anybody ever moves
    a failure check back above the per-camera branch.
    """
    print("\nverdict - a failure never erases a success")
    for reason in ("no gain from [0, 20] saw the tags at 863 us, and neither a "
                   "shorter nor a longer exposure did.",
                   "even gain 100 cannot see the tags at 863 us - the scene is "
                   "too dark for the blur budget.",
                   "the reference measurement did not itself pass."):
        rows = pts.parse_local("".join("%s %s\n" % (STAMP, m) for m in [
            "photontune run started: ",
            "\u2500\u2500 Left \u2500\u2500  current exposure 3453.9, gain 60",
            "   reference  gain 100  exposure 863  tags 0.50  ambiguity 0.9",
            "   !! " + reason,
            "\u2500\u2500 Right \u2500\u2500  current exposure 3438.8, gain 40",
            "   applied gain 20 / exposure 860 - confirmed. 6 px of smear",
            "completed in 78 s",
            "  Left           unchanged (nothing worked at any gain or exposure)",
            "  Right          exposure -> 860, gain 20",
            "FAILED Left: " + reason,
            "photontune run finished",
        ]))
        line, ready = pts.verdict(rows, "", local_seen=True, now=NOW)
        check("Right still named (%s...)" % reason[:22],
              line.startswith("tuned Right"), True)
        check("Left still named (%s...)" % reason[:22],
              "Left not tuned" in line, True)
        check("not ready (%s...)" % reason[:22], ready, False)


def test_a_service_restart_is_not_a_reboot():
    """"Since boot" must not be said about a box that has not rebooted.

    Observed 2026-11-16 within a minute of installing a new tuner: the Pi had
    been up 4 h 45, had been tuned three times in that, and the status line
    read "No tune since boot". The daemon had been restarted, not the box.

    The anchor cannot tell them apart on its own, because at boot the journal
    holds the boundary AND the service start, service start second - so the
    anchor is a service start either way. Counting starts since the boundary
    can: one is the boot-time start, two or more means somebody restarted it.
    """
    print("\nsince - a restart is not a reboot")
    boot_only = [(STAMP, "@@BOOT 1111"),
                 (STAMP, "Started photontune.service - PhotonTune"),
                 (STAMP, "boot baseline done in 1 s: ok=True L: already correct")]
    check("one start after the boundary reads as boot",
          pts._since(boot_only), "boot")

    restarted = boot_only + [
        (STAMP, "Started photontune.service - PhotonTune"),
        (STAMP, "boot baseline done in 1 s: ok=True L: already correct")]
    check("a second start reads as a restart",
          pts._since(restarted), "photontune restarted")

    # And it must reach the line a human reads.
    line, _r = pts.verdict(boot_only, "", local_seen=True, now=NOW)
    check("boot: line says since boot", "since boot" in line, True)
    line, _r = pts.verdict(restarted, "", local_seen=True, now=NOW)
    check("restart: line says since photontune restarted",
          "since photontune restarted" in line, True)
    check("restart: does NOT say since boot", "since boot" in line, False)

    # A later boot boundary resets the count - a reboot after a restart is
    # still a reboot, and the restarts belonged to the previous boot.
    rebooted = restarted + [
        (STAMP, "@@BOOT 2222"),
        (STAMP, "Started photontune.service - PhotonTune"),
        (STAMP, "boot baseline done in 1 s: ok=True L: already correct")]
    check("a boundary after the restarts reads as boot again",
          pts._since(rebooted), "boot")


if __name__ == "__main__":
    for fn in (test_parse, test_last_run_anchors_on_the_boundary, test_decisive,
               test_no_boundary_means_one_run, test_journal_without_journalctl,
               test_verdict_cases, test_danger_is_always_appended, test_age,
               test_uptime_overrides_a_lying_clock, test_parse_local,
               test_local_log_absent_is_not_an_error, test_merge,
               test_manual_run_is_an_anchor,
               test_caveat_depends_on_the_local_log,
               test_the_run_that_caught_the_lie,
               test_a_missing_log_never_claims_no_tune,
               test_dim_tags_are_not_the_same_as_no_tags,
               test_one_camera_tuned_the_other_did_not,
               test_a_failure_cannot_erase_a_success,
               test_a_service_restart_is_not_a_reboot):
        fn()
    print("")
    if FAILED:
        print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
        sys.exit(1)
    print("all photontune_status tests pass")
