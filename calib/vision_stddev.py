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
"""Measure the vision standard deviations instead of guessing them.

WPILib's pose estimator takes `visionMeasurementStdDevs` and gives you no way to
find out what yours are, so every team picks a number off a forum post.  This
measures them, binned by RANGE and TAG COUNT, because that is how the error
actually behaves - and emits a paste-ready Java constant.

    python3 vision_stddev.py --host photonvision.local

Hold the camera still.  Move it.  Hold it still somewhere else.  The tool finds
the still periods by itself - each one is a "station" - and reports the scatter
at each.  Two minutes over four or five stations at different distances is
enough.  Nothing is written anywhere and nothing on the camera is changed.

WHAT THIS MEASURES, AND WHAT IT DOES NOT
    It measures SCATTER: how much the estimate moves while the camera does not.
    That is a LOWER BOUND on the uncertainty you should hand the pose estimator,
    and usually a long way below it, because a pose estimate at one viewpoint
    also carries a BIAS that no amount of averaging there will reveal - roughly
    +/-10 mm on this rig, measured separately in mount/calibrate_mount.py.

    So the tool also reports, whenever it has two or more stations, how much the
    stations DISAGREE about where the same tag is.  Scatter cannot see that and
    it is the honest half of the error.  Feed the pose estimator something at
    least as large as the disagreement, not the scatter.

    And it is measured STANDING STILL, which is not when you use it.  A moving
    camera has motion blur and a stale timestamp on top of everything here.
"""
import argparse, asyncio, json, math, statistics as st, sys, time
from collections import defaultdict

try:
    import msgpack, websockets
except ImportError:
    sys.exit("need: pip install msgpack websockets")

MOVE_MM      = 25.0   # station ends when the estimate travels further than this
STILL_S      = 1.0    # ... and restarts after this long back under it
MIN_SAMPLES  = 40     # a station with fewer is not a measurement


def quat_yaw(q):
    """Yaw about Z from a quaternion, in radians."""
    w, x, y, z = q["qw"], q["qx"], q["qy"], q["qz"]
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def circ_std(angles):
    """Std dev of angles, done on the circle so a wrap does not invent spread."""
    if len(angles) < 2:
        return 0.0
    s = sum(math.sin(a) for a in angles) / len(angles)
    c = sum(math.cos(a) for a in angles) / len(angles)
    R = math.hypot(s, c)
    if R >= 1.0:
        return 0.0
    return math.sqrt(-2.0 * math.log(R))


class Station:
    """One period where the camera did not move."""

    def __init__(self, t0):
        self.t0 = t0
        self.x, self.y, self.z, self.yaw = [], [], [], []
        self.rng, self.ntags, self.reproj, self.amb = [], [], [], []
        self.tags = defaultdict(list)          # tag id -> [(x,y,z) in field frame]

    def add(self, p, yaw, rng, n, reproj, amb, tagpos):
        self.x.append(p[0]); self.y.append(p[1]); self.z.append(p[2])
        self.yaw.append(yaw); self.rng.append(rng); self.ntags.append(n)
        if reproj is not None:
            self.reproj.append(reproj)
        if amb is not None and amb >= 0:
            self.amb.append(amb)
        for tid, xyz in tagpos.items():
            self.tags[tid].append(xyz)

    def n(self):
        return len(self.x)

    def travel(self):
        if len(self.x) < 2:
            return 0.0
        return max(math.dist((self.x[-1], self.y[-1], self.z[-1]),
                             (self.x[i], self.y[i], self.z[i]))
                   for i in range(max(0, len(self.x) - 25), len(self.x)))

    def summary(self):
        return {
            "n": self.n(),
            "sx": st.stdev(self.x), "sy": st.stdev(self.y), "sz": st.stdev(self.z),
            "syaw": circ_std(self.yaw),
            "range": st.median(self.rng),
            "ntags": st.median(self.ntags),
            "reproj": st.median(self.reproj) if self.reproj else None,
            "amb": st.median(self.amb) if self.amb else None,
            "tags": {t: (sum(c[0] for c in v) / len(v),
                         sum(c[1] for c in v) / len(v),
                         sum(c[2] for c in v) / len(v)) for t, v in self.tags.items()},
        }


