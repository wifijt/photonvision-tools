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
"""Find the jitter in a field_viewer log and say what caused it.

    python3 analyze_rig_log.py rig.jsonl

A hand-moved rig cannot actually teleport, so a large position change between
two consecutive samples is the estimator failing, not the rig moving.  This
finds those jumps, groups them into episodes, and then - the useful part -
compares the conditions during jumps against the conditions the rest of the
time, so you get a cause rather than a complaint.
"""
import argparse, json, math, statistics as st, sys
from collections import Counter


def load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: r["t"])
    return rows


def jumps(rows):
    """How far each sample sits from where its neighbours say it should be.

    A plain step between consecutive samples cannot tell jitter from movement -
    carrying the rig at 1.6 m/s produces 165 mm steps at 10 Hz and they are all
    real.  The midpoint residual  |p[i] - (p[i-1]+p[i+1])/2|  is zero for any
    constant-velocity motion, so it measures only what the rig could NOT have
    physically done.  Steady acceleration still leaks in, at a*dt^2/2, which is
    about 2 mm at a brisk 0.4 m/s^2 - far below anything of interest.
    """
    out = []
    for a, b, c in zip(rows, rows[1:], rows[2:]):
        dt1, dt2 = b["t"] - a["t"], c["t"] - b["t"]
        if not (0 < dt1 < 1.0 and 0 < dt2 < 1.0):
            continue
        f = dt1 / (dt1 + dt2)
        mid = tuple(a[k] + f * (c[k] - a[k]) for k in ("x", "y", "z"))
        d = math.dist((b["x"], b["y"], b["z"]), mid)
        ya = a["yaw"] + f * ((c["yaw"] - a["yaw"] + 180) % 360 - 180)
        dy = abs((b["yaw"] - ya + 180) % 360 - 180)
        out.append({"t": b["t"], "dt": dt1, "d": d, "dyaw": dy,
                    "vel": math.dist((a["x"], a["y"], a["z"]),
                                     (b["x"], b["y"], b["z"])) / dt1,
                    "row": b, "prev": a})
    return out


