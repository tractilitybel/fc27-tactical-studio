# Roadmap

## ✅ Milestone 1 — Analysis foundation (this release)
Frame-exact playback, timeline, incident bookmarks, 10 drawing tools with full editing, telestrator and
presentation mode, projects with backup/autosave/recovery/relinking, image and CSV exports, configurable
shortcuts, automated tests, Windows build pipeline.

## Milestone 2 — Player manipulation and the tactics board
- Player cutouts: lift a player from a frame (polygon or brush selection) and reposition them to show where
  they *should* have been, with a ghost at the original position
- Before/after comparison: side-by-side and slider views of the original and corrected frame
- 2D tactics board: top-down pitch with draggable player tokens, formations, and links to video moments
- Distance and angle measurement (pitch-calibrated via four pitch-line points)

## Milestone 3 — Sequences and video output
- Freeze-frame sequences: pause automatically at a moment, show drawings, resume
- Keyframe animation of drawings (arrows that extend, moving spotlights)
- Export clips and full analysis videos (MP4) with drawings burned in
- Voice-over commentary recorded against the timeline

## Milestone 4 — Tracking
- Track a selected player across frames so spotlights and labels follow them
- Optional AI-assisted player detection for one-click selection (runs locally)

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
- No audio playback: the analysis tools are visual, and audio is planned alongside commentary (M3).
- 4K60 H.265 playback is CPU-decoded; on mid-range CPUs it drops frames to stay real time (stepping is
  always exact). Hardware decoding is planned for M6.
- Reverse playback is smooth within the frame cache and becomes approximate across long GOPs.
