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



# ---------------------------------------------------------------------------
# Where the team number lives.
#
# Exporting FRC_TEAM works and keeps working, but it lives in one shell. A
# student who opens a second terminal, or double-clicks a script, or reboots,
# has lost it - and the failure is a tool timing out against an address it
# guessed, which looks like a network fault rather than a missing variable.
# So: ask once, write it down, and never ask again.
#
# Precedence is deliberate and is the usual one:
#     --team on the command line   beats
#     $FRC_TEAM in this shell      beats
#     the file
# An export is a considered act for one terminal - borrowing another team's
# field, say - and must not be silently overridden by something written weeks
# ago.
# ---------------------------------------------------------------------------

#: One file, in the home directory rather than the repo, so it survives a
#: fresh `git clone` and is shared by every clone on the laptop.
CONFIG = os.path.join(os.path.expanduser("~"), ".photonvision-tools.conf")

_config_cache = None
_asked = set()


def read_config(path=None):
    """KEY=value lines from CONFIG. Missing or unreadable file -> {}."""
    global _config_cache
    if path is None and _config_cache is not None:
        return _config_cache
    out = {}
    try:
        with open(path or CONFIG) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except (IOError, OSError):
        pass
    if path is None:
        _config_cache = out
    return out


def write_config(values, path=None):
    """Merge `values` into the config file. Returns the path written."""
    global _config_cache
    path = path or CONFIG
    merged = dict(read_config(path))
    merged.update({k: v for k, v in values.items() if v not in (None, "")})
    with open(path, "w") as fh:
        fh.write("# photonvision-tools\n"
                 "#\n"
                 "# Written by preflight.py. Edit it by hand if you like, or run\n"
                 "#     python3 preflight.py --setup\n"
                 "# A --team flag or an exported FRC_TEAM overrides what is here.\n"
                 "#\n")
        for k in sorted(merged):
            fh.write("%s=%s\n" % (k, merged[k]))
    _config_cache = merged
    return path


def setting(name):
    """The value of FRC_TEAM / FRC_PI: environment first, then the file."""
    return os.environ.get(name) or read_config().get(name) or None


def _valid_team(text):
    """None if the team number is usable, else why not.

    Rejecting 5 digits is not pedantry: team_base() zero-pads to four and
    splits, so 12345 would silently become 10.12.345, which is not an address.
    """
    text = (text or "").strip()
    if not text.isdigit() or int(text) < 1:
        return "a team number is digits, like 5459"
    if len(text.lstrip("0")) > 4:
        return "FRC team numbers are at most four digits"
    return None


def interactive():
    """Is there a person here to answer a question?

    Checked BEFORE anything is printed, not after. A cron job, a CI run or a
    tool with its output piped to a file must not find half a question in its
    log, and must never block on a prompt nobody will ever type into.
    """
    try:
        return bool(sys.stdin and sys.stdin.isatty()
                    and sys.stderr and sys.stderr.isatty())
    except (ValueError, AttributeError):   # closed or replaced streams
        return False


def _ask(prompt, default=None):
    """Prompt on stderr, read stdin. None if there is nobody to ask.

    stderr, not stdout, so a tool whose output is being piped into a file does
    not write the question into the data.
    """
    if not interactive():
        return None
    sys.stderr.write(prompt + (" [%s]: " % default if default else ": "))
    sys.stderr.flush()
    try:
        answer = sys.stdin.readline()
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write("\n")
        return None
    if answer == "":           # EOF, not an empty line
        sys.stderr.write("\n")
        return None
    answer = answer.strip()
    return answer or default


def ask_team(why=""):
    """Get a team number, asking and saving if this is a terminal.

    Returns the number as a string, or None when there is nobody to ask - the
    caller then reports the problem instead of hanging a script forever.
    """
    have = setting("FRC_TEAM")
    if have:
        return have
    if "FRC_TEAM" in _asked or not interactive():
        return None
    _asked.add("FRC_TEAM")
    sys.stderr.write("\n%sWhat is your FRC team number?\n"
                     "  It sets the whole address range: 5459 -> 10.54.59.x,\n"
                     "  so the roboRIO is 10.54.59.2.\n" % (why + "\n" if why else ""))
    while True:
        answer = _ask("  team number")
        if answer is None:
            return None
        bad = _valid_team(answer)
        if not bad:
            break
        sys.stderr.write("  %s\n" % bad)
    team = str(int(answer))
    base = team_base(team)
    sys.stderr.write("  team %s -> %s.x   (roboRIO %s.2, radio %s.1)\n"
                     % (team, base, base, base))

    pi = setting("FRC_PI")
    if not pi:
        sys.stderr.write("\nAnd where does PhotonVision run? Enter to accept the default,\n"
                         "  or give the Pi's address - %s.12 is a common choice.\n" % base)
        pi = _ask("  PhotonVision host", "photonvision.local")

    saved = {"FRC_TEAM": team}
    if pi:
        saved["FRC_PI"] = pi
    try:
        path = write_config(saved)
        sys.stderr.write("\nSaved to %s - you will not be asked again.\n"
                         "  Delete that file, or run `python3 preflight.py --setup`, to change it.\n\n"
                         % path)
    except (IOError, OSError) as e:
        sys.stderr.write("\nCould not write %s (%s).\n"
                         "  Using %s for this run only.\n\n" % (CONFIG, e, team))
        os.environ["FRC_TEAM"] = team
        if pi:
            os.environ["FRC_PI"] = pi
    return team


