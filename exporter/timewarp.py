from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Break:
    start_ms: int
    end_ms: int
    slate_ms: int = 3000


class Timewarp:
    """
    Maps original session time -> output time by removing breaks and inserting a short slate.
    """

    def __init__(self, breaks: list[Break]):
        self.breaks = sorted(breaks, key=lambda b: b.start_ms)

    def map_time(self, t_ms: int) -> int:
        t_ms = int(t_ms)
        shift = 0
        for b in self.breaks:
            if t_ms < b.start_ms:
                break
            if t_ms >= b.end_ms:
                shift += (b.end_ms - b.start_ms) - b.slate_ms
            else:
                # If inside break, clamp to start + slate
                return int(b.start_ms - shift + b.slate_ms)
        return int(t_ms - shift)

    def is_inside_break(self, t_ms: int) -> bool:
        t_ms = int(t_ms)
        return any(b.start_ms <= t_ms < b.end_ms for b in self.breaks)

