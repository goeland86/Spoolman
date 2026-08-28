"""Decode a scanned tag's raw payload into fields the rest of Spoolman understands.

`POST /tag/scan` (spoolman/api/v1/tag.py) accepts a `format` and a raw `payload_b64` but,
on its own, never looks inside them -- that split is deliberate (see spoolman/tags.py): the
scan-relay layer only ever needs a UID. This module is where "Spoolman owns the codec" gets
exercised: format-specific decoding lives in its own codec module (openprinttag_codec.py,
tigertag_codec.py and, as a later PR adds it, qidi_codec.py), and `decode()` is the one place
that knows which codec answers for which format string.

Decoding is best-effort and pure: no database, no FastAPI, mirrors tags.py in that regard.
An unrecognized format or a payload that fails to parse returns None rather than raising --
a scan's core contract (resolve a UID) must never fail because enrichment of it didn't work.

One exception: TigerTag's on-tag `id_material`/`id_brand` are catalog IDs, not names, and
resolving them needs the optional external-DB add-on (spoolman/tigertagdb.py, off by default
-- see env.is_tigertag_enabled). `decode()` stays fully pure and reads only the add-on's
background-synced cache when it's enabled; `decode_async()` additionally awaits a live
per-tag API call first, for an exact catalog match, before falling back to that same cache.
Either way the add-on module itself is only ever imported when enabled, so a deployment that
never opts in never loads it and behaves exactly as if PR4 didn't exist.
"""

import logging
from dataclasses import dataclass

from spoolman import env, openprinttag_codec, tigertag_codec
from spoolman.tags import normalize_format

logger = logging.getLogger(__name__)


@dataclass
class DecodedTag:
    """Tag contents normalized to the fields Spoolman's data model cares about.

    Deliberately flatter than any one codec's own dataclass (e.g. OpenPrintTagData): this is
    the shape every format's decode eventually funnels into, so the wiring in tag.py and
    database/tag.py never has to know which codec produced it.
    """

    material_type: str | None = None
    material_name: str | None = None
    brand_name: str | None = None
    color_hex: str | None = None
    diameter_mm: float | None = None
    density_g_cm3: float | None = None
    net_weight_g: float | None = None  # full net filament weight, before any use
    empty_container_weight_g: float | None = None  # tare weight of the spool itself
    consumed_weight_g: float | None = None  # already used, per the tag -- shown, never applied automatically
    external_id: str | None = None  # a stable id for the specific roll/instance, if the tag carries one


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


def approximate_density(material_type: str | None) -> float | None:
    """Look up a rough density for a material type, or None if it is not in the table."""
    if material_type is None:
        return None
    return APPROXIMATE_DENSITY_BY_MATERIAL.get(material_type.upper())


def density_or_fallback(material_type: str | None) -> float:
    """Approximate density for a material type, or the PLA-equivalent fallback."""
    return approximate_density(material_type) or _FALLBACK_DENSITY


def decode(tag_format: str | None, payload: bytes, uid_bytes: bytes | None = None) -> DecodedTag | None:
    """Decode a tag's raw payload for a known format.

    Args:
        tag_format: The format string as reported by the scanning agent, e.g. "openprinttag".
            Matched case-insensitively via the same normalization `tags.py` uses for storage.
        payload: The tag's raw contents.
        uid_bytes: The tag's UID as raw bytes, if available. Some formats (OpenPrintTag) can
            derive an instance identifier from it when the tag itself doesn't carry one.

    Returns:
        DecodedTag | None: The decoded fields, or None if the format is unknown or the
        payload could not be parsed as that format.

    """
    fmt = normalize_format(tag_format)
    if fmt == "openprinttag":
        return _decode_openprinttag(payload, uid_bytes)
    if fmt == "tigertag":
        return _decode_tigertag(payload)
    return None


