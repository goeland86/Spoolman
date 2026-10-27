"""Decode a scanned tag's raw payload into fields the rest of Spoolman understands.

`POST /tag/scan` (spoolman/api/v1/tag.py) accepts a `format` and a raw `payload_b64` but,
on its own, never looks inside them -- that split is deliberate (see spoolman/tags/__init__.py):
the scan-relay layer only ever needs a UID. Format-specific decoding lives in one module per
format (openprinttag.py here, more later), and `decode()` is the one place that knows which
module answers for which format string.

Decoding is best-effort and pure: no database, no FastAPI. An unrecognized format or a payload
that fails to parse returns None rather than raising -- a scan's core contract (resolve a UID)
must never fail because enrichment of it didn't work.

One exception to "pure": TigerTag's on-tag `id_material`/`id_brand` are catalog IDs, not
names, and resolving them needs the optional external-DB add-on (spoolman/tigertagdb.py, off
by default -- see env.is_tigertag_enabled). `decode()` stays fully pure and reads only the
add-on's background-synced cache when it's enabled; `decode_async()` additionally awaits a
live per-tag API call first, for an exact catalog match, before falling back to that same
cache. Either way the add-on module itself is only ever imported when enabled, so a
deployment that never opts in never loads it.
"""

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spoolman.tags.tigertag import TigerTagData

from spoolman.tags import normalize_format

logger = logging.getLogger(__name__)


@dataclass
class DecodedTag:
    """Tag contents named after the fields of the rest of the API.

    Deliberately flatter than any one codec's own dataclass: every format's decode funnels into
    this shape, so the wiring in tag.py and database/tag.py never has to know which codec
    produced it.
    """

    material_class: str | None = None  # "FFF" or "SLA"; only FFF tags can create a filament
    material: str | None = None  # e.g. "PLA"
    name: str | None = None  # e.g. "PLA Galaxy Black"
    vendor_name: str | None = None  # e.g. "Prusament"
    article_number: str | None = None  # vendor GTIN, as text
    color_hex: str | None = None
    diameter: float | None = None
    density: float | None = None
    weight: float | None = None  # nominal full net weight of the filament, in grams
    initial_weight: float | None = None  # measured net weight on this roll, in grams
    spool_weight: float | None = None  # tare weight of the empty spool, in grams
    used_weight: float | None = None  # already used, per the tag -- shown, never applied automatically
    extruder_temp: int | None = None  # print temperature, °C
    bed_temp: int | None = None  # bed temperature, °C
    instance_uuid: str | None = None  # identifies this roll, not the product


# Approximate densities (g/cm^3) for common materials, used only as a fallback when a
# decoded tag doesn't carry its own density and the caller needs one anyway (creating a
# Filament requires a density -- see spoolman.database.filament.create). These are rough
# industry-typical values, not per-manufacturer figures; a wrong guess here is still better
# than refusing to auto-create a spool over a field most tags never bothered to include.
APPROXIMATE_DENSITY_BY_MATERIAL: dict[str, float] = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "ASA": 1.07,
    "TPU": 1.21,
    "PA6": 1.14,
    "PA11": 1.03,
    "PA12": 1.01,
    "PA66": 1.15,
    "PC": 1.20,
    "PCTG": 1.23,
    "HIPS": 1.04,
    "PVA": 1.23,
}

# Used when even the material type is unknown. PLA is the most common filament by far, so
# it is the least-wrong default rather than a principled one.
_FALLBACK_DENSITY = 1.24


def approximate_density(material: str | None) -> float | None:
    """Look up a rough density for a material, or None if it is not in the table."""
    if material is None:
        return None
    return APPROXIMATE_DENSITY_BY_MATERIAL.get(material.upper())


def density_or_fallback(material: str | None) -> float:
    """Approximate density for a material, or the PLA-equivalent fallback."""
    return approximate_density(material) or _FALLBACK_DENSITY


def _finite(value: float | None) -> float | None:
    """Drop NaN and infinity: CBOR can carry them, but JSON cannot, and neither is storable."""
    if value is None or math.isfinite(value):
        return value
    return None


def decode(tag_format: str | None, payload: bytes, uid_bytes: bytes | None = None) -> DecodedTag | None:
    """Decode a tag's raw payload for a known format.

    Args:
        tag_format: The format string as reported by the scanning agent, e.g. "openprinttag".
            Matched case-insensitively via the same normalization the storage layer uses.
        payload: The tag's raw contents.
        uid_bytes: The tag's UID as raw bytes, if available. Some formats (OpenPrintTag) derive
            the instance identifier from it when the tag itself doesn't carry one.

    Returns:
        DecodedTag | None: The decoded fields, or None if the format is unknown or the payload
        could not be decoded. Never raises: whatever a hostile or damaged payload does to a
        codec, the scan carries on as if the payload were not decodable.

    """
    fmt = normalize_format(tag_format)
    try:
        if fmt == "openprinttag":
            return _decode_openprinttag(payload, uid_bytes)
        if fmt == "tigertag":
            return _decode_tigertag(payload)
        if fmt == "qidi":
            return _decode_qidi(payload)
    except Exception:  # noqa: BLE001 -- best effort by contract, see the module docstring
        logger.warning("Could not decode %s payload", fmt, exc_info=True)
    return None


