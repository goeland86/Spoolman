"""Storage format of the `nfc_tag_id` spool extra field.

Extra field values are JSON-encoded strings: the API validates them with `json.loads` and
the web client calls `JSON.parse` on every one when it renders a spool. A `text` field holding
`abc` is therefore stored as `"abc"`, quotes included. A bare `abc` fails the parse and blanks
the whole page.

Rows written before this module existed hold the bare id. Reads accept both forms so those
spools still match; the UI only recovers once the row is rewritten in the encoded form.
"""

import json


def to_field_value(nfc_tag_id: str) -> str:
    """The value to store in `SpoolField.value` for a tag id."""
    return json.dumps(nfc_tag_id)


def field_values(nfc_tag_id: str) -> list[str]:
    """Every stored form a tag id may take: JSON-encoded, then the legacy bare string."""
    return [to_field_value(nfc_tag_id), nfc_tag_id]
