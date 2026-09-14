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
"""Set the active pipeline index in photon.sqlite (service must be stopped)."""
import sqlite3,json,sys
DB="/opt/photonvision/photonvision_config/photon.sqlite"
idx=int(sys.argv[1])
con=sqlite3.connect(DB)
name,cfg=con.execute("select unique_name,config_json from cameras").fetchone()
d=json.loads(cfg)
print("currentPipelineIndex: %s -> %s"%(d.get("currentPipelineIndex"),idx))
d["currentPipelineIndex"]=idx
con.execute("update cameras set config_json=? where unique_name=?",(json.dumps(d,indent=2),name))
con.commit(); con.close()
print("ok")
