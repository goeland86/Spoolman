"""Tests for the Qidi MIFARE Classic 1K codec."""

import pytest

from spoolman.qidi_codec import (
    MIFARE_BLOCK_SIZE,
    QidiTagData,
    color_code_from_hex,
    decode_qidi_block,
    encode_qidi_block,
    is_valid_qidi_block,
    material_code_from_name,
)

# --- QidiTagData properties --------------------------------------------------------


def test_material_name_and_type_for_known_code() -> None:
    data = QidiTagData(material_code=5)  # PLA-CF
    assert data.material_name == "PLA-CF"
    assert data.material_type == "PLA-CF"


def test_material_name_for_known_code_with_different_spoolman_type() -> None:
    data = QidiTagData(material_code=4)  # PLA Silk -> PLA
    assert data.material_name == "PLA Silk"
    assert data.material_type == "PLA"


def test_material_name_and_type_unknown_code() -> None:
    data = QidiTagData(material_code=200)
    assert data.material_name == "Unknown (200)"
    assert data.material_type == "Unknown"


def test_color_name_and_hex_for_known_code() -> None:
    data = QidiTagData(color_code=18)  # Red
    assert data.color_name == "Red"
    assert data.color_hex == "FF362D"


def test_color_name_and_hex_unknown_code() -> None:
    data = QidiTagData(color_code=99)
    assert data.color_name == "Unknown (99)"
    assert data.color_hex == "000000"


# --- decode_qidi_block / encode_qidi_block ------------------------------------------


def test_decode_reads_first_three_bytes() -> None:
    raw = bytes([5, 18, 1]) + bytes(13)
    data = decode_qidi_block(raw)
    assert (data.material_code, data.color_code, data.manufacturer_code) == (5, 18, 1)


def test_decode_raises_on_too_short_data() -> None:
    with pytest.raises(ValueError, match="Data too short"):
        decode_qidi_block(bytes([1, 2]))


def test_encode_produces_full_block_size_zero_padded() -> None:
    data = QidiTagData(material_code=5, color_code=18, manufacturer_code=1)
    block = encode_qidi_block(data)
    assert len(block) == MIFARE_BLOCK_SIZE
    assert block[:3] == bytes([5, 18, 1])
    assert block[3:] == bytes(13)


def test_round_trip_preserves_fields() -> None:
    original = QidiTagData(material_code=42, color_code=7, manufacturer_code=1)
    decoded = decode_qidi_block(encode_qidi_block(original))
    assert (decoded.material_code, decoded.color_code, decoded.manufacturer_code) == (42, 7, 1)


def test_encode_masks_fields_to_a_byte() -> None:
    data = QidiTagData(material_code=0x1FF, color_code=0x2FF, manufacturer_code=0x3FF)
    block = encode_qidi_block(data)
    assert block[:3] == bytes([0xFF, 0xFF, 0xFF])


# --- is_valid_qidi_block ------------------------------------------------------------


def test_is_valid_qidi_block_true_for_well_formed_block() -> None:
    raw = bytes([5, 18, 1]) + bytes(13)
    assert is_valid_qidi_block(raw)


def test_is_valid_qidi_block_false_when_too_short() -> None:
    assert not is_valid_qidi_block(bytes([5, 18, 1]))


def test_is_valid_qidi_block_false_when_padding_nonzero() -> None:
    raw = bytes([5, 18, 1]) + bytes([1]) + bytes(12)
    assert not is_valid_qidi_block(raw)


def test_is_valid_qidi_block_false_when_material_out_of_range() -> None:
    raw = bytes([51, 18, 1]) + bytes(13)
    assert not is_valid_qidi_block(raw)
    raw_zero = bytes([0, 18, 1]) + bytes(13)
    assert not is_valid_qidi_block(raw_zero)


def test_is_valid_qidi_block_false_when_color_out_of_range() -> None:
    raw = bytes([5, 25, 1]) + bytes(13)
    assert not is_valid_qidi_block(raw)


def test_is_valid_qidi_block_false_when_manufacturer_not_one() -> None:
    raw = bytes([5, 18, 2]) + bytes(13)
    assert not is_valid_qidi_block(raw)


# --- material_code_from_name --------------------------------------------------------


def test_material_code_from_name_exact_match() -> None:
    assert material_code_from_name("PLA-CF") == 5


def test_material_code_from_name_is_case_insensitive() -> None:
    assert material_code_from_name("pla silk") == 4


def test_material_code_from_name_unknown_returns_none() -> None:
    assert material_code_from_name("Nonexistent Material") is None


# --- color_code_from_hex -------------------------------------------------------------


def test_color_code_from_hex_exact_match() -> None:
    assert color_code_from_hex("FF362D") == 18  # Red


def test_color_code_from_hex_exact_match_with_hash_and_case() -> None:
    assert color_code_from_hex("#ff362d") == 18


def test_color_code_from_hex_nearest_neighbor() -> None:
    # One bit off pure white (FAFAFA); should still resolve to White (code 1).
    assert color_code_from_hex("FBFBFB") == 1


def test_color_code_from_hex_too_short_returns_none() -> None:
    assert color_code_from_hex("abc") is None
