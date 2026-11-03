# v1.0.10: video grid performance (Search Archive + Niche Finder)

Static changes. Profile on a real machine before release (QML_PROFILER / QSG_RENDER_TIMING=1).

## GPU / renderer
- PrimaryButton halo: layer + MultiEffect blur → 4 stacked rounded rects (GlassPanel's technique).
  Every Download button on every card used to own an offscreen texture chain + blur pass.
- IconButton tile halo: same. The layer was CREATED on every hover (hover hitch) and kept alive on
  every active tile (each favourited / picked card).
- Archive section avatars: Image + mask layer + MultiEffect → pre-cut `image://thumbs/circle/`.

## GUI thread / Python round-trips
- AppText: Persian detection in JS; Fonts.familyFor/weightFor (2 Python calls per text change of
  every label) only run for Persian text or a Persian UI.
- IconGlyph: Icons.hasSvg memoised in Utils.js.
- ArchiveVideoCard: downloads map / downloaded ids / saved ids / idea keys converted ONCE per page
  (new props downloadsMap, doneIds, savedList, ideaKeys; defaults unchanged). I18n strings and Theme
  colours used by row-dependent bindings resolved once per card.
- rowSig (vm/_row_sig.py): grids keep a card's row when its content is unchanged, so a live-stats
  tick / favourite toggle rebinds only the cards that changed instead of all 24.
- Paint culling: one mapToItem per frame per grid instead of two per card.
- Tooltips: LazyTooltip builds AppTooltip on first hover (~260 hidden tooltip trees per page before).

## Scrolling (SmoothScrollView, every page)
- Wheel: per-frame exponential follower (FrameAnimation) instead of restarting an OutCubic animation
  per notch: continuous speed, acceleration on fast spins (up to 2.6x), instant reversal,
  integer resting positions (crisp text). Touchpad path unchanged.
- `gliding` turns off 140 ms after motion stops (no hover-binding storms between notches).
