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
"""Rename a PhotonVision pipeline. Service must be stopped."""
import sqlite3,json,sys
DB="/opt/photonvision/photonvision_config/photon.sqlite"
idx=int(sys.argv[1]); newname=sys.argv[2]
con=sqlite3.connect(DB)
name,pj=con.execute("select unique_name,pipeline_jsons from cameras").fetchone()
outer=json.loads(pj); done=False
for i,entry in enumerate(outer):
    inner=json.loads(entry); s=inner[1]
    if s.get("pipelineIndex")!=idx: continue
    print("  pipeline %d: %r -> %r"%(idx,s.get("pipelineNickname"),newname))
    s["pipelineNickname"]=newname
    outer[i]=json.dumps(inner,indent=2); done=True
if not done: sys.exit("no pipeline with index %d"%idx)
con.execute("update cameras set pipeline_jsons=? where unique_name=?",(json.dumps(outer),name))
con.commit(); con.close(); print("  ok")
