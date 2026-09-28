"""Remote pane registry: what the OTHER linked windows hold (pure data).

The registry serves COLD paths only -- tag-menu vocabulary, audience counts,
chip/title state, and the publish-traffic gate. Delivery correctness never
depends on it: receivers apply the R3 domain rule against their OWN panes,
so a stale roster can at worst briefly suppress a publish (the next digest
heals it); it can never mis-deliver. Audience counters skip ``chat`` panes
(a chat tile's tags are organizational, like locally); they still feed the
vocabulary union.
"""

from typing import Dict, List, Sequence


class RemoteRegistry:
    """A snapshot of every other window: ``wid -> {name, panes}``.

    Replaced wholesale by rosters/welcomes; the workspace never mutates it.
    """

    def __init__(self) -> None:
        self._windows: Dict[str, Dict] = {}

    # --- maintenance ----------------------------------------------------------
    def apply_roster(self, windows: Sequence[Dict], self_id: str) -> None:
        """Wholesale replace, skipping our own row (we own our local truth)."""
        self._windows = {
            w["id"]: {"name": w["name"], "panes": list(w["panes"])}
            for w in windows if w["id"] != self_id
        }

    def prune(self, window_id: str) -> None:
        self._windows.pop(window_id, None)

    def clear(self) -> None:
        self._windows.clear()

    # --- queries ---------------------------------------------------------------
    def other_windows(self) -> int:
        return len(self._windows)

    def display_name(self, window_id: str) -> str:
        row = self._windows.get(window_id)
        return row["name"] if row else "another window"

    def known_tags(self) -> List[str]:
        """Union vocabulary in roster order (the hub keeps join order, so
        this is stable in practice and cosmetic even when it is not)."""
        out: List[str] = []
        for row in self._windows.values():
            for pane in row["panes"]:
                for tag in pane["tags"]:
                    if tag not in out:
                        out.append(tag)
        return out

    def _audience_panes(self) -> List[Sequence[str]]:
        """Alive console panes of other windows: the only panes that can
        ever receive a mirrored key or a scoped run."""
        return [
            pane["tags"]
            for row in self._windows.values()
            for pane in row["panes"]
            if pane["kind"] == "term" and pane["alive"]
        ]

    def alive_panes_with_tag(self, tag: str) -> int:
        return sum(1 for tags_ in self._audience_panes() if tag in tags_)

    def alive_pane_count(self) -> int:
        """Every alive console pane in other windows: the audience of the
        left-click (all-windows) sync arm."""
        return len(self._audience_panes())

    def windows_with_tag(self, tag: str) -> int:
        return sum(
            1
            for row in self._windows.values()
            if any(
                pane["kind"] == "term" and pane["alive"] and tag in pane["tags"]
                for pane in row["panes"]
            )
        )
