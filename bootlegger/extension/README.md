# Bootlegger Draft Overlay

The back room, pinned inside Sleeper's draft room. Shows The Call, the next
three suggestions with survival odds, the experts' dissent, your shelf needs,
and the pick-feed staleness lamp — refreshed every 2 seconds from the Pi's
board API. Clicking a player fills Sleeper's own search box in your logged-in
tab (clipboard fallback if Sleeper's DOM changes); the pick itself stays human.

## Install (Chrome / Edge, on the machine you draft from)

1. Open `chrome://extensions`
2. Toggle **Developer mode** (top right)
3. **Load unpacked** → select this `extension/` folder
4. Open your Sleeper draft room — the walnut panel appears bottom-right
   (click its header to fold/unfold)

## Notes

- Talks to `http://192.168.1.160:8484` (edit `BASE` in `background.js` if the
  Pi moves). Works only on the home LAN / Tailscale, by design.
- The panel mirrors whatever draft the Pi's poller is bound to; it warns if
  the page's draft id differs (repoint the poller with `SLEEPER_DRAFT_ID`).
- The red dot / STALE warning is the same C1 heartbeat the web board uses.
