"""Functions for syncing data from the TigerTag external filament database."""

import datetime
import json
import logging
from typing import Optional
from urllib.parse import urljoin

import hishel
from pydantic import BaseModel
from scheduler.asyncio.scheduler import Scheduler

from spoolman import filecache
from spoolman.env import get_cache_dir, get_tigertag_api_url, get_tigertag_sync_interval, is_tigertag_enabled
from spoolman.externaldb import ExternalFilament

logger = logging.getLogger(__name__)

TIGERTAG_CACHE_FILE = "tigertag_filaments.json"

controller = hishel.Controller(allow_stale=True)
try:
    cache_path = get_cache_dir() / "hishel_tigertag"
    cache_storage = hishel.AsyncFileStorage(base_path=cache_path)
except PermissionError:
    logger.warning(
        "Failed to setup disk-based cache for TigerTag due to permission error. "
        "Using in-memory cache instead as fallback.",
    )
    cache_storage = hishel.AsyncInMemoryStorage()


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

    id_product: int
    id_brand: Optional[int] = None
    brand_name: Optional[str] = None
    id_type: Optional[int] = None
    type_name: Optional[str] = None
    name: Optional[str] = None
    color_hex: Optional[str] = None
    diameter: Optional[float] = None
    weight: Optional[float] = None
    spool_weight: Optional[float] = None
    density: Optional[float] = None
    nozzle_temp_min: Optional[int] = None
    nozzle_temp_max: Optional[int] = None
    bed_temp_min: Optional[int] = None
    bed_temp_max: Optional[int] = None


def _to_external_filament(product: TigerTagProduct) -> ExternalFilament:
    """Convert a TigerTag product into an ExternalFilament."""
    manufacturer = product.brand_name or "Unknown"
    material = product.type_name or "Unknown"
    name = product.name or f"{manufacturer} {material}"
    diameter = product.diameter or 1.75
    density = product.density or 1.24
    weight = product.weight or 1000.0

    # Use midpoint of temp ranges if available
    extruder_temp = None
    if product.nozzle_temp_min is not None and product.nozzle_temp_max is not None:
        extruder_temp = (product.nozzle_temp_min + product.nozzle_temp_max) // 2
    elif product.nozzle_temp_min is not None:
        extruder_temp = product.nozzle_temp_min
    elif product.nozzle_temp_max is not None:
        extruder_temp = product.nozzle_temp_max

    bed_temp = None
    if product.bed_temp_min is not None and product.bed_temp_max is not None:
        bed_temp = (product.bed_temp_min + product.bed_temp_max) // 2
    elif product.bed_temp_min is not None:
        bed_temp = product.bed_temp_min
    elif product.bed_temp_max is not None:
        bed_temp = product.bed_temp_max

    # Clean up color hex - remove leading # if present
    color_hex = None
    if product.color_hex:
        color_hex = product.color_hex.lstrip("#")

    return ExternalFilament(
        id=f"tigertag_{product.id_product}",
        manufacturer=manufacturer,
        name=name,
        material=material,
        density=density,
        weight=weight,
        spool_weight=product.spool_weight,
        diameter=diameter,
        color_hex=color_hex,
        extruder_temp=extruder_temp,
        bed_temp=bed_temp,
        source="tigertag",
    )


async def _download_json(url: str) -> bytes:
    """Download JSON from a URL using cached HTTP client."""
    async with hishel.AsyncCacheClient(storage=cache_storage, controller=controller) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.read()


async def _sync_tigertag() -> None:
    logger.info("Syncing TigerTag DB.")

    base_url = get_tigertag_api_url()

    try:
        # Fetch products from TigerTag API
        products_url = urljoin(base_url, "product/filament/get")
        products_data = await _download_json(products_url)
        products_list = json.loads(products_data)

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
