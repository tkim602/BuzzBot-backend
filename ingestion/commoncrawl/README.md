# Common Crawl Module

Optional module for ingesting Georgia Tech pages from Common Crawl archives.

## Safety

- **Allowlist only**: `gatech.edu`, `registrar.gatech.edu`, `catalog.gatech.edu`
- **RateMyProfessors is explicitly excluded** — never add it to the allowlist.
- Controlled via `ENABLE_COMMONCRAWL=true/false` in `.env`.

## How it works

1. **CDX Client** (`cdx_client.py`): Queries the Common Crawl CDX API to find archived URLs matching the allowlist.
2. **WARC Fetch** (`warc_fetch.py`): Downloads WARC records for matched URLs.
3. **Pipeline** (`pipeline.py`): Orchestrates CDX query → WARC fetch → extract → chunk → index.

## Usage

```bash
ENABLE_COMMONCRAWL=true python -m ingestion.commoncrawl.pipeline
```
