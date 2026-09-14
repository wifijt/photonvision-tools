# photonvision-tools

Scripts for surveying a custom AprilTag layout for [PhotonVision](https://photonvision.org),
and for editing PhotonVision's configuration from the command line.

Companion to [photontune](https://github.com/wifijt/photontune).

Everything here was written and validated against a real Raspberry Pi 5 running
PhotonVision v2026.3.4 with an OV9281 CSI camera. The notes below record what actually
worked — and, just as usefully, what didn't.

---

## survey/ — build a field layout from tags you can see

Multi-tag PnP needs a map: each tag's pose in a common frame. On a real field that comes
from FIRST. For a practice rig, an off-season setup, or tags stuck on a wall, you have to
make one.

```sh
# 0. sanity-check the calibration the solve will use
python3 survey/photon_calib.py photonvision.local

# 1. record PhotonVision's own detected corners while you move the camera around
python3 survey/capture_corners.py frames.json 120 --host photonvision.local

# 2. bundle-adjust them into an AprilTagFieldLayout
#    (intrinsics are fetched from PhotonVision - never hardcode them)
python3 survey/solve_layout.py frames.json layout.json 0.1651 --host photonvision.local

# 3. upload (multipart, field name "data")
curl -X POST -F "data=@layout.json" \
     http://photonvision.local:5800/api/settings/aprilTagFieldLayout
```

`tag_size` is the **black square** edge in metres, excluding the white border
(0.1651 = 6.5 in). It sets the absolute scale of the entire map: get it wrong and
everything is uniformly wrong while looking perfectly self-consistent.

Calibration is pulled from the running PhotonVision by default, because hardcoding
intrinsics is a silent failure — the wrong camera matrix yields a map that reprojects
beautifully and is geometrically wrong. `--fx/--fy/--cx/--cy` override if you must.

Result on the reference rig: **5 tags, 0.211 px per-corner reprojection**, and every tag
recovered as level to within 0.62° — which matched the physical setup and was never given
to the solver.

### Three things that are easy to get wrong

**Use PhotonVision's corners, not OpenCV's.** Detecting tags yourself with
`cv2.aruco` and bundle-adjusting those produces a map that is internally beautiful and
systematically wrong — roughly 5% too large in testing, because the two detectors place
tag corners differently. The whole map then fails to load usefully. `capture_corners.py`
reads `detectedCorners` over NetworkTables, straight from PhotonVision.

**Object point order is `TL, TR, BR, BL`** in an X-right / Y-down / Z-into-scene tag frame:

```python
OBJ = [(-h, h, 0), (h, h, 0), (h, -h, 0), (-h, -h, 0)]
```

Verified empirically: correct order gives 0.03–0.29 px; wrong orders give 18000+ px.

**The tag frame conversion to WPILib is:**

```python
M = [[0, 0, -1],
     [1, 0,  0],
     [0, -1, 0]]
p_wpi = M @ p            R_wpi = M @ R @ M.T
```

Found by brute-forcing all 24 proper rotations: this one scores 0.336 px, the runner-up
31.6 px. Hand-deriving it produced a matrix wrong by two signs.

### Approaches that did not work

Recorded because each looked plausible and cost real time:

| Approach | Result |
|---|---|
| Bundle adjustment on `cv2.aruco` corners | 0.44 px internally, ~5% scale error, unusable layout |
| Chaining PhotonVision's single-tag **poses** | Looked superb (1.1 mm spread) — but only because the camera never moved. Widening the baseline dropped cluster agreement to 21% |
| Joint pose-graph over single-tag poses | 903 mm jitter, p95 reprojection 78739 |
| Same, constrained to level tags | Worse still — the tilt was absorbing camera-pose error, not tag tilt |

The through-line: **PhotonVision's single-tag poses are too noisy to build a map from**
(14–25 mm position, several degrees orientation). Reprojection on raw corners is far more
sensitive to geometry and is what finally worked.

### measure_quality.py

Reports per-tag ambiguity, pose jitter, fps and latency from a live pipeline. Useful for
before/after comparisons.

```sh
python3 survey/measure_quality.py 30
```

---

## Plain-English settings guide

**[GATING.md](GATING.md)** covers using vision as a *correction* to odometry rather than a
primary sensor, and the filtering that makes that safe — why a bad correction is worse
than no correction, and the gates that catch each failure mode.

**[SETTINGS.md](SETTINGS.md)** explains what every AprilTag setting actually does, what it
costs, and which ones depend on where you are. Read that before tuning anything.

## config/ — edit PhotonVision settings from a script

The web UI cannot be automated and some settings are awkward to reach. These edit
`photon.sqlite` directly.

**Stop PhotonVision first** — it rewrites config on shutdown and will clobber your edit.

`apply_baseline.py` sets the **structural** settings (decimate, threads, multi-tag,
rotation and friends) to known-good values, live over the websocket, with a measured
reason for each. `--explain` prints the reasoning; `--dry-run` shows what would change.

```sh
python3 config/apply_baseline.py --host photonvision.local --dry-run
python3 config/apply_baseline.py --explain
```

The rest edit `photon.sqlite` directly:

```sh
sudo systemctl stop photonvision
sudo python3 config/set_pipeline_setting.py 1 decimate=2 numIterations=40 threads=4
sudo python3 config/set_active_pipeline.py 1
sudo python3 config/rename_pipeline.py 1 "Competition"
sudo python3 config/set_nt_server.py true     # make PhotonVision its own NT server
sudo systemctl start photonvision
```

Most *pipeline* settings can instead be changed live over the websocket, with no restart:

```python
ws.send(msgpack.packb({"changePipelineSetting":
                       {"cameraExposureRaw": 4000.0, "cameraUniqueName": uuid}}))
```

Note the `cameraSettings` broadcast lags by a second or two, so reading a value straight
back can show the old one even though the change applied.

### set_streams.py — a third of your framerate goes to a stream nobody watches

PhotonVision copies, converts, draws on and encodes its camera streams on **every
frame, even with no client connected**. Closing the dashboard does not help — the
stream is *prepared* regardless. On a Pi 5 / OV9281 at 1280x800, measured over 3
interleaved 45-second A/B cycles with nothing attached to any stream port:

| streams | fps | latency | frame period |
|---|---|---|---|
| on (default) | 60.1 | 42.0 ms | 17.42 ms |
| off | 80.5 | 32.7 ms | **8.71 ms** |
| | **+20.4 (+34%)** | **−9.2 ms** | |

The frame period tells the story: 8.71 ms is the sensor's own period. With streams
off the pipeline keeps up with every camera frame; with them on it drops every
other one. An actual viewer, by contrast, costs only ~2.5 ms.

**Measure this on a freshly restarted service.** A process that had been running
for hours gave 55.9/60.1 fps instead of 60.1/80.5 — same direction, badly
distorted magnitude.

Every mode acts on **all** cameras unless you name one with `--camera`.

### Two cameras on one Pi 5 do not fit at the defaults

Adding a second OV9281 halved the first camera's framerate and tripled its
latency. Two settings recovered most of it, with no code:

| | OV9281 | OV9281 (1) | total |
|---|---|---|---|
| defaults (threads=4, streams on) | 29.9 fps / 120.5 ms | 43.8 fps / 92.4 ms | 73.7 |
| threads=1 each, streams off | **47.7 / 78.4** | **46.8 / 77.9** | **94.5** |

`threads` is **per camera**, so the stock 4 means 8 detection threads on 4 cores.
Even with a single camera, `threads=1` measured faster than `threads=4`
(14.90 ms vs 15.58 ms). `apply_baseline.py` sets 1.

The dashboard cannot turn this off: its stream selector requires at least one
stream to stay selected. The websocket API can.

```sh
python3 config/set_streams.py --status --measure   # what is set, what it costs
python3 config/set_streams.py --off                # match
python3 config/set_streams.py --on                 # pit
python3 config/set_streams.py --daemon             # let the robot decide
```

Applies live, no service restart. It reads the setting back and fails loudly if it
did not take.

Daemon mode follows NetworkTables so the robot can shed the streams itself:

| topic | direction | meaning |
|---|---|---|
| `/PhotonStreams/enable` | robot writes | `true` = on, `false` = off |
| `/PhotonStreams/state` | published | what is actually applied |
| `/PhotonStreams/latencyMs` | published | measured capture→publish |
| `/PhotonStreams/fps` | published | measured pipeline rate |

Set `enable=false` in `autonomousInit` and `true` in `disabledInit`; that is the
whole integration. Streams drop for the match and come back in the pit.

---

## mount/ — measure where the cameras are, by spinning

Tape-measuring `robotToCamera` is bad for reasons that have nothing to do with
care: the point you need is the **optical centre inside the lens**, which no ruler
reaches; the **mounting angles** matter more than the position and cannot be
measured by hand at all; and any error biases every pose estimate systematically,
so it never averages out.

Rotate the rig about a fixed vertical axis instead. Every camera traces a circle
about that same axis, so fitting all the circles **together against one shared
centre** recovers each camera's radius and bearing at once.

```sh
python3 mount/calibrate_mount.py record spin.npz 60 --host photonvision.local
python3 mount/calibrate_mount.py solve  spin.npz --origin-height 1.5415
```

Sharing the centre is what makes it usable on a real robot: with four cameras no
single camera sees tags for a whole rotation, and a camera with only a short arc
still solves because the centre is pinned by the ones that saw a long one. That
happened on the very first bench run — one camera dropped out a third of the way
through and the fit absorbed it.

### What needs an anchor

| quantity | anchor? | |
|---|---|---|
| radius, scale, relative bearing and yaw | no | differences; scale comes from the tag size |
| **Z (camera height)** | **yes** | `--origin-height`. A surveyed bench layout puts Z=0 at a **tag**, not the floor. The official FRC layout already uses true heights — pass 0. |
| **"robot forward"** | **yes** | one straight drive. Not meaningful on a bench. |

### Evening out the arc matters

Least squares weights by sample *count*, so anywhere you pause drags the fit.
Leaving a 34 s stationary tail in a 60 s run moved the radius by 4 % (344.0 →
330.5 mm) and made two cameras disagree by 2.4 mm instead of 0.5. The tool now
bins by angle and caps each bin.

### Accuracy, honestly

Bench: **±5–10 mm on position, ~1° on angles**. The bootstrap will claim ±0.3 mm —
that is *precision*, not accuracy. These pose estimates carry a viewpoint-dependent
bias of roughly ±10 mm that averaging at one viewpoint cannot remove.

Validated against a ruler: camera 1's fitted radius implied a 12.8 mm
entrance-pupil offset inside a 21 mm lens barrel — physically right. Camera 2's
tape reading implied 22.7 mm, i.e. an optical centre *behind the sensor*, so that
measurement was wrong rather than the fit. A physical constraint settled what
2500 frames could not.

---

## A bug worth knowing about

**`inputImageRotationMode` corrupts the multi-tag pose estimate** on v2026.3.4.

With `DEG_90_CCW` set, PhotonVision's multi-tag pose was wrong by **0.429 m**. With
`DEG_0`, its pose matched an independent PnP on the *same published corners and the same
layout* to **0.000 m**:

```
DEG_90_CCW : PhotonVision [+1.630 +0.323 -0.670]   independent [+2.012 +0.360 -0.480]
DEG_0      : PhotonVision [+2.013 +0.360 -0.480]   independent [+2.013 +0.360 -0.480]
```

The corners PhotonVision *publishes* stay consistent with the unrotated calibration — only
its own pose output is affected. If your camera is mounted sideways, leave rotation at
`DEG_0` and accept a sideways preview. Recalibration is not needed; the calibration was
always for the unrotated sensor image.

---

## Other notes

- `bestReprojectionError` from PhotonVision is an **aggregate over all corners**, not a
  per-corner RMS. 5.46 there corresponded to 0.211 px/corner measured independently.
- The **input stream (port 1181) is unreliable** on this build — frozen frames, and at one
  point a six-up tiled mosaic. Use port 1182 with `outputShouldDraw=false`; the drawn pose
  axes otherwise sit on top of the tags and destroy detection.
- `solvePNPEnabled` with no calibration for the active resolution makes PhotonVision throw
  a `NullPointerException` every frame and emit **nothing at all**, rather than degrading
  to 2D. Turn 3D off before switching to an uncalibrated resolution.

## Requirements

```sh
pip install msgpack websockets numpy scipy opencv-python-headless pyntcore photonlibpy
```

## License

GPLv3. See [LICENSE](LICENSE).
