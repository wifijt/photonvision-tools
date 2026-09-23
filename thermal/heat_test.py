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
"""Does this case cook the Pi?

    python3 heat_test.py --host photonvision.local --minutes 30 --ambient 22

Samples PhotonVision's own metrics over the websocket - no SSH, no credentials.
`cpuThr` is PhotonVision's decode of `vcgencmd get_throttled`, so throttling is
detected directly rather than inferred from a framerate dip.

WHAT IT IS ACTUALLY TESTING

Not "how hot does it get" - that number is worthless on its own, because it is a
property of the room as much as the case. What matters is the RISE above ambient,
which is a property of the case and the load. A case that settles 48 C above
ambient is fine in a 22 C workshop and throttling in a 34 C competition venue,
and the workshop test alone cannot tell you which you have.

So this reports the rise, and works backwards to the ambient temperature at which
the Pi would begin to throttle. That is the number to carry to an event.

A Pi 5 soft-throttles at 80 C (it starts pulling clocks back) and hard-throttles
at 85 C. Neither is damage - the chip is protecting itself - but both cost you
framerate exactly when the venue is hottest and the match matters.

RUN IT UNDER REAL LOAD. An idle Pi in a sealed case proves nothing. Both cameras
should be running their normal pipelines for the whole test.
"""
import argparse, asyncio, json, math, statistics as st, sys, time

try:
    import msgpack, websockets
except ImportError:
    sys.exit("need: pip install msgpack websockets")

SOFT_LIMIT = 80.0     # Pi 5 begins reducing clocks
HARD_LIMIT = 85.0     # hard throttle


class Run:
    def __init__(self):
        self.s = []              # (t, temp, util, thr, fps_total, lat_mean)
        self.throttle_events = []
        self.t0 = time.time()

    def add(self, temp, util, thr, fps, lat):
        t = time.time() - self.t0
        self.s.append((t, temp, util, thr, fps, lat))
        if thr and thr.lower() not in ("none", "", "null"):
            self.throttle_events.append((t, temp, thr))

    def window(self, secs, end=None):
        end = self.s[-1][0] if end is None else end
        return [r for r in self.s if end - secs <= r[0] <= end]

    def slope(self, secs=300):
        w = self.window(secs)
        if len(w) < 6:
            return None
        n = len(w)
        mx = sum(r[0] for r in w) / n
        my = sum(r[1] for r in w) / n
        den = sum((r[0] - mx) ** 2 for r in w)
        if den == 0:
            return None
        return 60.0 * sum((r[0] - mx) * (r[1] - my) for r in w) / den   # C per minute


async def sample(host, port, run, minutes, ambient, log, quiet):
    uri = "ws://%s:%d/websocket_data" % (host, port)
    deadline = time.time() + minutes * 60
    last_print = 0.0
    fps, lat = {}, {}
    met = None
    while time.time() < deadline:
        try:
            # ping_interval=None deliberately: the keepalive has been observed to
            # knock a camera offline mid-session, which would silently change the
            # very load this test is measuring.
            async with websockets.connect(uri, open_timeout=15, ping_interval=None,
                                          max_size=80_000_000) as ws:
                while time.time() < deadline:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    if not isinstance(raw, bytes):
                        continue
                    m = msgpack.unpackb(raw, raw=False)
                    if not isinstance(m, dict):
                        continue
                    if "metrics" in m:
                        met = m["metrics"]
                    upr = m.get("updatePipelineResult")
                    if isinstance(upr, dict):
                        for uid, v in upr.items():
                            if isinstance(v, dict):
                                if v.get("fps") is not None:
                                    fps[uid] = v["fps"]
                                if v.get("latency") is not None:
                                    lat[uid] = v["latency"]
                    now = time.time() - run.t0
                    if met and now - last_print >= 1.0:
                        last_print = now
                        temp = float(met.get("cpuTemp") or 0)
                        util = float(met.get("cpuUtil") or 0)
                        thr = str(met.get("cpuThr", "None"))
                        ftot = sum(fps.values()) if fps else 0.0
                        lmean = (sum(lat.values()) / len(lat)) if lat else 0.0
                        run.add(temp, util, thr, ftot, lmean)
                        if log:
                            log.write(json.dumps({"t": round(now, 1), "temp": temp,
                                                  "util": round(util, 1), "thr": thr,
                                                  "fps": round(ftot, 1),
                                                  "latency": round(lmean, 1),
                                                  "fps_each": {k[:8]: round(v, 1)
                                                               for k, v in fps.items()}}) + "\n")
                        if not quiet and int(now) % 30 == 0:
                            sl = run.slope()
                            print("  %5.1f min   %5.1f C   cpu %3.0f%%   %5.1f fps   "
                                  "%6.1f ms   %s%s"
                                  % (now / 60.0, temp, util, ftot, lmean,
                                     "THROTTLED: " + thr if thr.lower() != "none" else "ok",
                                     "   %+.2f C/min" % sl if sl is not None else ""))
        except asyncio.TimeoutError:
            print("  (no data for 30 s - retrying)")
        except Exception as exc:
            if time.time() >= deadline:
                break
            print("  (connection lost: %s - reconnecting)" % type(exc).__name__)
            await asyncio.sleep(3)


