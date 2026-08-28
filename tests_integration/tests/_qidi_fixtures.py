"""Build a synthetic Qidi MIFARE Classic block for integration tests.

Same construction as spoolman's own unit tests (tests/test_qidi_codec.py,
tests/test_tag_decode.py), duplicated here rather than imported: this package runs inside
the separate `spoolman-tester` container/image, which has no access to the `spoolman`
package's own test modules. Like TigerTag, this needs no extra dependency.
"""

MIFARE_BLOCK_SIZE = 16


def build_qidi(*, material_code: int, color_code: int, manufacturer_code: int = 1) -> bytes:
    """Build a 16-byte MIFARE Classic block (sector 1, block 0) of Qidi tag data."""
    block = bytearray(MIFARE_BLOCK_SIZE)
    block[0] = material_code & 0xFF
    block[1] = color_code & 0xFF
    block[2] = manufacturer_code & 0xFF
    return bytes(block)
