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
"""Can this laptop run the tools? Answer before the robot is on blocks.

`pip install -r ...` printing no errors is not the same as the imports working.
The gap is wide enough to lose an evening in: a conda environment that was
never activated in this terminal, a pip that installed into a different Python
than the one on PATH, a package half-overwritten by `conda install` on top of
`pip install`, a wheel that silently did not build.

This imports what each tool actually needs and reports per TOOL, because that
is the unit a person cares about - "can I calibrate" rather than "is msgpack
present". Exits non-zero if anything you asked about is missing.

    python3 setup/check_env.py              # everything
    python3 setup/check_env.py --session    # just tonight's path
"""
import argparse
import importlib
import sys

# module -> the pip name that provides it, where they differ
PIP = {
    "ntcore": "pyntcore",
    "cv2": "opencv-python-headless",
}

# module -> which requirements file carries it, so a miss points at the right
# one-line fix instead of making someone read three files to find out.
FILE = {
    "msgpack": "requirements.txt", "websockets": "requirements.txt",
    "numpy": "requirements.txt",
    "ntcore": "requirements-nt.txt", "photonlibpy": "requirements-nt.txt",
    "scipy": "requirements-survey.txt", "cv2": "requirements-survey.txt",
}

TOOLS = [
    ("calib/calib_view.py",        ["msgpack", "websockets"],                 True),
    ("calib/charuco_capture.py",   ["msgpack", "websockets"],                 True),
    ("calib/vision_stddev.py",     ["msgpack", "websockets"],                 False),
    ("config/*.py",                ["msgpack", "websockets"],                 False),
    ("monitor/camera_drift.py",    ["msgpack", "websockets", "numpy"],        False),
    ("survey/photon_calib.py",     ["numpy"],                                 True),
    ("survey/solve_layout.py",     ["numpy", "cv2", "scipy"],                 False),
    ("mount/calibrate_mount.py",   ["numpy", "ntcore", "photonlibpy"],        True),
    ("viz/field_viewer.py",        ["numpy", "ntcore", "photonlibpy"],        True),
    ("thermal/heat_test.py",       ["msgpack", "websockets"],                 False),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", action="store_true",
                    help="only the tools a normal session needs")
    a = ap.parse_args()

    tools = [t for t in TOOLS if t[2]] if a.session else TOOLS

    print("python %s" % sys.version.split()[0])
    print("        %s" % sys.executable)
    print()

    cache, missing = {}, set()
    width = max(len(t[0]) for t in tools)
    for name, mods, _ in tools:
        bad = []
        for m in mods:
            if m not in cache:
                try:
                    importlib.import_module(m)
                    cache[m] = True
                except Exception:
                    cache[m] = False
            if not cache[m]:
                bad.append(m)
                missing.add(m)
        print("  %-*s  %s" % (width, name,
                              "ok" if not bad else "MISSING " + ", ".join(bad)))

    if not missing:
        print("\nEverything imports. This laptop can run the tools.")
        return 0

    pips = sorted({PIP.get(m, m) for m in missing})
    files = sorted({FILE.get(m, "requirements-survey.txt") for m in missing})
    print("\n%d module(s) missing. Install with:" % len(missing))
    print("    pip install %s" % " ".join(pips))
    print("    or: pip install %s" % " ".join("-r " + f for f in files))
    print()
    print("If that already 'worked', check these in order - they are the usual")
    print("three, and none of them look like an error:")
    print("  1. conda environment not activated IN THIS TERMINAL")
    print("     -> conda activate photon, then run this again")
    print("  2. pip installed into a different Python than the one above")
    print("     -> python -m pip install ...  (not plain pip)")
    print("  3. conda install used on top of pip install, half-overwriting it")
    print("     -> pip install --force-reinstall <package>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
