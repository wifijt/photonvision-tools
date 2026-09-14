# Preparing streams nobody is watching costs a third of the pipeline's framerate

## Description

PhotonVision copies, converts, draws on and encodes its camera streams on every
frame even when **no client is connected**. On a Raspberry Pi 5 / OV9281 at
1280x800 this costs a third of the achievable framerate, and there is no way for
a user to turn it off from the dashboard.

Measured with `outputShouldShow` toggled and **nothing attached to any stream
port** (verified with `ss -tn` showing zero connections to 1181/1182 throughout):

| state | pipeline rate | latency (capture→publish) | frame period |
|---|---|---|---|
| `outputShouldShow = true` (default) | 60.1 fps | 42.0 ms | 17.42 ms |
| `outputShouldShow = false` | 80.5 fps | 32.7 ms | **8.71 ms** |
| **difference** | **+20.4 fps (+34%)** | **−9.2 ms** | |

3 interleaved A/B cycles of 45 s each (ON/OFF/ON/OFF/…) so drift affects both
arms equally. The distributions do not overlap:

```
per-cycle latency  ON: 41.8  41.7  42.4 ms
per-cycle latency OFF: 32.8  32.8  32.6 ms
```

**The frame period is the clearest signal.** 8.71 ms is the sensor's own period
(this camera runs at ~115 fps). With streams disabled the pipeline keeps up with
every frame the sensor produces. With streams enabled the period is exactly
2 x 8.71 ms — it is dropping every other camera frame to pay for stream
preparation.

Latency here is `publishTimestampMicros - captureTimestampMicros` from the
pipeline's own metadata, so this is PhotonVision's own accounting, not a network
measurement. `decimate` was unchanged (2) throughout.

## The cost is for nothing — a real viewer is cheap

Measured on a freshly started service, with `outputShouldShow=true` in all four
states:

| state | latency |
|---|---|
| before any client has ever connected | 42.7 ms |
| while a client is actively watching | 45.2 ms |
| 8 s after it disconnected | 42.8 ms |
| 60 s after it disconnected | 42.4 ms |

An actual viewer costs ~2.5 ms. The other ~9 ms is paid whether or not anyone is
there. So the overhead is not "streaming is expensive" — it is that the stream is
*prepared* unconditionally.

This is not a leak, either: 45 abruptly killed stream clients left latency
unchanged at 41.5-41.6 ms.

## Mechanism

`photon-core/src/main/java/org/photonvision/vision/processes/VisionRunner.java:183`

```java
frameSupplier.requestFrameCopies(settings.inputShouldShow, settings.outputShouldShow);
```

`outputShouldShow` alone gates whether a copy of each frame is taken for
streaming. When true, every frame is copied, resized, converted to BGR if needed,
drawn on and encoded in `OutputStreamPipeline.process()` — with no check for
whether the corresponding `MjpegServer` has a client.

Toggling `outputShouldDraw` separately shows the drawing itself is cheap
(~1.6 ms). The rest is the copy, conversion and encode.

## Why users cannot work around it

The dashboard's stream selector (`photon-client/src/views/CameraSettingsView.vue:36-37`)
is a multi-select that requires at least one stream to stay selected:

```js
inputShouldShow  = v.includes(0);
outputShouldShow = v.includes(1);
```

There is no "no streams" state reachable from the UI. The only way to set both
false is the websocket API:

```json
{"changePipelineSetting": {"cameraUniqueName": "...",
                           "inputShouldShow": false,
                           "outputShouldShow": false}}
```

The dashboard already surfaces a hint acknowledging a cost here —
`CamerasCard.vue:34`, *"Stop viewing the raw stream for better performance"* —
but following that advice does not help, because the preparation happens whether
you view it or not.

## Suggested fix

Skip the frame copy and the output-stream pipeline when no sink is consuming the
stream. cscore already tracks this — a `VideoSource` is disabled when nothing is
attached — so the condition should be available without new plumbing. Gate
`requestFrameCopies(...)` on it in addition to the `*ShouldShow` settings.

Failing that, letting the UI's stream selector reach a "none" state would at
least give teams a supported way to opt out for competition.

## Impact

Every team running PhotonVision in a match is paying this: the driver station is
not browsing the dashboard, so the streams have no consumer, yet a third of the
pipeline's throughput goes to preparing them. For scale on this hardware, the
entire AprilTag detection stage costs 11.6 ms per frame — comparable to the
overhead being spent on video nothing receives.

## A methodology note for anyone reproducing this

**Measure on a freshly restarted service.** A PhotonVision process that had been
running for four hours (with a lot of stream connections and setting changes
during that time) gave materially different numbers — 55.9 fps ON / 60.1 fps OFF,
versus 60.1 / 80.5 on a fresh process. The direction of the effect was the same
but the magnitude was badly distorted. I have not identified what degrades over
time; it may be worth investigating separately.

## Environment

- PhotonVision v2026.3.4 (linuxarm64)
- Raspberry Pi 5, 4 cores, active cooling, ~72 C under load
- Arducam OV9281 global shutter, CSI, 1280x800 @ 120 fps
- AprilTag 36h11 pipeline, `decimate=2`, `threads=4`, `numIterations=40`,
  `decisionMargin=35`, `streamingFrameDivisor=1`, exposure 3000 us
- 5 tags visible at 2.0-3.3 m, detected 428/428 frames