def extract(res):
    """Pull one usable multi-tag sample out of a websocket pipeline result."""
    mt = res.get("multitagResult")
    if not mt or not mt.get("bestTransform"):
        return None
    bt = mt["bestTransform"]
    pos = (bt["x"], bt["y"], bt["z"])
    yaw = quat_yaw(bt)
    used = mt.get("fiducialIDsUsed") or []
    tgts = res.get("targets") or []
    rngs, amb, tagpos = [], [], {}
    for t in tgts:
        tp = t.get("pose")
        if not tp:
            continue
        d = math.sqrt(tp["x"] ** 2 + tp["y"] ** 2 + tp["z"] ** 2)
        rngs.append(d)
        # crude field-frame tag position: camera pose composed with camera->tag.
        # Only used to compare stations against each other, so a common frame
        # error is harmless - it cancels in the comparison.
        tagpos[t["fiducialId"]] = (pos[0] + tp["x"], pos[1] + tp["y"], pos[2] + tp["z"])
        if t.get("ambiguity", -1) >= 0:
            amb.append(t["ambiguity"])
    if not rngs:
        return None
    return {"pos": pos, "yaw": yaw, "range": sum(rngs) / len(rngs),
            "ntags": len(used) if used else len(tgts),
            "reproj": mt.get("bestReprojectionError"),
            "amb": (sum(amb) / len(amb)) if amb else None,
            "tagpos": tagpos}


async def collect(host, port, seconds, only):
    uri = "ws://%s:%d/websocket_data" % (host, port)
    try:
        ws = await websockets.connect(uri, open_timeout=10, max_size=80_000_000)
    except Exception as exc:
        sys.exit("could not reach PhotonVision at %s (%s)" % (uri, type(exc).__name__))

    names, stations, cur, last_move = {}, defaultdict(list), {}, {}
    seen = defaultdict(set)
    t_end = time.time() + seconds
    print("collecting for %d s - hold still, move, hold still ...\n" % seconds)
    async with ws:
        while time.time() < t_end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if not isinstance(raw, bytes):
                continue
            msg = msgpack.unpackb(raw, raw=False)
            if not isinstance(msg, dict):
                continue
            for c in msg.get("cameraSettings", []) or []:
                names[c["uniqueName"]] = c.get("nickname", c["uniqueName"])
            upr = msg.get("updatePipelineResult")
            if not upr:
                continue
            now = time.time()
            for uid, res in upr.items():
                nick = names.get(uid, uid[:8])
                if only and nick.lower() not in only:
                    continue
                sid = res.get("sequenceID")
                if sid in seen[uid]:
                    continue
                seen[uid].add(sid)
                if len(seen[uid]) > 6000:
                    seen[uid] = set(list(seen[uid])[-2000:])
                s = extract(res)
                if s is None:
                    continue
                stn = cur.get(uid)
                if stn is None:
                    stn = cur[uid] = Station(now)
                stn.add(s["pos"], s["yaw"], s["range"], s["ntags"],
                        s["reproj"], s["amb"], s["tagpos"])
                if stn.travel() * 1000.0 > MOVE_MM:
                    if stn.n() >= MIN_SAMPLES:
                        stations[nick].append(stn.summary())
                        print("  %-14s station %d closed: %d samples, %.2f m"
                              % (nick, len(stations[nick]), stn.n(),
                                 st.median(stn.rng)))
                    cur[uid] = Station(now)
                    last_move[uid] = now
        for uid, stn in cur.items():
            if stn.n() >= MIN_SAMPLES:
                nick = names.get(uid, uid[:8])
                stations[nick].append(stn.summary())
                print("  %-14s station %d closed: %d samples, %.2f m"
                      % (nick, len(stations[nick]), stn.n(), stn.summary()["range"]))
    return stations


def bias_between_stations(sts):
    """How far the stations disagree about where the same tag is.

    Scatter cannot see this.  It is viewpoint-dependent bias, and it is the part
    that does not average away.
    """
    per_tag = defaultdict(list)
    for s in sts:
        for t, xyz in s["tags"].items():
            per_tag[t].append(xyz)
    spreads = []
    for t, pts in per_tag.items():
        if len(pts) < 2:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        spreads.append(max(math.dist(p, (cx, cy, cz)) for p in pts))
    return (max(spreads), len(spreads)) if spreads else (None, 0)