def describe(rows):
    """The conditions during a set of samples."""
    if not rows:
        return {}
    ncam = [r["n"] for r in rows]
    fixes, tags, reproj, dist = Counter(), [], [], []
    for r in rows:
        for nm, c in (r.get("cams") or {}).items():
            fixes[c["fix"]] += 1
            if c.get("tags"):
                tags.append(c["tags"])
            reproj.append(c.get("reproj") or 0.0)
            dist.append(c.get("dist") or 0.0)
    dg = [r["disagree_m"] for r in rows if r.get("disagree_m") is not None]
    return {"n": len(rows),
            "one_cam_pct": 100.0 * sum(1 for x in ncam if x < 2) / len(ncam),
            "single_pct": 100.0 * fixes["single"] / max(1, sum(fixes.values())),
            "tags": st.median(tags) if tags else 0,
            "reproj": st.median(reproj) if reproj else 0.0,
            "dist": st.median(dist) if dist else 0.0,
            "disagree_mm": 1000 * st.median(dg) if dg else None}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log")
    p.add_argument("--jump-mm", type=float, default=10.0,
                   help="non-physical deviation counted as jitter, mm (default 10)")
    p.add_argument("--top", type=int, default=8)
    a = p.parse_args()

    rows = load(a.log)
    if len(rows) < 10:
        sys.exit("only %d rows - let it run longer" % len(rows))
    js = jumps(rows)
    if not js:
        sys.exit("no usable consecutive samples")

    span = rows[-1]["t"] - rows[0]["t"]
    print("%d samples over %.0f s  (%.1f Hz)" % (len(rows), span, len(rows) / max(span, 1e-9)))
    both = sum(1 for r in rows if r["n"] >= 2)
    print("  both cameras %.0f%%   one camera %.0f%%"
          % (100.0 * both / len(rows), 100.0 * (len(rows) - both) / len(rows)))

    d = sorted(x["d"] for x in js)
    def q(f):
        return d[min(len(d) - 1, int(f * len(d)))]
    print("\nnon-physical deviation:  median %.1f mm   p95 %.1f mm   max %.1f mm"
          % (1000 * q(0.5), 1000 * q(0.95), 1000 * d[-1]))

    thr = a.jump_mm / 1000.0
    bad = [x for x in js if x["d"] > thr]
    print("  %d of %d samples exceed %.0f mm (%.1f%%)"
          % (len(bad), len(js), a.jump_mm, 100.0 * len(bad) / len(js)))

    if not bad:
        print("\nno jitter above threshold - lower --jump-mm to look closer")
        return

    # group consecutive bad steps into episodes
    eps, cur = [], [bad[0]]
    for x in bad[1:]:
        if x["t"] - cur[-1]["t"] <= 1.0:
            cur.append(x)
        else:
            eps.append(cur)
            cur = [x]
    eps.append(cur)
    eps.sort(key=lambda e: max(y["d"] for y in e), reverse=True)

    mv = sorted(x["vel"] for x in js)
    print("  (rig speed: median %.2f m/s, max %.2f m/s - real motion is excluded"
          " by construction)" % (mv[len(mv) // 2], mv[-1]))
    print("\n%d episode(s).  worst first:\n" % len(eps))
    t0 = rows[0]["t"]
    print("%8s %7s %9s %8s %6s %7s %7s %8s" %
          ("at", "for", "worst", "yaw", "cams", "tags", "reproj", "differ"))
    for e in eps[:a.top]:
        sub = [x["row"] for x in e]
        c = describe(sub)
        worst = max(e, key=lambda y: y["d"])
        print("%7.1fs %6.1fs %8.0fmm %7.1f° %5.1f %7.0f %7.2f %8s"
              % (e[0]["t"] - t0, e[-1]["t"] - e[0]["t"] + 0.1, 1000 * worst["d"],
                 worst["dyaw"], 2 - c["one_cam_pct"] / 100.0, c["tags"],
                 c["reproj"],
                 ("%.1fmm" % c["disagree_mm"]) if c["disagree_mm"] is not None else "-"))

    print("\n--- what is different during jitter ---")
    good = [x["row"] for x in js if x["d"] <= thr]
    B, G = describe([x["row"] for x in bad]), describe(good)
    rowsfmt = [("samples with one camera only", "one_cam_pct", "%.0f%%"),
               ("single-tag fixes", "single_pct", "%.0f%%"),
               ("median tags per camera", "tags", "%.0f"),
               ("median reprojection error", "reproj", "%.2f px"),
               ("median tag distance", "dist", "%.2f m"),
               ("camera disagreement", "disagree_mm", "%.1f mm")]
    print("%-30s %12s %12s" % ("", "jitter", "calm"))
    for label, key, fmt in rowsfmt:
        b, g = B.get(key), G.get(key)
        if b is None or g is None:
            continue
        print("%-30s %12s %12s" % (label, fmt % b, fmt % g))

    print("\n--- read ---")
    notes = []
    if B["one_cam_pct"] > G["one_cam_pct"] + 20:
        notes.append("Jitter happens where a camera drops out. The fused pose falls back to\n"
                     "  one camera, which cannot constrain its own viewing axis. Aim the\n"
                     "  cameras so their coverage overlaps the path you drive.")
    if B["single_pct"] > G["single_pct"] + 15:
        notes.append("Jitter tracks single-tag fixes. Single-tag PnP is ambiguous - it can\n"
                     "  flip between two valid solutions frame to frame. More tags in view is\n"
                     "  the only real fix; raising the reject threshold only hides it.")
    if B["reproj"] > G["reproj"] * 1.5:
        notes.append("Reprojection error is much higher during jitter, so the tags themselves\n"
                     "  disagreed. Suspect motion blur (shorten exposure) or a tag seen at a\n"
                     "  steep angle near the frame edge, where distortion is worst.")
    if B["dist"] > G["dist"] * 1.3:
        notes.append("Jitter is at longer range. Pose error grows with distance squared, so\n"
                     "  this may just be the geometry - lower decimate to keep tags detectable\n"
                     "  further out, at a framerate cost.")
    if B.get("disagree_mm") and G.get("disagree_mm") and B["disagree_mm"] > 20 \
            and B["disagree_mm"] > G["disagree_mm"] * 2:
        notes.append("The two cameras disagree badly during jitter. That is NOT sensor noise -\n"
                     "  a wrong mount transform or a wrong tag position would do this. Re-run\n"
                     "  the mount lock while stationary, and check the tag layout.")
    if not notes:
        notes.append("Nothing separates the jitter from the calm samples on these measures.\n"
                     "  Try --jump-mm lower, or log for longer to catch a clearer episode.")
    for i, n in enumerate(notes, 1):
        print("%d. %s" % (i, n))


if __name__ == "__main__":
    main()
