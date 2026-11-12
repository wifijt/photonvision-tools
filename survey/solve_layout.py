#!/usr/bin/env python3
#
# Copyright (C) 2026  wifijt
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  This program is distributed WITHOUT ANY WARRANTY; see the GNU
# General Public License at <https://www.gnu.org/licenses/> for details.
#
"""Reprojection bundle adjustment on PhotonVision's corners -> AprilTagFieldLayout."""
import sys,json,numpy as np,cv2
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
import argparse, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_ap=argparse.ArgumentParser(
    description="Bundle-adjust recorded corners into an AprilTagFieldLayout.")
_ap.add_argument("frames", help="json from capture_corners.py")
_ap.add_argument("--max-frames", type=int, default=250,
                 help="subsample to this many frames before solving (default 250). "
                      "More is slower without being more accurate.")
_ap.add_argument("out", help="output AprilTagFieldLayout json")
_ap.add_argument("tag_size", nargs="?", type=float, default=0.1651,
                 help="black-square edge in metres (default 0.1651 = 6.5in)")
_ap.add_argument("--host", default="photonvision.local",
                 help="fetch calibration from this PhotonVision (default)")
_ap.add_argument("--ref-tag", type=int, default=None, metavar="ID",
                 help="tag that becomes the origin. PIN THIS for any layout you "
                      "intend to reuse: without it the origin is whichever tag "
                      "happened to be seen most, which can change between surveys "
                      "of the same room and silently move the whole frame.")
_ap.add_argument("--ref-height", type=float, default=0.0, metavar="M",
                 help="metres from the floor up to the reference tag's CENTRE. "
                      "Shifts every z so the layout's z=0 is the floor instead of "
                      "a tag. Then calibrate_mount.py takes --origin-height 0.")
_ap.add_argument("--field", nargs=2, type=float, default=None,
                 metavar=("LENGTH", "WIDTH"),
                 help="metres, written into the layout's field block. WPILib uses "
                      "it for setOrigin(); tools that draw a field use it for the "
                      "extents. Default 8x8 is a placeholder, not a measurement.")
_ap.add_argument("--fx", type=float); _ap.add_argument("--fy", type=float)
_ap.add_argument("--cx", type=float); _ap.add_argument("--cy", type=float)
_a=_ap.parse_args()
FR=_a.frames; OUT=_a.out; TAG=_a.tag_size; MAXF=_a.max_frames
REF_H=_a.ref_height; FIELD=_a.field
if _a.fx and _a.fy and _a.cx and _a.cy:
    K=np.array([[_a.fx,0,_a.cx],[0,_a.fy,_a.cy],[0,0,1]]); D=np.zeros(8)
    print("using intrinsics from the command line (no distortion model)")
else:
    from photon_calib import fetch_calibration
    K,D,_wh,_nick,_n = fetch_calibration(_a.host)
    print("calibration from %s: %s @ %dx%d, %d snapshots" % (_a.host,_nick,_wh[0],_wh[1],_n))
    print("  fx %.2f fy %.2f cx %.2f cy %.2f" % (K[0,0],K[1,1],K[0,2],K[1,2]))
    if _n and _n < 50:
        print("  WARNING: only %d calibration snapshots - the map will inherit that error" % _n)
h=TAG/2
# verified empirically: PV corners pair as TL,TR,BR,BL in an X-right / Y-up / Z-out tag frame
OBJ=np.array([[-h,h,0],[h,h,0],[h,-h,0],[-h,-h,0]],dtype=np.float64)
def rt2T(r,t):
    T=np.eye(4); T[:3,:3]=cv2.Rodrigues(r.reshape(3,1))[0]; T[:3,3]=t; return T
def T2rt(T): return cv2.Rodrigues(T[:3,:3])[0].flatten(),T[:3,3]
frames=json.load(open(FR))
tags=sorted({int(t) for f in frames for t in f['ids']})
# Drop tags too sparsely seen to place, and anchor to the BEST-observed tag.
# REF used to be tags[0] - the lowest ID - so a single spurious detection of a
# low-numbered tag anchored the whole layout to something seen twice, and the
# solve started at an RMS of 2759 px. The reference defines the frame everything
# else is measured against; it has to be the tag you have the most evidence for.
from collections import Counter as _C
_counts = _C(int(t) for f in frames for t in f['ids'])
MIN_OBS = 20
_sparse = sorted(t for t in tags if _counts[t] < MIN_OBS)
if _sparse:
    print('dropping tags seen fewer than %d times: %s'
          % (MIN_OBS, {t: _counts[t] for t in _sparse}))
    keep = {t for t in tags if _counts[t] >= MIN_OBS}
    trimmed = []
    for f in frames:
        ids = [t for t in f['ids'] if int(t) in keep]
        if len(ids) >= 2:
            trimmed.append({'ids': ids,
                            'corners': {k: v for k, v in f['corners'].items()
                                        if int(k) in keep}})
    frames = trimmed
    tags = sorted(keep)
    print('  %d frames remain' % len(frames))
