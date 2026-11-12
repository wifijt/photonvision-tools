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
"""Fetch a camera's calibration straight from PhotonVision.

Hardcoding intrinsics is a silent failure: a survey solved with the wrong camera
matrix produces a map that looks internally consistent and is geometrically wrong.
Always pull them from the box that is actually running.
"""
import io, json, sqlite3, tempfile, urllib.request, zipfile, os
import numpy as np
import sys


def list_calibrations(host, timeout=60):
    """Every camera and every calibration it holds, straight from the Pi."""
    url = "http://%s:5800/api/settings/photonvision_config.zip" % host
    raw = urllib.request.urlopen(url, timeout=timeout).read()
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n.endswith("photon.sqlite")]
            if not names:
                raise RuntimeError("no photon.sqlite in the settings bundle")
            z.extract(names[0], td)
            db = os.path.join(td, names[0])
        con = sqlite3.connect(db)
        rows = con.execute("select unique_name, config_json from cameras").fetchall()
        con.close()
    out = []
    for unique, cfg in rows:
        d = json.loads(cfg)
        cals = []
        for c in d.get("calibrations", []) or []:
            res = c["resolution"]
            cals.append(dict(
                w=int(res["width"]), h=int(res["height"]),
                K=np.array(c["cameraIntrinsics"]["data"], dtype=float).reshape(3, 3),
                D=np.array(c["distCoeffs"]["data"], dtype=float),
                n=len(c.get("observations") or [])))
        out.append(dict(unique=unique, nickname=d.get("nickname", unique),
                        deactivated=bool(d.get("deactivated")), calibrations=cals))
    return out


def fetch_calibration(host, width=None, height=None, camera=None, timeout=60):
    """Return (K, D, (w,h), nickname, n_snapshots) for ONE camera.

    With more than one camera connected this REFUSES to guess. It used to take
    the highest-resolution calibration found across every camera, with no way
    to say which camera you meant - and two OV9281s on one Pi have the same
    resolutions, so the winner was whichever row sqlite returned first. A
    survey solved with the other camera's intrinsics is wrong and says
    nothing, which is the exact failure this module exists to prevent.
    """
    cams = [c for c in list_calibrations(host, timeout) if c["calibrations"]]
    if camera:
        want = str(camera).strip().lower()
        cams = [c for c in cams
                if c["nickname"].strip().lower() == want
                or c["unique"].strip().lower() == want]
        if not cams:
            raise RuntimeError("no camera called %r with a calibration" % camera)
    if not cams:
        raise RuntimeError("no calibration found - calibrate the camera in "
                           "PhotonVision first")
    if len(cams) > 1:
        raise RuntimeError(
            "%d cameras have calibrations (%s) - say which with --camera. "
            "Guessing would silently solve with the wrong intrinsics."
            % (len(cams), ", ".join(repr(c["nickname"]) for c in cams)))
    cam = cams[0]
    cals = cam["calibrations"]
    if width and height:
        cals = [c for c in cals if (c["w"], c["h"]) == (int(width), int(height))]
        if not cals:
            raise RuntimeError("%s has no calibration at %sx%s - it has %s"
                               % (cam["nickname"], width, height,
                                  ", ".join("%dx%d" % (c["w"], c["h"])
                                            for c in cam["calibrations"])))
    best = max(cals, key=lambda c: c["w"] * c["h"])
    return best["K"], best["D"], (best["w"], best["h"]), cam["nickname"], best["n"]


if __name__ == "__main__":
    import argparse
    # Accept BOTH forms. Every other tool in this repo spells it --host, so a
    # positional-only host here is a trap: `--host 10.0.0.1` resolved the
    # literal hostname "--host" and failed with getaddrinfo, naming neither the
    # flag nor the address.
    ap = argparse.ArgumentParser(
        description="List every camera's calibrations on a PhotonVision host.")
    ap.add_argument("host_pos", nargs="?", metavar="HOST", default=None,
                    help="the PhotonVision host; --host also works")
    ap.add_argument("--host", default=None)
    ap.add_argument("--running", nargs=2, type=int, default=None,
                    metavar=("W", "H"),
                    help="the resolution you will actually RUN. A camera with no "
                         "calibration at its active resolution publishes NOTHING, "
                         "which looks exactly like a dead camera, so this is the "
                         "only question that matters before a session.")
    a = ap.parse_args()
    host = a.host or a.host_pos or "photonvision.local"
    cams = list_calibrations(host)
    if not cams:
        sys.exit("no cameras on %s" % host)
    bad = 0
    for c in cams:
        print("\ncamera: %s%s" % (c["nickname"], "  (DEACTIVATED)" if c["deactivated"] else ""))
        if not c["calibrations"]:
            print("  NO CALIBRATION AT ALL - this camera will publish nothing")
            bad += 1
            continue
        for k in sorted(c["calibrations"], key=lambda x: -(x["w"] * x["h"])):
            note = ""
            if k["n"] < 50:
                note = "   <-- only %d snapshots, expect a poor fit" % k["n"]
            print("  %4dx%-4d  %3d snapshots  fx %.1f fy %.1f cx %.1f cy %.1f%s"
                  % (k["w"], k["h"], k["n"], k["K"][0, 0], k["K"][1, 1],
                     k["K"][0, 2], k["K"][1, 2], note))
        if a.running:
            w, h = a.running
            hit = [k for k in c["calibrations"] if (k["w"], k["h"]) == (w, h)]
            if not hit:
                print("  *** NOTHING AT %dx%d - at that resolution this camera" % (w, h))
                print("      publishes nothing at all. Calibrate it or change the")
                print("      resolution to one listed above.")
                bad += 1
            elif hit[0]["n"] < 50:
                print("  !! %dx%d has only %d snapshots. It will work, but the pose"
                      % (w, h, hit[0]["n"]))
                print("     error it carries is inherited by every layout and every")
                print("     robotToCamera measured through it.")
    if a.running and not bad:
        print("\nAll cameras have a calibration at %dx%d." % tuple(a.running))
    sys.exit(1 if bad else 0)