async def decode_async(
    tag_format: str | None,
    payload: bytes,
    uid_bytes: bytes | None = None,
    uid: str | None = None,
) -> DecodedTag | None:
    """Decode a tag's raw payload, resolving TigerTag names via a live API call if enabled.

    Identical to `decode()` for every format, and for TigerTag with the add-on off -- the
    only difference is TigerTag with it on, where this awaits
    `tigertagdb.lookup_product_by_tag(uid, id_product)` for an exact catalog match before
    falling back to the same background-synced cache `decode()` uses on its own. Prefer this
    from request-handling code, which is already async and can afford the await; `decode()`
    stays synchronous for pure/offline callers like the test suite.

    Args:
        tag_format: See `decode()`.
        payload: See `decode()`.
        uid_bytes: See `decode()`.
        uid: The tag's UID as the same normalized hex string `/tag/scan` resolves it to (see
            spoolman.tags.normalize_uid). TigerTag's live lookup needs it as a string, not
            bytes -- it's the key the tag was registered against on TigerTag's own API.

    Returns:
        DecodedTag | None: See `decode()`.

    """
    fmt = normalize_format(tag_format)
    try:
        if fmt == "tigertag":
            return await _decode_tigertag_async(payload, uid)
    except Exception:  # noqa: BLE001 -- best effort by contract, see the module docstring
        logger.warning("Could not decode %s payload", fmt, exc_info=True)
        return None
    return decode(tag_format, payload, uid_bytes)


def _decode_openprinttag(payload: bytes, uid_bytes: bytes | None) -> DecodedTag | None:
    # Imported here, not at module level: cbor2 is only needed when a payload actually arrives,
    # so a missing or broken cbor2 must not stop the server from starting.
    from spoolman.tags import openprinttag as openprinttag_codec  # noqa: PLC0415

    try:
        data = openprinttag_codec.decode_nfcv_memory(payload, nfc_tag_uid=uid_bytes)
    except ValueError:
        logger.debug("Could not decode payload as OpenPrintTag", exc_info=True)
        return None

    # Print and bed temperatures come as a range. Use the upper end: it is the value a
    # profile is normally set to, and it is still inside the range the vendor specifies.
    extruder_temp = data.max_print_temperature or data.min_print_temperature
    bed_temp = data.max_bed_temperature or data.min_bed_temperature

    return DecodedTag(
        material_class=data.material_class,
        material=data.material_type,
        name=data.material_name,
        vendor_name=data.brand_name,
        article_number=str(data.gtin) if data.gtin is not None else None,
        color_hex=data.primary_color_hex,
        diameter=_finite(data.effective_diameter),
        density=_finite(data.density),
        weight=_finite(data.nominal_netto_full_weight),
        initial_weight=_finite(data.actual_netto_full_weight),
        spool_weight=_finite(data.empty_container_weight),
        used_weight=_finite(data.consumed_weight),
        extruder_temp=extruder_temp,
        bed_temp=bed_temp,
        instance_uuid=data.effective_instance_uuid,
    )


def _decode_tigertag_raw(payload: bytes) -> "TigerTagData | None":
    # Imported here for the same reason as openprinttag above: the dependency should only be
    # paid for when a tigertag payload actually arrives.
    from spoolman.tags import tigertag as tigertag_codec  # noqa: PLC0415

    try:
        data = tigertag_codec.decode_ntag213(payload)
    except ValueError:
        logger.debug("Could not decode payload as TigerTag", exc_info=True)
        return None

    if not tigertag_codec.is_tigertag(data.id_tigertag):
        # Wrong magic number -- either not a TigerTag at all, or a blank/Init tag with
        # nothing written to it yet. Either way there's nothing usable to surface.
        return None

    return data


