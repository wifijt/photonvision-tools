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
"""Get MIPI/CSI cameras working on a Pi, and prove PhotonVision can see them.

    python3 csi_cameras.py --show                     what is connected now
    python3 csi_cameras.py --set ov9281,ov9281        write it, then reboot
    python3 csi_cameras.py --verify                   after the reboot

Runs ON the coprocessor.

WHY THIS EXISTS

`camera_auto_detect=1` handles the official Raspberry Pi cameras and SILENTLY
fails on everything else. An OV9281 - the global-shutter mono sensor most FRC
teams want, because a rolling shutter smears a tag the moment the robot turns -
is one of the ones it does not handle. The fix is two lines in config.txt that
nobody guesses:

    camera_auto_detect=0
    dtoverlay=ov9281,cam0
    dtoverlay=ov9281,cam1

Auto-detect must be OFF, and each sensor bound explicitly to its connector.
Getting this wrong looks like a dead camera with no error anywhere.

WHAT IT CANNOT DO

It cannot identify a sensor that has no overlay loaded. Nothing can, without
talking to it: the CSI i2c buses exist in sysfs but there is no /dev/i2c-* on
this image (i2c-dev is not loaded), and `libcamera-hello` is broken here -
`undefined symbol: _ZN7libpisp22compute_optimal_stride...`, a libcamera/libpisp
mismatch. So --show reports what the kernel has BOUND, which is the truth once
an overlay is loaded or auto-detect has run. For a sensor that is connected but
unconfigured, you have to say what it is. That is not a limitation worth hiding
behind a guess.

WHAT IT CHECKS THAT MATTERS

That PhotonVision sees the cameras - not that the kernel does. Those are
different, and the gap is where an evening goes. A camera can bind perfectly and
still be missing from PhotonVision, or arrive with no calibration for its
resolution, which makes it useless for pose and reports nothing at all rather
than failing loudly.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time

CONFIG = "/boot/firmware/config.txt"
FALLBACK_CONFIG = "/boot/config.txt"
OVERLAY_DIR = "/boot/firmware/overlays"

# Sensors whose driver `name` differs from the overlay you must write.
# ov9281 is the one that catches people: the DRIVER is ov9282.
NAME_TO_OVERLAY = {
    "ov9281": "ov9281", "ov9282": "ov9281",
    "ov7251": "ov7251", "ov2311": "ov2311", "ov5647": "ov5647",
    "imx219": "imx219", "imx477": "imx477", "imx708": "imx708",
    "imx296": "imx296", "imx290": "imx290", "imx327": "imx327",
    "imx462": "imx462", "imx519": "imx519", "imx258": "imx258",
    "ov64a40": "ov64a40",
}


def config_path():
    return CONFIG if os.path.exists(CONFIG) else FALLBACK_CONFIG


def bound_sensors():
    """[(i2c_bus, addr, name)] for every camera sensor the kernel has bound."""
    out = []
    for dev in glob.glob("/sys/bus/i2c/drivers/*/*-[0-9a-f]*"):
        nm = os.path.join(dev, "name")
        if not os.path.exists(nm):
            continue
        try:
            with open(nm) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name.lower() not in NAME_TO_OVERLAY:
            continue
        base = os.path.basename(dev)
        try:
            bus, addr = base.split("-", 1)
        except ValueError:
            continue
        out.append((int(bus), addr, name.lower()))
    return sorted(out)


def csi_buses():
    """i2c bus numbers that look like CSI connectors, in connector order.

    On a Pi 5 these are the DesignWare adapters; the lower bus number is the
    connector nearer the board edge. Reported rather than assumed, because the
    numbering differs between Pi models and kernels.
    """
    found = []
    for d in glob.glob("/sys/bus/i2c/devices/i2c-*"):
        try:
            with open(os.path.join(d, "name")) as f:
                nm = f.read().strip()
        except OSError:
            continue
        n = int(os.path.basename(d).split("-")[1])
        found.append((n, nm))
    return sorted(found)


def config_lines():
    p = config_path()
    try:
        with open(p) as f:
            return p, f.read().splitlines()
    except OSError as e:
        sys.exit("cannot read %s (%s)" % (p, e))


def current_config():
    p, lines = config_lines()
    auto, overlays = None, []
    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            continue
        m = re.match(r"camera_auto_detect\s*=\s*(\d+)", s)
        if m:
            auto = int(m.group(1))
        m = re.match(r"dtoverlay\s*=\s*([A-Za-z0-9_]+)\s*(?:,\s*(cam[01]))?", s)
        if m and m.group(1) in NAME_TO_OVERLAY.values():
            overlays.append((m.group(1), m.group(2) or "(no connector)"))
    return p, auto, overlays


def available_overlays():
    return sorted(os.path.splitext(os.path.basename(x))[0]
                  for x in glob.glob(os.path.join(OVERLAY_DIR, "*.dtbo"))
                  if os.path.splitext(os.path.basename(x))[0] in NAME_TO_OVERLAY.values())


async def photonvision_cameras(host, port=5800, timeout=20):
    """What PhotonVision reports - the only check that actually matters."""
    import msgpack, websockets
    uri = "ws://%s:%d/websocket_data" % (host, port)
    found = {}
    async with websockets.connect(uri, open_timeout=15, ping_interval=None,
                                  max_size=80_000_000) as ws:
        t0 = time.time()
        while time.time() - t0 < timeout and not found:
            raw = await __import__("asyncio").wait_for(ws.recv(), timeout=10)
            if not isinstance(raw, bytes):
                continue
            m = msgpack.unpackb(raw, raw=False)
            if isinstance(m, dict):
                for c in m.get("cameraSettings", []) or []:
                    st = c.get("currentPipelineSettings", {})
                    idx = st.get("cameraVideoModeIndex")
                    fmts = c.get("videoFormatList") or {}
                    fmt = fmts.get(idx) or fmts.get(str(idx)) or {}
                    cals = [(int((x.get("resolution") or {}).get("width", 0)),
                             int((x.get("resolution") or {}).get("height", 0)))
                            for x in (c.get("calibrations") or [])]
                    found[c["uniqueName"]] = {
                        "nickname": c.get("nickname", "?"),
                        "mode": (fmt.get("width"), fmt.get("height")),
                        "calibrations": cals,
                    }
    return found


def show(args):
    print("=== physically bound to the kernel ===")
    b = bound_sensors()
    if not b:
        print("  nothing. Either no camera is connected, or no overlay is loaded")
        print("  for it - a sensor with neither an overlay nor auto-detect is")
        print("  invisible, and that is indistinguishable from unplugged.")
    for bus, addr, name in b:
        print("  i2c-%-3d 0x%s  %s   -> overlay '%s'"
              % (bus, addr, name, NAME_TO_OVERLAY.get(name, "?")))
    print("\n=== i2c buses that look like CSI connectors ===")
    for n, nm in csi_buses():
        mark = "  <- has a sensor" if any(x[0] == n for x in b) else ""
        print("  i2c-%-3d %s%s" % (n, nm, mark))
    p, auto, ovl = current_config()
    print("\n=== %s ===" % p)
    print("  camera_auto_detect = %s%s"
          % (auto, "   <- must be 0 for non-Pi sensors" if auto else ""))
    if ovl:
        for name, conn in ovl:
            print("  dtoverlay=%s,%s" % (name, conn))
    else:
        print("  no camera overlays")
    print("\n=== overlays available on this image ===")
    print("  " + " ".join(available_overlays()))

    # the check that matters
    if args.host:
        print("\n=== what PhotonVision sees ===")
        import asyncio
        try:
            cams = asyncio.run(photonvision_cameras(args.host, args.port))
        except Exception as exc:
            print("  could not reach PhotonVision at %s (%s)" % (args.host, type(exc).__name__))
            return
        if not cams:
            print("  NONE. The kernel may have bound a sensor that PhotonVision")
            print("  has not picked up - restart PhotonVision, or check its logs.")
        for uid, c in cams.items():
            cal = ", ".join("%dx%d" % r for r in c["calibrations"]) or "NONE"
            flag = "" if tuple(c["mode"]) in c["calibrations"] else \
                   "   <- ACTIVE MODE IS NOT CALIBRATED"
            print("  %-14s active %sx%s   calibrated: %s%s"
                  % (c["nickname"], c["mode"][0], c["mode"][1], cal, flag))
        if len(cams) != len(b) and b:
            print("\n  MISMATCH: kernel has %d sensor(s), PhotonVision reports %d."
                  % (len(b), len(cams)))


def write_config(args):
    sensors = [s.strip().lower() for s in args.set.split(",") if s.strip()]
    if not sensors:
        sys.exit("--set needs at least one sensor, e.g. --set ov9281,ov9281")
    if len(sensors) > 2:
        sys.exit("a Pi has two CSI connectors; got %d sensors" % len(sensors))
    avail = available_overlays()
    for s in sensors:
        ov = NAME_TO_OVERLAY.get(s)
        if ov is None:
            sys.exit("unknown sensor %r. Known: %s"
                     % (s, ", ".join(sorted(set(NAME_TO_OVERLAY.values())))))
        if ov not in avail:
            sys.exit("overlay %r is not on this image. Available: %s" % (ov, " ".join(avail)))

    p, lines = config_lines()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak-csi-%s" % (p, stamp)
    if not args.dry_run:
        try:
            shutil.copy2(p, backup)
        except OSError as e:
            sys.exit("could not back up %s (%s) - run with sudo?" % (p, e))
        print("backed up %s -> %s" % (p, backup))

    # Comment out what is there rather than deleting it, so a human can see what
    # changed and put it back by hand if this tool got it wrong.
    out, changed = [], []
    for ln in lines:
        s = ln.strip()
        if re.match(r"camera_auto_detect\s*=", s) and not s.startswith("#"):
            out.append("# " + ln + "    # disabled by csi_cameras.py " + stamp)
            changed.append(s)
            continue
        m = re.match(r"dtoverlay\s*=\s*([A-Za-z0-9_]+)", s)
        if m and m.group(1) in NAME_TO_OVERLAY.values() and not s.startswith("#"):
            out.append("# " + ln + "    # replaced by csi_cameras.py " + stamp)
            changed.append(s)
            continue
        out.append(ln)

    block = ["", "### CSI cameras, written by csi_cameras.py %s" % stamp,
             "### auto-detect handles the official Pi cameras and silently fails",
             "### on others, so it is off and each sensor is bound explicitly.",
             "camera_auto_detect=0"]
    for i, s in enumerate(sensors):
        block.append("dtoverlay=%s,cam%d" % (NAME_TO_OVERLAY[s], i))
    out.extend(block)

    print("\n--- would write ---" if args.dry_run else "\n--- writing ---")
    for ln in block:
        if ln:
            print("  " + ln)
    if changed:
        print("  (commenting out: %s)" % "; ".join(changed))
    if args.dry_run:
        print("\ndry run - nothing written")
        return
    try:
        with open(p, "w") as f:
            f.write("\n".join(out) + "\n")
    except OSError as e:
        sys.exit("could not write %s (%s) - run with sudo" % (p, e))
    print("\nwritten. A reboot is required: the overlay is read at boot.")
    print("  sudo reboot")
    print("then:  python3 %s --verify --expect %d"
          % (os.path.basename(__file__), len(sensors)))


def verify(args):
    import asyncio
    print("=== kernel ===")
    b = bound_sensors()
    for bus, addr, name in b:
        print("  i2c-%-3d 0x%s  %s" % (bus, addr, name))
    if not b:
        print("  no sensors bound - the overlay did not take. Check config.txt")
        print("  and that the ribbon is seated the right way round.")
    print("\n=== PhotonVision ===")
    try:
        cams = asyncio.run(photonvision_cameras(args.host, args.port))
    except Exception as exc:
        print("  could not reach PhotonVision (%s)" % type(exc).__name__)
        sys.exit(1)
    for uid, c in cams.items():
        cal = ", ".join("%dx%d" % r for r in c["calibrations"]) or "NONE"
        print("  %-14s active %sx%s   calibrated: %s"
              % (c["nickname"], c["mode"][0], c["mode"][1], cal))
    print()
    ok = True
    if args.expect and len(cams) != args.expect:
        print("FAIL: expected %d camera(s) in PhotonVision, found %d"
              % (args.expect, len(cams)))
        ok = False
    if b and len(cams) != len(b):
        print("FAIL: kernel bound %d sensor(s), PhotonVision reports %d"
              % (len(b), len(cams)))
        ok = False
    uncal = [c["nickname"] for c in cams.values()
             if tuple(c["mode"]) not in c["calibrations"]]
    if uncal:
        print("NOT CALIBRATED at the active resolution: %s" % ", ".join(uncal))
        print("  A new camera arrives with no calibration. With solvePNP on,")
        print("  PhotonVision publishes NOTHING in that state - not a 2D")
        print("  fallback - so it looks like a dead camera. Calibrate it, or")
        print("  switch to a resolution that already is.")
        ok = False
    print("OK - %d camera(s), all calibrated at their active resolution" % len(cams)
          if ok else "\nnot ready yet")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--show", action="store_true", help="what is connected and configured")
    p.add_argument("--set", metavar="SENSORS",
                   help="comma-separated, in connector order: --set ov9281,ov9281")
    p.add_argument("--verify", action="store_true", help="after a reboot: did it work")
    p.add_argument("--expect", type=int, default=0, help="how many cameras --verify requires")
    p.add_argument("--host", default="127.0.0.1", help="PhotonVision host (default localhost)")
    p.add_argument("--port", type=int, default=5800)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    if a.set:
        write_config(a)
    elif a.verify:
        verify(a)
    else:
        a.host = a.host if a.show or True else None
        show(a)


if __name__ == "__main__":
    main()
