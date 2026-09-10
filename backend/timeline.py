"""Map provider-relative transcript time onto the recording timeline.

Providers report segment offsets relative to the audio they actually saw. Two
gaps make that differ from the audio the browser recorded:

* capture started before the model accepted its first chunk, and
* backpressure dropped audio that the recorder still wrote to disk.

The aligner keeps the recording origin and the dropped duration so far, so a
segment can be reported at the position the recording playhead must sit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CaptureTimeline:
    origin_ms: int = 0
    dropped_ms: int = 0
    origin_known: bool = False

    def observe(self, offset_ms: Optional[int]) -> None:
        """Record where the newest audio chunk sits on the client capture clock."""
        value = _non_negative_int(offset_ms)
        if value is None:
            return
        if not self.origin_known:
            # The first chunk the model accepts already has a recording
            # position: capture may have started well before the model was up.
            self.origin_known = True
            self.origin_ms = value

    def mark_dropped(self, duration_ms: int) -> None:
        """Count audio that was discarded after it had been captured."""
        value = _non_negative_int(duration_ms)
        if value:
            self.dropped_ms += value

    def absolute_ms(self, provider_ms: int) -> int:
        """Return the recording position for a provider-relative offset."""
        return max(0, self.origin_ms + self.dropped_ms + int(provider_ms))


def _non_negative_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(0, parsed)
