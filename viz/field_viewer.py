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
"""Live top-down view of where your cameras are on the field.

Reads PhotonVision's NetworkTables output, works out where each camera is, and
draws it on a field plan in a browser.  Move the rig, watch the dot move.

    python3 field_viewer.py --host 192.168.1.202
    open http://localhost:8077

It needs no configuration and no layout file.  The tag layout is RECONSTRUCTED
from the live stream - every multi-tag frame gives a camera pose and a set of
camera-to-tag transforms, and composing them puts each tag in the field frame.
So the picture always reflects the layout PhotonVision is actually using, not a
copy that may have drifted out of date.

Once a tag's field pose is known, a camera that can only see ONE tag still gets
a position (drawn hollow, because single-tag pose is ambiguous - see the README).
That matters here: while you walk the rig around, most frames see one tag.

FIELD OF VIEW is computed from the calibration INCLUDING distortion.  The usual
2*atan(w/2/fx) is a pinhole formula and understates a wide lens badly - measured
on an OV9281 it gives 60.1 deg where the true figure is 69.4.  Cones drawn from
the pinhole number are too narrow and will tell you that you have a blind spot
you do not have.
"""
import argparse, json, math, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np, ntcore
    from photonlibpy.packet import Packet
    from photonlibpy.targeting.photonPipelineResult import PhotonPipelineResult
except ImportError as e:
    sys.exit("need: pip install numpy robotpy-photonlibpy  (%s)" % e)

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, ".layout_cache.json")
STATE = {"cams": {}, "tags": {}, "fov": {}, "field": {}, "t": 0.0, "nt": False,
         "rig": None, "extrinsic": None, "hist": []}
STATS = {"rows": 0, "both": 0, "one": 0, "none": 0, "disagree": [], "t0": None,
         "worst": None, "path": None}
LOCK = threading.Lock()


def q2R(w, x, y, z):
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def T(tr):
    q = tr.rotation().getQuaternion()
    M = np.eye(4)
    M[:3, :3] = q2R(q.W(), q.X(), q.Y(), q.Z())
    M[:3, 3] = [tr.X(), tr.Y(), tr.Z()]
    return M


def yaw_of(M):
    return math.degrees(math.atan2(M[1, 0], M[0, 0]))


def fetch_fov(host, timeout=15):
    """True horizontal FOV per camera, from the calibration, distortion included."""
    out = {}
    try:
        import asyncio, msgpack, websockets

        async def go():
            uri = "ws://%s:5800/websocket_data" % host
            async with websockets.connect(uri, open_timeout=10, ping_interval=None,
                                          max_size=80_000_000) as ws:
                cams, t0 = {}, time.time()
                while time.time() - t0 < timeout and not cams:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                    if not isinstance(raw, bytes):
                        continue
                    m = msgpack.unpackb(raw, raw=False)
                    if isinstance(m, dict):
                        for c in m.get("cameraSettings", []) or []:
                            cams[c["uniqueName"]] = c
                return cams

        cams = asyncio.new_event_loop().run_until_complete(go())
    except Exception as exc:
        print("  could not read calibration (%s) - assuming 70 deg" % type(exc).__name__)
        return out

    try:
        import cv2
    except ImportError:
        cv2 = None
    for _u, c in cams.items():
        nick = c.get("nickname", _u)
        best = None
        for cal in c.get("calibrations", []) or []:
            res = cal.get("resolution", {})
            w, h = res.get("width"), res.get("height")
            K = (cal.get("cameraIntrinsics") or {}).get("data")
            D = (cal.get("distCoeffs") or {}).get("data")
            if not (w and K):
                continue
            if best is None or w > best[0]:
                best = (w, h, list(K), list(D or []))
        if not best:
            continue
        w, h, K, D = best
        fx = K[0]
        if cv2 and D:
            Km = np.array(K, dtype=float).reshape(3, 3)
            Dm = np.array(D, dtype=float).reshape(1, -1)
            pts = np.array([[[0.0, h / 2.0]], [[w - 1.0, h / 2.0]]], dtype=np.float64)
            u = cv2.undistortPoints(pts, Km, Dm).reshape(-1, 2)
            left, right = math.degrees(math.atan(u[0, 0])), math.degrees(math.atan(u[1, 0]))
        else:
            half = math.degrees(math.atan(w / 2.0 / fx))
            left, right = -half, half
        out[nick] = {"left": left, "right": right, "pinhole": 2 * math.degrees(math.atan(w / 2.0 / fx))}
        print("  %-14s FOV %.2f deg  (edges %+.2f / %+.2f)  pinhole would say %.2f"
              % (nick, right - left, left, right, out[nick]["pinhole"]))
    return out


def load_cache():
    try:
        with open(CACHE) as f:
            d = json.load(f)
        return {int(k): v for k, v in d.items()}
    except Exception:
        return {}


def save_cache(tags):
    """Merge into the cache, never replace it.

    A camera that cannot currently see a tag is not evidence the tag is gone.
    Replacing the file with only what is visible right now quietly deletes tags
    the other camera depends on - and then that camera cannot localise at all.
    """
    try:
        merged = {}
        try:
            with open(CACHE) as f:
                merged = json.load(f)
        except Exception:
            pass
        merged.update({str(k): v for k, v in tags.items()})
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(merged, f, indent=1)
        os.replace(tmp, CACHE)
    except Exception:
        pass



EXTR = os.path.join(HERE, ".rig_extrinsic.json")


def rot_axis(ax, th):
    n = np.linalg.norm(ax)
    if n < 1e-12:
        return np.eye(3)
    ax = ax / n
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * (K @ K)


def log_angle(R):
    return math.acos(max(-1.0, min(1.0, (np.trace(R) - 1.0) / 2.0)))


def half_rot(R):
    """The rotation half way from identity to R, along the geodesic."""
    a = log_angle(R)
    if a < 1e-9:
        return np.eye(3)
    ax = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(a))
    return rot_axis(ax, a / 2.0)


def so3_mean(Rs, ws):
    M = np.zeros((3, 3))
    for w, R in zip(ws, Rs):
        M += w * R
    U, _, Vt = np.linalg.svd(M)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    return R


def orthonormal(M):
    out = M.copy()
    U, _, Vt = np.linalg.svd(out[:3, :3])
    out[:3, :3] = U @ Vt
    return out


