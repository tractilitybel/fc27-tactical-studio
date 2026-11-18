# Architecture

## Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.12 | Fast development, mature media and GUI bindings |
| GUI | Qt 6 via PySide6 (LGPL) | Native Windows desktop widgets, high-DPI, QPainter vector rendering |
| Decoding | FFmpeg via PyAV | Every codec/container a capture tool produces; exact timestamps |
| Packaging | PyInstaller (one-folder) | Self-contained `.exe` + DLLs; no Python install needed |

## Package layout

```
fc27studio/
  core/                 Qt-free logic (fully unit-tested)
    video.py            frame index, frame-exact random access, caching
    model.py            Annotation, Bookmark, Project dataclasses + geometry / hit tests
    undo.py             command-based undo stack with merging and macros
    document.py         Project + undo stack + every editing operation
    storage.py          .fc27studio I/O, .bak backups, autosave/recovery, video relinking
    imaging.py          PNG codec, polygon masks, mask→outline tracing, cut-outs, background fill
    segment.py          AI player selection (MobileSAM via ONNX Runtime)
  ui/
    main_window.py      window, workspaces, menus/actions, file handling, exports, autosave
    canvas.py           video display + drawing/editing interaction
    render.py           QPainter rendering of annotations (shared by canvas and export)
    playback.py         decode thread, thumbnail thread, Player (clock, speed, loop, J/K/L)
    timeline.py         timeline widget and transport bar
    bookmarks.py        incident list, bookmark/category dialogs
    panels.py           tool palette, drawing properties, layers, telestrator bar
    dialogs.py          preferences, shortcut editor, F1 card, go-to
    export.py           image/CSV exports, before/after composites
    board.py            tactics board: pitch drawing, formations, board panel
    settings.py         preferences + shortcut registry (INI in %APPDATA%)
  app.py                entry point: theme, crash logging, --selfcheck
```

The `core` package has no Qt dependency so it can be tested in isolation and later reused by
background tools (batch export, player tracking).

## Frame-exact video access (`core/video.py`)

Game recordings are usually H.264/HEVC with B-frames, so *decode order ≠ display order* and a naive
"seek to time t" lands on the wrong frame.

1. **Indexing.** On open the file is *demuxed* (packets read, nothing decoded) and every video packet's
   presentation timestamp (PTS) and keyframe flag is collected. Sorting the PTS gives the display order;
   frame *i* is defined as the frame with the *i*-th smallest PTS. This takes a second or two even for a
   full match because nothing is decoded.
2. **Random access.** To show frame *i*: seek to the last keyframe whose PTS ≤ PTS[i], decode forward, map
   each decoded frame's PTS back to its index, and stop when frame *i* appears. Frames decoded on the way
   are kept in an LRU cache, so stepping backwards inside a GOP is instant.
3. **Sequential access.** If the requested frame is just ahead of the decoder position, decoding simply
   continues — no seek. This is the playback path.
4. **Tests** encode frame numbers as black/white bit blocks into synthetic H.264 (3 B-frames) and MPEG-4
   videos and verify sequential, random, backwards and keyframe-boundary access always returns exactly
   the requested frame.

## Threads

```
UI thread ── Player (QTimer clock) ── request(i) ──▶ DecodeWorker thread (own VideoSource)
    ▲                                                     │ frameReady(i, QImage)
    └─────────────────────────────────────────────────────┘
UI thread ── hover/bookmark thumbs ─▶ ThumbnailWorker thread (own VideoSource, low priority)
```

- Each worker owns a separate `VideoSource` (FFmpeg contexts are not shared between threads).
- Requests are **latest-wins**: while the user scrubs, intermediate requests are dropped, so the newest
  position is always decoded next and no backlog builds up.
- During playback the Player computes the frame that *should* be visible from the wall clock
  (`start_frame + elapsed × fps × speed`). If decoding falls behind, frames are skipped to stay in real time
  (or, with frame dropping disabled, every frame is shown at reduced speed).
- The canvas only switches to a frame's drawings when that frame's image arrives, so drawings never appear
  over the wrong picture.

## Data model and undo

- Annotations are stored in **video pixel coordinates** (independent of window size or zoom), with a
  visible frame range, z-order, group id and style. Rotation is applied about the bounding-box centre at
  render time; hit tests un-rotate the mouse position.
- Every change goes through `Document`, which records an undo `Command`. Interactive drags are applied
  live and registered once on mouse release (`set_annotation_states(before, after)`); slider changes use a
  merge key so dragging a slider produces a single undo step.
- The telestrator's temporary drawings live in the canvas only and never touch the project or undo stack.

## Player cut-outs (`core/imaging.py`)

1. The traced outline is rasterised into an anti-aliased mask (2× supersampled even-odd fill).
2. The player's pixels and mask are stored as an RGBA PNG inside the annotation (base64), with the crop
   rectangle and the original outline (`source_points`). The cut-out is self-contained: it does not
   depend on decoding the video again.
