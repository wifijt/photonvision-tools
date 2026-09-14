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
FR=sys.argv[1]; OUT=sys.argv[2]; TAG=float(sys.argv[3]) if len(sys.argv)>3 else 0.1651
K=np.array([[1105.88,0,616.38],[0,1111.20,393.63],[0,0,1]])
D=np.array([-0.42056,0.24813,0.00034,-0.00144,-0.08562,0.03356,-0.02389,0.02391])
h=TAG/2
# verified empirically: PV corners pair as TL,TR,BR,BL in an X-right / Y-up / Z-out tag frame
OBJ=np.array([[-h,h,0],[h,h,0],[h,-h,0],[-h,-h,0]],dtype=np.float64)
def rt2T(r,t):
    T=np.eye(4); T[:3,:3]=cv2.Rodrigues(r.reshape(3,1))[0]; T[:3,3]=t; return T
def T2rt(T): return cv2.Rodrigues(T[:3,:3])[0].flatten(),T[:3,3]
frames=json.load(open(FR))
tags=sorted({int(t) for f in frames for t in f['ids']})
REF=tags[0]
print('frames %d  tags %s  reference %d'%(len(frames),tags,REF))
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
res=least_squares(resid,x0,jac_sparsity=Js,x_scale='jac',ftol=1e-10,method='trf')
print('final   RMS %.3f px'%np.sqrt((res.fun**2).mean()))
tT,cT=unpack(res.x)
for t in tags:
    if t not in tT: continue
    sel=[k for k,(fi,tt) in enumerate(obs) if tt==t]
    e=np.concatenate([res.fun[k*8:k*8+8] for k in sel])
    print('  tag %-3d %5.3f px (%d frames)'%(t,np.sqrt((e**2).mean()),len(sel)))
C=np.array([np.linalg.inv(cT[f])[:3,3] for f in fids])
print('camera viewpoint spread: %s m'%np.round(C.max(axis=0)-C.min(axis=0),3))
# OpenCV tag frame (X right, Y up, Z out) -> WPILib (X normal, Y right, Z up)
Rc=np.array([[0,0,1.],[1,0,0],[0,1,0]])
def R2q(R):
    t=np.trace(R)
    if t>0:
        s=np.sqrt(t+1)*2; return (0.25*s,(R[2,1]-R[1,2])/s,(R[0,2]-R[2,0])/s,(R[1,0]-R[0,1])/s)
    if R[0,0]>R[1,1] and R[0,0]>R[2,2]:
        s=np.sqrt(1+R[0,0]-R[1,1]-R[2,2])*2; return ((R[2,1]-R[1,2])/s,0.25*s,(R[0,1]+R[1,0])/s,(R[0,2]+R[2,0])/s)
    if R[1,1]>R[2,2]:
        s=np.sqrt(1+R[1,1]-R[0,0]-R[2,2])*2; return ((R[0,2]-R[2,0])/s,(R[0,1]+R[1,0])/s,0.25*s,(R[1,2]+R[2,1])/s)
    s=np.sqrt(1+R[2,2]-R[0,0]-R[1,1])*2; return ((R[1,0]-R[0,1])/s,(R[0,2]+R[2,0])/s,(R[1,2]+R[2,1])/s,0.25*s)
lay={"tags":[],"field":{"length":8.0,"width":8.0}}
print('\ntag poses (WPILib convention, relative to tag %d):'%REF)
for t in tags:
    if t not in tT: continue
    R=Rc@tT[t][:3,:3]@Rc.T; p=Rc@tT[t][:3,3]
    w,x,y,z=R2q(R)
    nz=R@np.array([0,0,1.]); tilt=np.degrees(np.arccos(np.clip(nz@np.array([0,0,1.]),-1,1)))
    print('  tag %-3d  %+7.4f %+7.4f %+7.4f   tilt off vertical %5.2f deg'%(t,p[0],p[1],p[2],tilt))
    lay["tags"].append({"ID":int(t),"pose":{"translation":{"x":float(p[0]),"y":float(p[1]),"z":float(p[2])},
        "rotation":{"quaternion":{"W":float(w),"X":float(x),"Y":float(y),"Z":float(z)}}}})
lay["tags"].sort(key=lambda d:d["ID"])
json.dump(lay,open(OUT,'w'),indent=2)
print('wrote',OUT)
