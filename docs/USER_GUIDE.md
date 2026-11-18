# FC27 Tactical Studio — User Guide

## 1. Getting started

1. **Record your match.** Any common recording works: Xbox/PlayStation captures, NVIDIA ShadowPlay,
   OBS, the Xbox Game Bar (MP4/MKV, H.264/HEVC/AV1, any frame rate).
2. **Open the video**: *File → Open video* (Ctrl+Shift+O), or drag the file onto the window.
   A new project is created and named after the file. Long files take a few seconds to index the first time.
3. **Save the project**: Ctrl+S creates a `.fc27studio` file. Keep it next to the video if you can — then
   you can move both to another folder or PC and the video is found automatically.

The window has three **workspaces** (buttons at the top, or F2/F3/F4):

| Workspace | Use it for |
|-----------|-----------|
| **Review** | Watching, scrubbing, and bookmarking incidents |
| **Tactics** | Drawing arrows, zones, spotlights and labels on specific frames |
| **Present** | Talking through incidents with a live telestrator and full-screen presentation mode |

## 2. Playback

| Action | Keys |
|---|---|
| Play / pause | Space |
| Shuttle: reverse / pause / forward (press again for 2×, 4×) | J / K / L |
| Previous / next frame | ← / → (or mouse wheel over the video) |
| Jump back / forward (1 s by default) | Shift+← / Shift+→ |
| Start / end | Home / End |
| Slower / faster / normal speed | − / = / 0 |
| Go to a time or frame | Ctrl+J (accepts `12:34`, `1:02:03`, `12:34.15`, `75.5s` or a frame number) |

The frame box in the transport bar shows the exact frame number; type a number and press Enter to jump.

