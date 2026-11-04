"""The painted per-key colors and their undo history. No Qt.

``colors`` maps an LED index to ``'#rrggbb'``; an LED that is not in the map is unpainted
(transparent in the layer stack). Every edit pushes a snapshot of the map, which at most
a few hundred entries makes cheaper than being clever. Edits that share a ``merge`` key
and follow each other directly (dragging inside a color picker) become one undo step.
"""

from __future__ import annotations

from typing import Hashable, Iterable, Optional

MAX_UNDO = 200


class PaintDocument:
    def __init__(self, colors: Optional[dict] = None) -> None:
        self.colors: dict[int, str] = {}
        self._undo: list[dict[int, str]] = []  # states before each step
        self._open: Optional[Hashable] = None  # merge key of the gesture still in progress
        self._redo: list[dict[int, str]] = []
        self.load(colors or {})

    # -- state

    def load(self, colors: dict) -> None:
        """Replace everything (e.g. the layer changed elsewhere). Clears the history."""
        self.colors = {int(k): str(v).lower() for k, v in colors.items()}
        self._undo.clear()
        self._redo.clear()
        self._open = None

    def end_gesture(self) -> None:
        """The next edit starts a new undo step even if it has the same merge key."""
        self._open = None

    def to_params(self) -> dict:
        return {str(i): c for i, c in sorted(self.colors.items())}

    @property
    def is_empty(self) -> bool:
        return not self.colors

    def color_of(self, index: int) -> Optional[str]:
        return self.colors.get(index)

    # -- edits (each returns True if anything changed)

    def apply(self, indices: Iterable[int], color: str, merge: Optional[Hashable] = None) -> bool:
        color = color.lower()
        new = dict(self.colors)
        for i in indices:
            new[int(i)] = color
        return self._commit(new, merge)

    def clear(self, indices: Iterable[int]) -> bool:
        new = dict(self.colors)
        for i in indices:
            new.pop(int(i), None)
        return self._commit(new, None)

    def clear_all(self) -> bool:
        return self._commit({}, None)

    # -- history

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.colors)
        self.colors = self._undo.pop()
        self._open = None
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.colors)
        self.colors = self._redo.pop()
        self._open = None
        return True

    def _commit(self, new: dict[int, str], merge: Optional[Hashable]) -> bool:
        if new == self.colors:
            return False
        if merge is None or merge != self._open or not self._undo:
            self._undo.append(self.colors)  # else: keep the older "before" state, one step per gesture
            del self._undo[:-MAX_UNDO]
        self._open = merge
        self.colors = new
        self._redo.clear()
        return True
