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


def fetch_calibration(host, width=None, height=None, timeout=60):
    """Return (K, D, (w,h), nickname) for the camera's calibrated resolution.

    If width/height are given, that resolution must exist. Otherwise the
    highest-resolution calibration is used.
    """
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

    best = None
    for unique, cfg in rows:
        d = json.loads(cfg)
        nick = d.get("nickname", unique)
        for c in d.get("calibrations", []) or []:
            res = c["resolution"]
            w, h = int(res["width"]), int(res["height"])
            if width and height and (w, h) != (int(width), int(height)):
                continue
            K = np.array(c["cameraIntrinsics"]["data"], dtype=float).reshape(3, 3)
            D = np.array(c["distCoeffs"]["data"], dtype=float)
            n_obs = len(c.get("observations") or [])
            cand = (w * h, K, D, (w, h), nick, n_obs)
            if best is None or cand[0] > best[0]:
                best = cand
    if best is None:
        raise RuntimeError(
            "no calibration found%s - calibrate the camera in PhotonVision first"
            % ("" if not width else " at %sx%s" % (width, height)))
    _, K, D, wh, nick, n_obs = best
    return K, D, wh, nick, n_obs


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "photonvision.local"
    K, D, wh, nick, n = fetch_calibration(host)
    print("camera      : %s" % nick)
    print("resolution  : %dx%d  (%d calibration snapshots)" % (wh[0], wh[1], n))
    print("fx %.2f  fy %.2f  cx %.2f  cy %.2f" % (K[0, 0], K[1, 1], K[0, 2], K[1, 2]))
    print("distortion  : %s" % np.round(D, 5).tolist())
    if n and n < 50:
        print("WARNING: only %d snapshots - upstream now requires 100+. Expect a poor fit." % n)
