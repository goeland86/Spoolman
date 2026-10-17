#!/usr/bin/env python3
"""Re-encode bare `nfc_tag_id` spool extra values as JSON strings.

Extra field values are JSON: the web client calls JSON.parse on each one, so a bare
`tigertag_17_845587974` blanks the spool's page (`"tigertag_17_845587974"`, quotes included,
is what it must hold). Older NFC code wrote the bare form. This rewrites those rows and leaves
already-valid ones alone, so it is safe to run more than once.

    python scripts/fix_nfc_tag_id_json.py data/spoolman.db            # dry run
    python scripts/fix_nfc_tag_id_json.py data/spoolman.db --apply

Stop Spoolman first and keep a copy of the database. SQLite only.
"""

import argparse
import json
import sqlite3
import sys


def is_valid(value: str) -> bool:
    try:
        return isinstance(json.loads(value), str)
    except json.JSONDecodeError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", help="Path to spoolman.db")
    parser.add_argument("--apply", action="store_true", help="Write the changes (default: only report)")
    args = parser.parse_args()

    con = sqlite3.connect(args.database)
    rows = con.execute("SELECT spool_id, value FROM spool_field WHERE key = 'nfc_tag_id'").fetchall()
    bad = [(spool_id, value) for spool_id, value in rows if not is_valid(value)]
    print(f"{len(rows)} nfc_tag_id rows, {len(bad)} need fixing")
    for spool_id, value in bad:
        print(f"  spool {spool_id}: {value} -> {json.dumps(value)}")
    if args.apply and bad:
        with con:
            for spool_id, value in bad:
                con.execute(
                    "UPDATE spool_field SET value = ? WHERE spool_id = ? AND key = 'nfc_tag_id'",
                    (json.dumps(value), spool_id),
                )
        print("Applied.")
    elif bad:
        print("Dry run; pass --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
