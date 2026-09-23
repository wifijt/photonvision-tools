import math, numpy as np
from wpimath.kinematics import SwerveDrive4Kinematics, SwerveModuleState, ChassisSpeeds
from wpimath.geometry import Translation2d, Rotation2d
L=0.3
MOD=[Translation2d(L,L),Translation2d(L,-L),Translation2d(-L,L),Translation2d(-L,-L)]
k=SwerveDrive4Kinematics(*MOD)

# ---- 1. how many residual dimensions actually survive the steer loop? -------
# Steer loop pins reported angle = commanded angle. So the ONLY free per-module
# measurement is the scalar rolling speed. 4 scalars, 3 chassis DOF.
for tag,cs in [("translate",ChassisSpeeds(2,0,0)),("spin",ChassisSpeeds(0,0,2)),
               ("arc",ChassisSpeeds(2,0.7,1.1))]:
    cmd=k.toSwerveModuleStates(cs); A=[]
    for t,c in zip(MOD,cmd):
        a=c.angle.radians()
        # d(rolling speed)/d(vx,vy,omega)
        A.append([math.cos(a), math.sin(a), -t.Y()*math.cos(a)+t.X()*math.sin(a)])
    A=np.array(A)                       # 4 measurements x 3 unknowns
    sv=np.linalg.svd(A,compute_uv=False)
    print(f"{tag:10s} 4 speeds x 3 DOF  singular values {np.round(sv,4)}  rank {np.linalg.matrix_rank(A)}"
          f"  -> residual dims = {4-np.linalg.matrix_rank(A)}")

# ---- 2. THE BOWED BOARD: sign-correlated pair error -------------------------
def reported(cs, deltas):
    cmd=k.toSwerveModuleStates(cs); out=[]
    for t,c,d in zip(MOD,cmd,deltas):
        a_cmd=c.angle.radians(); a_phys=a_cmd-d
        vx=cs.vx-cs.omega*t.Y(); vy=cs.vy+cs.omega*t.X()
        out.append(SwerveModuleState(vx*math.cos(a_phys)+vy*math.sin(a_phys), Rotation2d(a_cmd)))
    return out
def resid(st):
    cs=k.toChassisSpeeds(tuple(st)); pr=k.toSwerveModuleStates(cs); r=[]
    for a,b in zip(st,pr):
        ax,ay=a.speed*math.cos(a.angle.radians()),a.speed*math.sin(a.angle.radians())
        bx,by=b.speed*math.cos(b.angle.radians()),b.speed*math.sin(b.angle.radians())
        r.append(math.hypot(ax-bx,ay-by))
    return r
D=math.radians(1.0)
print()
print("bowed board held against a PAIR - errors come sign-correlated:")
for tag,dl in [("FL +1, FR -1 (board bowed) ",[D,-D,0,0]),
               ("FL +1, FR +1 (same sign)   ",[D,D,0,0]),
               ("FL +1 only                 ",[D,0,0,0]),
               ("all four +1                ",[D,D,D,D])]:
    print(f"  {tag} translate residuals " + "  ".join(f"{v:.6f}" for v in resid(reported(ChassisSpeeds(2,0,0),dl))))

# MEASURED 2026-09-23 (robotpy wpimath). Two results, both bad for the spec.
#
# 1. HOW THIN THE INSTRUMENT ACTUALLY IS.
#    The spec says "8 numbers, 3 DOF, 5 residual dimensions discarded". Wrong.
#    The steer loop pins every reported angle to its command, so 4 of those 8
#    numbers carry no fault information at all. What is left is 4 scalar rolling
#    speeds against 3 chassis DOF:
#
#      translate  singular values [2.0  0.6  0.0 ]  rank 2 -> 2 residual dims
#      spin       singular values [1.41 1.41 0.85]  rank 3 -> 1 residual dim
#      arc        singular values [1.98 0.59 0.31]  rank 3 -> 1 residual dim
#
#    ONE to TWO residual dimensions per sample, not five. Under pure translation
#    the vy column is identically zero (all modules parallel), which is why the
#    rank drops to 2. Twelve live unknowns (8 position + 4 radius) have to be
#    identified from that, over many samples in varied motion.
#
# 2. THE BOWED BOARD IS A BLIND SPOT - the mentor's most likely real fault.
#    The board is held against a PAIR of wheels, so the pair's errors arrive
#    correlated. Translate at 2 m/s:
#
#      FL +1, FR -1 (bowed)    0.000152  0.000152  0.000152  0.000152
#      FL +1, FR +1 (same)     0.000152  0.000152  0.000152  0.000152
#      FL +1 only              0.000194  0.000054  0.000120  0.000054
#      all four +1             0.000000  0.000000  0.000000  0.000000
#
#    A PAIR error produces a residual that is UNIFORM ACROSS ALL FOUR MODULES.
#    A flat residual names nobody - there is no odd one out to find - and at
#    1.5e-4 it is ~170x below the per-module radius signal anyway. Both wheels
#    scrub for the whole match, the tread wears, and every observable the spec
#    defines reads clean.
#
#    (An adversarial audit reported this case as "exactly zero". It is not
#    exactly zero; it is uniformly 1.5e-4. Operationally the same - uniform
#    means unattributable - but the measured number belongs in the record.)
