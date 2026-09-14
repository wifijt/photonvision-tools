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
"""Capture PhotonVision's OWN detected corners over NetworkTables, across viewpoints."""
import sys,json,time,ntcore,numpy as np
from photonlibpy.photonCamera import PhotonCamera
from collections import Counter
OUT=sys.argv[1]; DUR=float(sys.argv[2]) if len(sys.argv)>2 else 120
inst=ntcore.NetworkTableInstance.getDefault()
inst.startClient4("claude-survey4")
inst.setServer("192.168.1.202", ntcore.NetworkTableInstance.kDefaultPort4)
cam=PhotonCamera("OV9281")
kept=[];sigs=[];last=0;seen=0
t0=time.time()
while time.time()-t0<DUR:
    r=cam.getLatestResult(); ts=r.getTargets()
    seen+=1
    if len(ts)<2: time.sleep(0.02); continue
    got={}
    for t in ts:
        dc=t.detectedCorners
        if dc and len(dc)==4: got[int(t.fiducialId)]=[[c.x,c.y] for c in dc]
    if len(got)<2: time.sleep(0.02); continue
    sig=np.concatenate([np.r_[np.mean(got[k],axis=0),[np.linalg.norm(np.array(got[k][0])-np.array(got[k][2]))]] for k in sorted(got)])
    if any(len(s)==len(sig) and np.linalg.norm(s-sig)<25 for s in sigs):
        time.sleep(0.02); continue
    sigs.append(sig)
    kept.append({'ids':sorted(got),'corners':{str(k):v for k,v in got.items()}})
    if time.time()-t0-last>10:
        last=time.time()-t0
        c=Counter()
        for f in kept:
            for k in f['ids']: c[k]+=1
        print('  %3.0fs  kept %4d   per-tag %s'%(last,len(kept),dict(sorted(c.items()))),flush=True)
    time.sleep(0.02)
json.dump(kept,open(OUT,'w'))
c=Counter(); pair=Counter()
for f in kept:
    for k in f['ids']: c[k]+=1
    for i in range(len(f['ids'])):
        for j in range(i+1,len(f['ids'])): pair[(f['ids'][i],f['ids'][j])]+=1
print('\nDONE kept %d frames'%len(kept))
print('per-tag:',dict(sorted(c.items())))
print('co-visibility:',dict(sorted(pair.items())))
