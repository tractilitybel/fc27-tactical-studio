"""Persistent application preferences and keyboard shortcut registry."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields

from PySide6.QtCore import QSettings, QStandardPaths

ORG = "FC27TacticalStudio"
APP = "FC27TacticalStudio"


def qsettings() -> QSettings:
    """INI-backed settings (in %APPDATA% on Windows) — easy to back up or reset."""
    return QSettings(QSettings.IniFormat, QSettings.UserScope, ORG, APP)


@dataclass
class Preferences:
    cache_mb: int = 768              # decoded-frame cache budget
    decoder_threads: int = 0         # 0 = automatic
    autosave_seconds: int = 60
    default_hold_seconds: float = 3.0  # how long a new drawing stays visible
    big_step_seconds: float = 1.0    # Left/Right
    jump_seconds: float = 5.0        # Up/Down
    volume: float = 1.0
    muted: bool = False
    show_frame_numbers: bool = True
    timeline_thumbnails: bool = True
    drop_frames: bool = True         # keep real-time speed by dropping frames
    telestrator_clear_on_play: bool = True
    jpeg_quality: int = 95
    recent_files: str = ""           # '\n' separated

    @classmethod
    def load(cls) -> "Preferences":
        s = qsettings()
        p = cls()
        for f in fields(cls):
            if s.contains("prefs/" + f.name):
                raw = s.value("prefs/" + f.name)
                try:
                    if f.type in ("bool", bool):
                        val = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes")
                    elif f.type in ("int", int):
                        val = int(raw)
                    elif f.type in ("float", float):
                        val = float(raw)
                    else:
                        val = str(raw)
                    setattr(p, f.name, val)
                except (TypeError, ValueError):
                    pass
        return p

    def save(self) -> None:
        s = qsettings()
        for f in fields(self):
            s.setValue("prefs/" + f.name, getattr(self, f.name))
        s.sync()

    # ------------------------------------------------------------ recents
    def recents(self) -> list[str]:
        return [r for r in self.recent_files.split("\n") if r]

    def add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        items = [r for r in self.recents() if os.path.normcase(r) != os.path.normcase(path)]
        self.recent_files = "\n".join([path] + items[:9])
        self.save()


def app_data_dir() -> str:
    d = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".fc27studio")
    os.makedirs(d, exist_ok=True)
    return d


def recovery_dir() -> str:
    d = os.path.join(app_data_dir(), "recovery")
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- shortcuts
# id: (category, label, default key sequence)
SHORTCUTS: dict[str, tuple[str, str, str]] = {
    # File
    "file.new": ("File", "New project", "Ctrl+N"),
    "file.open": ("File", "Open project", "Ctrl+O"),
    "file.open_video": ("File", "Open video", "Ctrl+Shift+O"),
    "file.save": ("File", "Save", "Ctrl+S"),
    "file.save_as": ("File", "Save as", "Ctrl+Shift+S"),
    "file.export_frame": ("File", "Export frame image", "Ctrl+E"),
    "file.copy_frame": ("File", "Copy frame to clipboard", "Ctrl+Shift+C"),
    "file.export_compare": ("File", "Export before/after image", "Ctrl+Shift+E"),
    "file.quit": ("File", "Quit", "Ctrl+Q"),
    # Edit
    "edit.undo": ("Edit", "Undo", "Ctrl+Z"),
    "edit.redo": ("Edit", "Redo", "Ctrl+Y"),
    "edit.cut": ("Edit", "Cut drawings", "Ctrl+X"),
    "edit.copy": ("Edit", "Copy drawings", "Ctrl+C"),
    "edit.paste": ("Edit", "Paste drawings", "Ctrl+V"),
    "edit.duplicate": ("Edit", "Duplicate drawings", "Ctrl+D"),
    "edit.delete": ("Edit", "Delete selection", "Delete"),
    "edit.select_all": ("Edit", "Select all drawings on frame", "Ctrl+A"),
    "edit.group": ("Edit", "Group", "Ctrl+G"),
    "edit.ungroup": ("Edit", "Ungroup", "Ctrl+Shift+G"),
    "edit.front": ("Edit", "Bring to front", "Ctrl+Shift+]"),
    "edit.back": ("Edit", "Send to back", "Ctrl+Shift+["),
    "edit.forward": ("Edit", "Bring forward", "Ctrl+]"),
    "edit.backward": ("Edit", "Send backward", "Ctrl+["),
    "edit.prefs": ("Edit", "Preferences", "Ctrl+,"),
    # Playback
    "play.toggle": ("Playback", "Play / pause", "Space"),
    "play.reverse": ("Playback", "Play reverse / faster reverse (J)", "J"),
    "play.pause": ("Playback", "Pause (K)", "K"),
    "play.forward": ("Playback", "Play / faster forward (L)", "L"),
    "play.next_frame": ("Playback", "Next frame", "."),
    "play.prev_frame": ("Playback", "Previous frame", ","),
    "play.big_forward": ("Playback", "Jump forward 1 s", "Right"),
    "play.big_back": ("Playback", "Jump back 1 s", "Left"),
    "play.jump_forward": ("Playback", "Jump forward 5 s", "Up"),
    "play.jump_back": ("Playback", "Jump back 5 s", "Down"),
    "play.start": ("Playback", "Go to start", "Home"),
    "play.end": ("Playback", "Go to end", "End"),
    "play.slower": ("Playback", "Slower", "-"),
    "play.faster": ("Playback", "Faster", "="),
    "play.normal_speed": ("Playback", "Normal speed", "0"),
    "play.set_a": ("Playback", "Set loop start (A)", "["),
    "play.set_b": ("Playback", "Set loop end (B)", "]"),
    "play.toggle_loop": ("Playback", "Toggle A/B loop", "Ctrl+L"),
    "play.clear_loop": ("Playback", "Clear A/B loop", "\\"),
    "play.goto": ("Playback", "Go to time / frame…", "Ctrl+J"),
    "play.mute": ("Playback", "Mute / unmute sound", "M"),
    # Bookmarks
    "bm.add": ("Bookmarks", "Add bookmark", "B"),
    "bm.add_detailed": ("Bookmarks", "Add bookmark with details…", "Shift+B"),
    "bm.next": ("Bookmarks", "Next bookmark", "Ctrl+Right"),
    "bm.prev": ("Bookmarks", "Previous bookmark", "Ctrl+Left"),
    # Tools
    "tool.select": ("Tools", "Select / move", "V"),
    "tool.arrow": ("Tools", "Arrow", "1"),
    "tool.curved_arrow": ("Tools", "Curved arrow", "2"),
    "tool.run": ("Tools", "Run (dashed arrow)", "3"),
    "tool.line": ("Tools", "Line", "4"),
    "tool.freehand": ("Tools", "Freehand", "5"),
    "tool.circle": ("Tools", "Circle", "6"),
    "tool.rect": ("Tools", "Rectangle", "7"),
    "tool.zone": ("Tools", "Zone", "8"),
    "tool.text": ("Tools", "Text", "9"),
    "tool.spotlight": ("Tools", "Spotlight", "Shift+9"),
    "tool.cutout": ("Tools", "Player cut-out", "X"),
    "tool.token": ("Tools", "Player token (board)", "P"),
    "tool.ball": ("Tools", "Ball (board)", "O"),
    "tool.measure": ("Tools", "Measure distance (board)", "Shift+M"),
    "tool.clear_telestrator": ("Tools", "Clear telestrator drawings", "C"),
    # View
    "view.review": ("View", "Review workspace", "F2"),
    "view.tactics": ("View", "Tactics workspace", "F3"),
    "view.present": ("View", "Present workspace", "F4"),
    "view.board": ("View", "Tactics board workspace", "F6"),
    "view.compare": ("View", "Before/after: off → split → side by side", "Y"),
    "view.presentation": ("View", "Presentation mode", "F5"),
    "view.fullscreen": ("View", "Full screen", "F11"),
    "view.zoom_fit": ("View", "Zoom to fit", "Ctrl+0"),
    "view.zoom_100": ("View", "Zoom 100%", "Ctrl+1"),
    "view.zoom_in": ("View", "Zoom in", "Ctrl+="),
    "view.zoom_out": ("View", "Zoom out", "Ctrl+-"),
    "view.hide_drawings": ("View", "Show / hide drawings", "H"),
    "help.card": ("Help", "Shortcut reference card", "F1"),
}


def load_shortcuts() -> dict[str, str]:
    s = qsettings()
    out = {}
    for aid, (_c, _l, default) in SHORTCUTS.items():
        val = s.value("shortcuts/" + aid)
        out[aid] = default if val is None else str(val)
    return out


def save_shortcuts(mapping: dict[str, str]) -> None:
    s = qsettings()
    for aid, seq in mapping.items():
        if SHORTCUTS.get(aid, (None, None, None))[2] == seq:
            s.remove("shortcuts/" + aid)
        else:
            s.setValue("shortcuts/" + aid, seq)
    s.sync()


def find_conflicts(mapping: dict[str, str]) -> dict[str, list[str]]:
    by_key: dict[str, list[str]] = {}
    for aid, seq in mapping.items():
        if seq:
            by_key.setdefault(seq.lower(), []).append(aid)
    return {k: v for k, v in by_key.items() if len(v) > 1}