class Rig:
    """The two cameras are bolted together, so they do not get to disagree about
    where they are.  This learns the fixed camera-to-camera transform while both
    have a good multi-tag fix, then uses it to force both into ONE rig pose.

    Why that is better than averaging two free poses: a camera seeing tags in a
    poor geometry has a pose that slides along its viewing axis.  Pinned to the
    rig, the other camera's tags - viewed from a completely different angle -
    constrain exactly the direction the first one is weak in.
    """

    WINDOW = 500

    def __init__(self, lock=False):
        self.X = None          # pose of cam B in cam A's frame
        self.n = 0
        self.samples = []      # recent (t, R) - bounded, so it can recover
        self.names = None
        self.spread = None
        self.locked = False
        self.hold = lock
        self.recent = []
        self._load()

    def _load(self):
        try:
            with open(EXTR) as f:
                d = json.load(f)
            self.X = np.array(d["X"], dtype=float)
            self.names = tuple(d["names"])
            self.n = int(d.get("n", 0))
            self.spread = d.get("spread")
            self.locked = True
            print("  loaded rig extrinsic %s -> %s from cache" % self.names)
        except Exception:
            pass

    def save(self):
        try:
            with open(EXTR, "w") as f:
                json.dump({"X": self.X.tolist(), "names": list(self.names),
                           "n": self.n, "spread": self.spread}, f, indent=1)
        except Exception:
            pass

    def observe(self, na, Ma, nb, Mb):
        if self.hold and self.locked:
            return
        if self.names is None:
            self.names = (na, nb)
        if self.names != (na, nb):
            return
        Xi = orthonormal(np.linalg.inv(Ma) @ Mb)
        self.samples.append((Xi[:3, 3].copy(), Xi[:3, :3].copy()))
        if len(self.samples) > self.WINDOW:
            del self.samples[:-self.WINDOW]
        self.n += 1
        ts = np.array([t for t, _ in self.samples])
        X = np.eye(4)
        X[:3, 3] = ts.mean(axis=0)
        X[:3, :3] = so3_mean([R for _, R in self.samples],
                             [1.0 / len(self.samples)] * len(self.samples))
        self.X = X
        if len(self.samples) >= 100:
            self.spread = float(np.linalg.norm(ts.std(axis=0)))
            if self.spread < 0.003:
                if not self.locked:
                    print("  mount locked: %s -> %s, %.1f mm apart, %.2f deg yaw"
                          % (self.names[0], self.names[1],
                             1000 * np.linalg.norm(self.X[:3, 3]),
                             math.degrees(math.atan2(self.X[1, 0], self.X[0, 0]))))
                self.locked = True
                self.save()

    def seats(self):
        """Pose of the rig origin expressed in each camera's own frame.

        So the rig's field pose is  M_camera @ seat  - NOT  M_camera @ inv(seat).
        Getting that backwards rotates each camera's answer by the half-angle in
        opposite directions, which showed up as the two cameras disagreeing by
        twice the mount angle.
        """
        if self.X is None:
            return None
        H = np.eye(4)
        H[:3, :3] = half_rot(self.X[:3, :3])
        H[:3, 3] = self.X[:3, 3] / 2.0
        return {self.names[0]: H, self.names[1]: orthonormal(np.linalg.inv(self.X) @ H)}


def sigma(obs, speed):
    """Standard deviation to expect from one camera's pose, in metres.

    Distance squared over tag count is the usual WPILib shape.  Reprojection
    error is folded in because it is the one number that actually reports how
    well the tags agreed with each other on THIS frame.  A single-tag fix is
    penalised hard - it is ambiguous, not merely noisy.
    """
    d = max(0.4, obs["dist"])
    n = max(1, obs["ntags"])
    s = 0.008 * d * d / n
    s *= 1.0 + max(0.0, obs["reproj"] - 0.6)
    if obs["fix"] == "single":
        s *= 6.0
        s *= 1.0 + 12.0 * max(0.0, obs.get("amb") or 0.0)
    s = math.hypot(s, speed * obs["age"])       # it moved while we waited
    return max(s, 0.0015)


def fuse(rig, last, speed):
    seats = rig.seats() or {}
    est, obs_used = [], []
    for nm, o in last.items():
        if o.get("M") is None or o["fix"] == "none" or o["age"] > 0.35:
            continue
        sp = speed.get(nm, 0.0) if isinstance(speed, dict) else speed
        seat = seats.get(nm)
        M = orthonormal(o["M"] if seat is None else o["M"] @ seat)
        est.append((nm, M, sigma(o, sp), o["fix"]))
        obs_used.append(nm)
    if not est:
        return None
    if not seats:
        # with no mount, averaging two cameras that sit apart is meaningless -
        # report the better one on its own and say so
        est = [min(est, key=lambda e: e[2])]
        obs_used = [est[0][0]]

    dropped = []
    # A single-tag pose is AMBIGUOUS, not merely noisy: PnP has two valid
    # solutions for a planar target and it can sit on the wrong one for a whole
    # second at a time.  Measured here: 118 mm of fused jitter while the two
    # cameras disagreed by 362 mm.  Down-weighting cannot fix a wrong answer, so
    # if any camera has a real multi-tag fix, the single-tag ones are not used.
    if len(est) > 1 and any(e[3] == "multi" for e in est):
        # Down-weight rather than drop.  Dropping makes the fused pose switch
        # between "two cameras" and "one camera", and that switch is itself a
        # step - measured, it turned 0.4% of samples into 7.9%.  At 20x sigma the
        # weight ratio is 400:1, so a flipped single-tag pose moves the answer by
        # well under a millimetre while the estimate stays continuous.
        out = []
        for e in est:
            if e[3] == "multi":
                out.append(e)
            else:
                out.append((e[0], e[1], e[2] * 20.0, e[3]))
                dropped.append((e[0], "single-tag, weight cut 400x"))
        est = out

    # Even between two multi-tag fixes, a gross disagreement is one of them being
    # wrong - not both being a bit noisy.  Averaging splits the difference and
    # puts the answer somewhere neither camera claims.  Keep the better one.
    if len(est) == 2:
        sep = float(np.linalg.norm(est[0][1][:3, 3] - est[1][1][:3, 3]))
        gate = max(0.050, 4.0 * math.hypot(est[0][2], est[1][2]))
        if sep > gate:
            worse = max(est, key=lambda e: e[2])
            dropped.append((worse[0], "disagreed by %.0f mm, gate %.0f mm"
                            % (1000 * sep, 1000 * gate)))
            est = [e for e in est if e[0] != worse[0]]

    obs_used = [e[0] for e in est]
    ws = np.array([1.0 / (s * s) for _, _, s, _ in est])
    ws /= ws.sum()
    t = sum(w * M[:3, 3] for w, (_, M, _, _) in zip(ws, est))
    R = so3_mean([M[:3, :3] for _, M, _, _ in est], list(ws))
    F = np.eye(4)
    F[:3, :3] = R
    F[:3, 3] = t
    out = {"mount": bool(seats), "x": float(t[0]), "y": float(t[1]), "z": float(t[2]),
           "yaw": yaw_of(F),
           "pitch": math.degrees(-math.asin(max(-1.0, min(1.0, R[2, 0])))),
           "roll": math.degrees(math.atan2(R[2, 1], R[2, 2])),
           "n": len(est), "from": obs_used,
           "sigma": float(1.0 / math.sqrt(sum(1.0 / (s * s) for _, _, s, _ in est))),
           "dropped": dropped,
           "parts": {nm: {"x": float(M[0, 3]), "y": float(M[1, 3]), "z": float(M[2, 3]),
                          "yaw": yaw_of(M), "sigma": float(s),
                          "resid": float(np.linalg.norm(M[:3, 3] - t))}
                     for nm, M, s, _ in est}}
    # Only compare like with like.  A down-weighted single-tag pose differing
    # from a multi-tag one by 300 mm is the single-tag ambiguity doing exactly
    # what it always does - it says nothing about whether the mount is right,
    # which is the only question this number is here to answer.
    mm = [e for e in est if e[3] == "multi"]
    if len(mm) == 2:
        Ma, Mb = mm[0][1], mm[1][1]
        out["disagree"] = float(np.linalg.norm(Ma[:3, 3] - Mb[:3, 3]))
        out["disagree_deg"] = math.degrees(log_angle(Ma[:3, :3].T @ Mb[:3, :3]))
    sing = [e for e in est if e[3] != "multi"]
    if sing and mm:
        out["single_off"] = float(np.linalg.norm(sing[0][1][:3, 3] - mm[0][1][:3, 3]))
    return out


