"""Snapshot-free command-based undo stack (Qt independent)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Command:
    label: str
    redo: Callable[[], None]
    undo: Callable[[], None]
    merge_key: Optional[str] = None  # consecutive commands with same key merge


class UndoStack:
    def __init__(self, limit: int = 500):
        self._items: list[Command] = []
        self._pos = 0          # number of applied commands
        self._clean = 0
        self.limit = limit
        self.listeners: list[Callable[[], None]] = []
        self._macro: Optional[list[Command]] = None
        self._macro_label = ""

    # ------------------------------------------------------------------
    def push(self, cmd: Command, already_done: bool = False) -> None:
        if not already_done:
            cmd.redo()
        if self._macro is not None:
            self._macro.append(cmd)
            return
        del self._items[self._pos:]
        if self._clean > self._pos:
            self._clean = -1
        if (cmd.merge_key and self._items and self._pos == len(self._items)
                and self._items[-1].merge_key == cmd.merge_key and self._clean != self._pos):
            prev = self._items[-1]
            self._items[-1] = Command(prev.label, cmd.redo, prev.undo, cmd.merge_key)
        else:
            self._items.append(cmd)
            self._pos += 1
            if len(self._items) > self.limit:
                drop = len(self._items) - self.limit
                del self._items[:drop]
                self._pos -= drop
                self._clean -= drop
        self._notify()

    def begin_macro(self, label: str) -> None:
        self._macro = []
        self._macro_label = label

    def end_macro(self) -> None:
        cmds, self._macro = self._macro, None
        if not cmds:
            return

        def redo():
            for c in cmds:
                c.redo()

        def undo():
            for c in reversed(cmds):
                c.undo()
        self.push(Command(self._macro_label, redo, undo), already_done=True)

    def undo(self) -> bool:
        if not self.can_undo():
            return False
        self._pos -= 1
        self._items[self._pos].undo()
        self._notify()
        return True

    def redo(self) -> bool:
        if not self.can_redo():
            return False
        self._items[self._pos].redo()
        self._pos += 1
        self._notify()
        return True

    def can_undo(self) -> bool:
        return self._pos > 0

    def can_redo(self) -> bool:
        return self._pos < len(self._items)

    def undo_label(self) -> str:
        return self._items[self._pos - 1].label if self.can_undo() else ""

    def redo_label(self) -> str:
        return self._items[self._pos].label if self.can_redo() else ""

    def set_clean(self) -> None:
        self._clean = self._pos
        self._notify()

    def is_clean(self) -> bool:
        return self._clean == self._pos

    def clear(self) -> None:
        self._items.clear()
        self._pos = 0
        self._clean = 0
        self._notify()

    def break_merge(self) -> None:
        """Prevent the next command from merging with the previous one."""
        if self._items and self._pos == len(self._items):
            last = self._items[-1]
            self._items[-1] = Command(last.label, last.redo, last.undo, None)

    def _notify(self) -> None:
        for fn in list(self.listeners):
            fn()
