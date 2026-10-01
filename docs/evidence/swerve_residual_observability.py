import math
from wpimath.kinematics import SwerveDrive4Kinematics, SwerveModuleState
from wpimath.geometry import Translation2d, Rotation2d

L=0.3
k=SwerveDrive4Kinematics(Translation2d(L,L),Translation2d(L,-L),
                         Translation2d(-L,L),Translation2d(-L,-L))

def residuals(true_states, reported):
    """Fit chassis speeds from REPORTED states, then see what each module's
    reported state disagrees with the fit by."""
    cs = k.toChassisSpeeds(tuple(reported))
    pred = k.toSwerveModuleStates(cs)
    out=[]
    for i,(r,p) in enumerate(zip(reported,pred)):
        # residual as a velocity VECTOR difference in module frame
        rx,ry = r.speed*math.cos(r.angle.radians()), r.speed*math.sin(r.angle.radians())
        px,py = p.speed*math.cos(p.angle.radians()), p.speed*math.sin(p.angle.radians())
        out.append((rx-px, ry-py))
    return cs,out

def show(tag, cs, res):
    print(f"{tag:38s} vx={cs.vx:+.4f} vy={cs.vy:+.4f} om={cs.omega:+.4f}")
    for i,(dx,dy) in enumerate(res):
        print(f"      module {i}  residual ({dx:+.5f},{dy:+.5f})  |r|={math.hypot(dx,dy):.5f}")

# --- case A: pure translation, module 0 CANcoder off by +2 deg -------------
true = k.toSwerveModuleStates(__import__("wpimath.kinematics",fromlist=["ChassisSpeeds"]).ChassisSpeeds(2.0,0.0,0.0))
rep = list(true)
rep[0] = SwerveModuleState(rep[0].speed, rep[0].angle + Rotation2d.fromDegrees(2.0))
cs,res = residuals(true, rep); show("A: translate, mod0 steer +2deg", cs,res)

# --- case B: ALL modules off by +2 deg (common bias) -----------------------
rep = [SwerveModuleState(s.speed, s.angle + Rotation2d.fromDegrees(2.0)) for s in true]
cs,res = residuals(true, rep); show("B: translate, ALL steer +2deg", cs,res)

# --- case C: pure rotation, module 0 steer off by +2 deg ------------------
truer = k.toSwerveModuleStates(__import__("wpimath.kinematics",fromlist=["ChassisSpeeds"]).ChassisSpeeds(0.0,0.0,2.0))
rep = list(truer)
rep[0] = SwerveModuleState(rep[0].speed, rep[0].angle + Rotation2d.fromDegrees(2.0))
cs,res = residuals(truer, rep); show("C: rotate, mod0 steer +2deg", cs,res)

# --- case D: module 0 wheel radius 2% small (reports 2% low speed) --------
rep = list(true); rep[0]=SwerveModuleState(rep[0].speed*0.98, rep[0].angle)
cs,res = residuals(true, rep); show("D: translate, mod0 radius -2%", cs,res)

# ── second experiment: module POSITION error, run as swres2 ──────────────────
# Reproduced here so one file carries the whole result.
#
# MEASURED (robotpy wpimath, 2026-09-23):
#
#   A  translate, module0 steer +2 deg   residuals 0.04420 0.02769 0.01256 0.01212
#   B  translate, ALL steer +2 deg       residuals 0.00000 0.00000 0.00000 0.00000
#   C  rotate,    module0 steer +2 deg   residuals 0.02221 0.00753 0.00728 0.00740
#   D  translate, module0 radius -2%     residuals 0.02550 0.00707 0.01581 0.00707
#
#   module POSITION error 15 mm:
#      pure translate 2 m/s   0.00000 0.00000 0.00000 0.00000
#      pure translate 4 m/s   0.00000 0.00000 0.00000 0.00000
#      pure rotate   2 rad/s  0.01912 0.01186 0.00530 0.00530
#      pure rotate   4 rad/s  0.03824 0.02372 0.01061 0.01061
#
# CONCLUSIONS, which the spec depends on:
#   1. A COMMON steer bias is invisible to the drivetrain (B is exactly zero) -
#      the fit absorbs it as a rotated chassis velocity, vy = 2*sin(2deg) =
#      0.0698. It can only be seen against vision.
#   2. PER-MODULE deviations are fully observable from the drivetrain ALONE.
#      No vision, no tags, no field, no survey.
#   3. Steer error and POSITION error are separable by signature: position
#      error is EXACTLY ZERO under pure translation and scales with omega;
#      steer error is present under pure translation and scales with |v|.
#   4. Swerve is over-determined - 8 measurements for 3 chassis DOF - so five
#      residual dimensions per sample exist and toChassisSpeeds discards them.