def setup():
    """`--setup`: ask both questions again and rewrite the file.

    Takes no `interactive` flag on purpose - that name would shadow the
    module-level interactive(), which is the one thing in here that must not
    be ambiguous.
    """
    global _config_cache
    current = read_config()
    if current:
        print("current settings in %s:" % CONFIG)
        for k in sorted(current):
            print("    %s=%s" % (k, current[k]))
        print("")
    _config_cache = {}
    saved_env = {}
    for k in ("FRC_TEAM", "FRC_PI"):
        if k in os.environ:
            saved_env[k] = os.environ.pop(k)
    _asked.discard("FRC_TEAM")
    team = ask_team()
    os.environ.update(saved_env)
    if not team:
        sys.exit("nothing to read from - run this in a terminal, or write %s by hand:\n"
                 "    FRC_TEAM=5459\n    FRC_PI=10.54.59.12" % CONFIG)
    for k, v in saved_env.items():
        print("note: $%s is exported as %s in this shell and still wins here." % (k, v))
    return team


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

    The team number comes from --team, then $FRC_TEAM, then the saved config
    file. A role that needs one and has none ASKS, when there is a terminal to
    ask, and writes the answer down so it is asked exactly once per laptop. In
    a script or a cron job there is nobody to ask, so it says what is missing
    and exits rather than hanging.
    """
    if not value:
        return value
    v = str(value).strip()
    low = v.lower()
    if low in ("pi", "photon", "photonvision", "coprocessor"):
        return setting("FRC_PI") or "photonvision.local"
    needs_team = low in ("rio", "roborio", "radio") or (
        v.startswith(".") and v[1:].isdigit())
    team = team or setting("FRC_TEAM")
    if needs_team and not team:
        team = ask_team("'%s' needs a team number." % v)
    if low in ("rio", "roborio", "radio"):
        if not team:
            sys.exit(_no_team(v))
        return "%s.%d" % (team_base(team), 1 if low == "radio" else 2)
    if v.startswith(".") and v[1:].isdigit():
        if not team:
            sys.exit(_no_team(v))
        return "%s%s" % (team_base(team), v)
    return v


def _no_team(value):
    """What to say when a role needs a team number and nobody can be asked."""
    return ("'%s' needs a team number, and there is no terminal to ask on.\n\n"
            "Any one of these fixes it:\n"
            "    --team 5459                     just this command\n"
            "    export FRC_TEAM=5459            this shell\n"
            "    python3 preflight.py --setup    this laptop, once, for good\n\n"
            "Team 5459 would make the roboRIO 10.54.59.2." % value)


def add_team_argument(parser):
    """`--team`, defaulted from the environment or the saved file.

    Resolved at parser-build time so `--help` shows the number this laptop
    will actually use, rather than the word "None".
    """
    parser.add_argument("--team", default=setting("FRC_TEAM"),
                        help="FRC team number, so --host can be 'rio' or '.12'. "
                             "Defaults to $FRC_TEAM, then %s. Asked for once "
                             "and remembered if neither is set." % CONFIG)

if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        print("USAGE\n"
              "    python3 preflight.py [--setup] [--show] [--team=N] [host[:port] ...]\n\n"
              "OPTIONS\n"
              "    --setup       ask for the team number and the Pi, and save them to\n"
              "                  %s. Run it once per laptop.\n"
              "    --show        print the saved settings and where they came from\n"
              "    --team=N      use this team number for this run only\n"
              "    host[:port]   also check these; default is rio:5810 and pi:5800\n"
              % CONFIG)
        sys.exit(0)

    if "--setup" in sys.argv:
        setup()
        sys.exit(0)

    if "--show" in sys.argv:
        print("config file: %s%s" % (CONFIG, "" if os.path.exists(CONFIG) else "   (does not exist yet)"))
        for k in ("FRC_TEAM", "FRC_PI"):
            env, filed = os.environ.get(k), read_config().get(k)
            if env:
                print("  %-9s %-20s from $%s (overrides the file)" % (k, env, k))
            elif filed:
                print("  %-9s %-20s from the file" % (k, filed))
            else:
                print("  %-9s %-20s run --setup" % (k, "not set"))
        sys.exit(0)

    print("interpreter:")
    print(interpreter())
    note = conda_mismatch()
    print("\nconda: " + (note if note else "running the activated environment (or none)"))
    print("\nlocal address: %s" % (local_address() or "unknown"))
    team = setting("FRC_TEAM")
    args = [a for a in sys.argv[1:] if not a.startswith("--team=")]
    for a in sys.argv[1:]:
        if a.startswith("--team="):
            team = a.split("=", 1)[1]
    if not team:
        team = ask_team("A team number is what turns 'rio' into an address.")
    if team:
        src = ("$FRC_TEAM" if os.environ.get("FRC_TEAM") == str(team)
               else CONFIG if read_config().get("FRC_TEAM") == str(team)
               else "--team")
        print("\nteam %s -> %s.x   (roboRIO %s.2, radio %s.1)   [from %s]"
              % (team, team_base(team), team_base(team), team_base(team), src))
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
