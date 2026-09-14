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
