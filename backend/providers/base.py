from typing import List, Optional, Protocol

import numpy as np

from ..models import SessionConfig, TranscriptSegment


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, action: Optional[str] = None):
        self.code = code
        self.message = message
        self.action = action
        super().__init__("%s: %s" % (code, message))

    def to_dict(self):
        payload = {"code": self.code, "message": self.message}
        if self.action:
            payload["action"] = self.action
        return payload


class TranscriptionProvider(Protocol):
    name: str
    model: Optional[str]

    def start(self, config: SessionConfig) -> None:
        ...

    def push(self, audio: np.ndarray) -> List[TranscriptSegment]:
        ...

    def flush(self) -> List[TranscriptSegment]:
        ...

    def close(self) -> None:
        ...
