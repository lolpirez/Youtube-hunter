# v1.2 — "Eclipse Glass" theme switch & theme transition

Files changed: `app/qml/components/ThemeSwitch.qml`, `app/qml/components/ThemeWipe.qml`.
No Python, no Main.qml, no API change: `wipeRequested(cx, cy, pref)`,
`run(cx, cy, targetColor, onCovered, onReveal, isReady)`, `warmUp()`,
`running`, `radius` all behave exactly as before.

## Title-bar switch
- **Glass orb** thumb: flat face, accent body, specular dome laid over it, two-layer shadow.
- **Real eclipse** instead of two glyphs: a disc in the orb's own face colour slides
  over the sun and leaves a crescent (~35 % of the moon's width). Sun → moon is one
  object changing state.
- **Recessed track** (inner shade at the top edge), accent sunrise by day, accent dusk + twinkling stars at night.
- **Hint of the other mode**: a ghost crescent on the free side by day (lights up in the accent on hover).
- **Liquid press**: the orb stretches along the track and springs back.
- The breathing sun loop is gone: calmer, and one less ambient animation.

## Theme transition ("theme loading")
- The orb rises to hero size in the centre and performs the switch as an eclipse:
  shadow slides across (to dark) or off while rays burst out (to light).
- **Totality**: corona ring; going dark, a "diamond ring" flash on the crescent tip and stars;
  going light, the rays keep turning slowly.
- **Orbit loader**: a thin comet circles the orb while the staged palette swap
  finishes, so the hold is visibly "loading the theme", not a pause.
- **Iris** opens from UNDER the orb (same colour, same size), so the glyph is never cut off at hand-off.
- Timing: reveal arms at 760 ms (was 700), total ≈ 1.4 s, same 900 ms gate cap.

## Performance contract (unchanged)
Everything that moves during the swap is an Animator (render thread). No colour
animations, no snapshots, no MultiEffect, static geometry only. The only
NumberAnimation is the press stretch, which runs before the curtain is up.

Rollback: restore the two files from v1.1.0.
