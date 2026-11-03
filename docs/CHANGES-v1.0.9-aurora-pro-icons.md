# v1.0.9 "Aurora Pro" icon refresh

Audit of the in-page icon set (`tools/build_icons.py` -> `assets/icons/`).
Source of truth is still the generator: edit `build_icons.py`, run
`python tools/build_icons.py`. Only the icons below changed; every other SVG
regenerates byte-identical.

## Dated metaphors, redrawn
| icon | before | after |
|---|---|---|
| `save` | floppy disk | tray + arrow-in (iOS/macOS style) |
| `key`, `vpn_key` | skeleton key | round passkey-style key |
| `settings` | hex nut | 8-tooth gear (same geometry as the sidebar's kinetic gear) |
| `data` | database cylinder | layer stack |
| `travel_explore` | globe inside a lens (Material 2018) | spark lens (matches the sidebar Niche Finder icon) |

## Skeuomorphic glints removed
`search`, `search_2`, `favorite`, `lightbulb`: the little highlight stroke read
as 2015 and became a bite mark on the bold face. `lightbulb` now uses the
sidebar bulb's silhouette.

## Broken bold faces rebuilt
`chart` (+ `insights`, `trending_up`, `page_insights`, `nav_insights`),
`query_stats` (+ `speed`), `language` / `translate` / `public`, `lock`
(keyhole was a "0"), `hourglass_empty`, `visibility_off`, `bar_chart`
(bars fused together).

## Font fallbacks replaced with native SVGs
These were still drawn from remixicon, a different stroke/corner language:
`telegram`, `menu_fold` / `menu_unfold` and `sidebar_fold` / `sidebar_unfold`
(now a sidebar-panel icon), `provider_gemini`, and the in-page uses of the
`nav_*` names. `done_all` and `upload_file` were never mapped at all and drew
the generic grid icon; both are mapped and drawn now.

## Generator
New role `"o"` in `build_icons.py`: a line-face-only detail, skipped on the
bold face (used for gauge ticks and the crossed-out pupil).