# Subsample. A bundle adjustment over 900 frames is ~5500 parameters against
# ~17800 residuals and grinds for a very long time for no extra accuracy - the
# original successful survey captured 1754 frames and solved ~210. Pick frames
# that BALANCE tag coverage rather than the first N, so a tag seen rarely is not
# crowded out by one seen constantly.
if len(frames) > MAXF:
    import random as _r
    _r.seed(0)
    _pool = list(range(len(frames))); _r.shuffle(_pool)
    _have = _C(); _chosen = []
    while _pool and len(_chosen) < MAXF:
        _target = min(tags, key=lambda t: _have[t])
        _pick = 0
        for _i, _fi in enumerate(_pool):
            if _target in [int(t) for t in frames[_fi]['ids']]:
                _pick = _i; break
        _fi = _pool.pop(_pick); _chosen.append(_fi)
        for _t in frames[_fi]['ids']: _have[int(_t)] += 1
    frames = [frames[i] for i in sorted(_chosen)]
    print('subsampled to %d frames, per-tag %s'
          % (len(frames), dict(sorted(_have.items()))))
    _counts = _C(int(t) for f in frames for t in f['ids'])

if _a.ref_tag is not None:
    if _a.ref_tag not in tags:
        sys.exit('--ref-tag %d was not placed by this survey. Tags here: %s'
                 % (_a.ref_tag, tags))
    REF = _a.ref_tag
    _best = max(_counts[t] for t in tags)
    if _counts[REF] < 0.25 * _best:
        print('!! tag %d is the origin but is among the least-seen (%d obs vs %d for'
              ' the best).' % (REF, _counts[REF], _best))
        print('   Every other tag is measured FROM it, so the whole frame inherits')
        print('   its noise. Pin a well-observed tag instead if you have the choice.')
else:
    REF = max(tags, key=lambda t: _counts[t])
    print('!! NO --ref-tag GIVEN, so the origin is whichever tag was seen most.')
    print('   That is fine for a one-off answer and WRONG for a layout you intend')
    print('   to reuse: survey the same room again, walk a different path, and a')
    print('   different tag can win - moving the entire coordinate frame, and with')
    print('   it every path you ever drew against it. Pin --ref-tag %d to keep this'
          % max(tags, key=lambda t: _counts[t]))
    print('   frame.')
print('frames %d  tags %s  reference %d (%d observations)'
      % (len(frames), tags, REF, _counts[REF]))
