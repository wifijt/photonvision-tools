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
"""Checks every tool runs BEFORE it asks anyone to drive a robot.

Written after an evening where three separate setup problems each presented as
a different confusing failure, and none of them named its own cause:

  1. A script launched directly rather than through the activated conda
     environment, so `import ntcore` failed while the same import worked in a
     REPL two inches away.
  2. A laptop that was not on the robot network. The tool connected to nothing
     and sat there recording zero rows, looking like a camera problem.
  3. A missing package reported from inside a function, a hundred lines after
     argument parsing, with the robot already in position.

None of these are hard to detect. They were simply never checked.

STDLIB ONLY, deliberately: this module has to work on a machine where nothing
is installed yet, since diagnosing that is half its job.
"""
import os
import socket
import sys

#: Which requirements file supplies each importable module.
FILE = {
    "msgpack": "requirements.txt",
    "websockets": "requirements.txt",
    "numpy": "requirements.txt",
    "ntcore": "requirements-nt.txt",
    "photonlibpy": "requirements-nt.txt",
    "scipy": "requirements-survey.txt",
    "cv2": "requirements-survey.txt",
}

#: pip name, where it differs from the import name.
PIP = {"ntcore": "pyntcore", "cv2": "opencv-python-headless"}


def interpreter():
    """The running interpreter, as two lines ready to print."""
    return "    %s\n    Python %s" % (sys.executable, sys.version.split()[0])


def conda_mismatch():
    """The exact trap that ate an hour: an activated env that is not running.

    `conda activate` sets CONDA_PREFIX in the shell. A script launched through
    its shebang, a file association or an editor's own interpreter inherits
    that variable while running a DIFFERENT python - so the environment the
    user installed into and the environment executing the code are not the
    same, and nothing says so. sys.prefix is the one actually in use, so a
    disagreement between the two is conclusive rather than a guess.

    Returns an explanatory string, or None when there is nothing to report.
    """
    env = os.environ.get("CONDA_PREFIX")
    if not env:
        return None
    if os.path.normcase(os.path.normpath(env)) == \
       os.path.normcase(os.path.normpath(sys.prefix)):
        return None
    return ("You have a conda environment ACTIVATED that is NOT the one\n"
            "running this script:\n"
            "    activated: %s\n"
            "    running:   %s\n"
            "Anything you installed went to the first and this is the second.\n"
            "Launch it as `python %s` rather than running the file directly -\n"
            "a shebang, a Windows file association and an editor's configured\n"
            "interpreter all bypass the activated environment."
            % (env, sys.prefix, os.path.basename(sys.argv[0] or "the script")))


def require(modules, what=""):
    """Import every module now, or exit explaining exactly what is missing.

    Called at the TOP of a tool, not where the module is first used. A tool
    that discovers a missing dependency halfway through has already wasted the
    setup around it.
    """
    missing = []
    for m in modules:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if not missing:
        return
    files = sorted({FILE.get(m, "requirements.txt") for m in missing})
    pips = " ".join(PIP.get(m, m) for m in missing)
    msg = ["missing: %s%s" % (", ".join(missing), (" - needed for %s" % what) if what else ""),
           "",
           "The interpreter that cannot see %s:"
           % ("them" if len(missing) > 1 else "it"),
           interpreter(),
           "",
           "    python -m pip install %s" % " ".join("-r " + f for f in files),
           "    python -m pip install %s" % pips,
           "",
           "Use `python -m pip`, never a bare `pip`: under conda they are",
           "routinely different environments, so the install reports success",
           "and the import still fails."]
    note = conda_mismatch()
    if note:
        msg += ["", note]
    if "ntcore" in missing:
        msg += ["",
                "pyntcore ships wheels for Python 3.10-3.14 only and declares no",
                "requires_python, so on 3.9 pip says 'could not find a version",
                "that satisfies the requirement' instead of naming the cause."]
    sys.exit("\n".join(msg))


def local_address(toward="8.8.8.8"):
    """This machine's address on the route toward `toward`. Sends nothing."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((toward, 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def reachable(host, port, timeout=3.0):
    """(ok, detail) for a TCP connect. Does not speak any protocol."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return False, "name does not resolve (%s)" % e.strerror
    last = "no address"
    for fam, typ, proto, _canon, addr in infos:
        s = socket.socket(fam, typ, proto)
        s.settimeout(timeout)
        try:
            s.connect(addr)
            return True, addr[0]
        except socket.timeout:
            last = "no answer within %gs" % timeout
        except OSError as e:
            last = e.strerror or str(e)
        finally:
            s.close()
    return False, last


