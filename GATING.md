# Using vision to correct odometry — and rejecting bad measurements

Notes on treating AprilTag vision as a *correction* to odometry rather than a primary
position sensor, and on the filtering that makes that safe.

All numbers here were measured on a Raspberry Pi 5 + OV9281 at 1280x800, `fx ≈ 1106 px`.
The shapes generalise; re-derive the values for your own camera.

---

## The model

Odometry and gyro are excellent in the short term and drift slowly. Vision is precise but
occasionally, confidently wrong. So use odometry as the continuous estimate and vision as
an intermittent correction.

Typical drift over a 15-second autonomous:

| source | drift |
|---|---|
| swerve odometry | 1–2% of distance — so 10–20 cm over a 10 m path |
| gyro yaw (NavX / Pigeon 2) | ~1–2 deg per minute → 0.25–0.5 deg over 15 s |
| that heading error, over a 5 m drive | 2–4 cm lateral |

Measured multi-tag jitter, stationary: **0.65 mm**.

**Vision is roughly 100x better than the drift it is correcting.** You therefore do not
need it often. You need it to be *right* when you use it.

---

## The principle everything follows from

> **A bad correction is worse than no correction.**

If odometry has you within 10 cm and vision injects a pose that is 2 m off, you have made
things dramatically worse. Observed on a real rig: a single tag confidently reported
itself **0.33 m away when it was 2.4 m** — a 2 m error, stable and repeatable, from a
detection that looked perfectly healthy.

So the design goal is not "get more vision measurements." It is **"never accept a bad
one."** Throwing away good measurements is nearly free; accepting one bad one is not.

---

## The gates

Apply all of them. Each catches a failure the others miss.

### 1. Agreement with odometry — the most valuable gate

```java
if (visionPose.getTranslation().getDistance(odometryPose.getTranslation()) > 1.0) {
    // reject - disagrees with where we know we are
}
```

You know roughly where you are already. Any vision pose that disagrees with odometry by
more than plausible drift is wrong, whatever it claims about itself.

**This single check catches almost everything** — ambiguity flips, a wrong field layout, a
coordinate-convention mistake, the rotation bug described below. Odometry is the sanity
check on vision precisely because it is good in the short term.

Size the threshold from your drift: start around 1 m, tighten as you gain confidence. Too
tight and you reject the corrections you need after a collision, which is exactly when you
need them most.

### 2. Freshness

```java
if (Timer.getFPGATimestamp() - result.getTimestampSeconds() > 0.25) { reject; }
```

**Every other check passes happily on stale data.** If the coprocessor crashes, the last
value sits in NetworkTables looking perfect — solving, low reprojection, plenty of tags.
A frozen good number is indistinguishable from a live one unless you check the clock.

### 3. Multi-tag, or ambiguity if single-tag

Prefer a multi-tag solve. If you only have one tag:

```java
if (target.getPoseAmbiguity() > 0.2) { reject; }
```

Single-tag PnP on a face-on tag is genuinely ambiguous — two solutions fit nearly equally
well and it flips between them. Measured on one rig: **96% of single-tag detections were
above the 0.2 threshold**, and half were above 0.5, essentially a coin flip.

Ambiguity is a function of viewing angle. The same tag read 0.962 face-on and 0.015 from
an angle. It is not a camera quality problem.

### 4. Reprojection error (multi-tag)

```java
if (multiTagResult.estimatedPose.bestReprojErr / numTagsUsed > 0.3) { reject; }
```

Reprojection is the best available measure of *self-consistency* — does this pose actually
explain the pixels? Measured: **0.037 per tag when healthy, 1.44 per tag with a corrupted
layout.** A 38x separation.

This is the gate that caught a PhotonVision bug where `inputImageRotationMode` silently
corrupted the pose by 43 cm while detection looked perfect
([issue #2613](https://github.com/PhotonVision/photonvision/issues/2613)).

Note: PhotonVision's `bestReprojectionError` is an **aggregate over all corners**, not a
per-corner RMS — normalise by tag count before comparing to a threshold.

### 5. Plausibility

```java
if (pose.getX() < 0 || pose.getX() > FIELD_LENGTH ||
    pose.getY() < 0 || pose.getY() > FIELD_WIDTH ||
    Math.abs(pose.getZ()) > 0.5) { reject; }   // robots are on the floor
```

Cheap, and catches gross layout or convention errors immediately.

### 6. Expected tags, if you have a planned route

In autonomous you know where you are going, so you know which tags *should* be visible.
A tag you should not be able to see is a misread — reject it, or at least do not let it
drive a correction on its own.

---

## Trust scaling, not just accept/reject

Once a measurement passes, tell the estimator how much to believe it. Distance is the
main driver — angular error becomes position error in proportion to range:

```java
double stdDev = BASE_STD_DEV * avgTagDistance * avgTagDistance / numTags;
estimator.addVisionMeasurement(visionPose, timestamp,
        VecBuilder.fill(stdDev, stdDev, headingStdDev));
```

Trust close, multi-tag observations far more than distant single-tag ones. If you trust
your gyro (you probably should), give heading a very large standard deviation so vision
corrects position while the gyro keeps heading.

---

## Log the rejections

Publish a counter per gate. When localization misbehaves you want to know *which* gate
fired, not just that vision "wasn't working".

A silent gate that rejects everything looks exactly like a camera that sees nothing.

---

## Still unmeasured

Every figure above was measured with a **stationary** camera. Multi-tag jitter while
moving at speed is unknown, and that is the number that decides how tight gate 1 can be.

It is a small experiment once you have a robot: drive a known path, log vision pose
against odometry, look at the disagreement distribution. Set the gate from the real
distribution rather than a guess.

Related: [SETTINGS.md](SETTINGS.md) for the camera settings these measurements assume.
