# Dashboard layouts

## photontune-elastic.json — Elastic

Everything photontune publishes, on one tab, with the trigger as a switch.

**To use it:** Elastic → the folder icon (top left) → **Open Layout** → pick this
file. Or drop it in your robot project at `src/main/deploy/elastic-layout.json`
and Elastic will offer to load it when it connects.

| widget | topic | |
|---|---|---|
| START A TUNE | `run` | **a switch — flip it to start.** Clears itself when done |
| OK? | `ok` | green = the last run is trustworthy |
| Busy | `busy` | on while tuning |
| Heartbeat | `heartbeat` | must be counting up. **Frozen = the service is dead** |
| Progress | `progress` | 0 → 1 across all cameras |
| Camera / Status | | which camera, and what it is doing right now |
| Summary | `summary` | `OV9281=863@g40`, or `FAILED: ...` |
| Warnings | `warnings` | not failures, but read them |
| Boot baseline | | whether the settings were asserted at power-on |

**Check the heartbeat before you flip the switch.** If the daemon has died, `ok`
and `busy` keep their last values forever and the switch does nothing at all.

If a widget loads as the wrong kind, change its type in Elastic and re-save —
`Toggle Switch` and `Toggle Button` are both writable booleans, and which one
you prefer is taste.

**AdvantageScope cannot flip `run`.** It displays all of these fine, but its
Tuning Mode only edits fields under the `/Tuning` table via the "NetworkTables 4
(AdvantageKit)" source. Watch there, trigger in Elastic.
