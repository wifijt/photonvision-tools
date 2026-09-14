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
import sqlite3,json,sys
DB="/opt/photonvision/photonvision_config/photon.sqlite"
val=sys.argv[1]=="true"
con=sqlite3.connect(DB)
row=con.execute("select contents from global where filename='networkConfig'").fetchone()
d=json.loads(row[0])
print("runNTServer: %s -> %s"%(d.get("runNTServer"),val))
d["runNTServer"]=val
con.execute("update global set contents=? where filename='networkConfig'",(json.dumps(d,indent=2),))
con.commit(); con.close(); print("ok")
