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
"""Fake recordings with a KNOWN robotToCamera, to test calibrate_mount.py.

calibrate_mount is the one tool here whose output is pasted straight into robot
code, and on a real rig there is nothing to check it against - that is the whole
reason it exists. So the check has to be synthetic: build recordings from a
robotToCamera you chose, solve them, and see whether you get it back.

    python3 mount/synthetic_rig.py good
    python3 mount/calibrate_mount.py solve syn-spin.npz --forward syn-drive.npz \
        --origin-height 0

    expect x and y within ~0.5 mm, angles within ~0.05 deg of the printed truth.

    python3 mount/synthetic_rig.py wander      # a spin about a drifting axis
    ... same solve ...

    expect a ~13 mm residual, "THE SPIN ITSELF IS BAD", and NO Java or JSON
    block. That last part is the regression: the fit-quality gate used to run
    AFTER the emission, so a bad spin still printed a pasteable Transform3d and
    put the warning seventy lines below it.

Column order matches record(): t, x, y, z, yaw, pitch, roll, gyro. Pitch and
roll are written in the FIELD frame, which is where a level robot puts them -
Rz(theta) * Rz(Y)Ry(p)Rx(r) = Rz(theta+Y)Ry(p)Rx(r), so only yaw moves.
"""
import numpy as np, math, sys

TRUTH = {
    "OV9281":      dict(x= 0.300, y= 0.100, z=0.600, yaw= 20.0, pitch=15.0, roll= 2.0),
    "OV9281 (1)":  dict(x=-0.200, y=-0.150, z=0.550, yaw=150.0, pitch=10.0, roll=-3.0),
}
ROBOT_AT = np.array([2.0, 1.0])     # robot origin in the layout frame
DRIVE_HEADING = math.radians(30.0)  # robot forward during the straight drive

def spin(path, n=900, noise=0.0015, wander=0.0, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    th = np.linspace(0, 4*math.pi, n)                 # two full turns
    # a wandering axis: the rotation centre itself drifts during the spin
    cen = ROBOT_AT[None, :] + wander * np.c_[np.sin(th*0.7), np.cos(th*0.5)]
    for name, t in TRUTH.items():
        rows = []
        for i, a in enumerate(th):
            c, s = math.cos(a), math.sin(a)
            x = cen[i, 0] + c*t["x"] - s*t["y"]
            y = cen[i, 1] + s*t["x"] + c*t["y"]
            rows.append([i*0.02,
                         x + rng.normal(0, noise), y + rng.normal(0, noise),
                         t["z"] + rng.normal(0, noise),
                         a + math.radians(t["yaw"]) + rng.normal(0, noise),
                         math.radians(t["pitch"]) + rng.normal(0, noise),
                         math.radians(t["roll"]) + rng.normal(0, noise),
                         math.degrees(a)])          # gyro in degrees, scale +1
        out[name] = np.array(rows)
    np.savez(path, **out, names=np.array(list(TRUTH)))

def drive(path, n=300, dist=3.0, noise=0.0015, seed=1):
    rng = np.random.default_rng(seed)
    a = DRIVE_HEADING
    c, s = math.cos(a), math.sin(a)
    out = {}
    for name, t in TRUTH.items():
        rows = []
        for i in range(n):
            d = dist * i/(n-1)
            x = ROBOT_AT[0] + d*c + c*t["x"] - s*t["y"]
            y = ROBOT_AT[1] + d*s + s*t["x"] + c*t["y"]
            rows.append([i*0.02,
                         x + rng.normal(0, noise), y + rng.normal(0, noise),
                         t["z"] + rng.normal(0, noise),
                         a + math.radians(t["yaw"]) + rng.normal(0, noise),
                         math.radians(t["pitch"]) + rng.normal(0, noise),
                         math.radians(t["roll"]) + rng.normal(0, noise),
                         math.degrees(a)])
        out[name] = np.array(rows)
    np.savez(path, **out, names=np.array(list(TRUTH)))

if __name__ == "__main__":
    which = sys.argv[1]
    if which == "good":
        spin("syn-spin.npz"); drive("syn-drive.npz")
    elif which == "wander":
        spin("syn-spin.npz", wander=0.020); drive("syn-drive.npz")
    print("TRUTH robotToCamera:")
    for n,t in TRUTH.items():
        print("  %-14s x %+.4f  y %+.4f  z %+.4f   roll %+.2f pitch %+.2f yaw %+.2f"
              %(n,t["x"],t["y"],t["z"],t["roll"],t["pitch"],t["yaw"]))
