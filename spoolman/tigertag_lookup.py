"""TigerTag to Spoolman spool matching and reverse mapping.

Provides functions to:
- Find a Spoolman spool from decoded TigerTag data
- Map a Spoolman spool/filament to TigerTag binary format
"""

import json
import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from spoolman.database.models import Filament, Spool, Vendor
from spoolman.tigertag_codec import TigerTagData

logger = logging.getLogger(__name__)


async def find_spool_by_tigertag(db: AsyncSession, tag_data: TigerTagData) -> Optional[Spool]:
    """Find a Spoolman spool matching decoded TigerTag data.

    Matching strategy:
    1. Match by external_id == "tigertag_{id_product}" on the filament table
    2. Return the most recent non-archived spool for the matched filament

    Args:
        db: Database session.
        tag_data: Decoded TigerTag data.

    Returns:
        Optional[Spool]: The matched spool, or None if no match found.

    """
    if tag_data.id_product > 0:
        # Strategy 1: Match by external_id
        external_id = f"tigertag_{tag_data.id_product}"
        stmt = (
            select(Spool)
            .join(Spool.filament)
            .options(selectinload(Spool.filament).selectinload(Filament.vendor))
            .where(Filament.external_id == external_id)
            .where(Spool.archived.is_(False))
            .order_by(Spool.registered.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        spool = result.scalar_one_or_none()
        if spool is not None:
            return spool

    return None


def map_spool_to_tigertag(
    spool: Spool,
    brand_map: Optional[dict[str, int]] = None,
    material_map: Optional[dict[str, int]] = None,
    diameter_map: Optional[float] = None,
) -> TigerTagData:
    """Map a Spoolman spool/filament to TigerTag binary data.

    Args:
        spool: The Spoolman spool to encode.
        brand_map: Optional mapping of brand name -> TigerTag brand ID.
        material_map: Optional mapping of material name -> TigerTag material type ID.
        diameter_map: Not used, diameter is determined from filament data.

    Returns:
        TigerTagData: The TigerTag data ready for encoding.

    """
    filament = spool.filament
    data = TigerTagData()

    # Try to extract product ID from external_id
    if filament.external_id and filament.external_id.startswith("tigertag_"):
        try:
            data.id_product = int(filament.external_id.split("_", 1)[1])
        except (ValueError, IndexError):
            pass

    # Brand ID lookup
    if brand_map and filament.vendor and filament.vendor.name:
        vendor_name = filament.vendor.name.lower()
        for name, brand_id in brand_map.items():
            if name.lower() == vendor_name:
                data.id_brand = brand_id
                break

    # Material ID lookup
    if material_map and filament.material:
        material_name = filament.material.lower()
        for name, material_id in material_map.items():
            if name.lower() == material_name:
                data.id_material = material_id
                break

    # Diameter
    if filament.diameter:
        if abs(filament.diameter - 1.75) < 0.1:
            data.id_diameter = 1
        elif abs(filament.diameter - 2.85) < 0.1:
            data.id_diameter = 2

    # Color
    if filament.color_hex:
        data.color_hex = filament.color_hex

    # Weight
    if filament.weight:
        data.weight = int(filament.weight)

    # Temperatures
    if filament.settings_extruder_temp:
        data.nozzle_temp = filament.settings_extruder_temp
    if filament.settings_bed_temp:
        data.bed_temp = filament.settings_bed_temp

    # Timestamp
    data.timestamp = int(time.time())

    return data


def _load_tigertag_brand_map() -> dict[str, int]:
    """Load brand name -> ID mapping from cached TigerTag data."""
    try:
        from spoolman import filecache  # noqa: PLC0415

        data = filecache.get_file_contents("tigertag_filaments.json")
        filaments = json.loads(data)
        brand_map: dict[str, int] = {}
        for f in filaments:
            # Extract brand from the filament entries
            manufacturer = f.get("manufacturer", "")
            fid = f.get("id", "")
            if manufacturer and fid.startswith("tigertag_"):
                # We don't have direct brand IDs in the filament cache,
                # so this mapping is approximate
                pass
        return brand_map
    except Exception:
        return {}
