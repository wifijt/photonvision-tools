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
import os
HOST=os.environ.get('PV_HOST','photonvision.local')
import asyncio,json,sys,time,msgpack,websockets,numpy as np
from collections import defaultdict
DUR=float(sys.argv[1]) if len(sys.argv)>1 else 30
OUT=sys.argv[2] if len(sys.argv)>2 else None
async def main():
    # wait for PV to come up
    t0=time.time()
    while time.time()-t0<180:
        try:
            ws=await websockets.connect('ws://%s:5800/websocket_data'%HOST,open_timeout=5,max_size=80_000_000)
            break
        except Exception:
            await asyncio.sleep(3)
    else:
        print("PV never came up"); return
    print("connected after %.0fs"%(time.time()-t0))
    async with ws:
        t0=time.time(); per=defaultdict(list); fps=[];lat=[];metrics=None;settings=None;nt=0
        while time.time()-t0<DUR:
            try: msg=await asyncio.wait_for(ws.recv(),timeout=10)
            except asyncio.TimeoutError: break
            if not isinstance(msg,bytes): continue
            d=msgpack.unpackb(msg,raw=False)
            if not isinstance(d,dict): continue
            if 'updatePipelineResult' in d:
                for cam,c in d['updatePipelineResult'].items():
                    fps.append(c['fps']); lat.append(c['latency']); nt+=1
                    for tg in c['targets']: per[tg['fiducialId']].append(tg)
            elif 'metrics' in d: metrics=d['metrics']
            elif 'cameraSettings' in d: settings=d['cameraSettings']
    if settings:
        s=settings[0]['currentPipelineSettings']
        print("ACTIVE pipeline %d (%s): decimate=%s iters=%s threads=%s margin=%s gain=%s exp=%s mode=%s multiTag=%s"%(
            s['pipelineIndex'],s['pipelineNickname'],s['decimate'],s['numIterations'],s['threads'],
            s['decisionMargin'],s['cameraGain'],s['cameraExposureRaw'],s['cameraVideoModeIndex'],s['doMultiTarget']))
    if fps: print("FPS mean %.1f  LATENCY mean %.1f ms p95 %.1f"%(np.mean(fps),np.mean(lat),np.percentile(lat,95)))
    if metrics: print("CPU %.0f%%  temp %.0fC  throttled=%s"%(metrics['cpuUtil'],metrics['cpuTemp'],metrics.get('cpuThr')))
    print()
    print("%-6s %7s %9s %9s %10s  %s"%("tagID","frames","amb_mean","amb_med","amb>0.2%","posStdXYZ(mm)"))
    allamb=[]
    for tid in sorted(per):
        ts=per[tid]; amb=np.array([t['ambiguity'] for t in ts]); allamb+=list(amb)
        x=np.array([t['pose']['x'] for t in ts]);y=np.array([t['pose']['y'] for t in ts]);z=np.array([t['pose']['z'] for t in ts])
        print("%-6d %7d %9.3f %9.3f %9.0f%%  %5.1f %5.1f %5.1f"%(tid,len(ts),amb.mean(),np.median(amb),
            100*(amb>0.2).mean(),x.std()*1000,y.std()*1000,z.std()*1000))
    a=np.array(allamb)
    if len(a): print("\nALL n=%d  amb mean %.3f median %.3f | >0.2: %.1f%%  >0.5: %.1f%%"%(len(a),a.mean(),np.median(a),100*(a>0.2).mean(),100*(a>0.5).mean()))
    if OUT: json.dump({'amb':list(map(float,a))},open(OUT,'w'))
asyncio.run(main())