def report(run, ambient):
    if len(run.s) < 20:
        sys.exit("\nnot enough samples (%d) - run longer" % len(run.s))
    temps = [r[1] for r in run.s]
    dur = run.s[-1][0] / 60.0
    start = st.median([r[1] for r in run.window(60, run.s[0][0] + 60)])
    tail = run.window(300)
    steady = st.median([r[1] for r in tail])
    peak = max(temps)
    slope = run.slope()

    print("\n" + "=" * 62)
    print("HEAT TEST  -  %.1f minutes, %d samples" % (dur, len(run.s)))
    print("=" * 62)
    print("  start (first minute)   %5.1f C" % start)
    print("  settled (last 5 min)   %5.1f C" % steady)
    print("  peak                   %5.1f C" % peak)
    if slope is not None:
        print("  still rising at        %+.2f C/min%s"
              % (slope, "   <-- NOT settled yet" if slope > 0.15 else "   (settled)"))

    util = st.median([r[2] for r in run.s])
    fps = [r[4] for r in run.s if r[4] > 0]
    print("\n  cpu load (median)      %5.1f %%" % util)
    if fps:
        early = [r[4] for r in run.window(120, run.s[0][0] + 180) if r[4] > 0]
        late = [r[4] for r in tail if r[4] > 0]
        if early and late:
            e, l = st.median(early), st.median(late)
            print("  fps  early %.1f  ->  late %.1f   (%+.1f%%)"
                  % (e, l, 100.0 * (l - e) / e if e else 0))
    lats = [r[5] for r in run.s if r[5] > 0]
    if lats:
        print("  latency (median)       %5.1f ms" % st.median(lats))

    print("\n  THROTTLING: ", end="")
    if run.throttle_events:
        first = run.throttle_events[0]
        print("YES - %d samples, first at %.1f min / %.1f C"
              % (len(run.throttle_events), first[0] / 60.0, first[1]))
        kinds = sorted({e[2] for e in run.throttle_events})
        for k in kinds:
            print("     reported as: %s" % k)
    else:
        print("none detected at any point")

    print("\n  headroom to soft limit (%.0f C)  %5.1f C" % (SOFT_LIMIT, SOFT_LIMIT - steady))
    print("  headroom to hard limit (%.0f C)  %5.1f C" % (HARD_LIMIT, HARD_LIMIT - steady))

    if ambient is not None:
        rise = steady - ambient
        print("\n  --- the number that travels ---")
        print("  ambient you gave       %5.1f C" % ambient)
        print("  RISE above ambient     %5.1f C   <-- this is the case, not the room"
              % rise)
        print("  would soft-throttle at an ambient above  %5.1f C (%.0f F)"
              % (SOFT_LIMIT - rise, (SOFT_LIMIT - rise) * 9 / 5 + 32))
        print("  would hard-throttle at an ambient above  %5.1f C (%.0f F)"
              % (HARD_LIMIT - rise, (HARD_LIMIT - rise) * 9 / 5 + 32))
        margin = (SOFT_LIMIT - rise) - ambient
        print("\n  VERDICT: ", end="")
        if run.throttle_events:
            print("FAILS - it is throttling already, in this room.")
        elif margin < 5:
            print("MARGINAL - only %.1f C of ambient rise before it throttles." % margin)
            print("           A warm venue will eat that. Improve airflow.")
        elif margin < 12:
            print("OK here, TIGHT for a hot venue - %.1f C of ambient margin." % margin)
        else:
            print("PASSES with %.1f C of ambient margin." % margin)
    else:
        print("\n  (pass --ambient with the room temperature to get the number that")
        print("   travels: the ambient at which this case would start throttling)")

    if slope is not None and slope > 0.15:
        print("\n  CAUTION: still climbing at %+.2f C/min when the test ended, so the"
              % slope)
        print("  settled figure above is a LOWER BOUND. Run longer for the real one.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local")
    p.add_argument("--port", type=int, default=5800)
    p.add_argument("--minutes", type=float, default=30.0)
    p.add_argument("--ambient", type=float, default=None,
                   help="room temperature in C - lets the test report the ambient "
                        "at which this case would begin to throttle")
    p.add_argument("--log", default=None, metavar="FILE")
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()

    run = Run()
    log = open(a.log, "a", buffering=1) if a.log else None
    print("heat test: %s for %.0f minutes%s"
          % (a.host, a.minutes, "  (logging to %s)" % a.log if a.log else ""))
    print("soft limit %.0f C, hard limit %.0f C\n" % (SOFT_LIMIT, HARD_LIMIT))
    try:
        asyncio.run(sample(a.host, a.port, run, a.minutes, a.ambient, log, a.quiet))
    except KeyboardInterrupt:
        print("\ninterrupted - reporting on what was collected")
    finally:
        if log:
            log.close()
    report(run, a.ambient)


if __name__ == "__main__":
    main()
