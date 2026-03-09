"""Functions for syncing data from the TigerTag external filament database."""

import datetime
import json
import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel
from scheduler.asyncio.scheduler import Scheduler

from spoolman import filecache
from spoolman.env import get_tigertag_api_url, get_tigertag_sync_interval, is_tigertag_enabled
from spoolman.externaldb import ExternalFilament

logger = logging.getLogger(__name__)

TIGERTAG_CACHE_FILE = "tigertag_filaments.json"


class TigerTagBrand(BaseModel):
    """A brand/manufacturer from the TigerTag API."""

    id_brand: int
    name: str


class TigerTagMaterial(BaseModel):
    """A material/type from the TigerTag API."""

    id_type: int
    name: str
    density: Optional[float] = None


class TigerTagProduct(BaseModel):
    """A filament product from the TigerTag API."""

    id: int
    product_type: Optional[str] = None
    brand: Optional[str] = None
    title: Optional[str] = None
    material: Optional[str] = None
    color: Optional[str] = None
    color_info: Optional[dict] = None
    measure: Optional[str] = None
    sku: Optional[str] = None


def _parse_weight_from_measure(measure: Optional[str]) -> float:
    """Parse weight in grams from measure string like '1 kg' or '500 g'."""
    if not measure:
        return 1000.0
    measure = measure.strip().lower()
    try:
        if "kg" in measure:
            return float(measure.replace("kg", "").strip()) * 1000
        if "g" in measure:
            return float(measure.replace("g", "").strip())
    except ValueError:
        pass
    return 1000.0


def _to_external_filament(product: TigerTagProduct) -> ExternalFilament:
    """Convert a TigerTag product into an ExternalFilament."""
    manufacturer = product.brand or "Unknown"
    material = product.material or "Unknown"
    name = product.title or f"{manufacturer} {material}"
    weight = _parse_weight_from_measure(product.measure)

    # Clean up color hex - remove leading # and alpha channel if present
    color_hex = None
    if product.color:
        hex_str = product.color.lstrip("#")
        # TigerTag returns 8-char RGBA hex, Spoolman expects 6-char RGB
        if len(hex_str) == 8:
            hex_str = hex_str[:6]
        color_hex = hex_str

    return ExternalFilament(
        id=f"tigertag_{product.id}",
        manufacturer=manufacturer,
        name=name,
        material=material,
        density=1.24,
        weight=weight,
        diameter=1.75,
        color_hex=color_hex,
        source="tigertag",
    )


TIGERTAG_FILAMENT_TYPE_ID = 142
TIGERTAG_PAGE_SIZE = 50


async def _fetch_all_products(base_url: str) -> list[dict]:
    """Fetch all filament products from TigerTag API using pagination."""
    products_url = urljoin(base_url, "product/get/all")
    all_items: list[dict] = []
    page = 1

    async with httpx.AsyncClient() as client:
        while True:
            response = await client.post(
                products_url,
                json={"page": page, "per_page": TIGERTAG_PAGE_SIZE, "product_type_id": TIGERTAG_FILAMENT_TYPE_ID},
            )
            response.raise_for_status()
            data = response.json()
            items = data.get("items", [])
            all_items.extend(items)

            if data.get("nextPage") is None:
                break
            page = data["nextPage"]

    return all_items


async def _sync_tigertag() -> None:
    logger.info("Syncing TigerTag DB.")

    base_url = get_tigertag_api_url()

    try:
        # Fetch all filament products via paginated API
        products_list = await _fetch_all_products(base_url)

        # Parse products
        products = [TigerTagProduct(**p) for p in products_list]

        # Convert to ExternalFilament format
        filaments = [_to_external_filament(p) for p in products]

        # Cache to local file
        filaments_json = json.dumps(
            [f.model_dump(exclude_none=True) for f in filaments],
            ensure_ascii=False,
        )
        filecache.update_file(TIGERTAG_CACHE_FILE, filaments_json.encode("utf-8"))

        logger.info("TigerTag DB synced. Filaments: %d", len(filaments))

    except Exception:
        logger.exception("Failed to sync TigerTag DB")


def get_tigertag_filaments_file():
    """Get the path to the cached TigerTag filaments file."""
    return filecache.get_file(TIGERTAG_CACHE_FILE)


def schedule_tasks(scheduler: Scheduler) -> None:
    """Schedule TigerTag sync tasks.

    Args:
        scheduler: The scheduler to use for scheduling tasks.

    """
    if not is_tigertag_enabled():
        logger.info("TigerTag integration is disabled. Skipping sync.")
        return

    logger.info("Scheduling TigerTag DB sync.")

    # Run once on startup
    scheduler.once(datetime.timedelta(seconds=0), _sync_tigertag)  # type: ignore[arg-type]

    sync_interval = get_tigertag_sync_interval()
    if sync_interval > 0:
        scheduler.cyclic(datetime.timedelta(seconds=sync_interval), _sync_tigertag)  # type: ignore[arg-type]
    else:
        logger.info("TigerTag sync interval is 0, skipping periodic sync.")