def _tigertag_decoded(
    raw: "TigerTagData",
    *,
    material: str | None = None,
    name: str | None = None,
    vendor_name: str | None = None,
    density: float | None = None,
) -> DecodedTag:
    # color_hex/diameter/weight always come straight off the physical tag, never from the
    # external-DB add-on -- the tag is ground truth for this specific roll's physical
    # properties; the add-on only ever fills in what the tag can't carry: names for
    # id_material/id_brand's catalog IDs, and a material-specific density.
    # id_product is a catalog SKU, not a per-roll identifier, so it's never used as
    # instance_uuid -- same instance-uniqueness caution as OpenPrintTag's UID-derived id.
    # TigerTag (NTAG213 "Maker"/"+") only ever tags filament spools, so material_class is
    # always "FFF" -- unlike OpenPrintTag, there is no resin variant of this format.
    return DecodedTag(
        material_class="FFF",
        material=material,
        name=name,
        vendor_name=vendor_name,
        color_hex=raw.color_hex,
        diameter=raw.diameter_mm or None,
        density=density,
        weight=float(raw.weight) or None,
    )


def _decode_qidi(payload: bytes) -> DecodedTag | None:
    # Imported here for the same reason as the other formats above.
    from spoolman.tags import qidi as qidi_codec  # noqa: PLC0415

    if not qidi_codec.is_valid_qidi_block(payload):
        # Covers both "too short to be a block" and "doesn't look like Qidi data" --
        # decode_qidi_block itself only checks length, so validity is checked separately.
        return None

    data = qidi_codec.decode_qidi_block(payload)

    # Unlike TigerTag, Qidi's material/color codes resolve to names from static tables baked
    # into the codec itself -- no external DB needed. A code outside the mapped ranges (already
    # excluded by is_valid_qidi_block's bounds check) can't happen here, but the property still
    # falls back to an "Unknown (n)" string rather than a real name, so guard on map membership.
    material_known = data.material_code in qidi_codec.MATERIAL_CODE_MAP
    color_known = data.color_code in qidi_codec.COLOR_CODE_MAP

    # Qidi (like TigerTag) only ever tags filament spools -- no resin variant of this format.
    return DecodedTag(
        material_class="FFF",
        material=data.material_type if material_known else None,
        name=data.material_name if material_known else None,
        # manufacturer_code == 1 is Qidi's own code for itself -- already required by
        # is_valid_qidi_block, so a decoded block always means a Qidi-branded tag.
        vendor_name="Qidi",
        color_hex=data.color_hex if color_known else None,
    )


def _tigertag_decoded_from_cache(raw: "TigerTagData") -> DecodedTag:
    """Resolve names for a raw TigerTag decode from the add-on's background-synced cache.

    Only called once the caller has already confirmed the add-on is enabled -- this is where
    spoolman/tigertagdb.py actually gets imported, kept out of every other code path so a
    deployment that never opts in never loads it.
    """
    from spoolman import tigertagdb  # noqa: PLC0415 -- see docstring

    return _tigertag_decoded(
        raw,
        material=tigertagdb.lookup_material_name(raw.id_material),
        name=tigertagdb.lookup_material_name(raw.id_material),
        vendor_name=tigertagdb.lookup_brand_name(raw.id_brand),
        density=tigertagdb.lookup_material_density(raw.id_material),
    )


def _decode_tigertag(payload: bytes) -> DecodedTag | None:
    from spoolman import env  # noqa: PLC0415

    raw = _decode_tigertag_raw(payload)
    if raw is None:
        return None
    if not env.is_tigertag_enabled():
        return _tigertag_decoded(raw)
    return _tigertag_decoded_from_cache(raw)


async def _decode_tigertag_async(payload: bytes, uid: str | None) -> DecodedTag | None:
    from spoolman import env  # noqa: PLC0415

    raw = _decode_tigertag_raw(payload)
    if raw is None:
        return None
    if not env.is_tigertag_enabled():
        return _tigertag_decoded(raw)

    from spoolman import tigertagdb  # noqa: PLC0415 -- see _tigertag_decoded_from_cache

    if raw.id_product and uid is not None:
        try:
            ext = await tigertagdb.lookup_product_by_tag(uid, raw.id_product)
        except Exception:  # noqa: BLE001 -- enrichment must never fail the scan it's decoding
            logger.debug("TigerTag live product lookup failed for uid=%s", uid, exc_info=True)
            ext = None
        if ext is not None:
            return _tigertag_decoded(
                raw,
                material=ext.material if ext.material != "Unknown" else None,
                name=ext.name,
                vendor_name=ext.manufacturer if ext.manufacturer != "Unknown" else None,
                # ext.density is a fixed 1.24 placeholder for every TigerTag product (see
                # tigertagdb._to_external_filament) -- the materials cache's own per-material
                # figure is the real one, so prefer it here too.
                density=tigertagdb.lookup_material_density(raw.id_material),
            )

    # No product id on the tag, or the live lookup was unavailable/failed/found nothing --
    # fall back to the coarser but still real background-synced cache.
    return _tigertag_decoded_from_cache(raw)
