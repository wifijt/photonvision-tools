# From a fresh PhotonVision image to a tuned rig

Written 2026-09-21, against PhotonVision **v2026.3.4** on a Raspberry Pi 5 with
CSI (MIPI) OV9281 cameras. Every command here was run on that hardware.

Two machines are involved. **The Pi** runs PhotonVision and photontune. **Your
laptop** runs the calibration tools, because calibration is a job for someone
standing in front of the camera holding a board.

---

## 0. What you need

On the **Pi** — all of these are already present on the PhotonVision image
except where noted:

```
python3 3.11          msgpack   websockets   numpy
ntcore + photonlibpy  (robotpy-native-ntcore, photonlibpy)
smbus2                (only if you want i2c probing; not required)
```

Check with:

```
python3 -c "import msgpack, websockets, ntcore, photonlibpy, numpy; print('ok')"
```

If anything is missing: `pip3 install msgpack websockets photonlibpy` (numpy
and ntcore arrive with photonlibpy's dependencies).

On your **laptop**, only two:

```
pip3 install msgpack websockets
```

The laptop tools talk to PhotonVision over its websocket. They do **not** need
ntcore, photonlibpy, or ssh.

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
`--mode <index>` — `csi_cameras.py --show` lists the modes.

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