async def decode_async(
    tag_format: str | None,
    payload: bytes,
    uid_bytes: bytes | None = None,
    uid: str | None = None,
) -> DecodedTag | None:
    """Decode a tag's raw payload, resolving TigerTag names via a live API call if enabled.

    Identical to `decode()` for every format, and for TigerTag with the add-on off --
    the only difference is TigerTag with it on, where this awaits
    `tigertagdb.lookup_product_by_tag(uid, id_product)` for an exact catalog match before
    falling back to the same background-synced cache `decode()` uses on its own. Prefer
    this from request-handling code, which is already async and can afford the await;
    `decode()` stays synchronous for pure/offline callers like the test suite.

    Args:
        tag_format: See `decode()`.
        payload: See `decode()`.
        uid_bytes: See `decode()`.
        uid: The tag's UID as the same normalized hex string `/tag/scan` resolves it to
            (see spoolman.tags.normalize_uid). TigerTag's live lookup needs it as a string,
            not bytes -- it's the key the tag was registered against on TigerTag's own API.

    Returns:
        DecodedTag | None: See `decode()`.

    """
    fmt = normalize_format(tag_format)
    if fmt == "tigertag":
        return await _decode_tigertag_async(payload, uid)
    return decode(tag_format, payload, uid_bytes)


def _decode_openprinttag(payload: bytes, uid_bytes: bytes | None) -> DecodedTag | None:
    try:
        data = openprinttag_codec.decode_nfcv_memory(payload, nfc_tag_uid=uid_bytes)
    except ValueError:
        logger.debug("Could not decode payload as OpenPrintTag", exc_info=True)
        return None

    return DecodedTag(
        material_type=data.material_type,
        material_name=data.material_name,
        brand_name=data.brand_name,
        color_hex=data.primary_color_hex,
        diameter_mm=data.effective_diameter,
        density_g_cm3=data.density,
        net_weight_g=data.effective_weight,
        empty_container_weight_g=data.empty_container_weight,
        consumed_weight_g=data.consumed_weight,
        external_id=data.effective_instance_uuid,
    )


def _decode_tigertag_raw(payload: bytes) -> tigertag_codec.TigerTagData | None:
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
    raw: tigertag_codec.TigerTagData,
    *,
    material_type: str | None = None,
    material_name: str | None = None,
    brand_name: str | None = None,
    density_g_cm3: float | None = None,
) -> DecodedTag:
    # color_hex/diameter_mm/net_weight_g always come straight off the physical tag, never
    # from the external-DB add-on -- the tag is ground truth for this specific roll's
    # physical properties; the add-on only ever fills in what the tag can't carry: names
    # for id_material/id_brand's catalog IDs, and a material-specific density.
    return DecodedTag(
        material_type=material_type,
        material_name=material_name,
        brand_name=brand_name,
        color_hex=raw.color_hex,
        diameter_mm=raw.diameter_mm or None,
        density_g_cm3=density_g_cm3,
        net_weight_g=float(raw.weight) or None,
        # id_product is a catalog SKU, not a per-roll identifier, so it's never used as
        # external_id -- same instance-uniqueness caution as OpenPrintTag's UID-derived id.
    )


def _tigertag_decoded_from_cache(raw: tigertag_codec.TigerTagData) -> DecodedTag:
    """Resolve names for a raw TigerTag decode from the add-on's background-synced cache.

    Only called once the caller has already confirmed the add-on is enabled -- this is
    where spoolman/tigertagdb.py actually gets imported, kept out of every other code path
    so a deployment that never opts in never loads it.
    """
    from spoolman import tigertagdb  # noqa: PLC0415 -- see docstring

    return _tigertag_decoded(
        raw,
        material_type=tigertagdb.lookup_material_name(raw.id_material),
        material_name=tigertagdb.lookup_material_name(raw.id_material),
        brand_name=tigertagdb.lookup_brand_name(raw.id_brand),
        density_g_cm3=tigertagdb.lookup_material_density(raw.id_material),
    )


def _decode_tigertag(payload: bytes) -> DecodedTag | None:
    raw = _decode_tigertag_raw(payload)
    if raw is None:
        return None
    if not env.is_tigertag_enabled():
        return _tigertag_decoded(raw)
    return _tigertag_decoded_from_cache(raw)


async def _decode_tigertag_async(payload: bytes, uid: str | None) -> DecodedTag | None:
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
                material_type=ext.material if ext.material != "Unknown" else None,
                material_name=ext.name,
                brand_name=ext.manufacturer if ext.manufacturer != "Unknown" else None,
                # ext.density is a fixed 1.24 placeholder for every TigerTag product
                # (see tigertagdb._to_external_filament) -- the materials cache's own
                # per-material figure is the real one, so prefer it here too.
                density_g_cm3=tigertagdb.lookup_material_density(raw.id_material),
            )

    # No product id on the tag, or the live lookup was unavailable/failed/found nothing --
    # fall back to the coarser but still real background-synced cache.
    return _tigertag_decoded_from_cache(raw)
