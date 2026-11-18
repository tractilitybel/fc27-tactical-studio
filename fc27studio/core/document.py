"""Document = project + undo stack + editing operations.

All mutations of a project made by the UI go through this class so that
they are undoable and so that listeners are notified.
"""
from __future__ import annotations

import copy
from typing import Callable, Iterable, Optional

from .model import Annotation, Bookmark, Project, new_id
from .undo import Command, UndoStack


class Document:
    def __init__(self, project: Optional[Project] = None, path: Optional[str] = None):
        self.project = project or Project()
        self.path = path
        self.undo = UndoStack()
        self.listeners: list[Callable[[str], None]] = []  # arg: "annotations"/"bookmarks"/"project"
        self._dirty_extra = False

    # ---------------------------------------------------------------- state
    @property
    def dirty(self) -> bool:
        return self._dirty_extra or not self.undo.is_clean()

    def mark_dirty(self) -> None:
        self._dirty_extra = True
        self._emit("project")

    def mark_clean(self) -> None:
        self._dirty_extra = False
        self.undo.set_clean()

    def _emit(self, what: str) -> None:
        for fn in list(self.listeners):
            fn(what)

    def _cmd(self, label: str, redo, undo, what: str, merge_key: Optional[str] = None):
        def r():
            redo()
            self._emit(what)

        def u():
            undo()
            self._emit(what)
        self.undo.push(Command(label, r, u, merge_key))

    # ---------------------------------------------------------- annotations
    def add_annotations(self, anns: Iterable[Annotation], label: str = "Add drawing") -> None:
        anns = list(anns)
        if not anns:
            return
        base = self.project.next_z()
        for i, a in enumerate(anns):
            a.z = base + i
        lst = self.project.annotations

        def redo():
            lst.extend(anns)

        def undo():
            ids = {a.id for a in anns}
            lst[:] = [a for a in lst if a.id not in ids]
        self._cmd(label, redo, undo, "annotations")

    def add_annotation(self, ann: Annotation) -> None:
        self.add_annotations([ann], f"Add {ann.kind.replace('_', ' ')}")

    def remove_annotations(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        lst = self.project.annotations
        removed = [(i, a) for i, a in enumerate(lst) if a.id in ids]
        if not removed:
            return

        def redo():
            lst[:] = [a for a in lst if a.id not in ids]

        def undo():
            for i, a in removed:
                lst.insert(i, a)
        self._cmd("Delete drawing" if len(removed) == 1 else f"Delete {len(removed)} drawings",
                  redo, undo, "annotations")

    def modify_annotations(self, ids: Iterable[str], fn: Callable[[Annotation], None],
                           label: str = "Edit drawing", merge_key: Optional[str] = None) -> None:
        """Apply *fn* to each annotation; undo restores the previous state."""
        ids = set(ids)
        targets = [a for a in self.project.annotations if a.id in ids]
        if not targets:
            return
        before = {a.id: copy.deepcopy(a.__dict__) for a in targets}
        work = {a.id: copy.deepcopy(a) for a in targets}
        for a in work.values():
            fn(a)
        after = {aid: copy.deepcopy(a.__dict__) for aid, a in work.items()}
        self._replace_states(before, after, label, merge_key)

    def set_annotation_states(self, before: dict, after: dict, label: str) -> None:
        """Register an edit already applied interactively (e.g. mouse drag)."""
        if before == after:
            return
        self._replace_states(before, after, label, None, already_applied=True)

    def _replace_states(self, before, after, label, merge_key, already_applied=False):
        proj = self.project

        def apply(states):
            for a in proj.annotations:
                st = states.get(a.id)
                if st is not None:
                    a.__dict__.update(copy.deepcopy(st))

        if already_applied:
            def r():
                apply(after)
                self._emit("annotations")

            def u():
                apply(before)
                self._emit("annotations")
            self.undo.push(Command(label, r, u, merge_key), already_done=True)
            self._emit("annotations")
        else:
            self._cmd(label, lambda: apply(after), lambda: apply(before), "annotations", merge_key)

    def snapshot(self, ids: Iterable[str]) -> dict:
        ids = set(ids)
        return {a.id: copy.deepcopy(a.__dict__) for a in self.project.annotations if a.id in ids}

    # ------------------------------------------------------------- layering
    def reorder(self, ids: Iterable[str], mode: str) -> None:
        """mode: 'front', 'back', 'forward', 'backward'."""
        ids = set(ids)
        order = sorted(self.project.annotations, key=lambda a: a.z)
        sel = [a for a in order if a.id in ids]
        rest = [a for a in order if a.id not in ids]
        if not sel:
            return
        if mode == "front":
            new = rest + sel
        elif mode == "back":
            new = sel + rest
        else:
            new = order[:]
            rng = range(len(new) - 2, -1, -1) if mode == "forward" else range(1, len(new))
            for i in rng:
                j = i + 1 if mode == "forward" else i - 1
                if new[i].id in ids and new[j].id not in ids:
                    new[i], new[j] = new[j], new[i]
        before = {a.id: {"z": a.z} for a in order}
        after = {a.id: {"z": i} for i, a in enumerate(new)}
        self._replace_states_partial(before, after, "Change layer order")

    def _replace_states_partial(self, before, after, label):
        proj = self.project

        def apply(states):
            for a in proj.annotations:
                if a.id in states:
                    a.__dict__.update(states[a.id])
        self._cmd(label, lambda: apply(after), lambda: apply(before), "annotations")

    def group(self, ids: Iterable[str]) -> Optional[str]:
        ids = list(ids)
        if len(ids) < 2:
            return None
        gid = new_id()
        self.modify_annotations(ids, lambda a: setattr(a, "group_id", gid), "Group")
        return gid

    def ungroup(self, ids: Iterable[str]) -> None:
        self.modify_annotations(ids, lambda a: setattr(a, "group_id", None), "Ungroup")

    def expand_groups(self, ids: Iterable[str]) -> set[str]:
        ids = set(ids)
        groups = {a.group_id for a in self.project.annotations if a.id in ids and a.group_id}
        if groups:
            ids |= {a.id for a in self.project.annotations if a.group_id in groups}
        return ids

    # ------------------------------------------------------------ bookmarks
    def add_bookmark(self, bm: Bookmark) -> None:
        lst = self.project.bookmarks
        self._cmd("Add bookmark", lambda: lst.append(bm),
                  lambda: lst.remove(bm) if bm in lst else None, "bookmarks")

    def remove_bookmarks(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        lst = self.project.bookmarks
        removed = [(i, b) for i, b in enumerate(lst) if b.id in ids]
        if not removed:
            return

        def redo():
            lst[:] = [b for b in lst if b.id not in ids]

        def undo():
            for i, b in removed:
                lst.insert(i, b)
        self._cmd("Delete bookmark" if len(removed) == 1 else f"Delete {len(removed)} bookmarks",
                  redo, undo, "bookmarks")

    def update_bookmark(self, bid: str, **changes) -> None:
        bm = self.project.bookmark(bid)
        if bm is None:
            return
        before = {k: copy.deepcopy(getattr(bm, k)) for k in changes}
        if all(before[k] == v for k, v in changes.items()):
            return
        after = copy.deepcopy(changes)

        def apply(st):
            for k, v in st.items():
                setattr(bm, k, copy.deepcopy(v))
        self._cmd("Edit bookmark", lambda: apply(after), lambda: apply(before), "bookmarks")

    # --------------------------------------------------------- categories
    def set_categories(self, cats: list[dict]) -> None:
        before = copy.deepcopy(self.project.categories)
        after = copy.deepcopy(cats)
        proj = self.project
        self._cmd("Edit categories", lambda: setattr(proj, "categories", copy.deepcopy(after)),
                  lambda: setattr(proj, "categories", copy.deepcopy(before)), "bookmarks")

    def set_project_fields(self, label: str = "Edit project", **changes) -> None:
        proj = self.project
        before = {k: copy.deepcopy(getattr(proj, k)) for k in changes}
        after = copy.deepcopy(changes)

        def apply(st):
            for k, v in st.items():
                setattr(proj, k, copy.deepcopy(v))
        self._cmd(label, lambda: apply(after), lambda: apply(before), "project")
