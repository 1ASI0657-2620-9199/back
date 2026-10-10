"""Strategy pattern preserving the backend's existing request-hash format."""

import hashlib
import json
from typing import Any, Mapping, Protocol


class FingerprintStrategy(Protocol):
    def fingerprint(self, payload: Mapping[str, Any]) -> str:
        """Return a deterministic fingerprint of a JSON-compatible payload."""
        ...


class CanonicalJsonSha256:
    def fingerprint(self, payload: Mapping[str, Any]) -> str:
        encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class FingerprintService:
    def __init__(self, strategy: FingerprintStrategy | None = None) -> None:
        self.strategy = strategy if strategy is not None else CanonicalJsonSha256()

    def fingerprint(self, payload: Mapping[str, Any]) -> str:
        return self.strategy.fingerprint(payload)
