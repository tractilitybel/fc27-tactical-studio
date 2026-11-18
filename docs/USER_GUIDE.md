# FC27 Tactical Studio — User Guide

## 1. Getting started

1. **Record your match.** Any common recording works: Xbox/PlayStation captures, NVIDIA ShadowPlay,
   OBS, the Xbox Game Bar (MP4/MKV, H.264/HEVC/AV1, any frame rate).
2. **Open the video**: *File → Open video* (Ctrl+Shift+O), or drag the file onto the window.
   A new project is created and named after the file. Long files take a few seconds to index the first time.
3. **Save the project**: Ctrl+S creates a `.fc27studio` file. Keep it next to the video if you can — then
   you can move both to another folder or PC and the video is found automatically.

The window has four **workspaces** (buttons at the top, or F2 / F3 / F6 / F4):

| Workspace | Use it for |
|-----------|-----------|
| **Review** | Watching, scrubbing, and bookmarking incidents |
| **Tactics** | Drawing arrows, zones, spotlights and labels on specific frames; moving players |
| **Board** | A top-down tactics board with player tokens and formations |
| **Present** | Talking through incidents with a live telestrator and full-screen presentation mode |

## 2. Playback

| Action | Keys |
|---|---|
| Play / pause | Space |
| Shuttle: reverse / pause / forward (press again for 2×, 4×) | J / K / L |
| Previous / next frame | `,` / `.` (or mouse wheel over the video) |
| Jump back / forward 1 s | ← / → |
| Jump back / forward 5 s | ↓ / ↑ |
| Start / end | Home / End |
| Slower / faster / normal speed | − / = / 0 |
| Go to a time or frame | Ctrl+J (accepts `12:34`, `1:02:03`, `12:34.15`, `75.5s` or a frame number) |

**Sound** works like YouTube: it plays at every speed from 0.25× to 4× with normal pitch (2× sounds
faster, not higher), and is silent while paused, scrubbing, stepping frames or playing in reverse.
Use the speaker button and volume slider in the transport bar, or **M** to mute. If your recording has
several audio tracks (e.g. game sound and your microphone from OBS), pick one in *Playback → Audio track*.
Volume and mute are remembered.

The frame box in the transport bar shows the exact frame number; type a number and press Enter to jump.

