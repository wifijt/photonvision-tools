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


if __name__ == "__main__":
    for fn in (test_parse, test_last_run_anchors_on_the_boundary, test_decisive,
               test_no_boundary_means_one_run, test_journal_without_journalctl):
        fn()
    print("")
    if FAILED:
        print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
        sys.exit(1)
    print("all photontune_status tests pass")