def nt_loop(host, table, trail_n, hold_extrinsic=False, log_path=None, log_hz=10.0):
    inst = ntcore.NetworkTableInstance.getDefault()
    inst.startClient4("field_viewer")
    inst.setServer(host, ntcore.NetworkTableInstance.kDefaultPort4)
    root = inst.getTable(table)
    # NT4 only announces topics a client has subscribed to, so without this the
    # table looks empty and no cameras are ever discovered.
    multi = ntcore.MultiSubscriber(inst, ["/%s/" % table])
    subs, seen, trail = {}, {}, {}
    rig = Rig(lock=hold_extrinsic)
    last = {}                 # newest usable observation per camera
    vel = {}                  # per-camera recent positions, for a speed estimate
    logf, log_dt, next_log = None, (1.0 / log_hz if log_hz > 0 else 0.0), 0.0
    if log_path:
        logf = open(log_path, "a", buffering=1)
        STATS["path"] = log_path
        STATS["t0"] = time.time()
        print("  logging to %s at %.0f Hz" % (log_path, log_hz))
    fused_hist = []           # (wall time, xy) - drawn as the rig trail
    speed = {}
    # running mean of each tag's field pose, keyed by tag id
    tag_acc = {}
    for tid, v in load_cache().items():
        # a cached tag is already well determined; loading it at n=1 would let a
        # single noisy frame move it AND would fail the n>=5 gate below, which is
        # exactly the gate that lets a one-tag camera still get a pose
        tag_acc[tid] = {"n": min(200, max(20, int(v.get("n", 20)))),
                        "M": np.array(v["M"], dtype=float)}
    last_save = time.time()

    while True:
        for nick in root.getSubTables():
            if nick in subs:
                continue
            topic = root.getSubTable(nick).getRawTopic("rawBytes")
            subs[nick] = topic.subscribe("rawBytes", b"", ntcore.PubSubOptions(
                periodic=0.005, sendAll=True, keepDuplicates=True))
            seen[nick] = set()
            trail[nick] = []
            print("  tracking camera '%s'" % nick)

        now = time.time()
        with LOCK:
            STATE["nt"] = inst.isConnected()
        for nick, sub in subs.items():
            raw = sub.get()
            if not raw:
                continue
            try:
                r = PhotonPipelineResult.photonStruct.unpack(Packet(raw))
            except Exception:
                continue
            sid = r.metadata.sequenceID
            if sid in seen[nick]:
                continue
            seen[nick].add(sid)
            if len(seen[nick]) > 4000:
                seen[nick] = set(list(seen[nick])[-1000:])

            mt = getattr(r, "multitagResult", None)
            if mt is not None and hasattr(mt, "isPresent"):
                mt = mt.get() if mt.isPresent() else None
            ids = [t.fiducialId for t in r.targets]

            Mfc, kind, amb = None, "none", None
            if mt is not None and getattr(mt, "estimatedPose", None) is not None:
                Mfc = T(mt.estimatedPose.best)
                kind = "multi"
                amb = getattr(mt.estimatedPose, "ambiguity", None)
                # learn the layout
                for t in r.targets:
                    ct = getattr(t, "bestCameraToTarget", None)
                    if ct is None:
                        continue
                    M = Mfc @ T(ct)
                    a = tag_acc.setdefault(t.fiducialId, {"n": 0, "M": np.zeros((4, 4))})
                    a["M"] = (a["M"] * a["n"] + M) / (a["n"] + 1)
                    a["n"] += 1
            elif r.targets:
                # single tag: only possible once we have learned where that tag is
                for t in r.targets:
                    ct = getattr(t, "bestCameraToTarget", None)
                    a = tag_acc.get(t.fiducialId)
                    if ct is None or a is None or a["n"] < 5:
                        continue
                    Mfc = a["M"] @ np.linalg.inv(T(ct))
                    kind = "single"
                    amb = getattr(t, "poseAmbiguity", None)
                    break

            if Mfc is None:
                last[nick] = {"M": None, "fix": "none", "seen_at": now}
                with LOCK:
                    c = STATE["cams"].setdefault(nick, {})
                    c.update(fix="none", tags=ids, age=0.0, seen_at=now)
                continue

            dists = []
            for t in r.targets:
                ct = getattr(t, "bestCameraToTarget", None)
                if ct is not None:
                    dists.append(math.sqrt(ct.X() ** 2 + ct.Y() ** 2 + ct.Z() ** 2))
            reproj = 0.0
            if mt is not None and getattr(mt, "estimatedPose", None) is not None:
                reproj = float(getattr(mt.estimatedPose, "bestReprojErr", 0.0) or 0.0)
            last[nick] = {"M": Mfc, "fix": kind, "seen_at": now,
                          "cap": r.metadata.captureTimestampMicros / 1e6,
                          "ntags": len(ids), "reproj": reproj,
                          "dist": (sum(dists) / len(dists)) if dists else 3.0,
                          "amb": (float(amb) if amb is not None and amb >= 0 else 0.0)}
            vh = vel.setdefault(nick, [])
            vh.append((now, Mfc[:3, 3].copy()))
            if len(vh) > 10:
                del vh[:-10]

            R = Mfc[:3, :3]
            pose = dict(x=float(Mfc[0, 3]), y=float(Mfc[1, 3]), z=float(Mfc[2, 3]),
                        yaw=yaw_of(Mfc),
                        pitch=math.degrees(-math.asin(max(-1.0, min(1.0, R[2, 0])))),
                        roll=math.degrees(math.atan2(R[2, 1], R[2, 2])),
                        fix=kind, tags=ids, amb=(float(amb) if amb is not None else None),
                        seen_at=now, age=0.0)
            trail[nick].append((pose["x"], pose["y"]))
            if len(trail[nick]) > trail_n:
                del trail[nick][:-trail_n]
            pose["trail"] = list(trail[nick])
            with LOCK:
                STATE["cams"].setdefault(nick, {}).update(pose)

        # --- one rig pose from both cameras -------------------------------
        speed_prev = speed if isinstance(speed, dict) else {}
        fresh = {k: v for k, v in last.items()
                 if v.get("M") is not None and now - v["seen_at"] < 0.35}
        if len(fresh) == 2:
            (na, va), (nb, vb) = sorted(fresh.items())
            # only learn the mount while BOTH have a real multi-tag fix and the
            # two frames were actually captured at the same moment
            # Learn the mount only while the rig is STILL.  The two cameras
            # publish independently, so any residual time offset turns rig motion
            # straight into apparent mount error: at 0.5 m/s, 20 ms of skew is
            # 10 mm of pure fiction, which is what made the first estimate wander.
            still = all(speed_prev.get(n, 0.0) < 0.05 for n in (na, nb))
            if (va["fix"] == "multi" and vb["fix"] == "multi"
                    and abs(va["cap"] - vb["cap"]) < 0.010
                    and va["reproj"] < 3.0 and vb["reproj"] < 3.0 and still):
                rig.observe(na, va["M"], nb, vb["M"])

        # speed per camera, from that camera's own poses - independent of the
        # mount, so it is usable before the mount is known
        speed = {}
        for nm, vh in vel.items():
            if len(vh) >= 2 and vh[-1][0] > vh[0][0]:
                speed[nm] = float(np.linalg.norm(vh[-1][1] - vh[0][1]) / (vh[-1][0] - vh[0][0]))
            else:
                speed[nm] = 0.0
        for v in last.values():
            v["age"] = now - v["seen_at"]
        f = fuse(rig, last, speed)
        if f:
            f["speed"] = max(speed.values()) if speed else 0.0
            fused_hist.append((now, (f["x"], f["y"])))
            if len(fused_hist) > 12:
                del fused_hist[:-12]
            f["trail"] = [p for _, p in fused_hist]
        if f is not None:
            STATS["rows"] += 1
            STATS["both" if f["n"] >= 2 else "one"] += 1
            dg = f.get("disagree")
            if dg is not None:
                STATS["disagree"].append(dg)
                if STATS["worst"] is None or dg > STATS["worst"][1]:
                    STATS["worst"] = (now, dg, list(f["from"]))
            with LOCK:
                h = STATE["hist"]
                h.append([round(now, 2), round((dg or 0.0) * 1000, 3),
                          round(f["sigma"] * 1000, 2), f["n"]])
                if len(h) > 900:
                    del h[:-900]
        else:
            STATS["none"] += 1

        if logf is not None and f is not None and now >= next_log:
            next_log = now + log_dt
            row = {"t": round(now, 4), "x": f["x"], "y": f["y"], "z": f["z"],
                   "yaw": f["yaw"], "pitch": f["pitch"], "roll": f["roll"],
                   "sigma": f["sigma"], "n": f["n"], "mount": f["mount"],
                   "speed": f.get("speed"),
                   "disagree_m": f.get("disagree"), "disagree_deg": f.get("disagree_deg"),
                   "dropped": [list(x) for x in f.get("dropped", [])],
                   "single_off": f.get("single_off"),
                   "mount_locked": rig.locked,
                   "mount_yaw": (math.degrees(math.atan2(rig.X[1, 0], rig.X[0, 0]))
                                 if rig.X is not None else None),
                   "cams": {nm: {"fix": o["fix"], "tags": o.get("ntags"),
                                 "reproj": round(o.get("reproj", 0.0), 3),
                                 "dist": round(o.get("dist", 0.0), 3),
                                 "sigma": round(f["parts"][nm]["sigma"], 5),
                                 "resid": round(f["parts"][nm]["resid"], 5)}
                            for nm, o in last.items() if nm in f.get("parts", {})}}
            logf.write(json.dumps(row) + "\n")

        with LOCK:
            STATE["rig"] = f
            STATE["extrinsic"] = ({"names": list(rig.names), "n": rig.n,
                                   "locked": rig.locked,
                                   "spread_mm": (rig.spread * 1000 if rig.spread else None),
                                   "sep_mm": float(np.linalg.norm(rig.X[:3, 3]) * 1000),
                                   "yaw": math.degrees(math.atan2(rig.X[1, 0], rig.X[0, 0]))}
                                  if rig.X is not None and rig.names else None)

        if now - last_save > 5:
            last_save = now
            tags = {}
            for tid, a in tag_acc.items():
                if a["n"] < 3:
                    continue
                M = a["M"].copy()
                U, _, Vt = np.linalg.svd(M[:3, :3])
                M[:3, :3] = U @ Vt
                tags[tid] = {"x": float(M[0, 3]), "y": float(M[1, 3]), "z": float(M[2, 3]),
                             "yaw": yaw_of(M), "n": int(a["n"]), "M": M.tolist()}
            with LOCK:
                STATE["tags"] = tags
                STATE["nt"] = inst.isConnected()
                STATE["t"] = now
            save_cache(tags)
        time.sleep(0.004)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/data"):
            now = time.time()
            with LOCK:
                cams = {}
                for k, v in STATE["cams"].items():
                    v = dict(v)
                    v["age"] = round(now - v.get("seen_at", now), 3)
                    v.pop("seen_at", None)
                    cams[k] = v
                payload = {"cams": cams, "rig": STATE.get("rig"),
                           "hist": STATE.get("hist", [])[-240:],
                           "extrinsic": STATE.get("extrinsic"),
                           "tags": {str(k): {kk: vv for kk, vv in t.items() if kk != "M"}
                                    for k, t in STATE["tags"].items()},
                           "fov": STATE["fov"], "field": STATE["field"], "nt": STATE["nt"]}
            self._send(json.dumps(payload), "application/json")
        elif self.path == "/" or self.path.startswith("/index"):
            self._send(PAGE, "text/html; charset=utf-8")
        else:
            self.send_error(404)


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Field Viewer</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Saira+Condensed:wght@500;600&family=IBM+Plex+Mono:wght@400;500&family=Source+Sans+3:wght@400;600&display=swap">
<style>
:root{
  --bg:#EEF1F2; --panel:#FFFFFF; --ink:#12191D; --dim:#5C6B73; --line:#C3CFD4;
  --grid:#D8E0E3; --accent:#1F6F8B; --cam1:#D98324; --cam2:#2C9C8F;
  --bad:#C0463A; --ok:#3E8E5A; --blind:#C0463A --fuse:#7B3FA0;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0E1417; --panel:#161F24; --ink:#E6EDF0; --dim:#8DA0A9; --line:#2A3940;
  --grid:#1E2A30; --accent:#4FB0D0; --cam1:#E8A33D; --cam2:#4FB8A8;
  --bad:#E06A5A; --ok:#5FBF83; --blind:#E06A5A --fuse:#B584E0;
}}
:root[data-theme="dark"]{
  --bg:#0E1417; --panel:#161F24; --ink:#E6EDF0; --dim:#8DA0A9; --line:#2A3940;
  --grid:#1E2A30; --accent:#4FB0D0; --cam1:#E8A33D; --cam2:#4FB8A8;
  --bad:#E06A5A; --ok:#5FBF83; --blind:#E06A5A --fuse:#B584E0;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font-family:"Source Sans 3",system-ui,sans-serif;font-size:14px}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;
       padding:14px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
h1{font-family:"Saira Condensed",system-ui,sans-serif;font-weight:600;
   font-size:22px;letter-spacing:.02em;margin:0}
.sub{color:var(--dim);font-size:13px}
.pill{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.04em;
      padding:3px 9px;border-radius:2px;border:1px solid var(--line);color:var(--dim)}
.pill.live{color:var(--ok);border-color:var(--ok)}
.pill.down{color:var(--bad);border-color:var(--bad)}
main{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:0;
     height:calc(100vh - 57px)}
@media(max-width:860px){main{grid-template-columns:1fr;height:auto}}
#wrap{position:relative;min-height:420px}
canvas{display:block;width:100%;height:100%}
aside{border-left:1px solid var(--line);background:var(--panel);overflow-y:auto;padding:16px}
@media(max-width:860px){aside{border-left:0;border-top:1px solid var(--line)}}
.lab{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.11em;
     text-transform:uppercase;color:var(--dim);margin:0 0 7px}
.card{border:1px solid var(--line);border-radius:3px;padding:11px 12px;margin-bottom:12px}
.card h3{font-family:"Saira Condensed",sans-serif;font-size:16px;font-weight:600;
         margin:0 0 8px;display:flex;align-items:center;gap:7px}
.swatch{width:10px;height:10px;border-radius:50%;flex:none}
table{width:100%;border-collapse:collapse;font-family:"IBM Plex Mono",monospace;
      font-size:12px;font-variant-numeric:tabular-nums}
td{padding:2px 0}
td:last-child{text-align:right}
td.k{color:var(--dim)}
.fix{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.06em;
     padding:2px 6px;border-radius:2px;border:1px solid currentColor;margin-left:auto}
.fix.multi{color:var(--ok)} .fix.single{color:var(--cam1)} .fix.none{color:var(--bad)}
label.tog{display:flex;align-items:center;gap:8px;margin:7px 0;cursor:pointer;color:var(--dim)}
label.tog input{accent-color:var(--accent)}
.note{color:var(--dim);font-size:12px;line-height:1.5;margin:9px 0 0}
kbd{font-family:"IBM Plex Mono",monospace;font-size:11px;border:1px solid var(--line);
    border-radius:2px;padding:1px 4px}
</style>
<header>
  <h1>Field Viewer</h1>
  <span class="sub">where the cameras are, live</span>
  <span class="pill" id="nt">connecting</span>
  <span class="pill" id="rate">&mdash;</span>
</header>
<main>
  <div id="wrap"><canvas id="c"></canvas></div>
  <aside>
    <p class="lab">Fused rig pose</p>
    <div id="rig"></div>
    <p class="lab">Mount</p>
    <div id="extr"></div>
    <p class="lab">Cameras</p>
    <div id="cams"></div>
    <p class="lab">View</p>
    <div class="card">
      <label class="tog"><input type="checkbox" id="tFov" checked> field of view</label>
      <label class="tog"><input type="checkbox" id="tBlind" checked> blind wedge between</label>
      <label class="tog"><input type="checkbox" id="tTrail" checked> trail</label>
      <label class="tog"><input type="checkbox" id="tFused" checked> fused rig pose</label>
      <label class="tog"><input type="checkbox" id="tField" checked> field outline</label>
      <label class="tog"><input type="checkbox" id="tGrid"> 1 ft grid</label>
      <p class="note">Drag to pan, scroll to zoom, <kbd>0</kbd> to reset.</p>
    </div>
    <p class="lab">Tags</p>
    <div class="card"><table id="tags"></table>
      <p class="note">Positions are reconstructed from the live stream, so they
      are whatever layout PhotonVision is running right now.</p></div>
  </aside>
</main>
<script>
const $=s=>document.querySelector(s), C=$('#c'), X=C.getContext('2d');
let D={cams:{},tags:{},fov:{},field:{}}, view={s:0,ox:0,oy:0,auto:true}, hist=[];
const FT=0.3048;
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function bounds(){
  const p=[]; for(const k in D.tags){p.push([D.tags[k].x,D.tags[k].y]);}
  for(const k in D.cams){const c=D.cams[k]; if(c.x!==undefined)p.push([c.x,c.y]);}
  const f=D.field||{};
  if(f.poly){f.poly.forEach(q=>p.push(q));}
  if(!p.length)return{x0:-1,y0:-1,x1:1,y1:1};
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const[a,b]of p){x0=Math.min(x0,a);x1=Math.max(x1,a);y0=Math.min(y0,b);y1=Math.max(y1,b);}
  const m=0.7; return{x0:x0-m,y0:y0-m,x1:x1+m,y1:y1+m};
}
function fit(){
  const b=bounds(), w=C.width/devicePixelRatio, h=C.height/devicePixelRatio;
  const s=Math.min(w/(b.x1-b.x0), h/(b.y1-b.y0));
  view.s=s; view.ox=w/2-s*(b.x0+b.x1)/2; view.oy=h/2+s*(b.y0+b.y1)/2;
}
const PX=(x)=>view.ox+view.s*x, PY=(y)=>view.oy-view.s*y;
function resize(){
  const r=C.parentElement.getBoundingClientRect(), d=devicePixelRatio||1;
  C.width=r.width*d; C.height=Math.max(r.height,420)*d;
  X.setTransform(d,0,0,d,0,0); if(view.auto)fit(); draw();
}
addEventListener('resize',resize);
C.addEventListener('wheel',e=>{e.preventDefault();view.auto=false;
  const r=C.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
  const k=Math.exp(-e.deltaY*0.0016);
  view.ox=mx-(mx-view.ox)*k; view.oy=my-(my-view.oy)*k; view.s*=k; draw();},{passive:false});
