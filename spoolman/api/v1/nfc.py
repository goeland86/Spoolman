"""NFC tag reader/writer API endpoints."""

import base64
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.database.database import get_db_session
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
        spool = result.scalar_one_or_none()

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
