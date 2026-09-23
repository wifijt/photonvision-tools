import math
from wpimath.kinematics import SwerveDrive4Kinematics, SwerveModuleState, ChassisSpeeds
from wpimath.geometry import Translation2d, Rotation2d
L=0.3
k=SwerveDrive4Kinematics(Translation2d(L,L),Translation2d(L,-L),
                         Translation2d(-L,L),Translation2d(-L,-L))
MOD=[Translation2d(L,L),Translation2d(L,-L),Translation2d(-L,L),Translation2d(-L,-L)]

def module_vel(cs, t):
    return (cs.vx - cs.omega*t.Y(), cs.vy + cs.omega*t.X())

def reported(cs_actual, cs_cmd, deltas):
    """Physically honest. The steer loop drives the REPORTED angle to the
    commanded angle, so the wheel physically sits at (cmd - delta). The drive
    encoder only sees the component of the module's true velocity ALONG the
    wheel's physical heading; the perpendicular part is scrub and is not
    measured at all."""
    cmd = k.toSwerveModuleStates(cs_cmd)
    out=[]
    for i,(t,c,d) in enumerate(zip(MOD,cmd,deltas)):
        a_cmd = c.angle.radians()
        a_phys = a_cmd - d                      # where the wheel really points
        vx,vy = module_vel(cs_actual, t)
        s = vx*math.cos(a_phys) + vy*math.sin(a_phys)   # rolling component only
        out.append(SwerveModuleState(s, Rotation2d(a_cmd)))  # reports cmd angle
    return out

def resid(states):
    cs = k.toChassisSpeeds(tuple(states))
    pred = k.toSwerveModuleStates(cs)
    r=[]
    for a,b in zip(states,pred):
        ax,ay=a.speed*math.cos(a.angle.radians()),a.speed*math.sin(a.angle.radians())
        bx,by=b.speed*math.cos(b.angle.radians()),b.speed*math.sin(b.angle.radians())
        r.append(math.hypot(ax-bx,ay-by))
    return cs,r

D=math.radians(1.0)
print("ONE module's CANcoder physically off by 1 deg (module 0)\n")
for tag,cmd in [("drive straight 2 m/s ",ChassisSpeeds(2,0,0)),
                ("strafe 2 m/s         ",ChassisSpeeds(0,2,0)),
                ("spin 2 rad/s         ",ChassisSpeeds(0,0,2)),
                ("arc 2 m/s + 2 rad/s  ",ChassisSpeeds(2,0,2))]:
    cs,r = resid(reported(cmd, cmd, [D,0,0,0]))
    print(f"  {tag} odo: vx={cs.vx:+.4f} vy={cs.vy:+.4f} om={cs.omega:+.5f}")
    print(f"      residuals  " + "  ".join(f"{v:.6f}" for v in r))
print()
print("control: all four modules off by 1 deg")
cs,r = resid(reported(ChassisSpeeds(2,0,0), ChassisSpeeds(2,0,0), [D]*4))
print(f"  odo: vx={cs.vx:+.4f} vy={cs.vy:+.4f} om={cs.omega:+.5f}")
print(f"  residuals  " + "  ".join(f"{v:.6f}" for v in r))

# ── WHY THIS FILE EXISTS: it retracts part of swerve_residual_observability.py
#
# That file perturbed the REPORTED module angle while leaving the reported speed
# at full magnitude, and concluded a 2 deg CANcoder error gives a 0.044 residual.
# That is not a physical scenario. A wheel pointing 2 deg off cannot also roll at
# the full chassis speed - the drive encoder only ever sees the component of the
# module's velocity ALONG the wheel's physical heading, and the perpendicular
# part is scrub, which nothing measures.
#
# More importantly, the STEER LOOP CLOSES ON THE CANCODER. It drives the
# REPORTED angle to the commanded angle, so the reported angle always equals the
# command and the misalignment never appears there at all. What is left is a
# cos(delta) deficit on the reported speed.
#
# MEASURED, physically honest (robotpy wpimath, 2026-09-23):
#
#   ONE module's CANcoder physically off by 1 deg
#     drive straight 2 m/s   residuals  0.000194  0.000054  0.000120  0.000054
#     spin 2 rad/s           residuals  0.000065  0.000046  0.000046  0.000000
#
#   for comparison, same harness:
#     module 0 wheel radius -2%, translate 2 m/s   0.025495 0.007071 0.015811 ...
#     module 0 mounted 15 mm off, translate        0.000000 0.000000 0.000000 ...
#     module 0 mounted 15 mm off, spin 2 rad/s     0.010607 0.007500 0.007500 ...
#
# 1 - cos(1 deg) = 1.52e-4, times 2 m/s = 3.0e-4. That is the entire effect.
#
# THE PRINCIPLE, which predicts all of the above:
#   A CLOSED LOOP HIDES THE ERROR IN THE SENSOR IT CLOSES ON.
#   - steer loop closes on the CANcoder  -> CANcoder misalignment INVISIBLE
#   - no loop corrects distance-per-rotation -> wheel radius VISIBLE
#   - nothing closes a loop on where a module is bolted -> position VISIBLE
#     (in rotation only; exactly zero under pure translation)
#
# CONSEQUENCE: a mis-zeroed CANcoder - the single most common real fault, since
# the wheels are zeroed with a bowed board held by eye - CANNOT be found from
# the drivetrain alone. It needs a reference OUTSIDE the steer loop: vision
# (the course actually taken, delta - psi), the gyro, or possibly steer motor
# current, since a fighting module is held against a lateral scrub load.
# The current idea is UNTESTED - physics, not a measurement.
