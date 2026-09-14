# Stream frames are copied, drawn and encoded even when no client is connected

## Description

PhotonVision prepares its camera streams on every frame regardless of whether
anyone is watching. On a competition robot — driver station laptop not browsing
the dashboard — this is pure overhead, and there is no way for a user to turn it
off from the UI.

Measured on a Raspberry Pi 5 with an OV9281 (CSI) at 1280x800, AprilTag pipeline,
`decimate=2`, `threads=4`, PhotonVision v2026.3.4:

| state | latency (capture→publish) | pipeline rate |
|---|---|---|
| `outputShouldShow = true` (default) | 59.7 ms | 55.9 fps |
| `outputShouldShow = false` | 41.0 ms | 60.1 fps |
| **difference** | **−18.7 ms** | **+4.3 fps** |

**~19 ms of added latency, paid whether or not a client is attached.**

Measured as 4 interleaved A/B cycles of 60 s each (ON/OFF/ON/OFF/…) so that any
drift in load or temperature affects both arms equally. The two distributions do
not overlap:

```
per-cycle latency  ON: 60.3  59.7  59.6  59.2 ms
per-cycle latency OFF: 40.9  40.7  41.4  41.1 ms
```

Latency here is `publishTimestampMicros - captureTimestampMicros` from the
pipeline's own metadata, so it is PhotonVision's own accounting, not a
measurement of the network. `decimate` was unchanged throughout and the measured
frame period stayed at 17.42 ms in every condition, so this is not a detection
effect.

## This is not just "don't watch the stream"

I first assumed the cost only applied while a browser was open. It does not.
With **zero** established connections to ports 1181, 1182 and 5800 (verified with
`ss -tn` on the coprocessor), latency was still 59.1 ms — indistinguishable from
the 60.7 ms measured with the dashboard open, and far from the 41.0 ms measured
with `outputShouldShow=false`.

So closing the dashboard does not recover the cost. The work happens regardless.

## Mechanism

`photon-core/src/main/java/org/photonvision/vision/processes/VisionRunner.java:183`

```java
frameSupplier.requestFrameCopies(settings.inputShouldShow, settings.outputShouldShow);
```

`outputShouldShow` gates whether a copy of each frame is taken for streaming.
When true, every frame is copied, then resized, converted to BGR if needed, drawn
on, and JPEG-encoded in `OutputStreamPipeline.process()` — with no check for
whether the corresponding `MjpegServer` has any active connection.

Splitting the cost by toggling `outputShouldDraw` separately: drawing the
overlays is cheap (~1.6 ms). The remaining ~17 ms is the frame copy, conversion
and encode.

## Why users cannot work around it

The dashboard's stream selector (`photon-client/src/views/CameraSettingsView.vue:36-37`)
is a multi-select that requires at least one stream to remain selected:

```js
inputShouldShow  = v.includes(0);
outputShouldShow = v.includes(1);
```

There is no "no streams" state reachable from the UI. The only way to set both
false is the websocket API directly:

```json
{"changePipelineSetting": {"cameraUniqueName": "...",
                           "inputShouldShow": false,
                           "outputShouldShow": false}}
```

Notably, the dashboard already surfaces a hint acknowledging the cost —
`CamerasCard.vue:34`, *"Stop viewing the raw stream for better performance"* —
but following that advice does not actually help, because the encode happens
whether you view it or not.

## Suggested fix

Skip the frame copy and the whole output-stream pipeline when no sink is
consuming the stream. cscore already tracks this: a `VideoSource` is disabled
when nothing is attached to it, so the condition should be available without new
plumbing. Gate `requestFrameCopies(...)` on that in addition to the
`*ShouldShow` settings.

Failing that, allowing the UI's stream selector to reach a "none" state would at
least give teams a supported way to opt out for competition.

## Impact

This affects every team on every robot, not just this hardware — any coprocessor
running PhotonVision in a match is spending time and CPU encoding video that
nothing consumes. Latency matters for pose estimation: 19 ms is compensated by
WPILib's timestamped `addVisionMeasurement`, but it still widens the window that
odometry has to bridge, and the 4.3 fps is real throughput lost.

For scale on this hardware: total detection time is 11.6 ms per frame
(`decimate=2`, measured by comparing `decimate=1` vs `decimate=2` frame times).
The stream overhead is therefore larger than the entire AprilTag detection stage
it is competing with, and larger than the saving available from the ROI/cropping
work currently under discussion (#2592, #2604, #2607).

## Environment

- PhotonVision v2026.3.4 (linuxarm64)
- Raspberry Pi 5, 4 cores, active cooling
- Arducam OV9281 global shutter, CSI, 1280x800 @ 120 fps
- AprilTag 36h11 pipeline, `decimate=2`, `threads=4`, `numIterations=40`,
  `decisionMargin=35`, `streamingFrameDivisor=1`
- 5 tags visible at 2.0–3.3 m, detected 428/428 frames
