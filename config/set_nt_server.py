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
"""Turn PhotonVision's own NetworkTables server on or off, from the Pi.

    sudo python3 set_nt_server.py true     # bench: BE a server
    sudo python3 set_nt_server.py false    # robot: the roboRIO is the server

RUN THIS ON THE PI. The database path below is local to it.

A RESTART IS REQUIRED. PhotonVision reads networkConfig at startup, so nothing
changes until `sudo systemctl restart photonvision`. Nothing here takes effect
on a running instance.

WHY IT EDITS SQLITE INSTEAD OF USING THE API. The only programmatic route is
POST /api/settings/general, which calls NetworkManager.reinitialize() - that
resets the network interface and restarts the web server, dropping every
websocket and NT client, including the one that made the request. Writing the
config and restarting deliberately is the calmer path.

WHEN YOU NEED IT ON: survey/capture_corners.py, mount/calibrate_mount.py and
viz/field_viewer.py are NT CLIENTS. On a bench there is no roboRIO, so without
this they connect to nothing and report no data rather than an error.

WHEN YOU NEED IT OFF: before the Pi meets a roboRIO. Two NT servers on one
network fight each other.
"""
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
