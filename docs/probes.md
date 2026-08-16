# Source Probes

Run a small public OSCAR check before any schedule synchronization:

```bash
python3 -m ingestion.probes.cli oscar --term 202608 --subject CS --course 7650
```

The command makes at most five HTTP attempts, parses at most twenty sections, stops immediately on HTTP 429 or authentication, and writes only safe report metadata plus the public response body under ignored `artifacts/probes/`.

`READY` proves only that the sample is publicly reachable and structurally parseable. It does not authorize bulk collection; the subsequent sync must still enforce rate limits, staging, coverage validation, and transactional publishing.

Statuses:

- `READY`: public sample parsed with the required fields
- `UNAVAILABLE`: transport or non-auth HTTP failure
- `RATE_LIMITED`: HTTP 429; no immediate retry
- `AUTH_REQUIRED`: authentication response or login redirect
- `PARSE_FAILED`: public HTML was reachable but required structure was missing

The probe does not initialize the database, read `.env`, call an LLM, or create embeddings.
