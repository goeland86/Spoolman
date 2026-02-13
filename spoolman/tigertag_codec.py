"""TigerTag NTAG213 binary encoder/decoder.

Implements the TigerTag Maker format for encoding/decoding filament data
to/from NTAG213 NFC chips (144 bytes user memory, pages 4-39).

Based on the TigerTag RFID Guide specification.
"""

import struct
from dataclasses import dataclass, field
from typing import Optional

# NTAG213 has 144 bytes of user memory (pages 4-39, 36 pages x 4 bytes)
NTAG213_USER_BYTES = 144


@dataclass
class TigerTagData:
    """Represents all data fields stored on a TigerTag NFC chip."""

    # Core identifiers
    id_tigertag: int = 0  # 4 bytes - unique tag ID
    id_product: int = 0  # 4 bytes - product/filament ID
    id_material: int = 0  # 2 bytes - material type ID
    id_diameter: int = 0  # 1 byte - diameter ID (0=unknown, 1=1.75mm, 2=2.85mm)
    id_aspect: int = 0  # 2 bytes - aspect/finish ID
    id_type: int = 0  # 1 byte - type ID
    id_brand: int = 0  # 2 bytes - brand/manufacturer ID

    # Color as RGBA
    color_r: int = 0  # 1 byte
    color_g: int = 0  # 1 byte
    color_b: int = 0  # 1 byte
    color_a: int = 255  # 1 byte

    # Spool properties
    weight: int = 0  # 2 bytes - net weight in grams
    volume: int = 0  # 2 bytes - volume in cm3

    # Temperature settings
    nozzle_temp: int = 0  # 2 bytes - nozzle temp in C
    bed_temp: int = 0  # 2 bytes - bed temp in C
    drying_temp: int = 0  # 2 bytes - drying temp in C
    drying_duration: int = 0  # 2 bytes - drying duration in minutes

    # Metadata
    timestamp: int = 0  # 4 bytes - Unix timestamp
    emoji: int = 0  # 4 bytes - emoji codepoint

    # User message - 28 bytes UTF-8
    user_message: str = ""

    @property
    def color_hex(self) -> str:
        """Get color as hex string (without alpha)."""
        return f"{self.color_r:02x}{self.color_g:02x}{self.color_b:02x}"

    @color_hex.setter
    def color_hex(self, value: str) -> None:
        """Set color from hex string."""
        value = value.lstrip("#")
        if len(value) == 6:
            self.color_r = int(value[0:2], 16)
            self.color_g = int(value[2:4], 16)
            self.color_b = int(value[4:6], 16)
        elif len(value) == 8:
            self.color_r = int(value[0:2], 16)
            self.color_g = int(value[2:4], 16)
            self.color_b = int(value[4:6], 16)
            self.color_a = int(value[6:8], 16)

    @property
    def diameter_mm(self) -> float:
        """Get diameter in mm from the diameter ID."""
        if self.id_diameter == 1:
            return 1.75
        if self.id_diameter == 2:
            return 2.85
        return 0.0


# Binary format layout (144 bytes total, big-endian):
# Offset  Size  Field
# 0       4     id_tigertag
# 4       4     id_product
# 8       2     id_material
# 10      1     id_diameter
# 11      2     id_aspect
# 13      1     id_type
# 14      2     id_brand
# 16      1     color_r
# 17      1     color_g
# 18      1     color_b
# 19      1     color_a
# 20      2     weight
# 22      2     volume
# 24      2     nozzle_temp
# 26      2     bed_temp
# 28      2     drying_temp
# 30      2     drying_duration
# 32      4     timestamp
# 36      4     emoji
# 40      28    user_message (UTF-8, null-padded)
# 68-143  76    reserved (zeros)

_HEADER_FORMAT = "!II HBH BH BBBB HH HH HH HH I I"
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)  # 40 bytes
_USER_MESSAGE_SIZE = 28
_DATA_SIZE = _HEADER_SIZE + _USER_MESSAGE_SIZE  # 68 bytes


def decode_ntag213(raw_bytes: bytes) -> TigerTagData:
    """Decode raw NTAG213 user memory bytes into TigerTagData.

    Args:
        raw_bytes: The raw bytes from NTAG213 pages 4-39 (up to 144 bytes).

    Returns:
        TigerTagData: The decoded tag data.

    Raises:
        ValueError: If the data is too short to decode.

    """
    if len(raw_bytes) < _DATA_SIZE:
        raise ValueError(f"Data too short: expected at least {_DATA_SIZE} bytes, got {len(raw_bytes)}")

    values = struct.unpack_from(_HEADER_FORMAT, raw_bytes, 0)

    data = TigerTagData(
        id_tigertag=values[0],
        id_product=values[1],
        id_material=values[2],
        id_diameter=values[3],
        id_aspect=values[4],
        id_type=values[5],
        id_brand=values[6],
        color_r=values[7],
        color_g=values[8],
        color_b=values[9],
        color_a=values[10],
        weight=values[11],
        volume=values[12],
        nozzle_temp=values[13],
        bed_temp=values[14],
        drying_temp=values[15],
        drying_duration=values[16],
        timestamp=values[17],
        emoji=values[18],
    )

    # Decode user message (28 bytes, UTF-8, null-terminated)
    msg_bytes = raw_bytes[_HEADER_SIZE : _HEADER_SIZE + _USER_MESSAGE_SIZE]
    # Strip null bytes
    null_idx = msg_bytes.find(b"\x00")
    if null_idx >= 0:
        msg_bytes = msg_bytes[:null_idx]
    data.user_message = msg_bytes.decode("utf-8", errors="replace")

    return data


def encode_ntag213(data: TigerTagData) -> bytes:
    """Encode TigerTagData into raw bytes for NTAG213 user memory.

    Returns:
        bytes: 144 bytes to write to NTAG213 pages 4-39.

    """
    header = struct.pack(
        _HEADER_FORMAT,
        data.id_tigertag,
        data.id_product,
        data.id_material,
        data.id_diameter,
        data.id_aspect,
        data.id_type,
        data.id_brand,
        data.color_r,
        data.color_g,
        data.color_b,
        data.color_a,
        data.weight,
        data.volume,
        data.nozzle_temp,
        data.bed_temp,
        data.drying_temp,
        data.drying_duration,
        data.timestamp,
        data.emoji,
    )

    # Encode user message (28 bytes, null-padded)
    msg_bytes = data.user_message.encode("utf-8")[:_USER_MESSAGE_SIZE]
    msg_padded = msg_bytes.ljust(_USER_MESSAGE_SIZE, b"\x00")

    # Combine header + message + padding to fill 144 bytes
    payload = header + msg_padded
    padding = b"\x00" * (NTAG213_USER_BYTES - len(payload))

    return payload + padding
