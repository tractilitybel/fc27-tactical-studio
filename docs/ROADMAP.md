# Roadmap

## ✅ Milestone 1 — Analysis foundation (1.0)
Frame-exact playback, timeline, incident bookmarks, 10 drawing tools with full editing, telestrator and
presentation mode, projects with backup/autosave/recovery/relinking, image and CSV exports, configurable
shortcuts, automated tests, Windows build pipeline.

## ✅ Milestone 2 — Player manipulation and the tactics board (1.1)
Player cut-outs with background fill, ghost and movement arrow; before/after comparison (split slider,
side by side, image export, per-drawing before/after layers); Board workspace with player tokens,
formation presets, ball, distance measurement in metres, multiple boards linked to video moments.

**Moved to a later milestone:** measuring real distances *on the video* needs the camera perspective to be
calibrated (click four known pitch points). That goes with tracking in Milestone 4, which needs the same
calibration. On the tactics board, distances are already exact.

## ✅ Milestone 3 — Sequences and video output (1.4)
Sound like YouTube (1.3); freeze frames that pause playback with a countdown; animated drawings
(keyframed movement with easing, draw-on reveal); MP4 export of sections and highlight reels with
title cards, drawings, animations, freezes, game sound and commentary; voice commentary recording
with automatic ducking.

## ✅ Milestone 4 — Tracking and calibration (1.5)
Player tracking (median-flow with forward–backward checks, appearance check and an occlusion-aware motion
model) with ring/spotlight/label highlights and "Track from here"; one-click AI player selection
(shipped in 1.2); pitch calibration from named landmarks with a live pitch-line overlay, carried
through camera pans/zooms; real distances on the video; distance covered and top speed of tracked
players; tracked runs drawn onto tactics boards.

## Milestone 5 — Live capture
- Record the game window or screen directly in the app (Windows Graphics Capture + hardware encoding)
- Replay buffer: a hotkey saves the last N seconds and adds a bookmark
- Bookmark while playing using a global hotkey

## Milestone 6 — Release quality
- Performance work: GPU-accelerated decoding (D3D11VA/DXVA2), smoother 4K60 playback
- Installer (MSIX or Inno Setup), file associations for `.fc27studio`, code signing
- LGPL-only FFmpeg build for public distribution (see LICENSING.md)
- Accessibility, localisation, crash reporting

## Known limitations of Milestone 1
- 4K60 H.265 playback is CPU-decoded; on mid-range CPUs it drops frames to stay real time (stepping is
  always exact). Hardware decoding is planned for M6.
- Reverse playback is smooth within the frame cache and becomes approximate across long GOPs.
