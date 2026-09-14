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
"""Edit PhotonVision pipeline settings in photon.sqlite.
Usage: pv_set.py <pipelineIndex> key=value [key=value ...]
Service MUST be stopped first, or PhotonVision will overwrite on shutdown."""
import sqlite3,json,sys,shutil,datetime

DB="/opt/photonvision/photonvision_config/photon.sqlite"
idx=int(sys.argv[1])
kvs={}
for a in sys.argv[2:]:
    k,v=a.split("=",1)
    try: v=json.loads(v)
    except Exception: pass
    kvs[k]=v

con=sqlite3.connect(DB)
rows=con.execute("select unique_name,pipeline_jsons from cameras").fetchall()
assert len(rows)==1, "expected exactly 1 camera, got %d"%len(rows)
name,pj=rows[0]
outer=json.loads(pj)
changed=[]
for i,entry in enumerate(outer):
    inner=json.loads(entry)          # ["AprilTagPipelineSettings", {...}]
    settings=inner[1]
    if settings.get("pipelineIndex")!=idx: continue
    for k,v in kvs.items():
        if k not in settings:
            print("  !! key %r not present in pipeline %d - SKIPPED"%(k,idx)); continue
        old=settings[k]
        if old==v:
            print("  == %-24s already %s"%(k,json.dumps(old))); continue
        settings[k]=v
        changed.append((k,old,v))
        print("  -> %-24s %s  ->  %s"%(k,json.dumps(old),json.dumps(v)))
    outer[i]=json.dumps(inner,indent=2)
if not changed:
    print("no changes"); sys.exit(0)
con.execute("update cameras set pipeline_jsons=? where unique_name=?",(json.dumps(outer),name))
con.commit(); con.close()
print("committed %d change(s) to pipeline %d"%(len(changed),idx))