3. The fill patch: the hole is dilated a few pixels (to remove the edge halo), then same-shaped regions at
   several offsets around it are compared on a ring of surrounding pixels, and the best match is copied,
   which continues grass stripes and pitch lines. A harmonic (Laplace) correction field, solved by Jacobi
   relaxation from the colour differences along the border, removes visible seams; this is
   Poisson-style seamless cloning.
4. Moving, scaling and rotating edit only the outline points. The renderer maps the original crop
   rectangle through the same transform (`Annotation.source_transform`), so the cut-out and its handles
   always agree.

## AI player selection (`core/segment.py`)

- Model: MobileSAM (a distilled Segment Anything), as two ONNX files (image encoder 28 MB, prompt
  decoder 16 MB) run on the CPU with ONNX Runtime. Downloaded and SHA-256-verified at build time and
  bundled with the app.
- Players are small in broadcast frames, so the encoder sees a **zoomed square crop** around the click
  (≈26% of the frame height) instead of the whole frame. Refinement clicks reuse the cached embedding
  (≈40 ms); a click outside the crop re-crops to cover all clicks.
- SAM proposes several masks (shirt, upper body, whole player). The **largest confident** mask that is
  still player-sized (<20% of the crop) is chosen, which is the whole player.
- Measured on 121 players in real broadcast frames (Voxel51 Football-Player-Segmentation, CC0) with
  one click on the shirt: mean IoU 0.89 against hand-drawn outlines (median 0.93; 91% above 0.8),
  0.48 s per click on 4 CPU cores. Whole-frame segmentation scored 0.75 by comparison.
- The mask is reduced to its connected blob, holes are filled, and it is traced to an exact pixel-edge
  outline (then simplified) for editing; the mask itself gives the cut-out's alpha.

## Before/after and boards

- Every annotation has a `layer` (`both` / `before` / `after`). The canvas renders the *before* scene
  and the *after* scene with the same code, clipped at the split divider or side by side.
- A tactics board is a `Board` in the project with its own annotation list. `ScopedDocument` edits that
  list with the same operations as the video drawings, sharing the project's undo stack, listeners and
  dirty state, so the Board workspace reuses the canvas, tools, properties panel and undo unchanged.
  Board coordinates are pitch pixels at 10 px per metre, which is what the measure tool uses.

## Rendering

`ui/render.py` paints annotations with QPainter in video coordinates. The canvas applies the view transform
(fit/zoom/pan) and calls the same functions that export uses on a full-resolution `QImage`, so exported
images match the screen exactly. Spotlights are painted first so they darken the video but not other drawings.

## Persistence

`.fc27studio` is UTF-8 JSON:

```json
{ "format": "fc27studio", "version": 1, "saved": 1763460000.0,
  "project": { "name": "...", "video": { "path": "...", "rel_path": "...", "fingerprint": {...} },
               "bookmarks": [...], "annotations": [...], "categories": [...], "players": [...],
               "last_frame": 0, "loop_a": null, "loop_b": null } }
```

- **Atomic save**: write to a temp file in the same folder, `fsync`, then `os.replace`. The previous file is
  copied to `.bak` first. Loading a damaged file falls back to `.bak`.
- **Video lookup order**: absolute path → path relative to the project → same file name next to the
  project → ask the user. A fingerprint (frame count, resolution, size) detects a different video.
- **Autosave** writes snapshots to `%APPDATA%\FC27TacticalStudio\recovery\<project-id>.autosave`; a clean
  exit or save removes them, so a leftover snapshot at start-up means the previous session crashed.
- **Forward compatibility**: unknown fields are ignored on load; files from a newer format version are
  refused with a clear message instead of being silently damaged.

## Testing

`python -m pytest -q tests` (74 tests):

- `test_video.py` — frame accuracy (B-frames, MPEG-4, random/backward/keyframe boundaries, frame-skipping playback), index cache, timecodes
- `test_document.py` — undo/redo, merging, layering, grouping, bookmarks, geometry/hit tests, project I/O,
  backups, corrupted files, relinking, recovery
- `test_gui.py` — the real main window offscreen: seeking shows the exact frame, keyboard stepping, J/K/L,
  all 10 drawing tools, move/resize/rotate with undo, copy/paste of groups, bookmark navigation and search,
  A/B loop, telestrator, save → move folder → reopen, full-resolution export, autosave/recovery

- `test_milestone2.py` — PNG codec, polygon masks, cut-out fill quality (player removed, pitch line
  continued), cut-out geometry, boards/scoped undo, formations, and GUI flows: lasso and click cut-outs,
  move/ghost/reset, before/after layers and compare export, board formations/colours/measure/tokens,
  duplicate/rename/delete, isolation from video drawings

- `test_ai_select.py` — mask utilities (exact outline tracing, components, holes), mask cut-outs,
  mask choice, and with the model present: selecting a player and refining it, plus click → Enter →
  cut-out in the real window

`FC27TacticalStudio.exe --selfcheck` runs a smoke test inside the packaged build (used by CI).
