# From a fresh PhotonVision image to a tuned rig

Written 2026-09-21, against PhotonVision **v2026.3.4** on a Raspberry Pi 5 with
CSI (MIPI) OV9281 cameras. Every command here was run on that hardware.

Two machines are involved. **The Pi** runs PhotonVision and photontune. **Your
laptop** runs the calibration tools, because calibration is a job for someone
standing in front of the camera holding a board.

---

## 0. What you need

**Nothing below ships on the PhotonVision image** — not even `pip3` — and the
package lists are stale on a fresh boot. Checked against the apt history on
this Pi: every one of these was installed after the fact.

On the **Pi**:

```
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3-msgpack python3-websockets python3-pip
sudo pip3 install --break-system-packages pyntcore photonlibpy
```

`msgpack` and `websockets` are needed by everything. `pyntcore` is needed only
by photontune's daemon mode. `photonlibpy` is optional and expensive to skip:
without it photontune cannot decode detections off NetworkTables and falls back
to the websocket at ~9 results/s instead of ~43. `numpy` arrives with
photonlibpy.

Confirm:

```
python3 -c "import msgpack, websockets, ntcore, photonlibpy, numpy; print('ok')"
```

On your **laptop**, only two:

```
pip3 install msgpack websockets
```

The laptop tools talk to PhotonVision over its websocket. They do **not** need
ntcore, photonlibpy, or ssh.

---

## 0b. Installing with no internet (a private network, or an event)

The commands above need the internet. A team network usually has none, and a
competition field certainly has none. **Build the bundle at home, carry it in.**

### One thing you must do while you still have internet

**Install `pip3` on the Pi.** The PhotonVision image does not ship it, and
bootstrapping pip itself offline on Debian is genuinely awkward. Do this once,
at home, and the bundle below covers everything afterwards:

```
sudo apt-get update && sudo apt-get install -y --no-install-recommends python3-pip
```

### Pick a tier first — photontune needs far less than everything

photontune degrades deliberately, so you may not need the big bundle at all.
Measured 2026-10-01 for Python 3.11 / aarch64:

| tier | gets you | packages | size |
|---|---|---|---|
| **1** | the CLI — `photontune.py --host ...`, a real tune | `msgpack websockets` | **2 wheels, 680 KB** |
| **2** | **+ daemon mode**, the `PhotonTune` NT table, dashboard trigger | `+ pyntcore` | **8 wheels, 6.4 MB** |
| **3** | + detections read over NT instead of the websocket | `+ wpilib robotpy-apriltag photonlibpy` | **18 wheels, 20 MB** |

**Tier 2 is what the systemd unit needs**, because it runs `--daemon`. Tier 1
alone still gives you a working tuner you can run by hand over ssh.

**Tier 3 buys speed, nothing else.** Without `photonlibpy` photontune logs
`photonlibpy not available - sampling will use the websocket` and carries on at
~9 results/s instead of ~43. Every trial is then built on 4.8x fewer frames, so
the answers are noisier, not wrong. If 14 MB of carry weight is awkward, skip
it and accept a slower, less certain tune.

### Build the bundle, on a machine with internet

This works from a Mac or any machine — you are downloading *for* the Pi, not
installing. **The platform tags are the part that wastes an evening.** The
robotpy wheels are built for newer glibc than `manylinux2014`
(`manylinux_2_35_aarch64` for `pyntcore` and `wpilib`, `manylinux_2_34_aarch64`
for the `robotpy-native-*` packages), so a plain `--platform
manylinux2014_aarch64` silently matches nothing and pip only tells you no
distribution was found. Pass all three.

Tier 1 or 2 — one command, no special cases:

```sh
mkdir photontune-offline && cd photontune-offline

pip3 download --only-binary=:all: --python-version 311 \
  --platform manylinux_2_35_aarch64 \
  --platform manylinux_2_34_aarch64 \
  --platform manylinux2014_aarch64 \
  -d . msgpack websockets pyntcore          # drop pyntcore for tier 1
```

Tier 3 adds a second command, because `photonlibpy` has to be taken on its own:

```sh
pip3 download --only-binary=:all: --python-version 311 \
  --platform manylinux_2_35_aarch64 \
  --platform manylinux_2_34_aarch64 \
  --platform manylinux2014_aarch64 \
  -d . wpilib robotpy-apriltag

pip3 download --only-binary=:all: --python-version 311 \
  --platform manylinux_2_35_aarch64 \
  --platform manylinux_2_34_aarch64 \
  --platform manylinux2014_aarch64 \
  --no-deps -d . photonlibpy
```

