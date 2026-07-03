#!/usr/bin/env python3
"""
Generate a Channable-ready XML product feed from an Airtable table.

Reads records (including rich-content fields) from Airtable via the REST API,
converts rich text (Markdown) to HTML, and writes an XML feed with each
field wrapped in CDATA so HTML/special characters survive intact.

Configuration is via environment variables (see README.md):
    AIRTABLE_TOKEN   Airtable Personal Access Token (secret)
    AIRTABLE_BASE_ID Base ID, e.g. appXXXXXXXXXXXXXX
    AIRTABLE_TABLE   Table name or ID, e.g. "Kaufland DE"
    FEED_OUTPUT      Output path, default: docs/feed.xml
    FIELD_MAP        Optional JSON mapping {airtable_field: xml_tag}
    RICH_FIELDS      Optional comma list of fields to treat as rich text -> HTML
    ID_FIELD         Airtable field used as the product id tag (default: EAN)
    STATUS_FIELD     Optional field name to filter on
    STATUS_VALUE     Only export records whose STATUS_FIELD equals this value
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

try:
    import markdown  # type: ignore
    _HAS_MD = True
except Exception:
    _HAS_MD = False


API_ROOT = "https://api.airtable.com/v0"


def env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"ERROR: required environment variable {name} is not set")
    return val


def md_to_html(text):
    """Convert Airtable rich-text (Markdown) to HTML. Falls back to <br>."""
    if not text:
        return ""
    if _HAS_MD:
        return markdown.markdown(text, extensions=["extra", "sane_lists"])
    # Minimal fallback: preserve paragraphs/line breaks.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(
        "<p>" + escape(p).replace("\n", "<br/>") + "</p>" for p in paragraphs
    )


def fetch_records(token, base_id, table):
    """Fetch all records from a table, following pagination."""
    headers = {"Authorization": f"Bearer {token}"}
    table_enc = urllib.parse.quote(table, safe="")
    base_url = f"{API_ROOT}/{base_id}/{table_enc}"
    records = []
    offset = None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        url = base_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:  # rate limited
                    time.sleep(2 ** attempt)
                    continue
                sys.exit(f"ERROR: Airtable API {e.code}: {e.read().decode('utf-8')}")
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records


def cdata(value):
    """Wrap a value in CDATA, escaping any nested CDATA terminators."""
    text = "" if value is None else str(value)
    text = text.replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{text}]]>"


def build_xml(records, field_map, rich_fields, id_field,
              status_field, status_value):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<products>"]
    exported = 0
    for rec in records:
        f = rec.get("fields", {})

        # Optional status filter
        if status_field and status_value:
            if str(f.get(status_field, "")).strip() != status_value.strip():
                continue

        # Skip rows with no identifier
        rec_id = f.get(id_field) or rec.get("id")

        lines.append("  <product>")
        lines.append(f"    <id>{escape(str(rec_id))}</id>")

        for src_field, xml_tag in field_map.items():
            raw = f.get(src_field, "")
            if isinstance(raw, list):
                raw = ", ".join(str(x) for x in raw)
            if src_field in rich_fields:
                raw = md_to_html(raw)
            lines.append(f"    <{xml_tag}>{cdata(raw)}</{xml_tag}>")

        lines.append("  </product>")
        exported += 1

    lines.append("</products>")
    return "\n".join(lines) + "\n", exported


def main():
    token = env("AIRTABLE_TOKEN", required=True)
    base_id = env("AIRTABLE_BASE_ID", required=True)
    table = env("AIRTABLE_TABLE", "Kaufland DE")
    out_path = env("FEED_OUTPUT", "docs/feed.xml")
    # Unique feed key. Use the Shopify variant ID: some products share an EAN,
    # so EAN is NOT unique and must not be the <id>.
    id_field = env("ID_FIELD", "Shopify variant ID")
    status_field = env("STATUS_FIELD", "")
    status_value = env("STATUS_VALUE", "")

    # Which Airtable fields map to which XML tags.
    default_map = {
        "EAN": "ean",
        "Product title": "title",
        "Description": "description",
    }
    field_map = json.loads(env("FIELD_MAP", json.dumps(default_map)))

    rich_fields = set(
        x.strip() for x in env("RICH_FIELDS", "Description").split(",") if x.strip()
    )

    records = fetch_records(token, base_id, table)
    xml, exported = build_xml(
        records, field_map, rich_fields, id_field, status_field, status_value
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(xml)

    print(f"Fetched {len(records)} record(s); exported {exported} to {out_path}")


if __name__ == "__main__":
    main()
