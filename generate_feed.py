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
import re
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
    # Treat an empty/whitespace value the same as unset, so an empty GitHub
    # Actions variable ("${{ vars.X }}" when X is not defined) falls back to
    # the default instead of overriding it with "".
    val = os.environ.get(name)
    if val is None or str(val).strip() == "":
        val = default
    if required and not val:
        sys.exit(f"ERROR: required environment variable {name} is not set")
    return val


_LIST_RE = re.compile(r'^\s*([-*+]|\d+[.)])\s+')


def normalize_markdown(text):
    """Make Airtable rich-text Markdown parseable as real lists.

    Airtable often exports a bullet/numbered list WITHOUT a blank line before
    it (e.g. "Voordelen:\\n- a\\n- b"), which Markdown parsers do not treat as
    a list. It may also use the unicode bullet "•". This:
      - converts unicode bullets (• ▪ ‣ ·) to Markdown "- "
      - inserts a blank line before the first item of a list block
    so lists become proper <ul>/<ol> in the HTML.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r'^(\s*)[•▪‣·]\s+', r'\1- ', ln) for ln in text.split("\n")]
    out = []
    for ln in lines:
        if _LIST_RE.match(ln) and out:
            prev = out[-1]
            if prev.strip() != "" and not _LIST_RE.match(prev):
                out.append("")  # blank line before the first list item
        out.append(ln)
    return "\n".join(out)


def md_to_html(text):
    """Convert Airtable rich-text (Markdown) to HTML. Falls back to <br>."""
    if not text:
        return ""
    if _HAS_MD:
        return markdown.markdown(
            normalize_markdown(text), extensions=["extra", "sane_lists"]
        )
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
        # Return fields keyed by field ID (stable) instead of by name.
        params = {"pageSize": 100, "returnFieldsByFieldId": "true"}
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


# FAQ table (Airtable base appPn95FPfn3fzi4n, table "FAQ" = tbl170la2gBX32opn).
# One row per Shopify FAQ metaobject, with Question/Answer pairs per published
# shop locale (field IDs, stable across renames). "en" is the shop's primary
# locale (also the live voldt.co.uk storefront language), the rest are the 11
# translated locales.
FAQ_LOCALE_FIELDS = [
    ("en", "fldcEWBfDfi77noHr", "fldVdfVRFKBnwIYi5"),
    ("da", "fld4xlFBnAsoDaRNg", "fldSM9rSBru7ihdhE"),
    ("de", "fldWqPHiRUCQnqAiZ", "fldPcgyKoTCl4wFtj"),
    ("es", "fldswzXA84Ecqc5pO", "fldjQZamQiwv7Zuaq"),
    ("fi", "fld4rZFFuZJNV5HDW", "fldem2NXuWBobnAty"),
    ("fr", "flddMTYCBEKkijmDT", "fldXSrwN3WBQkclKG"),
    ("it", "fld1mGnfdrsqs8grH", "fldcBHW6QeROtowIU"),
    ("nb", "fldjrvo3FXqNYPn6f", "fldjCQ4ZM8i0u4FRa"),
    ("nl", "fldCKJP84sbZJEjKG", "fldA0xTjEU1LxUbum"),
    ("pl", "fldD9DwJeZ0aSa1qC", "fldlQxmYqrMXQqIHA"),
    ("pt-PT", "fldwQRjDxNHJnZsxF", "fldP3Ls9wORygstAf"),
    ("sv", "fldCdN5j5Cmt2DHlX", "fldpp8ulV7X3lRL6y"),
]


def build_faq_map(token, base_id, faq_table):
    """Fetch the FAQ table and index it by record id.

    Returns {faq_record_id: [(locale, question, answer_html), ...]}, only
    including locale entries where both question and answer are non-empty
    (skips FAQs that are missing a translation for a given locale).
    """
    faq_map = {}
    for r in fetch_records(token, base_id, faq_table):
        ff = r.get("fields", {})
        pairs = []
        for locale, q_field, a_field in FAQ_LOCALE_FIELDS:
            q = str(ff.get(q_field, "") or "").strip()
            a_raw = str(ff.get(a_field, "") or "").strip()
            if not q or not a_raw:
                continue
            pairs.append((locale, q, md_to_html(a_raw)))
        faq_map[r["id"]] = pairs
    return faq_map


def render_faq_blocks(faq_ids, faq_map):
    """Render repeated <question_and_answer> blocks for one product.

    One block per (linked FAQ x locale with content), in FAQ link order then
    locale order. Each block carries its own <locale> so Channable's per-market
    Kaufland mapping (DE / PL / ...) can filter on it.
    """
    lines = []
    for faq_id in faq_ids:
        for locale, question, answer_html in faq_map.get(faq_id, []):
            lines.append("    <question_and_answer>")
            lines.append(f"      <locale>{escape(locale)}</locale>")
            lines.append("      " + render_tag("question", question, use_cdata=False).strip())
            lines.append("      " + render_tag("answer", answer_html, use_cdata=True).strip())
            lines.append("    </question_and_answer>")
    return lines


def render_tag(tag, value, use_cdata):
    """Render one XML element.

    - Empty value  -> clean empty element, e.g. <ean></ean>
    - use_cdata    -> wrap in CDATA (for HTML/rich content), only when non-empty
    - otherwise    -> XML-escaped plain text (for identifiers/plain text)
    """
    text = "" if value is None else str(value)
    if text == "":
        return f"    <{tag}></{tag}>"
    if use_cdata:
        safe = text.replace("]]>", "]]]]><![CDATA[>")
        return f"    <{tag}><![CDATA[{safe}]]></{tag}>"
    return f"    <{tag}>{escape(text)}</{tag}>"


def build_xml(records, field_map, rich_fields, html_fields, id_field,
              status_field, status_value, faq_map=None, faq_link_field=None):
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
            is_rich = src_field in rich_fields   # Markdown -> HTML
            is_html = src_field in html_fields   # already HTML, keep as-is
            if is_rich:
                raw = md_to_html(raw)
            # CDATA for HTML content (rich or already-HTML); plain text otherwise.
            lines.append(render_tag(xml_tag, raw, use_cdata=is_rich or is_html))

        if faq_map is not None and faq_link_field:
            faq_ids = f.get(faq_link_field) or []
            if isinstance(faq_ids, str):
                faq_ids = [faq_ids]
            lines.extend(render_faq_blocks(faq_ids, faq_map))

        lines.append("  </product>")
        exported += 1

    lines.append("</products>")
    return "\n".join(lines) + "\n", exported


def main():
    token = env("AIRTABLE_TOKEN", required=True)
    base_id = env("AIRTABLE_BASE_ID", required=True)
    table = env("AIRTABLE_TABLE", "Kaufland DE")
    out_path = env("FEED_OUTPUT", "docs/feed.xml")

    # Fields are read by field ID (returnFieldsByFieldId=true), so all mappings
    # below use Airtable field IDs — stable and unambiguous (unlike names).
    # Field IDs for the Kaufland DE table:
    #   fldmjoKFSUZ04vBNq = EAN
    #   flddPikssfX9HOByh = Product title
    #   fldGacObhaxghnvbF = Description (rich text)
    #   fldKPLh1iF8YWTyEI = Status (singleSelect)
    #   fldZGT8wfUSarYAP0 = Shopify variant ID

    # Unique feed key. Use the Shopify variant ID: some products share an EAN,
    # so EAN is NOT unique and must not be the <id>.
    id_field = env("ID_FIELD", "fldZGT8wfUSarYAP0")

    # Export ALL records regardless of status. To only export approved rows,
    # set STATUS_FIELD="fldKPLh1iF8YWTyEI" and STATUS_VALUE="Ready to publish"
    # as repo Variables.
    status_field = env("STATUS_FIELD", "")
    status_value = env("STATUS_VALUE", "")

    # Which Airtable fields (by ID) map to which XML tags.
    #   fldqFwtF8Ly0P0pnP = Kaufland title
    #   fld9aUho6YLIYuWCD = Kaufland description (rich text)
    # The two "__cdiscount_*__" keys are synthetic: they are filled in below by
    # joining a separate table (see cDiscount join), not read from this table.
    default_map = {
        "fldmjoKFSUZ04vBNq": "ean",
        "flddPikssfX9HOByh": "title",
        "fldGacObhaxghnvbF": "description",
        "fldqFwtF8Ly0P0pnP": "kaufland_title",
        "fld9aUho6YLIYuWCD": "kaufland_description",
        "fldSr3TsBkQBcJSNe": "kaufland_title_pl",
        "fldul0aRAHgW0yhRp": "kaufland_description_pl",
        "fld3H1Owa8X8vXuUb": "title_nl",
        "fldEqFLWovYTtcKVz": "description_nl",
        "__cdiscount_title__": "cdiscount_title",
        "__cdiscount_description__": "cdiscount_description",
    }
    field_map = json.loads(env("FIELD_MAP", json.dumps(default_map)))

    # Rich (Markdown -> HTML) fields: Shopify, Kaufland (DE + PL) and Cdiscount.
    rich_fields = set(
        x.strip()
        for x in env(
            "RICH_FIELDS",
            "fldGacObhaxghnvbF,fld9aUho6YLIYuWCD,fldul0aRAHgW0yhRp,"
            "__cdiscount_description__",
        ).split(",")
        if x.strip()
    )

    # Already-HTML fields: CDATA-wrapped as-is, NOT run through the Markdown
    # converter. Description NL comes from Shopify metafields already as HTML.
    html_fields = set(
        x.strip() for x in env("HTML_FIELDS", "fldEqFLWovYTtcKVz").split(",")
        if x.strip()
    )

    records = fetch_records(token, base_id, table)

    # --- Cdiscount join -----------------------------------------------------
    # Cdiscount title/description live in a SEPARATE table ("cDiscount products"),
    # matched to each variant on the "Product title Shopify" text.
    #   cd table          = tblcymF9sjcRb0p9B
    #   cd match key       = fldQa9Xuun7VCRAWg (Product title Shopify)
    #   cd title           = fldpMb6xWBtqmvXSi (cDiscount title (FR))
    #   cd description     = fldviWIilHV0l8leW (cDiscount Description (FR))
    #   main match field   = flddPikssfX9HOByh (Product title Shopify)
    cd_table = env("CDISCOUNT_TABLE", "tblcymF9sjcRb0p9B")
    cd_key = env("CDISCOUNT_KEY_FIELD", "fldQa9Xuun7VCRAWg")
    cd_title = env("CDISCOUNT_TITLE_FIELD", "fldpMb6xWBtqmvXSi")
    cd_desc = env("CDISCOUNT_DESC_FIELD", "fldviWIilHV0l8leW")
    main_join = env("CDISCOUNT_MATCH_FIELD", "flddPikssfX9HOByh")

    cd_map = {}
    if cd_table:
        for r in fetch_records(token, base_id, cd_table):
            ff = r.get("fields", {})
            key = str(ff.get(cd_key, "")).strip()
            if key:
                cd_map[key] = (ff.get(cd_title, ""), ff.get(cd_desc, ""))
        matched = 0
        for rec in records:
            ff = rec.setdefault("fields", {})
            t, d = cd_map.get(str(ff.get(main_join, "")).strip(), ("", ""))
            ff["__cdiscount_title__"] = t
            ff["__cdiscount_description__"] = d
            if t or d:
                matched += 1
        print(f"Cdiscount: {len(cd_map)} rows loaded, joined onto {matched} records")

    # --- FAQ join ------------------------------------------------------------
    # FAQ table ("FAQ" = tbl170la2gBX32opn) has one row per Shopify FAQ
    # metaobject, with Question/Answer pairs per locale (FAQ_LOCALE_FIELDS).
    # Each Marketplace_Content record links to its FAQs (up to 4, mirroring
    # Shopify's custom.faq_1..faq_4 product metafields) via the "FAQ" field.
    faq_table = env("FAQ_TABLE", "tbl170la2gBX32opn")
    faq_link_field = env("FAQ_LINK_FIELD", "fldWSH8N2abTucrro")
    faq_map = build_faq_map(token, base_id, faq_table) if faq_table else None
    if faq_map is not None:
        blocks = sum(
            len(faq_map.get(fid, []))
            for rec in records
            for fid in (rec.get("fields", {}).get(faq_link_field) or [])
        )
        print(f"FAQ: {len(faq_map)} FAQ record(s) loaded, {blocks} question_and_answer block(s) to emit")

    xml, exported = build_xml(
        records, field_map, rich_fields, html_fields, id_field,
        status_field, status_value, faq_map=faq_map, faq_link_field=faq_link_field
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(xml)

    print(f"Fetched {len(records)} record(s); exported {exported} to {out_path}")


if __name__ == "__main__":
    main()
