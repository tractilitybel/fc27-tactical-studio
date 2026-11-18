"""Reading/writing ``.fc27studio`` project files, backups, autosave, recovery."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from .model import Project

FORMAT = "fc27studio"
VERSION = 1
EXTENSION = ".fc27studio"


class ProjectError(Exception):
    pass


def _atomic_write(path: str, data: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def serialise(project: Project) -> str:
    payload = {"format": FORMAT, "version": VERSION, "saved": time.time(),
               "project": project.to_dict()}
    return json.dumps(payload, indent=1, ensure_ascii=False)


def save_project(project: Project, path: str, make_backup: bool = True) -> None:
    """Atomically save; the previous file (if any) becomes ``<path>.bak``."""
    path = os.path.abspath(path)
    if project.video.path:
        try:
            project.video.rel_path = os.path.relpath(project.video.path, os.path.dirname(path))
        except ValueError:  # different drive on Windows
            project.video.rel_path = ""
    data = serialise(project)
    if make_backup and os.path.isfile(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    _atomic_write(path, data)


def parse(text: str) -> Project:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectError(f"Project file is corrupted ({exc.msg} at line {exc.lineno}).") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ProjectError("This is not an FC27 Tactical Studio project file.")
    version = payload.get("version", 0)
    if version > VERSION:
        raise ProjectError(f"Project was saved by a newer version (format {version}). "
                           "Please update FC27 Tactical Studio.")
    try:
        return Project.from_dict(payload.get("project") or {})
    except (TypeError, ValueError, KeyError) as exc:
        raise ProjectError(f"Project file contains invalid data: {exc}") from exc


def load_project(path: str) -> Project:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise ProjectError(f"Cannot read project: {exc}") from exc
    return parse(text)


def load_with_backup_fallback(path: str) -> tuple[Project, bool]:
    """Load *path*; if it is corrupted, try ``.bak``. Returns (project, used_backup)."""
    try:
        return load_project(path), False
    except ProjectError:
        bak = path + ".bak"
        if os.path.isfile(bak):
            return load_project(bak), True
        raise


# ----------------------------------------------------------------- video
def resolve_video(project: Project, project_path: Optional[str]) -> Optional[str]:
    """Find the project's video: absolute path, relative path, then same name
    next to the project. Returns None if it cannot be found."""
    v = project.video
    candidates = []
    if v.path:
        candidates.append(v.path)
    if project_path:
        pdir = os.path.dirname(os.path.abspath(project_path))
        if v.rel_path:
            candidates.append(os.path.normpath(os.path.join(pdir, v.rel_path)))
        if v.path:
            name = os.path.basename(v.path.replace("\\", "/"))
            candidates.append(os.path.join(pdir, name))
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def fingerprint_matches(project: Project, info) -> bool:
    fp = project.video.fingerprint or {}
    if not fp:
        return True
    ref = info.fingerprint()
    keys = ("frame_count", "width", "height")
    return all(fp.get(k) == ref.get(k) for k in keys if k in fp)


# -------------------------------------------------------------- recovery
@dataclass
class RecoveryEntry:
    file: str
    project_path: Optional[str]
    name: str
    saved: float


class Recovery:
    """Autosave snapshots kept in an app-data folder, one per project id."""

    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    def _file_for(self, project: Project) -> str:
        return os.path.join(self.dir, f"{project.id}.autosave")

    def write(self, project: Project, project_path: Optional[str]) -> str:
        path = self._file_for(project)
        payload = {"format": FORMAT, "version": VERSION, "saved": time.time(),
                   "project_path": project_path, "project": project.to_dict()}
        _atomic_write(path, json.dumps(payload, ensure_ascii=False))
        return path

    def discard(self, project: Project) -> None:
        try:
            os.unlink(self._file_for(project))
        except OSError:
            pass

    def discard_file(self, file: str) -> None:
        try:
            os.unlink(file)
        except OSError:
            pass

    def entries(self) -> list[RecoveryEntry]:
        out = []
        for fn in os.listdir(self.dir):
            if not fn.endswith(".autosave"):
                continue
            full = os.path.join(self.dir, fn)
            try:
                with open(full, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                proj = payload.get("project", {})
                out.append(RecoveryEntry(full, payload.get("project_path"),
                                         proj.get("name", "Untitled"), payload.get("saved", 0)))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda e: -e.saved)

    def load(self, entry: RecoveryEntry) -> Project:
        with open(entry.file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return Project.from_dict(payload.get("project") or {})
