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
"""Tests for preflight's addressing and saved settings.

Run it with no arguments; it exits non-zero on the first failure, so it works
as a pre-commit hook or a CI step:

    python3 tests/test_preflight.py

STDLIB ONLY, like preflight itself, so it runs on a laptop where nothing is
installed yet.

Every test points preflight.CONFIG at a temporary directory. None of this ever
reads or writes the real ~/.photonvision-tools.conf - a test suite that can
overwrite a team's saved settings the night before an event is worse than no
test suite.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preflight as pf

FAILED = []


def check(name, got, want):
    ok = got == want
    print("  %-50s %s" % (name, "ok" if ok else "FAIL  got=%r want=%r" % (got, want)))
    if not ok:
        FAILED.append(name)


def fresh():
    """A config file nobody else is using, and a clean environment."""
    pf.CONFIG = os.path.join(tempfile.mkdtemp(), ".photonvision-tools.conf")
    pf._config_cache = None
    pf._asked.clear()
    for k in ("FRC_TEAM", "FRC_PI", "FRC_PI2"):
        os.environ.pop(k, None)


def test_team_base():
    """The FRC rule is zero-pad-and-split, not arithmetic."""
    print("team_base - the 10.TE.AM rule")
    check("5459 (four digits)", pf.team_base(5459), "10.54.59")
    check("123  (three)",       pf.team_base(123),  "10.1.23")
    check("9    (one)",         pf.team_base(9),    "10.0.9")
    check("1    (the oldest)",  pf.team_base(1),    "10.0.1")
    check("9999 (the largest)", pf.team_base(9999), "10.99.99")
    check("a string works too", pf.team_base("254"), "10.2.54")


def test_roles():
    print("\nresolve_host - roles become addresses")
    fresh()
    pf.write_config({"FRC_TEAM": "5459", "FRC_PI": "10.54.59.12"})
    pf._config_cache = None
    check("rio",            pf.resolve_host("rio"),      "10.54.59.2")
    check("roborio",        pf.resolve_host("roborio"),  "10.54.59.2")
    check("RIO (any case)", pf.resolve_host("RIO"),      "10.54.59.2")
    check("radio",          pf.resolve_host("radio"),    "10.54.59.1")
    check("pi",             pf.resolve_host("pi"),       "10.54.59.12")
    check("photon",         pf.resolve_host("photon"),   "10.54.59.12")
    check(".12 last octet", pf.resolve_host(".12"),      "10.54.59.12")
    check(".2 last octet",  pf.resolve_host(".2"),       "10.54.59.2")
    check("an address passes through",
          pf.resolve_host("10.54.59.77"), "10.54.59.77")
    check("a hostname passes through",
          pf.resolve_host("photonvision.local"), "photonvision.local")
    check("whitespace is trimmed", pf.resolve_host("  rio "), "10.54.59.2")
    check("None stays None",       pf.resolve_host(None), None)
    check("empty stays empty",     pf.resolve_host(""), "")


def test_precedence():
    """--team beats the environment beats the file. An export is deliberate."""
    print("\nprecedence - flag > environment > file")
    fresh()
    pf.write_config({"FRC_TEAM": "5459"})
    pf._config_cache = None
    check("file alone",      pf.resolve_host("rio"), "10.54.59.2")
    os.environ["FRC_TEAM"] = "123"
    pf._config_cache = None
    check("export wins over the file", pf.resolve_host("rio"), "10.1.23.2")
    check("--team wins over the export", pf.resolve_host("rio", 9), "10.0.9.2")
    os.environ.pop("FRC_TEAM")
    pf._config_cache = None
    check("file again once unexported", pf.resolve_host("rio"), "10.54.59.2")

    os.environ["FRC_PI"] = "10.0.0.99"
    pf._config_cache = None
    check("FRC_PI export wins too", pf.resolve_host("pi"), "10.0.0.99")
    os.environ.pop("FRC_PI")


def test_no_team():
    """With nobody to ask, say what is missing. Never guess, never hang."""
    print("\nno team and no terminal - advice, not a guess")
    fresh()
    try:
        pf.resolve_host("rio")
        check("raises SystemExit", "returned normally", "SystemExit")
        return
    except SystemExit as e:
        msg = str(e)
    check("names the role that failed", "'rio'" in msg, True)
    check("offers --team",              "--team 5459" in msg, True)
    check("offers an export",           "export FRC_TEAM=5459" in msg, True)
    check("offers --setup",             "preflight.py --setup" in msg, True)
    check("invents no address",         "10.0.0" in msg, False)

    fresh()
    try:
        pf.resolve_host(".12")
        check(".12 raises too", "returned normally", "SystemExit")
    except SystemExit:
        check(".12 raises too", True, True)

    fresh()
    check("'pi' still works with no team",
          pf.resolve_host("pi"), "photonvision.local")
    check("an address still works with no team",
          pf.resolve_host("10.54.59.12"), "10.54.59.12")


def test_validation():
    """team_base would turn 12345 into 10.12.345, which is not an address."""
    print("\n_valid_team - reject what would silently become nonsense")
    check("5459 accepted", pf._valid_team("5459"), None)
    check("9 accepted",    pf._valid_team("9"), None)
    check("0059 accepted (leading zeros)", pf._valid_team("0059"), None)
    check("five digits rejected", pf._valid_team("12345") is not None, True)
    check("letters rejected",     pf._valid_team("five") is not None, True)
    check("zero rejected",        pf._valid_team("0") is not None, True)
    check("empty rejected",       pf._valid_team("") is not None, True)
    check("None rejected",        pf._valid_team(None) is not None, True)
    check("negative rejected",    pf._valid_team("-5") is not None, True)


def test_file():
    """The file is read by students and edited by hand. Be forgiving."""
    print("\nthe config file - written once, read forever")
    fresh()
    path = pf.write_config({"FRC_TEAM": "5459", "FRC_PI": "10.54.59.12"})
    check("write_config returns the path", path, pf.CONFIG)
    pf._config_cache = None
    check("round trip: team", pf.read_config().get("FRC_TEAM"), "5459")
    check("round trip: pi",   pf.read_config().get("FRC_PI"), "10.54.59.12")

    pf.write_config({"FRC_PI": "10.54.59.20"})
    pf._config_cache = None
    check("a partial write merges, not clobbers",
          pf.read_config().get("FRC_TEAM"), "5459")
    check("...and updates what was given",
          pf.read_config().get("FRC_PI"), "10.54.59.20")

    pf.write_config({"FRC_PI": ""})
    pf._config_cache = None
    check("an empty value is ignored, not stored",
          pf.read_config().get("FRC_PI"), "10.54.59.20")

    with open(pf.CONFIG, "w") as fh:
        fh.write("# a comment\n\n  FRC_TEAM = 5459  \nnonsense with no equals\n"
                 "FRC_PI='quoted.local'\n")
    pf._config_cache = None
    check("comments and blanks skipped", pf.read_config().get("FRC_TEAM"), "5459")
    check("quotes stripped", pf.read_config().get("FRC_PI"), "quoted.local")
    check("junk lines ignored, not fatal", "nonsense" in str(pf.read_config()), False)

    fresh()
    pf.CONFIG = os.path.join(tempfile.mkdtemp(), "no", "such", "dir", "x.conf")
    pf._config_cache = None
    check("a missing file is {}, not a crash", pf.read_config(), {})


def test_require_nt_message():
    """The NT failure message must FORMAT. It used to raise instead.

    `seconds` was missing from its % tuple, so the %g received the address
    string and the call died with "TypeError: must be real number, not str".
    The one code path whose whole job is to explain an NT failure produced a
    traceback instead - on the commonest failure in the repo, from inside
    calibrate_mount, with a robot already in position. A message is not
    verified by reading it; it is verified by forcing it.
    """
    print("\nrequire_nt - the failure message must format, not raise")
    fresh()
    pf.write_config({"FRC_TEAM": "5459"})
    pf._config_cache = None

    class NeverConnects(object):
        def isConnected(self):
            return False

    try:
        pf.require_nt(NeverConnects(), "10.54.59.2", seconds=0.01)
        check("raises SystemExit", "returned normally", "SystemExit")
        return
    except SystemExit as e:
        msg = str(e)
    except TypeError as e:
        check("formats without TypeError", "TypeError: %s" % e, "a message")
        return
    check("formats without raising",         isinstance(msg, str), True)
    check("names the host",                  "10.54.59.2" in msg, True)
    check("reports the wait it actually did", "0.01s" in msg, True)
    check("explains startClient4 is silent", "startClient4" in msg, True)
    check("says rio not Pi on a robot",      "roboRIO, not the Pi" in msg, True)
    check("warns two servers fight",         "both go quiet" in msg, True)

    class Connected(object):
        def isConnected(self):
            return True

    try:
        pf.require_nt(Connected(), "10.54.59.2", seconds=5)
        check("returns at once when connected", True, True)
    except SystemExit:
        check("returns at once when connected", "exited", "returned")


def test_require_nt_names_the_right_interface():
    """On a dual-homed machine it must report the robot-facing address.

    local_address() defaults to the route toward 8.8.8.8, which on a laptop
    joined to school wifi AND cabled to the robot names the wifi interface -
    the exact opposite of the question being asked. require_nt passes the host
    instead whenever the host is numeric.
    """
    print("\nrequire_nt - which interface it names")
    fresh()
    calls = []
    real = pf.local_address
    pf.local_address = lambda toward="8.8.8.8": (calls.append(toward), "10.0.0.1")[1]

    class NeverConnects(object):
        def isConnected(self):
            return False

    try:
        pf.require_nt(NeverConnects(), "10.54.59.2", seconds=0.01)
    except SystemExit:
        pass
    pf.local_address = real
    check("asks the route toward the HOST", calls, ["10.54.59.2"])

    calls2 = []
    pf.local_address = lambda toward="8.8.8.8": (calls2.append(toward), "10.0.0.1")[1]
    try:
        pf.require_nt(NeverConnects(), "photonvision.local", seconds=0.01)
    except SystemExit:
        pass
    pf.local_address = real
    check("falls back to 8.8.8.8 for a NAME", calls2, ["8.8.8.8"])


def test_loopback_message():
    """Loopback gets a different explanation, because the network is not it.

    "Check this machine is on the robot network: 127.0.0.1" sent a reader to
    inspect the one thing that cannot be the cause. On loopback there is one
    explanation left - nothing is serving NT here - and the message says so.
    """
    print("\nrequire_nt - loopback says the right thing")
    fresh()

    class NeverConnects(object):
        def isConnected(self):
            return False

    for host in ("127.0.0.1", "localhost", "127.0.1.1"):
        check("is_loopback(%s)" % host, pf.is_loopback(host), True)
    for host in ("10.54.59.12", "photonvision.local", "", None):
        check("is_loopback(%r) false" % host, pf.is_loopback(host), False)

    try:
        pf.require_nt(NeverConnects(), "127.0.0.1", seconds=0.01)
        check("raises", "returned", "SystemExit"); return
    except SystemExit as e:
        msg = str(e)
    check("does NOT blame the network",
          "on the robot network" in msg, False)
    check("says nothing is serving here",
          "nothing is serving NetworkTables here" in msg, True)
    check("names the UI toggle", "Run NetworkTables Server" in msg, True)
    check("warns to turn it off for a roboRIO", "both go quiet" in msg, True)
    check("still reports the wait", "0.01s" in msg, True)


def test_photon_absent_message():
    """The message must name what fits the evidence, not what is commonest.

    This is the one that cost an evening. A Pi that was UP, with cameras
    tuned and working and runNTServer already off, was accused of running its
    own NT server, because that was the only cause the message knew.
    """
    print("\nphoton_absent_message() - three causes, not one")
    busy = pf.photon_absent_message("10.54.59.2:5810",
                                    ["SmartDashboard", "FMSInfo", "LiveWindow"])
    check("names the server as in use", "IS in use" in busy, True)
    check("shows what it DID find", "SmartDashboard" in busy, True)
    check("offers the runNTServer cause", "set_nt_server.py false" in busy, True)
    check("says the restart is required", "restart photonvision" in busy, True)
    check("also offers down-or-elsewhere",
          "different address or team" in busy, True)

    bare = pf.photon_absent_message("10.54.59.2:5810", [])
    check("empty server: does not accuse photonvision",
          "set_nt_server.py" in bare, False)
    check("empty server: questions the server instead",
          "not the server your robot code is using" in bare, True)
    check("empty server: still names the host", "10.54.59.2:5810" in bare, True)


def test_absent_message_never_contradicts_itself():
    """It may not blame runNTServer on evidence that cannot support it."""
    print("\nphoton_absent_message() - no unsupported accusations")
    bare = pf.photon_absent_message("rio", [])
    for word in ("DANGER", "its OWN NT server", "runNTServer"):
        check("silent on %r with no evidence" % word, word in bare, False)


def test_second_pi():
    """A second coprocessor is a role, and a missing one is fatal.

    THE POINT OF THE FATAL. Resolving 'pi2' to the FIRST Pi when none is
    saved would point a camera tool at the wrong box, and a calibration
    written to the wrong Pi looks exactly like a calibration. A role name
    whose meaning can silently change is worse than no role name.
    """
    print("\nresolve_host('pi2') - the second coprocessor")
    fresh()
    pf.write_config({"FRC_TEAM": "5459", "FRC_PI": "10.54.59.12",
                     "FRC_PI2": "10.54.59.13"})
    check("pi  -> the first",  pf.resolve_host("pi"),  "10.54.59.12")
    check("pi1 -> the first",  pf.resolve_host("pi1"), "10.54.59.12")
    check("pi2 -> the second", pf.resolve_host("pi2"), "10.54.59.13")
    check("photon2 is the same role", pf.resolve_host("photon2"), "10.54.59.13")

    fresh()
    pf.write_config({"FRC_TEAM": "5459", "FRC_PI": "10.54.59.12"})
    try:
        got = pf.resolve_host("pi2")
        check("pi2 with none saved exits", "returned %r" % got, "SystemExit")
    except SystemExit as e:
        msg = str(e)
        check("pi2 with none saved exits", True, True)
        check("says it does not fall back", "does NOT fall back" in msg, True)
        check("never hands back the FIRST Pi", "10.54.59.12" in msg, False)
        check("says how to fix it", "--setup" in msg, True)


def test_duplicate_camera_names():
    """Two boxes, one nickname: NT has no coprocessor in a camera's path."""
    print("\nduplicate_camera_names() - the flat namespace")
    clean = [("pi-front", "AA", ["FL", "FR"]), ("pi-back", "BB", ["BL", "BR"])]
    check("unique names are silent", pf.duplicate_camera_names(clean), {})

    clash = [("pi-front", "AA", ["Front", "Left"]),
             ("pi-back", "BB", ["Front", "Right"])]
    got = pf.duplicate_camera_names(clash)
    check("the collision is found", sorted(got), ["Front"])
    check("both claimants are named", sorted(got.get("Front", [])),
          ["pi-back", "pi-front"])
    check("the innocent names are not flagged", "Left" in got, False)

    noname = [("", "AA", ["Front"]), ("", "BB", ["Front"])]
    check("falls back to the MAC when hostname is blank",
          sorted(pf.duplicate_camera_names(noname).get("Front", [])), ["AA", "BB"])


def test_non_interactive():
    """A piped or scheduled run must not print half a question."""
    print("\ninteractive() - nobody to ask")
    fresh()
    check("not a tty under the test runner", pf.interactive(), False)
    check("ask_team returns None rather than blocking", pf.ask_team(), None)


if __name__ == "__main__":
    for fn in (test_team_base, test_roles, test_precedence, test_no_team,
               test_validation, test_file, test_require_nt_message,
               test_require_nt_names_the_right_interface,
               test_loopback_message,
               test_photon_absent_message,
               test_second_pi,
               test_duplicate_camera_names,
               test_absent_message_never_contradicts_itself,
               test_non_interactive):
        fn()
    print("")
    if FAILED:
        print("FAILED %d: %s" % (len(FAILED), ", ".join(FAILED)))
        sys.exit(1)
    print("all preflight tests pass")
