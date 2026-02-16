# Sources Policy

## Overview

BuzzBot uses a source registry (`ingestion/sources.yaml`) with explicit policy gates. Each source must be explicitly allowed before ingestion.

## Registered Sources

| Source | URL | Allowed | Reason |
|--------|-----|---------|--------|
| gt-registrar | registrar.gatech.edu | Yes | Official academic calendar and registration |
| gt-catalog | catalog.gatech.edu | Yes | Official course descriptions and degree policies |
| ratemyprofessors | ratemyprofessors.com | **No** | ToS prohibit scraping; user-provided mode only |

## Policy Gates

Every source in `sources.yaml` has:
- `allowed: true/false` — hard gate for ingestion pipeline
- `reason` — human-readable justification

Sources with `allowed: false` are completely skipped during ingestion. The pipeline logs a message and moves on.

## RateMyProfessors Policy

**No automated access.** BuzzBot's RMP integration is strictly user-provided:
1. User pastes an excerpt into the chat input
2. BuzzBot summarizes the excerpt with explicit "unofficial / user-provided" labels
3. Citations reference `user-provided:rmp` (not an actual URL)

This design respects:
- RMP's Terms of Service (no crawling, scraping, or spidering)
- User data sovereignty (user controls what content enters the system)
- Transparency (every RMP-sourced claim is labeled)

## Common Crawl Policy

When `ENABLE_COMMONCRAWL=true`, only these domains are queried:
- `gatech.edu`
- `registrar.gatech.edu`
- `catalog.gatech.edu`

An explicit blocklist prevents `ratemyprofessors.com` from ever being included, regardless of configuration.

## Adding New Sources

1. Add an entry to `ingestion/sources.yaml`
2. Set `allowed: true` and provide a `reason`
3. Configure `sitemap_url`, `include_patterns`, `exclude_patterns`
4. Run `make ingest`

Always verify the source's `robots.txt` and Terms of Service before adding.