def report(stations, args):
    for nick, sts in sorted(stations.items()):
        print("\n" + "=" * 72)
        print("%s   %d station(s)" % (nick, len(sts)))
        print("=" * 72)
        if not sts:
            print("  no usable stations - was a multi-tag solve ever available?")
            continue
        print("  %-7s %-6s %-6s %8s %8s %8s %9s %8s %7s"
              % ("range", "tags", "n", "sx mm", "sy mm", "sz mm", "syaw deg",
                 "reproj", "amb"))
        for s in sorted(sts, key=lambda a: a["range"]):
            print("  %-7.2f %-6.1f %-6d %8.2f %8.2f %8.2f %9.3f %8s %7s"
                  % (s["range"], s["ntags"], s["n"], s["sx"] * 1000,
                     s["sy"] * 1000, s["sz"] * 1000, math.degrees(s["syaw"]),
                     ("%.3f" % s["reproj"]) if s["reproj"] is not None else "-",
                     ("%.3f" % s["amb"]) if s["amb"] is not None else "-"))

        # fit sigma = base * d^2 / n, the shape AdvantageKit uses
        pts = [(s["range"], max(s["ntags"], 1.0),
                math.hypot(s["sx"], s["sy"]), s["syaw"]) for s in sts]
        lin = [p[2] * p[1] / (p[0] ** 2) for p in pts]
        ang = [p[3] * p[1] / (p[0] ** 2) for p in pts]
        base_lin, base_ang = st.median(lin), st.median(ang)

        bias, ntag = bias_between_stations(sts)
        print()
        print("  SCATTER-DERIVED baseline at 1 m, 1 tag:")
        print("      linear  %.4f m      angular %.4f rad" % (base_lin, base_ang))
        print("      (AdvantageKit's published starting point: 0.02 m, 0.06 rad)")

        if bias is None:
            # One viewpoint can only show scatter, and scatter is the small half.
            # Printing a constant here would be handing over a number that is
            # confidently too small, and a printed constant gets pasted.
            print()
            print("  NO CONSTANT EMITTED.")
            print("      Only one station shares tags, so the bias - the half of")
            print("      the error that does NOT average away - was not measured.")
            print("      Scatter alone runs far below the real uncertainty: this")
            print("      run would have suggested %.4f m against AdvantageKit's"
                  % base_lin)
            print("      published 0.02 m. Move the camera to two or more places")
            print("      that see a shared tag and run again.")
            continue

        print("  BETWEEN-STATION DISAGREEMENT on %d shared tag(s): %.1f mm"
              % (ntag, bias * 1000))
        med_scatter = st.median([p[2] for p in pts])
        if bias > med_scatter * 1.5:
            print("      Larger than the scatter, as expected. The scatter is NOT")
            print("      your uncertainty - it is only the part one viewpoint can")
            print("      show you.")
        rec = max(base_lin, bias)
        print()
        print("  // measured %s, %s, STATIONARY, %d stations"
              % (time.strftime("%Y-%m-%d"), nick, len(sts)))
        print("  // linear scales as distance^2 / tagCount")
        print("  // floor set by between-station disagreement, not by scatter")
        print("  public static final double kLinearStdDevBaseline  = %.4f;  // m" % rec)
        print("  public static final double kAngularStdDevBaseline = %.4f;  // rad"
              % base_ang)

    print("\n" + "-" * 72)
    print("These are STATIONARY numbers. A moving camera adds motion blur and a")
    print("stale timestamp on top. Treat them as a floor, and remember the field")
    print("itself is built to +/-0.5 in - it will usually dominate all of this.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local")
    p.add_argument("--port", type=int, default=5800)
    p.add_argument("--seconds", type=int, default=120)
    p.add_argument("--cameras", default=None, help="comma-separated nicknames")
    p.add_argument("--json", metavar="FILE", help="also write the raw summaries")
    a = p.parse_args()
    only = {c.strip().lower() for c in a.cameras.split(",")} if a.cameras else None
    stations = asyncio.run(collect(a.host, a.port, a.seconds, only))
    if not stations:
        sys.exit("\nno multi-tag samples at all - check that two or more tags are "
                 "in view and that solvePNP is on")
    report(stations, a)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({k: v for k, v in stations.items()}, f, indent=2, default=str)
        print("\nwrote %s" % a.json)


if __name__ == "__main__":
    main()
