# Tonight's card

Short version, in order. No explanations — those are in README.md and SETUP.md.
Replace `PI` with the Pi's **IP address** (not `photonvision.local`; mDNS usually
fails on a team network — find it with `hostname -I` on the Pi).

---

## 0. Laptop setup, once

```sh
pip install -r requirements.txt -r requirements-survey.txt
```

No internet? See [SETUP.md](SETUP.md), section 0b.

---

## 1. photontune onto the Pi

```sh
scp -r photontune-offline-full photon@PI:/tmp/        # password: vision
ssh photon@PI
sudo bash /tmp/photontune-offline-full/install.sh
```

Check it, no camera needed:

```sh
python3 /opt/photontune/sabotage_test.py --verdict-matrix
```

Safe first real command — asserts settings, no exposure sweep:

```sh
python3 /opt/photontune/photontune.py --host 127.0.0.1 --baseline-only
```

**STOP if that fails.** Nothing below will work.

---

## 2. Calibrate each camera

⚠️ **Do this at the resolution you will actually run.** A camera with no
calibration for its active resolution publishes **nothing at all** — it looks
exactly like a dead camera.

On the laptop. Open the mirrored view first so left is left:

```sh
python3 calib/calib_view.py --host PI
```
→ open <http://localhost:8078>, pick the camera.

Rehearse (captures nothing, writes nothing):

```sh
python3 calib/charuco_capture.py --host PI --camera "NAME" --dry-run
```

For real — **check the board numbers against your printed sheet**:

```sh
python3 calib/charuco_capture.py --host PI --camera "NAME" \
    --width 8 --height 8 --square 1.0 --marker 0.75 --family 4x4 --end
```

- Watch the **kept** count, not the shot count.
- Nothing is written until `--end`. Ctrl-C costs you nothing.
- Repeat per camera.

---

## 3. New field layout

⚠️ **Turn PhotonVision's NT server ON first, or steps 3 and 4 silently see no
data** — they are NetworkTables *clients* and there is no roboRIO on a bench.

```sh
ssh photon@PI
sudo python3 /path/to/config/set_nt_server.py true
sudo systemctl restart photonvision          # required; config is read at boot
```

⚠️ **Measure a printed tag with a ruler.** `tag_size` is the only thing setting
the scale of your whole field. 1% wrong print = 1% wrong field, undetectable.

On the laptop:

```sh
python3 survey/photon_calib.py --host PI                    # sanity-check intrinsics
python3 survey/capture_corners.py --host PI frames.json     # move the camera around
python3 survey/solve_layout.py frames.json layout.json 0.1651   # <- YOUR measured size
```

While capturing: every tag must be seen **together with at least two others**,
from **more than one** viewpoint. A tag seen on one link gets placed with false
confidence (DEFECTS #9).

Upload:

```sh
curl -F "data=@layout.json" http://PI:5800/api/settings/apriltagFieldLayout
```

---

## 4. Robot camera offsets

Spin the robot in place, slowly, two full turns, tags in view the whole time.

```sh
python3 mount/calibrate_mount.py record spin.npz 60
python3 mount/calibrate_mount.py solve  spin.npz
```

Gives each camera's radius and bearing from the spin axis, and the
camera-to-camera yaw. **"Robot forward" needs one extra straight drive** — this
step gives you everything else.

Sanity-check the reported radius against a tape from the pivot to the lens.

---

## 5. Before the Pi meets a roboRIO

```sh
sudo python3 config/set_nt_server.py false
sudo systemctl restart photonvision
```

Two NT servers on one network fight each other.

---

# Watching and triggering photontune from a dashboard

photontune publishes under **`/PhotonTune`**. Set `run` true to start a tune.

| topic | |
|---|---|
| `run` | **set true to start**; clears itself when finished |
| `busy` | true while tuning |
| `progress` | 0→1 across all cameras |
| `ok` | **go / no-go for the last run** |
| `summary` | `OV9281=863@g40`, or `FAILED: ...` |
| `status` | live line |
| `warnings` | not failures, but read them |
| `heartbeat` | ticks constantly — **frozen means the service is dead** |
| `camera` | which camera, "2 of 3" |
| `result` | full JSON |

## Elastic — use this one to TRIGGER it

1. Connect Elastic to the robot (team number, or the Pi's IP as the NT server).
2. Drag `/PhotonTune/run` onto the layout → change the widget type to
   **Toggle Switch**. That one is writable; flipping it sets the topic true.
3. Add `ok`, `summary`, `status`, `progress` and `heartbeat` as Text Displays.

## AdvantageScope — displays it, but CANNOT trigger it

AdvantageScope shows every `/PhotonTune` topic fine. Add them from the sidebar;
`progress` and `heartbeat` suit a line graph, the rest a table.

**It cannot flip `run`.** Editing requires Tuning Mode, and Tuning Mode only
edits fields published under the **`/Tuning`** table using the
**"NetworkTables 4 (AdvantageKit)"** live source. `/PhotonTune/run` is not under
`/Tuning`, so it is read-only there.

So: **watch in AdvantageScope, trigger in Elastic** — or in Shuffleboard or
Glass, which also write.

(If you want AdvantageScope to trigger it, photontune would have to publish a
mirror of `run` under `/Tuning/`. Not done; ask and it is a small change.)

**Check `heartbeat` before you press anything.** If the daemon has died, `ok`
and `busy` hold their last values forever and the button does nothing.