pnp={}
for fi,f in enumerate(frames):
    for t in f['ids']:
        img=np.array(f['corners'][str(t)],dtype=np.float64)
        ok,rv,tv=cv2.solvePnP(OBJ,img,K,D,flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if ok: pnp[(fi,int(t))]=rt2T(rv.flatten(),tv.flatten())
tagT={REF:np.eye(4)}; camT={}
for _ in range(8):
    for fi,f in enumerate(frames):
        if fi in camT: continue
        for t in f['ids']:
            if int(t) in tagT and (fi,int(t)) in pnp:
                camT[fi]=pnp[(fi,int(t))]@np.linalg.inv(tagT[int(t)]); break
    for fi,f in enumerate(frames):
        if fi not in camT: continue
        for t in f['ids']:
            t=int(t)
            if t not in tagT and (fi,t) in pnp: tagT[t]=np.linalg.inv(camT[fi])@pnp[(fi,t)]
print('initialised tags %d/%d frames %d/%d'%(len(tagT),len(tags),len(camT),len(frames)))
opt=[t for t in tags if t!=REF and t in tagT]; fids=sorted(camT); NT=len(opt)
obs=[(fi,int(t)) for fi,f in enumerate(frames) if fi in camT for t in f['ids'] if int(t) in tagT]
print('observations %d corners'%(len(obs)*4))
def pack():
    v=[]
    for t in opt: r,tr=T2rt(tagT[t]); v+=list(r)+list(tr)
    for fi in fids: r,tr=T2rt(camT[fi]); v+=list(r)+list(tr)
    return np.array(v)
def unpack(v):
    tT={REF:np.eye(4)}
    for i,t in enumerate(opt): tT[t]=rt2T(v[i*6:i*6+3],v[i*6+3:i*6+6])
    cT={}
    for j,fi in enumerate(fids):
        o=NT*6+j*6; cT[fi]=rt2T(v[o:o+3],v[o+3:o+6])
    return tT,cT
fidx={f:j for j,f in enumerate(fids)}
def resid(v):
    tT,cT=unpack(v); out=np.empty(len(obs)*8)
    for k,(fi,t) in enumerate(obs):
        M=cT[fi]@tT[t]
        proj,_=cv2.projectPoints(OBJ,cv2.Rodrigues(M[:3,:3])[0],M[:3,3],K,D)
        out[k*8:k*8+8]=(proj.reshape(4,2)-np.array(frames[fi]['corners'][str(t)])).ravel()
    return out
x0=pack(); print('initial RMS %.3f px'%np.sqrt((resid(x0)**2).mean()))
Js=lil_matrix((len(obs)*8,len(x0)),dtype=int)
for k,(fi,t) in enumerate(obs):
    rows=slice(k*8,k*8+8)
    if t!=REF: i=opt.index(t); Js[rows,i*6:i*6+6]=1
    j=fidx[fi]; Js[rows,NT*6+j*6:NT*6+j*6+6]=1
# verbose=2 so it is visible that it is working - with no output a long solve
# is indistinguishable from a hang.
res=least_squares(resid,x0,jac_sparsity=Js,x_scale='jac',ftol=1e-8,
                  method='trf',verbose=2)
print('final   RMS %.3f px'%np.sqrt((res.fun**2).mean()))
tT,cT=unpack(res.x)
for t in tags:
    if t not in tT: continue
    sel=[k for k,(fi,tt) in enumerate(obs) if tt==t]
    e=np.concatenate([res.fun[k*8:k*8+8] for k in sel])
    print('  tag %-3d %5.3f px (%d frames)'%(t,np.sqrt((e**2).mean()),len(sel)))
C=np.array([np.linalg.inv(cT[f])[:3,3] for f in fids])
print('camera viewpoint spread: %s m'%np.round(C.max(axis=0)-C.min(axis=0),3))
# Tag frame -> WPILib (X = tag normal, Z = up).
# Found by brute-forcing all 24 proper rotations against PhotonVision's own
# multi-tag solve: this one scores 0.34 px, the runner-up 31.6 px. Hand-deriving
# it gave a matrix wrong by two signs, which produced a layout that loaded fine
# and reprojected at 1415 px. Do not "simplify" this.
Rc=np.array([[0,0,-1.],[1,0,0],[0,-1,0]])
def R2q(R):
    t=np.trace(R)
    if t>0:
        s=np.sqrt(t+1)*2; return (0.25*s,(R[2,1]-R[1,2])/s,(R[0,2]-R[2,0])/s,(R[1,0]-R[0,1])/s)
    if R[0,0]>R[1,1] and R[0,0]>R[2,2]:
        s=np.sqrt(1+R[0,0]-R[1,1]-R[2,2])*2; return ((R[2,1]-R[1,2])/s,0.25*s,(R[0,1]+R[1,0])/s,(R[0,2]+R[2,0])/s)
    if R[1,1]>R[2,2]:
        s=np.sqrt(1+R[1,1]-R[0,0]-R[2,2])*2; return ((R[0,2]-R[2,0])/s,(R[0,1]+R[1,0])/s,0.25*s,(R[1,2]+R[2,1])/s)
    s=np.sqrt(1+R[2,2]-R[0,0]-R[1,1])*2; return ((R[1,0]-R[0,1])/s,(R[0,2]+R[2,0])/s,(R[1,2]+R[2,1])/s,0.25*s)
if FIELD is None:
    FIELD=[8.0,8.0]
    print('\n!! no --field given, writing the 8x8 placeholder. Nothing in this repo')
    print('   reads it, but WPILib uses it for setOrigin() and anything that DRAWS')
    print('   a field takes its extents from it. Measure your space and pass it.')
lay={"tags":[],"field":{"length":float(FIELD[0]),"width":float(FIELD[1])}}
if REF_H:
    print('\nz shifted by %+.4f m: the layout origin is now the FLOOR under tag %d,'
          % (REF_H, REF))
    print('not the tag itself. calibrate_mount.py now takes --origin-height 0.')
print('\ntag poses (WPILib convention, relative to tag %d):'%REF)
for t in tags:
    if t not in tT: continue
    R=Rc@tT[t][:3,:3]@Rc.T; p=Rc@tT[t][:3,3]+np.array([0,0,REF_H])
    w,x,y,z=R2q(R)
    nz=R@np.array([0,0,1.]); tilt=np.degrees(np.arccos(np.clip(nz@np.array([0,0,1.]),-1,1)))
    print('  tag %-3d  %+7.4f %+7.4f %+7.4f   tilt off vertical %5.2f deg'%(t,p[0],p[1],p[2],tilt))
    lay["tags"].append({"ID":int(t),"pose":{"translation":{"x":float(p[0]),"y":float(p[1]),"z":float(p[2])},
        "rotation":{"quaternion":{"W":float(w),"X":float(x),"Y":float(y),"Z":float(z)}}}})
lay["tags"].sort(key=lambda d:d["ID"])
json.dump(lay,open(OUT,'w'),indent=2)
print('wrote',OUT)
