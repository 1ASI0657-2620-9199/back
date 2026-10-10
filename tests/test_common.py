"""Compatibility matters because persisted idempotency hashes predate this package."""

import hashlib
import json

from finanzen_common import FingerprintService


def test_common_preserves_hashes_of_existing_unicode_payloads():
    payload = {"description": "Educación y café", "amount": "1000.00", "type": "income"}
    legacy = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert FingerprintService().fingerprint(payload) == legacy


def test_common_ignores_mapping_key_order():
    service = FingerprintService()
    assert service.fingerprint({"amount": "1.00", "type": "income"}) == service.fingerprint(
        {"type": "income", "amount": "1.00"}
    )


def test_common_detects_changed_content_without_mutating_input():
    original = {"amount": "1.00", "nested": {"category": "salary"}}
    snapshot = json.loads(json.dumps(original))
    service = FingerprintService()
    assert service.fingerprint(original) != service.fingerprint({**original, "amount": "2.00"})
    assert original == snapshot


def test_common_accepts_an_injected_strategy():
    class RecordingStrategy:
        def __init__(self):
            self.received = None

        def fingerprint(self, payload):
            self.received = payload
            return "example-fingerprint"

    strategy = RecordingStrategy()
    payload = {"amount": "1.00"}
    assert FingerprintService(strategy).fingerprint(payload) == "example-fingerprint"
    assert strategy.received is payload