**A/B loop** — press `[` at the start of a passage and `]` at the end; the loop turns on automatically.
Ctrl+L toggles it, `\` clears it. The loop is saved with the project.

**Timeline** — click or drag to scrub. Hover to see a thumbnail. Ctrl+wheel zooms the timeline (useful for
a 90-minute match), wheel scrolls it when zoomed. Coloured flags are bookmarks (click to jump, double-click
to edit); the shaded blue area is the A/B loop; the thin bars under the flags show when drawings are visible.

## 3. Bookmarking incidents

- **B** adds a quick bookmark at the current frame ("Incident 7").
- **Shift+B** opens the full form: title, category, tags, players, importance, notes and an optional clip end.
- Double-click a bookmark in the **Incidents** list (or its flag on the timeline) to edit it.
- **Ctrl+← / Ctrl+→** jump to the previous / next bookmark.
- Search matches title, tags, players and notes (every word must match). Filter by category or player and
  sort by time, category, importance, recently added or title.
- *Edit → Bookmark categories…* to rename categories, change colours, or add your own.
- Add your squad in the **Match** tab (one name per line) so player names autocomplete.

## 4. Drawing (Tactics workspace)

Choose a tool from the left toolbar or with the number keys:

| Key | Tool | How to draw |
|---|---|---|
| V | Select | Click to select, drag to move; drag empty space for a selection box |
| 1 | Arrow | Drag from start to tip |
| 2 | Curved arrow | Drag; then move the orange middle handle to bend it |
| 3 | Run | Dashed arrow for off-the-ball runs |
| 4 | Line | Drag (e.g. defensive line) |
| 5 | Freehand | Drag |
| 6 | Circle | Drag a box; Shift = perfect circle |
| 7 | Rectangle | Drag; Shift = square |
| 8 | Zone | Click each corner; finish with double-click, Enter or clicking the first point |
| 9 | Text | Click where the label goes, type the text |
| Shift+9 | Spotlight | Drag around a player; everything else is darkened |

**Editing:** select a drawing to see its handles — square handles resize (Shift keeps proportions), the round
handle above rotates (Shift snaps to 15°), white dots move individual points of arrows, lines and zones.
Alt+arrow keys nudge (add Shift for 10 px). Double-click a text label to edit it.

**Style:** the *Drawing* panel changes colour, thickness, opacity, fill/shade, text size and rotation of the
selected drawings, or — with nothing selected — the style of the next drawing.

**When is a drawing visible?** A new drawing appears from the frame you drew it on for the time set in
*New drawings* (3 s by default). For a selected drawing, set *From frame / To frame*, or use *◀ Now* /
*Now ▶* to start or end it at the current frame. Right-click a drawing for the same options.

**Arranging:** To front / Forward / Backward / To back, Group (Ctrl+G) so several drawings move together,
Ungroup (Ctrl+Shift+G). The list at the bottom of the panel shows every drawing on the current frame;
untick one to hide it. **H** hides or shows all drawings.

**Clipboard:** Ctrl+C / Ctrl+V copies drawings to another moment (they are pasted at the current frame),
Ctrl+D duplicates, Delete removes. Everything can be undone with Ctrl+Z and redone with Ctrl+Y.

**Zooming the video:** Ctrl+wheel zooms at the mouse pointer, middle-drag (or Alt+drag) pans,
Ctrl+0 fits the video to the window, Ctrl+1 shows it at 100%.

## 5. Presenting

In the **Present** workspace you draw freely over the video like a TV pundit. These telestrator drawings are
temporary: they disappear when playback starts (configurable) or when you press **C**. Tick
*Keep drawings* to save them into the project instead.

**F5** starts presentation mode: full screen, only the video, timeline and transport. Esc or F5 leaves it.
The *Incident playlist* jumps straight to each bookmark.

## 6. Exporting

- *File → Export → Export frame image* (Ctrl+E): PNG or JPG at the video's full resolution, with drawings.
- *Export clean frame*: the same without drawings.
- *Copy frame to clipboard* (Ctrl+Shift+C): paste straight into Discord, WhatsApp, PowerPoint…
- *Export bookmark images*: one image per bookmark (respects the current search/filter).
- *Export bookmark list (CSV)*: opens in Excel or Google Sheets.

## 7. Safety: saving, autosave and recovery

- Saving is atomic, and the previous version is kept as `yourproject.fc27studio.bak`. If a project file is
  ever damaged, the backup is opened automatically and you are told about it.
- Unsaved work is autosaved every 60 s (change in *Edit → Preferences*) to the app's data folder
  (`%APPDATA%\FC27TacticalStudio\recovery`). After a crash or power cut you are offered to recover it on the
  next start.
- If the video was moved or renamed you are asked to locate it (*File → Relink video…* works at any time).
  Bookmarks and drawings are kept. You are warned if the new file has a different length or resolution.

## 8. Preferences and shortcuts

*Edit → Preferences* (Ctrl+,): frame cache size, decoder threads, frame dropping, autosave interval,
default drawing duration, jump size, timecode format, timeline thumbnails, telestrator behaviour,
JPG quality.

*Edit → Keyboard shortcuts…*: reassign any shortcut; conflicts are flagged. Press **F1** for a printable
reference card of the current shortcuts.

## 9. Performance tips

- 1080p60 plays in real time on any recent PC. For 4K60 recordings enable *Drop frames to keep real-time
  speed* (on by default); frame stepping is always exact regardless.
- Stepping backwards is fastest within the frame cache. Raise *Frame cache* in Preferences if you have spare
  RAM (each 1080p frame ≈ 6 MB, each 4K frame ≈ 25 MB).
- Recordings with very long keyframe intervals (some capture tools use 10 s or more) make random seeks
  slower. If seeking feels slow, set your capture software to a 1–2 second keyframe interval.

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot open video" | The file may still be recording or be incomplete; check it plays in another player. |
| Video not found when opening a project | Click *Locate video…*, or keep project and video in the same folder. |
| Something went wrong | Error details are in `%APPDATA%\FC27TacticalStudio\logs`. Your work was autosaved. |
| Reset all settings | Delete `%APPDATA%\FC27TacticalStudio\FC27TacticalStudio.ini`. |
