"""Integration tests for POST /tag/scan with hostile or malformed Qidi payloads.

Qidi stores exactly 3 meaningful bytes (material code, color code, manufacturer code) inside
a 16-byte MIFARE Classic block, each read straight from a fixed index -- no CBOR, no dynamic
typing, no NDEF. The CBOR-specific type-confusion cases test_scan_hostile.py covers (a number
field holding a list, a bignum, NaN, Infinity) cannot arrive here: nothing in this format is
ever anything but a single byte, 0-255. What this format *can* carry that still needs
guarding against is a block that's the wrong shape entirely (too short, or non-Qidi MIFARE
data with nonzero padding) and a value that's a technically-valid byte but outside the real
spec's defined code ranges -- this file covers those instead.
"""

import base64
import uuid
from typing import Any

import httpx

from .._qidi_fixtures import build_qidi
from ..conftest import URL, assert_httpx_success
from .test_scan_create import _delete_created_spool
from .test_scan_hostile import _assert_list_endpoints_still_work, _row_counts


def _uid() -> str:
    return uuid.uuid4().hex[:14].upper()


def _reader_id() -> str:
    return f"reader-{uuid.uuid4().hex[:8]}"


def _scan(raw: bytes, *, create: bool | None = None) -> httpx.Response:
    body: dict[str, Any] = {
        "uid": _uid(),
        "reader_id": _reader_id(),
        "format": "qidi",
        "payload_b64": base64.b64encode(raw).decode("ascii"),
    }
    if create is not None:
        body["create"] = create
    return httpx.post(f"{URL}/api/v1/tag/scan", json=body)


def test_too_short_payload_creates_nothing():
    """Below the full 16-byte MIFARE block -- is_valid_qidi_block requires the whole block."""
    before = _row_counts()
    result = _scan(bytes([5, 18, 1]), create=True)
    assert_httpx_success(result)
    body = result.json()

    assert body.get("created") is not True
    assert "decoded" not in body
    assert _row_counts() == before
    _assert_list_endpoints_still_work()


def test_nonzero_padding_creates_nothing():
    """Non-Qidi MIFARE data (or just a card with leftover bytes) must not be read as Qidi."""
    before = _row_counts()
    raw = build_qidi(material_code=5, color_code=18, padding=bytes([0xFF]) + bytes(12))
    result = _scan(raw, create=True)
    assert_httpx_success(result)
    body = result.json()

    assert body.get("created") is not True
    assert "decoded" not in body
    assert _row_counts() == before
    _assert_list_endpoints_still_work()


def test_unmapped_material_code_still_creates_without_a_name():
    """A code in the valid 1-50 range but absent from MATERIAL_CODE_MAP (e.g. 9) isn't invalid.

    is_valid_qidi_block accepts it -- it's the real spec's own gap, confirmed against two
    independent third-party readers (see test_qidi_codec.py) -- so create still succeeds,
    just without a material name, same as TigerTag's unmatched-tag case.
    """
    raw = build_qidi(material_code=9, color_code=18)
    result = _scan(raw, create=True)
    assert_httpx_success(result)
    body = result.json()
    try:
        assert body["created"] is True
        assert body["spool"]["filament"].get("material") is None
        assert body["spool"]["filament"]["color_hex"] == "FF362D"
    finally:
        _delete_created_spool(body["spool"])


def test_codes_at_the_top_of_each_valid_range_still_create():
    """50/24 are the real spec's highest defined codes (TPU / Dark Orange), not a rounding."""
    raw = build_qidi(material_code=50, color_code=24)
    result = _scan(raw, create=True)
    assert_httpx_success(result)
    body = result.json()
    try:
        assert body["created"] is True
        assert body["spool"]["filament"]["material"] == "TPU"
    finally:
        _delete_created_spool(body["spool"])