let drag=null;
C.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];view.auto=false;C.setPointerCapture(e.pointerId)});
C.addEventListener('pointermove',e=>{if(!drag)return;
  view.ox+=e.clientX-drag[0]; view.oy+=e.clientY-drag[1]; drag=[e.clientX,e.clientY]; draw()});
addEventListener('pointerup',()=>drag=null);
addEventListener('keydown',e=>{if(e.key==='0'){view.auto=true;fit();draw()}});

function wedge(cx,cy,a0,a1,rad,fill,stroke){
  X.beginPath(); X.moveTo(PX(cx),PY(cy));
  const n=48; for(let i=0;i<=n;i++){const a=(a0+(a1-a0)*i/n)*Math.PI/180;
    X.lineTo(PX(cx+rad*Math.cos(a)),PY(cy+rad*Math.sin(a)));}
  X.closePath(); if(fill){X.fillStyle=fill;X.fill();}
  if(stroke){X.strokeStyle=stroke;X.lineWidth=1.25;X.stroke();}
}
function reach(){const b=bounds();
  return Math.min(9,Math.hypot(b.x1-b.x0,b.y1-b.y0));}
function draw(){
  const w=C.width/devicePixelRatio,h=C.height/devicePixelRatio;
  X.clearRect(0,0,w,h); X.fillStyle=css('--bg'); X.fillRect(0,0,w,h);
  const f=D.field||{};
  if($('#tGrid').checked){
    X.strokeStyle=css('--grid'); X.lineWidth=1; const b=bounds();
    for(let x=Math.ceil(b.x0/FT)*FT;x<b.x1;x+=FT){X.beginPath();X.moveTo(PX(x),PY(b.y0));X.lineTo(PX(x),PY(b.y1));X.stroke();}
    for(let y=Math.ceil(b.y0/FT)*FT;y<b.y1;y+=FT){X.beginPath();X.moveTo(PX(b.x0),PY(y));X.lineTo(PX(b.x1),PY(y));X.stroke();}
  }
  if($('#tField').checked && f.poly){
    const q=f.poly;
    X.save();
    // the wall the tags actually sit on - solid, it is measured
    X.strokeStyle=css('--line'); X.lineWidth=2;
    X.beginPath(); X.moveTo(PX(q[0][0]),PY(q[0][1])); X.lineTo(PX(q[1][0]),PY(q[1][1]));
    X.stroke();
    // the other three - dashed, nothing in the survey constrains the width
    X.setLineDash([7,5]); X.strokeStyle=css('--dim'); X.lineWidth=1.5;
    X.beginPath();
    for(let i=1;i<4;i++){X.moveTo(PX(q[i][0]),PY(q[i][1]));
      X.lineTo(PX(q[(i+1)%4][0]),PY(q[(i+1)%4][1]));}
    X.stroke(); X.restore();
    X.fillStyle=css('--dim'); X.font='11px "IBM Plex Mono",monospace';
    if(f.long_label){
      const mx=(q[0][0]+q[1][0])/2,my=(q[0][1]+q[1][1])/2;
      X.fillText(f.long_label,PX(mx)-26,PY(my)-8);
    }
    if(f.short_label){
      const mx=(q[0][0]+q[3][0])/2,my=(q[0][1]+q[3][1])/2;
      X.fillText(f.short_label,PX(mx)-70,PY(my)+4);
    }
  }
  const ids=Object.keys(D.cams).sort();
  const cols=[css('--cam1'),css('--cam2'),css('--accent'),css('--ok')];
  // blind wedge between the first two cameras that have a fix
  if($('#tBlind').checked){
    const fixed=ids.filter(k=>D.cams[k].x!==undefined&&D.cams[k].fix!=='none');
    if(fixed.length>=2){
      const a=D.cams[fixed[0]], b=D.cams[fixed[1]];
      const fa=D.fov[fixed[0]]||{left:-34.7,right:34.7}, fb=D.fov[fixed[1]]||{left:-34.7,right:34.7};
      let e1=a.yaw+fa.right, e2=b.yaw+fb.left;
      let g=((e2-e1)%360+360)%360; if(g>180){e1=b.yaw+fb.right; e2=a.yaw+fa.left;
        g=((e2-e1)%360+360)%360;}
      if(g>0.5&&g<180){
        const mx=(a.x+b.x)/2,my=(a.y+b.y)/2;
        const R=reach();
        X.save(); X.globalAlpha=0.13; wedge(mx,my,e1,e1+g,R,css('--blind'),null); X.restore();
        X.save(); X.globalAlpha=0.55; X.setLineDash([4,4]);
        X.strokeStyle=css('--blind'); X.lineWidth=1;
        for(const e of [e1,e1+g]){X.beginPath();X.moveTo(PX(mx),PY(my));
          X.lineTo(PX(mx+R*Math.cos(e*Math.PI/180)),PY(my+R*Math.sin(e*Math.PI/180)));X.stroke();}
        X.restore();
        const am=(e1+g/2)*Math.PI/180;
        X.fillStyle=css('--blind'); X.font='600 12px "Saira Condensed",sans-serif';
        X.fillText(g.toFixed(1)+'° blind',PX(mx+1.5*Math.cos(am))-22,PY(my+1.5*Math.sin(am)));
      }
    }
  }
  ids.forEach((k,i)=>{
    const c=D.cams[k], col=cols[i%cols.length];
    if(c.x===undefined)return;
    const fv=D.fov[k]||{left:-34.7,right:34.7};
    if($('#tFov').checked&&c.fix!=='none'){
      X.save(); X.globalAlpha=0.16;
      const R=reach();
      wedge(c.x,c.y,c.yaw+fv.left,c.yaw+fv.right,R,col,null); X.restore();
      X.save(); X.globalAlpha=0.5;
      wedge(c.x,c.y,c.yaw+fv.left,c.yaw+fv.right,R,null,col); X.restore();
    }
    if($('#tTrail').checked&&c.trail&&c.trail.length>1){
      X.save(); X.globalAlpha=0.5; X.strokeStyle=col; X.lineWidth=1.5;
      X.beginPath(); c.trail.forEach((p,j)=>j?X.lineTo(PX(p[0]),PY(p[1])):X.moveTo(PX(p[0]),PY(p[1])));
      X.stroke(); X.restore();
    }
    const px=PX(c.x),py=PY(c.y);
    X.strokeStyle=col; X.lineWidth=2.5; X.beginPath(); X.moveTo(px,py);
    X.lineTo(px+22*Math.cos(c.yaw*Math.PI/180),py-22*Math.sin(c.yaw*Math.PI/180)); X.stroke();
    X.beginPath(); X.arc(px,py,6,0,7);
    if(c.fix==='single'||c.age>0.6){X.strokeStyle=col;X.lineWidth=2;X.stroke();}
    else{X.fillStyle=col;X.fill();}
    X.fillStyle=col; X.font='600 12px "Saira Condensed",sans-serif';
    X.fillText(k,px+9,py-9);
  });
  for(const id in D.tags){
    const t=D.tags[id], a=(t.yaw+90)*Math.PI/180, L=0.0825;
    const x0=t.x+L*Math.cos(a), y0=t.y+L*Math.sin(a);
    const x1=t.x-L*Math.cos(a), y1=t.y-L*Math.sin(a);
    X.strokeStyle=css('--ink'); X.lineWidth=4; X.lineCap='round';
    X.beginPath(); X.moveTo(PX(x0),PY(y0)); X.lineTo(PX(x1),PY(y1)); X.stroke();
    const n=t.yaw*Math.PI/180;
    X.strokeStyle=css('--dim'); X.lineWidth=1;
    X.beginPath(); X.moveTo(PX(t.x),PY(t.y));
    X.lineTo(PX(t.x+0.18*Math.cos(n)),PY(t.y+0.18*Math.sin(n))); X.stroke();
    X.fillStyle=css('--ink'); X.font='600 12px "Saira Condensed",sans-serif';
    X.fillText(id,PX(t.x)-3+14*Math.cos(n),PY(t.y)+4-14*Math.sin(n));
  }
  const g=D.rig;
  if($('#tFused').checked&&g){
    const px=PX(g.x),py=PY(g.y),col=css('--fuse');
    X.save();
    X.strokeStyle=col; X.lineWidth=2;
    X.beginPath(); X.moveTo(px,py);
    X.lineTo(px+30*Math.cos(g.yaw*Math.PI/180),py-30*Math.sin(g.yaw*Math.PI/180)); X.stroke();
    if(g.trail&&g.trail.length>1){X.globalAlpha=.55;X.lineWidth=2;X.beginPath();
      g.trail.forEach((q,j)=>j?X.lineTo(PX(q[0]),PY(q[1])):X.moveTo(PX(q[0]),PY(q[1])));
      X.stroke();X.globalAlpha=1;}
    X.translate(px,py); X.rotate(Math.PI/4);
    X.fillStyle=col; X.fillRect(-6,-6,12,12);
    X.strokeStyle=css('--panel'); X.lineWidth=1.5; X.strokeRect(-6,-6,12,12);
    X.restore();
    X.fillStyle=col; X.font='600 13px "Saira Condensed",sans-serif';
    X.fillText('rig',px+11,py+15);
  }
  // scale bar
  X.strokeStyle=css('--dim'); X.lineWidth=1.5; X.font='11px "IBM Plex Mono",monospace';
  const bx=16,by=h-18,bl=view.s*FT*3;
  X.beginPath();X.moveTo(bx,by);X.lineTo(bx+bl,by);
  X.moveTo(bx,by-4);X.lineTo(bx,by+4);X.moveTo(bx+bl,by-4);X.lineTo(bx+bl,by+4);X.stroke();
  X.fillStyle=css('--dim'); X.fillText('3 ft',bx+bl/2-9,by-8);
}
function panel(){
  const cols=[css('--cam1'),css('--cam2'),css('--accent'),css('--ok')];
  const ids=Object.keys(D.cams).sort();
  $('#cams').innerHTML=ids.map((k,i)=>{
    const c=D.cams[k], col=cols[i%cols.length];
    const fx=c.fix||'none';
    const rows=(c.x===undefined)?'<tr><td class="k">no pose</td><td>&mdash;</td></tr>':
      [['x',(c.x).toFixed(3)+' m'],['y',(c.y).toFixed(3)+' m'],['z',(c.z).toFixed(3)+' m'],
       ['yaw',(c.yaw>=0?'+':'')+c.yaw.toFixed(2)+'°'],
       ['pitch',(c.pitch>=0?'+':'')+c.pitch.toFixed(2)+'°'],
       ['roll',(c.roll>=0?'+':'')+c.roll.toFixed(2)+'°'],
       ['tags',(c.tags||[]).join(' ')||'none'],
       ['fov',(D.fov[k]?(D.fov[k].right-D.fov[k].left).toFixed(1)+'°':'?')],
       ['age',(c.age||0).toFixed(2)+' s']].map(r=>
      `<tr><td class="k">${r[0]}</td><td>${r[1]}</td></tr>`).join('');
    return `<div class="card"><h3><span class="swatch" style="background:${col}"></span>${k}
      <span class="fix ${fx}">${fx}</span></h3><table>${rows}</table></div>`;
  }).join('')||'<div class="card"><p class="note">waiting for a camera&hellip;</p></div>';
  const g=D.rig;
  $('#rig').innerHTML = g ? (function(){
    const rows=[['x',g.x.toFixed(4)+' m'],['y',g.y.toFixed(4)+' m'],['z',g.z.toFixed(4)+' m'],
      ['yaw',(g.yaw>=0?'+':'')+g.yaw.toFixed(3)+'°'],
      ['pitch',(g.pitch>=0?'+':'')+g.pitch.toFixed(3)+'°'],
      ['roll',(g.roll>=0?'+':'')+g.roll.toFixed(3)+'°'],
      ['&sigma;',(g.sigma*1000).toFixed(1)+' mm'],
      ['speed',(g.speed*1000).toFixed(0)+' mm/s'],
      ['from',g.n+' camera'+(g.n>1?'s':'')],
      ['mount',g.mount?'applied':'not learned']].map(r=>
      `<tr><td class="k">${r[0]}</td><td>${r[1]}</td></tr>`).join('');
    let d='';
    (g.dropped||[]).forEach(x=>{d+=`<tr><td class="k" style="color:${css('--bad')}">rejected</td>`
      +`<td style="color:${css('--bad')}">${x[0]}</td></tr>`
      +`<tr><td colspan="2" class="k" style="text-align:left;font-size:11px">&nbsp;&nbsp;${x[1]}</td></tr>`;});
    if(g.disagree!==undefined){
      const bad=g.disagree>0.05;
      d=`<tr><td class="k">cameras differ</td><td style="color:${bad?css('--bad'):css('--ok')}">`
        +`${(g.disagree*1000).toFixed(1)} mm / ${g.disagree_deg.toFixed(2)}°</td></tr>`;
    }
    const parts=Object.keys(g.parts||{}).map(k=>
      `<tr><td class="k">&nbsp;&nbsp;${k}</td><td>&plusmn;${(g.parts[k].sigma*1000).toFixed(0)}`
      +` &rarr; ${(g.parts[k].resid*1000).toFixed(1)} mm</td></tr>`).join('');
    return `<div class="card"><h3><span class="swatch" style="background:${css('--fuse')}"></span>`
      +`rig origin</h3><table>${rows}${d}${parts}</table>`
      +`<canvas id="spark" width="278" height="46" style="width:100%;height:46px;`
      +`margin-top:9px;display:block"></canvas>`
      +`<p class="note" style="margin-top:4px">${g.mount
         ? 'Weight is 1/&sigma;&sup2; per camera; &sigma; grows with tag distance, shrinks with'
           +' tag count, and is inflated by reprojection error.'
         : 'No camera-to-camera transform yet, so this is one camera alone. Point the rig so'
           +' <em>both</em> cameras see 2+ tags for a few seconds to learn the mount.'}`
      +`</p></div>`;
  })() : '<div class="card"><p class="note">waiting for two cameras with a fix&hellip;</p></div>';

  const e=D.extrinsic;
  $('#extr').innerHTML = e ? (function(){
    const st=e.locked?'locked':'learning';
    const rows=[['A &rarr; B',e.names.join(' &rarr; ')],
      ['separation',e.sep_mm.toFixed(1)+' mm'],
      ['yaw',(e.yaw>=0?'+':'')+e.yaw.toFixed(2)+'°'],
      ['samples',e.n],
      ['scatter',e.spread_mm!=null?e.spread_mm.toFixed(2)+' mm':'&mdash;']].map(r=>
      `<tr><td class="k">${r[0]}</td><td>${r[1]}</td></tr>`).join('');
    return `<div class="card"><h3>camera&ndash;to&ndash;camera`
      +`<span class="fix ${e.locked?'multi':'single'}">${st}</span></h3><table>${rows}</table></div>`;
  })() : '<div class="card"><p class="note">learning the mount&hellip;</p></div>';

  spark();
  const t=Object.keys(D.tags).sort((a,b)=>a-b);
  $('#tags').innerHTML=t.map(id=>{const g=D.tags[id];
    return `<tr><td class="k">${id}</td><td>${g.x.toFixed(2)}, ${g.y.toFixed(2)} m</td></tr>`;
  }).join('')||'<tr><td class="k">none yet</td></tr>';
}
function spark(){
  const el=document.getElementById('spark'); if(!el)return;
  const h=(D.hist||[]).filter(r=>r[3]>=2); const c=el.getContext('2d');
  const W=el.width,H=el.height; c.clearRect(0,0,W,H);
  c.fillStyle=css('--bg'); c.fillRect(0,0,W,H);
  if(h.length<2){c.fillStyle=css('--dim');c.font='10px "IBM Plex Mono",monospace';
    c.fillText('waiting for both cameras',6,H/2+3);return;}
  const v=h.map(r=>r[1]); const mx=Math.max(4,Math.ceil(Math.max(...v)*1.25));
  const Y=q=>H-3-(H-9)*Math.min(1,q/mx);
  // the 20 mm line: above this, suspect the mount or the layout, not noise
  if(mx>=20){c.strokeStyle=css('--bad');c.globalAlpha=.45;c.setLineDash([3,3]);
    c.beginPath();c.moveTo(0,Y(20));c.lineTo(W,Y(20));c.stroke();
    c.setLineDash([]);c.globalAlpha=1;}
  c.beginPath();
  h.forEach((r,i)=>{const x=W*i/(h.length-1);i?c.lineTo(x,Y(r[1])):c.moveTo(x,Y(r[1]));});
  c.lineTo(W,H);c.lineTo(0,H);c.closePath();
  c.fillStyle=css('--fuse');c.globalAlpha=.16;c.fill();c.globalAlpha=1;
  c.beginPath();
  h.forEach((r,i)=>{const x=W*i/(h.length-1);i?c.lineTo(x,Y(r[1])):c.moveTo(x,Y(r[1]));});
  c.strokeStyle=css('--fuse');c.lineWidth=1.5;c.stroke();
  const last=h[h.length-1];
  c.fillStyle=css('--fuse');c.beginPath();c.arc(W-2,Y(last[1]),2.5,0,7);c.fill();
  c.fillStyle=css('--dim');c.font='9px "IBM Plex Mono",monospace';
  c.fillText(mx.toFixed(0)+' mm',3,9);
  c.fillText('camera disagreement',3,H-3);
}
async function tick(){
  try{
    const r=await fetch('/data',{cache:'no-store'}); const j=await r.json();
    D=j; hist.push(Date.now()); if(hist.length>30)hist.shift();
    $('#nt').textContent=j.nt?'NT connected':'NT down';
    $('#nt').className='pill '+(j.nt?'live':'down');
    if(hist.length>4){const hz=1000*(hist.length-1)/(hist[hist.length-1]-hist[0]);
      $('#rate').textContent=hz.toFixed(0)+' Hz';}
    if(view.auto)fit();
    panel(); draw();
  }catch(e){$('#nt').textContent='viewer offline';$('#nt').className='pill down';}
}
for(const id of ['tFov','tBlind','tTrail','tField','tGrid','tFused'])
  $('#'+id).addEventListener('change',draw);
