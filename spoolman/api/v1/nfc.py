"""NFC tag reader/writer API endpoints."""

import base64
import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.database import filament as filament_db
from spoolman.database import spool as spool_db
from spoolman.database import vendor as vendor_db
from spoolman.database.database import get_db_session
from spoolman.database.models import Filament, Vendor
from spoolman.env import is_nfc_enabled

router = APIRouter(
    prefix="/nfc",
    tags=["nfc"],
)

# ruff: noqa: D103,B008

logger = logging.getLogger(__name__)


class NfcStatusResponse(BaseModel):
    """Response for NFC status endpoint."""

    enabled: bool = Field(description="Whether server-side NFC is enabled.")
    status: str = Field(description="Reader status: 'connected', 'disconnected', 'disabled', etc.")


class TigerTagDataResponse(BaseModel):
    """Decoded TigerTag data from an NFC tag."""

    id_tigertag: int = 0
    id_product: int = 0
    id_material: int = 0
    id_diameter: int = 0
    id_brand: int = 0
    color_hex: str = ""
    weight: int = 0
    nozzle_temp: int = 0
    bed_temp: int = 0
    drying_temp: int = 0
    drying_duration: int = 0
    user_message: str = ""
    diameter_mm: float = 0.0


class NfcReadResponse(BaseModel):
    """Response for NFC read endpoint."""

    success: bool
    tag_data: Optional[TigerTagDataResponse] = None
    spool_id: Optional[int] = None
    raw_data_b64: Optional[str] = None
    message: str = ""


class NfcEncodeRequest(BaseModel):
    """Request body for NFC encode endpoint."""

    spool_id: int = Field(description="The spool ID to encode as TigerTag binary.")
    user_message: str = Field(default="", max_length=28, description="Optional user message (max 28 chars).")


class NfcEncodeResponse(BaseModel):
    """Response for NFC encode endpoint."""

    success: bool
    binary_b64: str = Field(default="", description="Base64-encoded 144-byte TigerTag binary.")
    message: str = ""


class NfcWriteRequest(BaseModel):
    """Request body for NFC write endpoint."""

    spool_id: int = Field(description="The spool ID to encode onto the NFC tag.")
    user_message: str = Field(default="", max_length=28, description="Optional user message (max 28 chars).")


class NfcWriteResponse(BaseModel):
    """Response for NFC write endpoint."""

    success: bool
    message: str = ""


@router.get(
    "/status",
    name="Get NFC reader status",
    response_model=NfcStatusResponse,
)
async def nfc_status() -> NfcStatusResponse:
    """Get the status of the server-side NFC reader."""
    if not is_nfc_enabled():
        return NfcStatusResponse(enabled=False, status="disabled")

    try:
        from spoolman.nfc_service import nfc_service  # noqa: PLC0415

        return NfcStatusResponse(enabled=True, status=nfc_service.get_status())
    except Exception:
        logger.exception("Error getting NFC status")
        return NfcStatusResponse(enabled=True, status="error")