def require_host(host, port, what, timeout=3.0):
    """Exit unless `host:port` accepts a connection, saying what to check.

    The failure this replaces is the worst kind: a tool pointed at a host it
    cannot reach does not error, it simply receives nothing, and every symptom
    downstream looks like a camera or a layout problem.
    """
    ok, detail = reachable(host, port, timeout)
    if ok:
        return
    mine = local_address(host if _looks_numeric(host) else "8.8.8.8")
    msg = ["cannot reach %s on port %d - %s" % (host, port, detail),
           "", "%s lives there. Nothing below this point can work." % what, ""]
    if mine:
        msg.append("This machine's address on that route: %s" % mine)
        if _looks_numeric(host) and mine.split(".")[:3] != host.split(".")[:3]:
            msg += ["The host is on %s.x and you are on %s.x - DIFFERENT networks."
                    % (".".join(host.split(".")[:3]), ".".join(mine.split(".")[:3])),
                    "On a robot that usually means the laptop is still on the",
                    "school or house wifi instead of the robot radio."]
    msg += ["", "Check, in this order:",
            "  1. the laptop is joined to the right network",
            "  2. the address is right (the roboRIO and the Pi are NOT the same box)",
            "  3. the thing on the other end is actually running"]
    sys.exit("\n".join(msg))


def _looks_numeric(host):
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def require_nt(inst, host, seconds=10.0):
    """Block until NetworkTables actually connects, or exit saying it did not.

    startClient4() returns immediately and never fails: an unreachable server
    just means no topics ever arrive. Tools that skip this record zero rows and
    blame the cameras.
    """
    import time
    t0 = time.time()
    while time.time() - t0 < seconds:
        if inst.isConnected():
            return
        time.sleep(0.2)
    sys.exit(
        "NetworkTables never connected to %s (waited %gs).\n\n"
        "startClient4() does not fail on a bad address - it just never\n"
        "receives anything, which is why this is checked rather than assumed.\n\n"
        "  - On a robot the NT server is the roboRIO, not the Pi.\n"
        "  - Off a robot, PhotonVision's own NT server must be ON\n"
        "    (config/set_nt_server.py true), and it must be OFF whenever a\n"
        "    roboRIO is present, or the two servers fight and both go quiet.\n"
        "  - Check the laptop is on the robot network: %s"
        % (host, local_address() or "could not determine this machine's address"))



def team_base(team):
    """10.TE.AM from a team number, by the documented FRC rule.

    Zero-pad to four digits and split: 5459 -> 10.54.59, 123 -> 10.1.23,
    9 -> 10.0.9. Doing it by arithmetic instead (divmod by 100) gets the
    short team numbers wrong, which is the kind of bug that only shows up
    at the one event where you borrow a laptop from another team.
    """
    d = "%04d" % int(team)
    return "10.%d.%d" % (int(d[:2]), int(d[2:]))


def resolve_host(value, team=None):
    """Turn a role into an address, so nobody types an IP under pressure.

        rio / roborio   ->  10.TE.AM.2      the NT server on a robot
        radio           ->  10.TE.AM.1
        pi / photon     ->  $FRC_PI, else photonvision.local
        .12             ->  10.TE.AM.12
        anything else   ->  itself

    The team number comes from --team or $FRC_TEAM. Roles that need it and
    cannot get it say so rather than guessing.
    """
    if not value:
        return value
    v = str(value).strip()
    low = v.lower()
    if low in ("pi", "photon", "photonvision", "coprocessor"):
        return os.environ.get("FRC_PI") or "photonvision.local"
    team = team or os.environ.get("FRC_TEAM")
    if low in ("rio", "roborio", "radio"):
        if not team:
            sys.exit("'%s' needs a team number: pass --team, or set FRC_TEAM.\n"
                     "Team 5459 would make the roboRIO 10.54.59.2." % v)
        return "%s.%d" % (team_base(team), 1 if low == "radio" else 2)
    if v.startswith(".") and v[1:].isdigit():
        if not team:
            sys.exit("'%s' is a last octet and needs a team number: pass --team, "
                     "or set FRC_TEAM." % v)
        return "%s%s" % (team_base(team), v)
    return v


def add_team_argument(parser):
    """`--team`, defaulted from the environment. One line per tool."""
    parser.add_argument("--team", default=os.environ.get("FRC_TEAM"),
                        help="FRC team number, so --host can be 'rio' or '.12'. "
                             "Defaults to $FRC_TEAM.")

if __name__ == "__main__":
    print("interpreter:")
    print(interpreter())
    note = conda_mismatch()
    print("\nconda: " + (note if note else "running the activated environment (or none)"))
    print("\nlocal address: %s" % (local_address() or "unknown"))
    team = os.environ.get("FRC_TEAM")
    args = [a for a in sys.argv[1:] if not a.startswith("--team=")]
    for a in sys.argv[1:]:
        if a.startswith("--team="):
            team = a.split("=", 1)[1]
    if team:
        print("\nteam %s -> %s.x   (roboRIO %s.2, radio %s.1)"
              % (team, team_base(team), team_base(team), team_base(team)))
    if not args:
        args = ["rio:5810", "pi:5800"] if team else ["pi:5800"]
        print("checking the usual two; name others as host[:port]")
    print("")
    for h in args:
        host, _, port = h.rpartition(":")
        if not host:
            host, port = h, "5800"
        addr = resolve_host(host, team)
        port = int(port or 5800)
        ok, detail = reachable(addr, port)
        label = addr if addr == host else "%s = %s" % (host, addr)
        print("%-28s :%-5d %s  (%s)" % (label, port, "OK" if ok else "UNREACHABLE", detail))
