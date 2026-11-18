"""Document = project + undo stack + editing operations.

All mutations of a project made by the UI go through this class so that
they are undoable and so that listeners are notified.
"""
from __future__ import annotations

import copy
from typing import Callable, Iterable, Optional

from .model import Annotation, Board, Bookmark, Project, new_id
from .undo import Command, UndoStack


class Document:
    ANN_EVENT = "annotations"
    keyframe_frame: Optional[int] = None   # frame on screen (set by the canvas) for keyframe edits

    def __init__(self, project: Optional[Project] = None, path: Optional[str] = None):
        self.project = project or Project()
        self.path = path
        self.undo = UndoStack()
        self.listeners: list[Callable[[str], None]] = []  # "annotations"/"bookmarks"/"project"/"boards"/"board"
        self._dirty_extra = False

    # ------------------------------------------------- annotation scope
    @property
    def annotations(self) -> list[Annotation]:
        """The annotation list this document edits (video drawings here)."""
        return self.project.annotations

    def annotations_at(self, frame: int) -> list[Annotation]:
        return self.project.annotations_at(frame)

    def find_annotation(self, aid: str) -> Optional[Annotation]:
        for a in self.annotations:
            if a.id == aid:
                return a
        return None

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
        lst = self.annotations
        base = max((a.z for a in lst), default=0) + 1
        for i, a in enumerate(anns):
            a.z = base + i

        def redo():
            lst.extend(anns)

        def undo():
            ids = {a.id for a in anns}
            lst[:] = [a for a in lst if a.id not in ids]
        self._cmd(label, redo, undo, self.ANN_EVENT)

    def add_annotation(self, ann: Annotation) -> None:
        self.add_annotations([ann], f"Add {ann.kind.replace('_', ' ')}")

    def remove_annotations(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        lst = self.annotations
        removed = [(i, a) for i, a in enumerate(lst) if a.id in ids]
        if not removed:
            return

        def redo():
            lst[:] = [a for a in lst if a.id not in ids]

        def undo():
            for i, a in removed:
                lst.insert(i, a)
        self._cmd("Delete drawing" if len(removed) == 1 else f"Delete {len(removed)} drawings",
                  redo, undo, self.ANN_EVENT)

    def modify_annotations(self, ids: Iterable[str], fn: Callable[[Annotation], None],
                           label: str = "Edit drawing", merge_key: Optional[str] = None) -> None:
        """Apply *fn* to each annotation; undo restores the previous state."""
        ids = set(ids)
        targets = [a for a in self.annotations if a.id in ids]
        if not targets:
            return
        before = {a.id: copy.deepcopy(a.__dict__) for a in targets}
        work = {a.id: copy.deepcopy(a) for a in targets}
        for a in work.values():
            old = (list(a.points), a.rotation)
            fn(a)
            # Animated drawing moved/rotated: store that pose as a keyframe at the current frame.
            if a.keyframes and self.keyframe_frame is not None and (list(a.points), a.rotation) != old:
                from .animation import set_keyframe
                set_keyframe(a, self.keyframe_frame)
        after = {aid: copy.deepcopy(a.__dict__) for aid, a in work.items()}
        self._replace_states(before, after, label, merge_key)

    def set_annotation_states(self, before: dict, after: dict, label: str) -> None:
        """Register an edit already applied interactively (e.g. mouse drag)."""
        if before == after:
            return
        self._replace_states(before, after, label, None, already_applied=True)

    def _replace_states(self, before, after, label, merge_key, already_applied=False):
        lst = self.annotations

        def apply(states):
            for a in lst:
                st = states.get(a.id)
                if st is not None:
                    a.__dict__.update(copy.deepcopy(st))

        if already_applied:
            def r():
                apply(after)
                self._emit(self.ANN_EVENT)

            def u():
                apply(before)
                self._emit(self.ANN_EVENT)
            self.undo.push(Command(label, r, u, merge_key), already_done=True)
            self._emit(self.ANN_EVENT)
        else:
            self._cmd(label, lambda: apply(after), lambda: apply(before), self.ANN_EVENT, merge_key)

    def snapshot(self, ids: Iterable[str]) -> dict:
        ids = set(ids)
        return {a.id: copy.deepcopy(a.__dict__) for a in self.annotations if a.id in ids}

    # ------------------------------------------------------------- layering
    def reorder(self, ids: Iterable[str], mode: str) -> None:
        """mode: 'front', 'back', 'forward', 'backward'."""
        ids = set(ids)
        order = sorted(self.annotations, key=lambda a: a.z)
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
        lst = self.annotations

        def apply(states):
            for a in lst:
                if a.id in states:
                    a.__dict__.update(states[a.id])
        self._cmd(label, lambda: apply(after), lambda: apply(before), self.ANN_EVENT)

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
        groups = {a.group_id for a in self.annotations if a.id in ids and a.group_id}
        if groups:
            ids |= {a.id for a in self.annotations if a.group_id in groups}
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

    # ------------------------------------------------ freezes / commentary
    def _list_add(self, lst: list, item, label: str, event: str) -> None:
        self._cmd(label, lambda: lst.append(item), lambda: lst.remove(item) if item in lst else None, event)

    def _list_remove(self, lst: list, item_id: str, label: str, event: str) -> None:
        item = next((x for x in lst if x.id == item_id), None)
        if item is None:
            return
        pos = lst.index(item)
        self._cmd(label, lambda: lst.remove(item), lambda: lst.insert(pos, item), event)

    def _list_update(self, lst: list, item_id: str, label: str, event: str, **changes) -> None:
        item = next((x for x in lst if x.id == item_id), None)
        if item is None:
            return
        before = {k: copy.deepcopy(getattr(item, k)) for k in changes}
        if all(before[k] == v for k, v in changes.items()):
            return
        after = copy.deepcopy(changes)

        def apply(st):
            for k, v in st.items():
                setattr(item, k, copy.deepcopy(v))
        self._cmd(label, lambda: apply(after), lambda: apply(before), event)

    def add_freeze(self, freeze) -> None:
        self._list_add(self.project.freezes, freeze, "Add freeze frame", "freezes")

    def remove_freeze(self, fid: str) -> None:
        self._list_remove(self.project.freezes, fid, "Delete freeze frame", "freezes")

    def update_freeze(self, fid: str, **changes) -> None:
        self._list_update(self.project.freezes, fid, "Edit freeze frame", "freezes", **changes)

    def add_commentary(self, clip) -> None:
        self._list_add(self.project.commentary, clip, "Add commentary", "commentary")

    def remove_commentary(self, cid: str) -> None:
        self._list_remove(self.project.commentary, cid, "Delete commentary", "commentary")

    def update_commentary(self, cid: str, **changes) -> None:
        self._list_update(self.project.commentary, cid, "Edit commentary", "commentary", **changes)

    # ---------------------------------------------------------------- boards
    def add_board(self, board: Board, index: Optional[int] = None) -> None:
        lst = self.project.boards
        pos = len(lst) if index is None else index
        self._cmd("Add board", lambda: lst.insert(pos, board),
                  lambda: lst.remove(board) if board in lst else None, "boards")

    def remove_board(self, bid: str) -> None:
        lst = self.project.boards
        b = self.project.board(bid)
        if b is None:
            return
        pos = lst.index(b)
        self._cmd("Delete board", lambda: lst.remove(b), lambda: lst.insert(pos, b), "boards")

    def update_board(self, bid: str, label: str = "Edit board", **changes) -> None:
        b = self.project.board(bid)
        if b is None:
            return
        before = {k: copy.deepcopy(getattr(b, k)) for k in changes}
        if all(before[k] == v for k, v in changes.items()):
            return
        after = copy.deepcopy(changes)

        def apply(st):
            for k, v in st.items():
                setattr(b, k, copy.deepcopy(v))
        self._cmd(label, lambda: apply(after), lambda: apply(before), "boards")


class ScopedDocument(Document):
    """Edits the drawings of one tactics board while sharing the parent
    document's project, undo stack, listeners and dirty state."""
    ANN_EVENT = "board"

    def __init__(self, parent: Document, board: Board):
        self.parent = parent
        self.board = board
        self.project = parent.project
        self.undo = parent.undo
        self.listeners = parent.listeners

    @property
    def path(self):
        return self.parent.path

    @property
    def annotations(self) -> list[Annotation]:
        return self.board.annotations

    def annotations_at(self, frame: int) -> list[Annotation]:
        return sorted((a for a in self.board.annotations if a.visible), key=lambda a: a.z)

    @property
    def dirty(self) -> bool:
        return self.parent.dirty

    def mark_dirty(self) -> None:
        self.parent.mark_dirty()

    def mark_clean(self) -> None:
        self.parent.mark_clean()