@router.post(
    "/read",
    name="Read NFC tag",
    response_model=NfcReadResponse,
)
async def nfc_read(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NfcReadResponse:
    """Read an NFC tag via the server-side reader, decode TigerTag data, and match to a spool."""
    if not is_nfc_enabled():
        return NfcReadResponse(success=False, message="NFC is not enabled on the server.")

    try:
        from spoolman.nfc_service import nfc_service  # noqa: PLC0415
        from spoolman.tigertag_codec import decode_ntag213  # noqa: PLC0415
        from spoolman.tigertag_lookup import find_spool_by_tigertag  # noqa: PLC0415

        raw_data = nfc_service.read_tag(timeout=10.0)
        if raw_data is None:
            return NfcReadResponse(success=False, message="No tag detected. Please place a tag on the reader.")

        # Decode TigerTag data
        tag_data = decode_ntag213(raw_data)

        tag_response = TigerTagDataResponse(
            id_tigertag=tag_data.id_tigertag,
            id_product=tag_data.id_product,
            id_material=tag_data.id_material,
            id_diameter=tag_data.id_diameter,
            id_brand=tag_data.id_brand,
            color_hex=tag_data.color_hex,
            weight=tag_data.weight,
            nozzle_temp=tag_data.nozzle_temp,
            bed_temp=tag_data.bed_temp,
            drying_temp=tag_data.drying_temp,
            drying_duration=tag_data.drying_duration,
            user_message=tag_data.user_message,
            diameter_mm=tag_data.diameter_mm,
        )

        # Try to match to a spool
        spool = await find_spool_by_tigertag(db, tag_data)
        spool_id = spool.id if spool else None

        return NfcReadResponse(
            success=True,
            tag_data=tag_response,
            spool_id=spool_id,
            raw_data_b64=base64.b64encode(raw_data).decode("ascii"),
            message="Tag read successfully." if spool_id else "Tag read but no matching spool found.",
        )

    except Exception:
        logger.exception("Error reading NFC tag")
        return NfcReadResponse(success=False, message="Failed to read NFC tag.")


@router.post(
    "/write",
    name="Write NFC tag",
    response_model=NfcWriteResponse,
)
async def nfc_write(
    request: NfcWriteRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NfcWriteResponse:
    """Encode spool data as TigerTag Maker format and write to an NFC tag."""
    if not is_nfc_enabled():
        return NfcWriteResponse(success=False, message="NFC is not enabled on the server.")

    try:
        from sqlalchemy import select  # noqa: PLC0415
        from sqlalchemy.orm import selectinload  # noqa: PLC0415

        from spoolman.database.models import Filament, Spool  # noqa: PLC0415
        from spoolman.nfc_service import nfc_service  # noqa: PLC0415
        from spoolman.tigertag_codec import encode_ntag213  # noqa: PLC0415
        from spoolman.tigertag_lookup import map_spool_to_tigertag  # noqa: PLC0415

        # Fetch the spool
        stmt = (
            select(Spool)
            .options(selectinload(Spool.filament).selectinload(Filament.vendor))
            .where(Spool.id == request.spool_id)
        )
        result = await db.execute(stmt)
        spool = result.unique().scalar_one_or_none()

        if spool is None:
            return NfcWriteResponse(success=False, message=f"Spool with ID {request.spool_id} not found.")

        # Map spool to TigerTag data
        tag_data = map_spool_to_tigertag(spool)
        tag_data.user_message = request.user_message

        # Encode to binary
        raw_data = encode_ntag213(tag_data)

        # Write to tag
        success = nfc_service.write_tag(raw_data)
        if success:
            return NfcWriteResponse(success=True, message="Tag written successfully.")
        return NfcWriteResponse(success=False, message="Failed to write tag. Ensure tag is placed on reader.")

    except Exception:
        logger.exception("Error writing NFC tag")
        return NfcWriteResponse(success=False, message="Failed to write NFC tag.")


@router.post(
    "/encode",
    name="Encode spool as TigerTag binary",
    response_model=NfcEncodeResponse,
)
async def nfc_encode(
    request: NfcEncodeRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NfcEncodeResponse:
    """Encode spool data as TigerTag Maker binary. Returns base64-encoded 144-byte payload.

    This endpoint does not require NFC hardware — it just generates the binary data.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415
        from sqlalchemy.orm import selectinload  # noqa: PLC0415

        from spoolman.database.models import Filament, Spool  # noqa: PLC0415
        from spoolman.tigertag_codec import encode_ntag213  # noqa: PLC0415
        from spoolman.tigertag_lookup import map_spool_to_tigertag  # noqa: PLC0415

        # Fetch the spool
        stmt = (
            select(Spool)
            .options(selectinload(Spool.filament).selectinload(Filament.vendor))
            .where(Spool.id == request.spool_id)
        )
        result = await db.execute(stmt)
        spool = result.unique().scalar_one_or_none()

        if spool is None:
            return NfcEncodeResponse(success=False, message=f"Spool with ID {request.spool_id} not found.")

        # Map spool to TigerTag data
        tag_data = map_spool_to_tigertag(spool)
        tag_data.user_message = request.user_message

        # Encode to binary
        raw_data = encode_ntag213(tag_data)

        return NfcEncodeResponse(
            success=True,
            binary_b64=base64.b64encode(raw_data).decode("ascii"),
            message="Encoded successfully.",
        )

    except Exception:
        logger.exception("Error encoding TigerTag data")
        return NfcEncodeResponse(success=False, message="Failed to encode TigerTag data.")


class NfcCreateFromTagRequest(BaseModel):
    """Request body for creating a spool from decoded TigerTag data."""

    id_product: int = Field(default=0, description="TigerTag product ID.")
    id_material: int = Field(default=0, description="TigerTag material type ID.")
    id_diameter: int = Field(default=0, description="TigerTag diameter ID (1=1.75mm, 2=2.85mm).")
    id_brand: int = Field(default=0, description="TigerTag brand ID.")
    color_hex: str = Field(default="", description="Color hex string (without #).")
    weight: int = Field(default=0, description="Filament weight in grams.")
    nozzle_temp: int = Field(default=0, description="Nozzle temperature in °C.")
    bed_temp: int = Field(default=0, description="Bed temperature in °C.")
    drying_temp: int = Field(default=0, description="Drying temperature in °C.")
    drying_duration: int = Field(default=0, description="Drying duration in minutes.")
    diameter_mm: float = Field(default=0.0, description="Diameter in mm (decoded from id_diameter).")


class NfcCreateFromTagResponse(BaseModel):
    """Response for creating a spool from TigerTag data."""

    success: bool
    spool_id: Optional[int] = None
    message: str = ""


async def _find_or_create_vendor(db, name: str) -> int:
    """Find a vendor by name or create one."""
    vendors, _ = await vendor_db.find(db=db, name=name)
    if vendors:
        return vendors[0].id
    new_vendor = await vendor_db.create(db=db, name=name)
    return new_vendor.id


async def _find_filament_by_external_id(db, external_id: str) -> Optional[Filament]:
    """Find an existing filament by external_id."""
    stmt = select(Filament).where(Filament.external_id == external_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _lookup_tigertag_product(id_product: int):
    """Look up a product in the TigerTag external DB cache. Returns ExternalFilament or None."""
    try:
        from spoolman import filecache  # noqa: PLC0415

        data = filecache.get_file_contents("tigertag_filaments.json")
        filaments = json.loads(data)
        target_id = f"tigertag_{id_product}"
        for f in filaments:
            if f.get("id") == target_id:
                from spoolman.externaldb import ExternalFilament  # noqa: PLC0415

                return ExternalFilament(**f)
    except Exception:
        logger.debug("Could not look up TigerTag product %d in cache", id_product)
    return None


@router.post(
    "/create-from-tag",
    name="Create spool from TigerTag data",
    response_model=NfcCreateFromTagResponse,
)
async def nfc_create_from_tag(
    request: NfcCreateFromTagRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NfcCreateFromTagResponse:
    """Create a filament and spool from decoded TigerTag data.

    If the tag's id_product matches an entry in the TigerTag external DB cache,
    rich filament data (name, material, vendor, etc.) is used. Otherwise, a minimal
    filament is created from the raw tag fields.
    """
    try:
        external_id = f"tigertag_{request.id_product}" if request.id_product > 0 else None

        # Step 1: Check for existing filament with this external_id
        existing_filament = None
        if external_id:
            existing_filament = await _find_filament_by_external_id(db, external_id)

        if existing_filament:
            filament_id = existing_filament.id
        else:
            # Step 2: Try to look up rich data from TigerTag external DB cache
            ext_filament = _lookup_tigertag_product(request.id_product) if request.id_product > 0 else None

            if ext_filament:
                # Create vendor if needed
                vendor_id = await _find_or_create_vendor(db, ext_filament.manufacturer)

                db_filament = await filament_db.create(
                    db=db,
                    density=ext_filament.density,
                    diameter=ext_filament.diameter,
                    name=ext_filament.name,
                    vendor_id=vendor_id,
                    material=ext_filament.material,
                    weight=ext_filament.weight,
                    color_hex=ext_filament.color_hex,
                    settings_extruder_temp=ext_filament.extruder_temp,
                    settings_bed_temp=ext_filament.bed_temp,
                    external_id=external_id,
                )
                filament_id = db_filament.id
            else:
                # Step 3: Create minimal filament from raw tag data
                diameter = request.diameter_mm if request.diameter_mm > 0 else (1.75 if request.id_diameter == 1 else 2.85 if request.id_diameter == 2 else 1.75)  # noqa: E501
                color = request.color_hex if request.color_hex else None
                weight = float(request.weight) if request.weight > 0 else None

                db_filament = await filament_db.create(
                    db=db,
                    density=1.24,
                    diameter=diameter,
                    name=f"TigerTag {external_id or 'Unknown'}",
                    weight=weight,
                    color_hex=color,
                    settings_extruder_temp=request.nozzle_temp if request.nozzle_temp > 0 else None,
                    settings_bed_temp=request.bed_temp if request.bed_temp > 0 else None,
                    external_id=external_id,
                )
                filament_id = db_filament.id

        # Step 4: Create spool
        db_spool = await spool_db.create(
            db=db,
            filament_id=filament_id,
        )

        return NfcCreateFromTagResponse(
            success=True,
            spool_id=db_spool.id,
            message="Spool created successfully.",
        )

    except Exception:
        logger.exception("Error creating spool from TigerTag data")
        return NfcCreateFromTagResponse(success=False, message="Failed to create spool from tag data.")
