# v1.0.11: hover after scroll (Search Archive + Niche Finder grids)

Symptom: hover works on a still grid, but after scrolling the card under the pointer shows no hover (lift, cover zoom, glow, quick actions) for roughly 1-10 s.

## Causes and fixes
1. **HoverHandler toggled off mid-scroll** (`ArchiveVideoCard` had `hoverGlow: scrollIdle`, and `GlassPanel` enabled its HoverHandler only while `hoverGlow` was on). A HoverHandler re-enabled while the pointer is already inside its item stays `hovered: false` until the pointer leaves and re-enters, so the card you were on never lit up until you crossed to another one.
   - `GlassPanel`: new `hoverTrack` (defaults to `hoverGlow`, so every other panel is unchanged) drives the HoverHandler; `hoverGlow` only drives the glow.
   - `ArchiveVideoCard`: `hoverTrack: true`; new `_hot: hovered && scrollIdle` used by every effect (elevation, lift, cover zoom, quick actions, heat bars, title sheen, channel comet, save pop). Sheen/comet now listen to `_hotChanged`, so they also fire when the grid comes to rest under a pointer that was already there.
2. **`gliding` held on by things that are not visible motion** (`SmoothScrollView`):
   - Flickable `moving` (lingers through its own wheel momentum / rebound / movement-ending timer) replaced with `dragging || flicking`.
   - pixel-delta path ignores deltas that do not move the content (inertia pressing on the top/bottom edge), and calls `cancelFlick()` like the wheel path.
   - wheel follower snaps when < 1 px from target (was 0.5 px).
3. **Pointer intent wins**: a passive HoverHandler on the scroller releases `gliding` as soon as the pointer moves > 6 px after the wheel has been quiet for 150 ms. Pointer motion while still wheeling only moves the anchor (no on/off flapping). The next wheel event that moves the content re-arms it.

Not run on a live Qt build from here: check on Windows with a notched wheel, a free-spin wheel and a precision touchpad.
