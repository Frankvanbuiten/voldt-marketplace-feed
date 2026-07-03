# Airtable → Channable XML feed

Pulls rich-content fields from an Airtable table and publishes a Channable-ready
XML feed at a fixed, public URL. A GitHub Action regenerates the feed once a day,
so Channable always reads fresh content.

```
Airtable (Marketplace content / Kaufland DE)
        │  Airtable REST API (daily)
        ▼
GitHub Action  →  docs/feed.xml  →  GitHub Pages URL  →  Channable import
```

## Files

| File | Purpose |
|------|---------|
| `generate_feed.py` | Reads Airtable, converts rich text (Markdown) to HTML, writes `docs/feed.xml` with CDATA. |
| `requirements.txt` | Python dependency (`markdown`). |
| `.github/workflows/feed.yml` | Daily cron + manual run; commits the feed. |
| `docs/feed.xml` | The generated feed (served by Pages). |

> Note: in the repo, place `feed.yml` at `.github/workflows/feed.yml`.

---

## Step 1 — Create the Airtable base

Create a base called **Marketplace content** with a table **Kaufland DE** and these fields:

| Field | Type | Notes |
|-------|------|-------|
| `EAN` | Single line text | **Primary field.** Note: not unique — some products share an EAN. |
| `Product title` | Single line text | Optional. |
| `Description` | **Long text → enable Rich text formatting** | The rich content field. |
| `Status` | Single select: `Draft`, `Ready to publish` | Only `Ready to publish` rows are exported. |
| `Shopify variant ID` | Single line text | **Unique feed key** (`<id>`), because EAN is not unique. |

You can rename/add fields later — the script is configurable (see Step 5).

## Step 2 — Create an Airtable Personal Access Token

1. Go to https://airtable.com/create/tokens
2. Scopes: `data.records:read` and `schema.bases:read`.
3. Access: add the **Marketplace content** base.
4. Copy the token (starts with `pat...`). You'll paste it into GitHub next.

Also note your **Base ID** (starts with `app...`) from https://airtable.com/api
or the base URL.

## Step 3 — Create the GitHub repo

1. Create a new repository (private is fine).
2. Add these files at the repo root, keeping `feed.yml` at `.github/workflows/feed.yml`.
3. Commit and push.

## Step 4 — Add secrets and variables

In the repo: **Settings → Secrets and variables → Actions**

Under **Secrets**:
- `AIRTABLE_TOKEN` = your `pat...` token

Under **Variables**:
- `AIRTABLE_BASE_ID` = `appPn95FPfn3fzi4n`  (the Marketplace content base)
- `AIRTABLE_TABLE` = `Kaufland DE`
- (optional) `STATUS_FIELD` = `Status`
- (optional) `STATUS_VALUE` = `Ready to publish`
- (optional) `ID_FIELD` = `Shopify variant ID`  (the unique feed key; EAN is not unique)

## Step 5 — Enable GitHub Pages

**Settings → Pages → Build and deployment → Source: Deploy from a branch.**
Branch: `main`, folder: `/docs`. Save.

Your feed URL will be:
```
https://<your-username>.github.io/<repo-name>/feed.xml
```

## Step 6 — Run it once

Go to **Actions → Build Channable feed → Run workflow**. It generates
`docs/feed.xml` and commits it. After the first run, open the Pages URL to confirm
the XML loads.

## Step 7 — Connect in Channable

In Channable: **Add import → Data file → XML**, paste the Pages feed URL.
Channable will read `<id>` as the identifier and `<title>` / `<description>`
(HTML inside CDATA) as content fields. Set the import refresh to daily.

---

## Feed format

```xml
<?xml version="1.0" encoding="UTF-8"?>
<products>
  <product>
    <id>4012345678901</id>
    <title><![CDATA[Voldt Type 2 Cable 7m]]></title>
    <description><![CDATA[<h2>...</h2><p>...</p>]]></description>
  </product>
</products>
```

## Customizing fields

The script reads optional env vars:

- `FIELD_MAP` — JSON mapping Airtable field → XML tag, e.g.
  `{"Product title":"title","Description":"description","Bullet points":"features"}`
- `RICH_FIELDS` — comma list of fields converted from Markdown to HTML
  (default: `Description`).
- `ID_FIELD`, `STATUS_FIELD`, `STATUS_VALUE`, `AIRTABLE_TABLE`, `FEED_OUTPUT`.

To add a second marketplace later, copy the table (e.g. `Bol NL`), add a second
job/workflow with `AIRTABLE_TABLE=Bol NL` and `FEED_OUTPUT=docs/bol-nl.xml`.

## Run locally (optional test)

```bash
pip install -r requirements.txt
export AIRTABLE_TOKEN=pat... AIRTABLE_BASE_ID=app... AIRTABLE_TABLE="Kaufland DE"
export STATUS_FIELD=Status STATUS_VALUE="Ready to publish" FEED_OUTPUT=docs/feed.xml
python generate_feed.py
```