resize(); tick(); setInterval(tick,60);
</script>
"""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="photonvision.local", help="PhotonVision / NT server")
    p.add_argument("--port", type=int, default=8077)
    p.add_argument("--table", default="photonvision")
    p.add_argument("--trail", type=int, default=400, help="trail points kept per camera")
    p.add_argument("--field-long", type=float, default=None,
                   help="known long dimension in metres, drawn as the outline")
    p.add_argument("--field-short", type=float, default=None,
                   help="short dimension in metres (drawn dashed - the survey cannot check it)")
    p.add_argument("--no-field", action="store_true")
    p.add_argument("--log", default=None, metavar="FILE",
                   help="append one JSON object per fused pose (JSONL)")
    p.add_argument("--log-hz", type=float, default=10.0,
                   help="how often to write a log row (default 10)")
    p.add_argument("--lock-extrinsic", action="store_true",
                   help="use the cached camera-to-camera transform and stop re-learning it")
    a = p.parse_args()

    print("field viewer")
    print("  reading calibration from %s ..." % a.host)
    STATE["fov"] = fetch_fov(a.host)

    if not a.no_field and a.field_long and a.field_short:
        STATE["field"] = dict(
            poly=None, x0=0.0, x1=a.field_long, y0=None, y1=None,
            long_label="%.2f ft" % (a.field_long / 0.3048),
            short_label="%.0f in (assumed)" % (a.field_short / 0.0254))

    t = threading.Thread(target=nt_loop,
                         args=(a.host, a.table, a.trail, a.lock_extrinsic,
                               a.log, a.log_hz), daemon=True)
    t.start()

    # the field rectangle is anchored to the wall the tags are on, once we have them
    def anchor():
        """Lay the field outline along the REAL wall, not along the axes.

        The layout frame is anchored to whichever tag the survey used as its
        reference, so its axes have no relationship to the room.  Drawing an
        axis-aligned rectangle therefore paints a wall a couple of degrees away
        from the true one, and every tag on it looks tilted - which is a lie
        about the tags.  Fit the wall to the tags that sit on it instead.
        """
        while True:
            time.sleep(1.0)
            with LOCK:
                tags = dict(STATE["tags"])
                f = dict(STATE["field"]) if STATE["field"] else None
            if not (f and len(tags) >= 2 and f.get("poly") is None):
                continue
            # group tags by which way they face; the biggest group is the wall
            items = sorted(tags.items(), key=lambda kv: int(kv[0]))
            groups = []
            for tid, v in items:
                for g in groups:
                    d = abs(((v["yaw"] - tags[g[0]]["yaw"] + 180) % 360) - 180)
                    if d < 35:
                        g.append(tid)
                        break
                else:
                    groups.append([tid])
            wall = max(groups, key=len)
            if len(wall) < 2:
                continue
            P = np.array([[tags[t]["x"], tags[t]["y"]] for t in wall])
            c = P.mean(axis=0)
            u = np.linalg.svd(P - c)[2][0]           # direction along the wall
            nz = np.mean([math.radians(tags[t]["yaw"]) for t in wall])
            n = np.array([math.cos(nz), math.sin(nz)])   # normal, into the room
            n -= u * float(n @ u)
            nn = np.linalg.norm(n)
            if nn < 1e-6:
                continue
            n /= nn
            allp = np.array([[v["x"], v["y"]] for v in tags.values()])
            t0 = float(np.min((allp - c) @ u))
            p0 = c + u * t0
            L, W = a.field_long, a.field_short
            poly = [(p0 + u * s_ + n * w_).tolist()
                    for s_, w_ in ((0, 0), (L, 0), (L, W), (0, W))]
            with LOCK:
                STATE["field"]["poly"] = poly
                STATE["field"]["wall_deg"] = math.degrees(math.atan2(u[1], u[0]))
                STATE["field"]["wall_tags"] = [int(t) for t in wall]
            print("  field outline fitted to tags %s, wall at %+.2f deg"
                  % ([int(t) for t in wall], math.degrees(math.atan2(u[1], u[0]))))
    threading.Thread(target=anchor, daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("\n  open  http://localhost:%d\n" % a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        summarise()


def summarise():
    d = sorted(STATS["disagree"])
    print("\n--- session ---")
    if STATS["t0"]:
        print("  %.0f s, %d fused poses logged to %s"
              % (time.time() - STATS["t0"], STATS["rows"], STATS["path"]))
    else:
        print("  %d fused poses (not logged - pass --log FILE to keep them)" % STATS["rows"])
    print("  both cameras %d   one camera %d   no fix %d"
          % (STATS["both"], STATS["one"], STATS["none"]))
    if d:
        def q(p):
            return d[min(len(d) - 1, int(p * len(d)))]
        print("  camera disagreement  median %.2f mm   p95 %.2f mm   max %.2f mm"
              % (1000 * q(0.5), 1000 * q(0.95), 1000 * d[-1]))
        if STATS["worst"]:
            print("  worst at t=%.1f (%s)" % (STATS["worst"][0], ", ".join(STATS["worst"][2])))
        if 1000 * q(0.5) > 20:
            print("  NOTE: a median above ~20 mm usually means the mount transform or the"
                  "\n        tag layout is wrong, not that the cameras are noisy.")


if __name__ == "__main__":
    main()