**Why `photonlibpy` needs `--no-deps`.** It declares
`opencv-python; platform_machine != "roborio"`, and resolving the whole set
together fails outright. photontune never touches OpenCV — the decode path
imports `hal, native, ntcore, photonlibpy, robotpy_apriltag, wpilib, wpimath,
wpinet, wpiutil` and no `cv2` at all, checked by importing it and listing what
came in. So skip it. OpenCV does have aarch64 wheels
(`cp37-abi3-manylinux2014_aarch64`) if you want it for something else later.

### Install it on the Pi

```sh
scp -r photontune-offline photonvision:/tmp/
ssh photonvision
  # tier 1 or 2 - name only what you downloaded
  sudo pip3 install --break-system-packages --no-index \
      --find-links /tmp/photontune-offline msgpack websockets pyntcore

  # tier 3 only
  sudo pip3 install --break-system-packages --no-index \
      --find-links /tmp/photontune-offline wpilib robotpy-apriltag
  sudo pip3 install --break-system-packages --no-index --no-deps \
      --find-links /tmp/photontune-offline photonlibpy
```

`--no-index` is what matters: without it pip will try to reach PyPI, hang on the
timeout, and fail with a network error instead of using the files in front of it.

Confirm:

```sh
python3 -c "import msgpack, websockets, ntcore, photonlibpy, numpy; print('ok')"
```

### If there is no pip3 at all and no way to get it

A wheel is a zip file. Unpack them and point Python at the result:

```sh
mkdir -p /opt/pylibs && cd /opt/pylibs
for w in /tmp/photontune-offline/*.whl; do unzip -oq "$w"; done
PYTHONPATH=/opt/pylibs python3 /opt/photontune/photontune.py --host 127.0.0.1
```

Compiled extensions work this way too — they are just `.so` files inside the
zip. Add `Environment=PYTHONPATH=/opt/pylibs` to the systemd unit to make it
stick. This is a last resort, not the recommended path.

### The laptop side needs almost nothing

The calibration and measurement tools need only `msgpack`, `websockets` and
`numpy`, and your laptop is the machine most likely to have internet anyway.
If it does not:

```sh
pip3 download -d laptop-offline msgpack websockets numpy   # on a connected machine
pip3 install --no-index --find-links laptop-offline msgpack websockets numpy
```

No `--platform` flags here, because you are installing on the same kind of
machine you downloaded on.

---

## 1. Get the cameras seen (CSI/MIPI only)

**This is the step that eats an evening if you skip it.**
`camera_auto_detect=1` handles the official Raspberry Pi cameras and *silently*
fails on everything else, including the OV9281. No error appears anywhere — the
camera simply is not there.

On the Pi:

```
python3 setup/csi_cameras.py --show
```

If your sensors are not listed under "physically bound to the kernel", write the
config (in connector order, `cam0` first):

```
sudo python3 setup/csi_cameras.py --set ov9281,ov9281
sudo reboot
```

After the reboot:

```
python3 setup/csi_cameras.py --verify --expect 2
```

It exits non-zero if PhotonVision cannot see them, or if a camera has no
calibration for its active resolution — see step 2 for why that matters.

What it writes, and why, is just:

```
camera_auto_detect=0
dtoverlay=ov9281,cam0
dtoverlay=ov9281,cam1
```

A timestamped backup of `config.txt` is made first, and anything replaced is
commented out rather than deleted.

**A USB camera needs none of this.** It is only the CSI path.

---

## 2. Calibrate each camera, at the resolution you will actually run

A camera with no calibration for its **active** resolution is worse than
useless: with `solvePNPEnabled` on, PhotonVision publishes **nothing at all** —
not a 2D fallback — so it looks exactly like a dead camera. Calibrate the
resolution you intend to run, and if you change resolution later, calibrate that
one too.

This is done from **your laptop**, because you are the one holding the board.

**First, open the mirrored view.** You stand facing the camera, so moving the
board to your left puts it on the right of the image and every correction you
make is backwards.

```
python3 calib/calib_view.py --host photonvision.local
# then open http://localhost:8078
```

Pick the camera with the buttons at the top. The mirror is display-only — the
frames PhotonVision calibrates from are untouched, which matters, because a
flipped image would produce a flipped calibration.

**Rehearse without capturing anything:**

```
python3 calib/charuco_capture.py --host photonvision.local --camera "OV9281" --dry-run
```

**Then for real.** Check the board arguments match the sheet you actually
printed — a size mismatch produces a confident, wrong calibration:

```
python3 calib/charuco_capture.py --host photonvision.local --camera "OV9281" \
    --width 8 --height 8 --square 1.0 --marker 0.75 --family 4x4 --end
```

It walks a 12-phase coverage plan and shoots on a timer so your hands stay on
the board. Watch the "kept" count, not the shot count: a snapshot where the
board was not fully found is discarded silently.