**A/B loop** — the boxed *Loop* controls in the transport bar repeat a section: press `[` (or **A**) at the
start of a passage and `]` (or **B**) at the end; the loop turns on automatically and the box shows its
times. On the timeline the section is shaded blue with **A** and **B** tags.
Ctrl+L toggles it, `\` clears it. The loop is saved with the project.

**Timeline** — click or drag to scrub. Each drawing (arrow, text, cut-out…) has a coloured bar under
the bookmark flags; **click a bar to jump to the first frame of that drawing** (it is selected for
editing). White squares mark tactics boards linked to a moment (double-click to open the board).
Navigation keys (`,` `.` ← → ↑ ↓) always return playback to normal speed (1×). Hover to see a thumbnail. Ctrl+wheel zooms the timeline (useful for
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

## 5. Moving players (player cut-outs)

Show where a player *should* have been, on the real footage:

1. Pause on the moment and switch to **Tactics** (F3).
2. Press **X** (or the ✂ tool) and **click on the player's shirt**. The AI outlines them — head, arms,
   legs and boots — and shows the selection in blue. Then:
   - **Left-click** a part that was missed (e.g. a leg) to add it;
   - **Right-click** something that shouldn't be included (e.g. a teammate touching them) to remove it;
   - **Enter** or **double-click** to cut the player out, **Esc** to cancel.

   You can also **drag around the player** like a lasso instead; that works without the AI too.
   The AI runs on your own PC (no internet needed) and takes under half a second per click.
3. The player is lifted out and selected, so **drag them to the right position**. Corner handles scale
   (always in proportion), the round handle rotates.
4. The original spot is filled with nearby pitch texture (grass stripes and lines continue through it).
   In the *Drawing* panel:
   - **Original player**: 0% removes them completely; the default 35% leaves a faint ghost so viewers
     see where they were.
   - **Outline + arrow from original spot**: shows the movement with a dashed outline and arrow.
   - **Back to original position** puts the player back.
5. Like other drawings, a cut-out is visible for the set time from the frame it was made on, and it can be
   undone, copied, layered, grouped and exported.

Cut-outs are meant for **paused frames**. The moved player is a still image, so during playback they
stay put while everyone else moves.

## 6. Before / after comparison

Press **Y** (or use the *Before/after* box at the top right) to cycle:

- **Split**: one picture with a white divider. Left of it is *before* (what happened), right is *after*
  (your corrections). Drag the divider across to reveal the change. You can keep editing in this mode.
- **Side by side**: two pictures next to each other (view only).
- **Off**: normal view, showing "after".

Player cut-outs are "after only" by default, so the *before* side shows the real footage. For any drawing,
use **Show in** in the Drawing panel: *Before and after*, *Before only* (e.g. a red arrow for the run
that was actually made) or *After only* (e.g. the green arrow for the correct run).

*File → Export → Export before/after image* (Ctrl+Shift+E) saves the comparison: the split view if
split is on, otherwise side by side.

## 7. Tactics board

Press **F6** (or the *Board* button) for a top-down pitch.

- **New** creates a board with both teams in formation (home 4-3-3, away 4-4-2) and links it to the
  current video moment. A project can have as many boards as you like: Duplicate, Rename, Delete.
- **Teams**: pick a formation and press *Place* to set out a team (it replaces that team's tokens);
  *Clear* removes a team. Click the colour square to change a team's colour.
- **Drag tokens** to move players. **Double-click** a token to change its number or add a name.
  **P** adds a token in the current colour, **O** adds the ball.
- **Shift+M** measures distances. The label shows real metres (the pitch is 105 × 68 m).
- Arrows, runs, lines, zones, circles, rectangles, freehand and text work as on the video. Everything
  is undoable.
- **Video link**: *Link to current frame* ties the board to a moment; *Show video moment* jumps there.
- **Export board image…** saves the board as PNG/JPG.

## 8. Presenting

In the **Present** workspace you draw freely over the video like a TV pundit. These telestrator drawings are
temporary: they disappear when playback starts (configurable) or when you press **C**. Tick
*Keep drawings* to save them into the project instead.

**F5** starts presentation mode: full screen, only the video, timeline and transport. Esc or F5 leaves it.
The *Incident playlist* jumps straight to each bookmark.

## 9. Freeze frames, animation, commentary and video

**Freeze frames.** Press **F** on a moment: when playback reaches it, the video pauses for 3 seconds
(a countdown shows top right), then continues, like a TV analyst stopping the action. Draw on that
frame and the drawings are shown during the pause. Double-click the blue ❚❚ marker on the timeline to
change the length, add a note or delete it. *Playback → Play freeze frames* turns them all on/off;
**Space** during a freeze continues straight away.

**Animated drawings** (Drawing panel → *Animation*):
- **Draw-on**: set e.g. 1 s and arrows, runs and lines draw themselves from start to tip (others fade in).
- **Moving drawings**: select a drawing, press **◆ Keyframe here**, go to a later frame and drag it to
  the new position — it glides smoothly between the two. Every further drag at another frame adds a
  keyframe. ◀ ▶ jump between keyframes; *Remove here* / *Stop moving* undo the animation. Keyframes
  show as white diamonds on the drawing's timeline bar. Works for spotlights, circles, zones, text,
  arrows and player cut-outs.

**Voice commentary.** Press **R** (or the red ● button): the video plays and your microphone records
until you press **R** again or pause. The clip is saved next to your project (save the project first)
and shows as a purple bar on the timeline; double-click it to rename, change its volume or delete it.
Game sound is muted while recording (so it doesn't leak into the mic — turn this off under *Playback*
if you use headphones) and is automatically lowered under your voice during playback and export
(*Playback → Lower game sound under commentary*).

**Export video** (*File → Export → Export video*, **Ctrl+Alt+E**, or *Export video of these incidents*
under the Incidents list):
- *A section*: the whole video, the A–B loop or a custom range.
- *Highlights reel*: the incidents shown in the list (or the ones you selected), each with a title card
  and a few seconds before/after (bookmarks with a clip end use exactly that range) — as one video or one
  file per incident.
- Choose resolution (original, 1080p, 720p, 480p), quality, and whether to include drawings and
  animations, freeze frames, game sound and commentary. The result is a standard MP4 (H.264 + AAC) that
  plays on any PC or phone and uploads to YouTube, Discord or WhatsApp.

## 10. Tracking players and calibrating the pitch

**Track a player.** In *Tactics*, press **T** (⌖) and click the player (the AI finds him), or drag a box
around him. Choose the highlight first in the Drawing panel under *Player tracking*: ring at the feet,
spotlight, name label, or ring + label (with the name to show), and how many seconds to track. The
highlight then follows him; it copes with team-mates running past or in front of him. If it ever drifts
(e.g. two players in the same kit running side by side on a fast camera pan), go to that frame, drag the
highlight back onto him and press **Track from here**. If he leaves the picture, tracking stops there and
the status bar says where.

**Calibrate the pitch** (*Tools → Calibrate pitch*, **Ctrl+K**). On a frame where some pitch markings
are visible: click a point on the mini pitch (e.g. the corner of the box), then click the same spot on
the video; repeat for 4–6 points spread over the picture (not all on one line). Dashed pitch lines are
drawn over the video so you can see at once whether it fits; points that disagree turn orange. Press
**Save calibration**. One calibration covers about 30 seconds either side — the app measures how the
camera pans and zooms and carries it along. Calibrated frames have a green ▲ on the timeline.
*View → Show pitch lines* (Ctrl+Shift+P) shows the overlay any time.

Once calibrated:
- **Measure (Shift+M)** on the video shows real metres.
- A selected **tracked player** shows *distance covered* and *top / average speed* in the Drawing panel,
  and **Draw this run on a tactics board** plots his exact path on the Board.

## 11. Exporting images

- *File → Export → Export frame image* (Ctrl+E): PNG or JPG at the video's full resolution, with drawings.
- *Export clean frame*: the same without drawings.
- *Copy frame to clipboard* (Ctrl+Shift+C): paste straight into Discord, WhatsApp, PowerPoint…
- *Export bookmark images*: one image per bookmark (respects the current search/filter).
- *Export bookmark list (CSV)*: opens in Excel or Google Sheets.

## 12. Safety: saving, autosave and recovery

- Saving is atomic, and the previous version is kept as `yourproject.fc27studio.bak`. If a project file is
  ever damaged, the backup is opened automatically and you are told about it.
- Unsaved work is autosaved every 60 s (change in *Edit → Preferences*) to the app's data folder
  (`%APPDATA%\FC27TacticalStudio\recovery`). After a crash or power cut you are offered to recover it on the
  next start.
- If the video was moved or renamed you are asked to locate it (*File → Relink video…* works at any time).
  Bookmarks and drawings are kept. You are warned if the new file has a different length or resolution.

## 13. Preferences and shortcuts

*Edit → Preferences* (Ctrl+,): frame cache size, decoder threads, frame dropping, autosave interval,
default drawing duration, jump size, timecode format, timeline thumbnails, telestrator behaviour,
JPG quality.

*Edit → Keyboard shortcuts…*: reassign any shortcut; conflicts are flagged. Press **F1** for a printable
reference card of the current shortcuts.

## 14. Performance tips

- 1080p60 plays in real time on any recent PC. For 4K60 recordings enable *Drop frames to keep real-time
  speed* (on by default); frame stepping is always exact regardless.
- Stepping backwards is fastest within the frame cache. Raise *Frame cache* in Preferences if you have spare
  RAM (each 1080p frame ≈ 6 MB, each 4K frame ≈ 25 MB).
- Recordings with very long keyframe intervals (some capture tools use 10 s or more) make random seeks
  slower. If seeking feels slow, set your capture software to a 1–2 second keyframe interval.

## 15. Troubleshooting

| Problem | Fix |
|---|---|
| "Cannot open video" | The file may still be recording or be incomplete; check it plays in another player. |
| Video not found when opening a project | Click *Locate video…*, or keep project and video in the same folder. |
| Something went wrong | Error details are in `%APPDATA%\FC27TacticalStudio\logs`. Your work was autosaved. |
| Reset all settings | Delete `%APPDATA%\FC27TacticalStudio\FC27TacticalStudio.ini`. |