**Nothing is written until `--end`.** Abandon a bad session by not passing it,
or by pressing Ctrl-C, and the existing calibration survives.

Repeat for the second camera. To calibrate a *different* resolution, add
`--mode <index>`. The index is the position in the dashboard's resolution
dropdown, counting from 0; `--dry-run` prints the mode and the resolution it
resolves to, so confirm there before shooting.

---

## 3. Install photontune on the Pi

From the photontune repo on your laptop:

```
scp photontune.py sabotage_test.py photonvision:/tmp/
ssh photonvision
  sudo mkdir -p /opt/photontune
  sudo cp /tmp/photontune.py /tmp/sabotage_test.py /opt/photontune/
  sudo chmod 755 /opt/photontune/photontune.py
```

Check it runs before making it a service:

```
python3 /opt/photontune/photontune.py --help
python3 /opt/photontune/photontune.py            # a real tune, ~45 s
```

Then the unit, `/etc/systemd/system/photontune.service`:

```ini
[Unit]
Description=PhotonTune - boot baseline + NetworkTables-triggered tuner
After=network-online.target photonvision.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/photontune
# -u matters: without it Python block-buffers stdout to the journal pipe and
# SIGTERM discards the buffer, so the daemon logs nothing for its whole life.
ExecStart=/usr/bin/python3 -u /opt/photontune/photontune.py \
    --daemon \
    --host 127.0.0.1 \
    --nt-server 127.0.0.1 \
    --nt-table PhotonTune \
    --boot-timeout 90
Restart=on-failure
RestartSec=5
Nice=5

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl daemon-reload
sudo systemctl enable --now photontune
journalctl -u photontune -f
```

At boot it asserts the **structural baseline only** — about one second, and it
cannot leave a camera mid-sweep. It does not tune at boot: pit light is not
field light, and a 45-second sweep through blind exposures while the robot is
on a cart about to be enabled is the wrong default.

**On a robot, point `--nt-server` at the roboRIO** (`--team <number>`, or
`10.TE.AM.2`). That is also the fast path: photontune reads detections over
NetworkTables at roughly 4.8x the websocket rate.

---

## 4. Tuning at the field

Trigger it from your dashboard. Set `/PhotonTune/run` to **true** and watch:

| key | what it gives you |
|---|---|
| `busy` | true while tuning |
| `camera` | which camera, "1 of 2" |
| `progress` | 0 → 1 |
| `status` | what it is doing right now |
| `ok` | **the go / no-go for the last run** |
| `summary` | one short line, e.g. `OK OV9281 863us g40 \| OV9281 (1) 860us g20` |
| `warnings` | things that are not failures but a human should know |
| `result` | the full JSON, if you want it |
| `heartbeat` | proves the service is alive, ticks through a run |

Tune **where you will play** — it takes about 45 seconds and it is measuring
your light.

---

## 5. Bench vs competition

One setting differs, and it matters.

**Bench:** turn PhotonVision's own NT server **on** (dashboard → Settings →
`runNTServer`). Nothing else on the bench provides one, and with it photontune
samples at ~43 results/s instead of ~9.

**Competition:** turn it **off** before the Pi meets a roboRIO. Two NT servers
on one network fight each other.

photontune never changes this setting itself — it uses a server if one is there
and falls back to the websocket if not.

---

## 6. Check it works

```
python3 /opt/photontune/sabotage_test.py          # breaks 15 settings, checks each is repaired
python3 /opt/photontune/sabotage_test.py --verdict-matrix --cli-smoke
```

The first takes about 90 seconds and leaves your cameras as it found them.

---

## Known traps

- **Never set Orientation to 90° or 270° on a CSI camera.** PhotonVision rotates
  the calibration but not the image on the libcamera path, so the pose is wrong
  by ~0.25 m while the preview and the published corners look perfect.
  Filed upstream as PhotonVision #2613. Put the rotation in `robotToCamera`
  instead. photontune asserts `DEG_0` and repairs it.
- **Never select an unused placeholder pipeline.** Selecting pipeline index 0 on
  this rig put PhotonVision into throwing `NullPointerException: "src" is null`
  on every dispatch, and it stopped broadcasting entirely.
- **`/api/settings/general` resets the network interface** and restarts the web
  server. It is the only way to toggle `runNTServer` programmatically. Do it
  from the dashboard, not a script, and never over the link it is resetting.
- **`libcamera-hello` is broken on this image** (`undefined symbol:
  _ZN7libpisp22compute_optimal_stride...`). Use `csi_cameras.py --show`.
- `DEFECTS.md` in the photontune repo lists what is still wrong and what to do
  about it.
