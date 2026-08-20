# Phase 2 Official Document Smoke Report

## Bounded probes

| Source | Status | Requests |
|---|---:|---:|
| `gt-registrar` | READY | 1 |
| `gt-academic-calendar` | READY | 1 |
| `gt-catalog` | READY | 1 |
| `gt-omscs` | FETCH_FAILED (certificate hostname mismatch on `www`) | 1 |
| `gt-admission` | READY | 1 |

No adjacent URLs were discovered and the failed OMSCS source was not retried.

## One-document synchronization

| Source | Outcome | Requests | Chunks |
|---|---:|---:|---:|
| `gt-registrar` | UNCHANGED | 2 | 2 |
| `gt-academic-calendar` | UNCHANGED | 2 | 1 |
| `gt-catalog` | INDEXED | 2 | 33 |
| `gt-admission` | INDEXED | 2 | 3 |

The first CLI attempt stopped before HTTP fetch because Pydantic-loaded `.env` values were not
passed into the OpenAI SDK. The shared OpenAI client creation paths now explicitly receive the
configured key; no document requests or API cost occurred during the failed attempt.

## Retrieval smoke

- Registration policy: 3 official Registrar citations returned.
- Exact academic deadline: 1 `gt-academic-calendar` citation returned after metadata-only source
  reclassification; no re-embedding was required.
- OpenAI usage tracker after ingestion and retrieval: `$0.00034188 / $3.00`.

Known limitation: the configured `www.omscs.gatech.edu` seed failed TLS hostname verification and
remains unavailable for this bounded run. It was not retried today; the registry now uses the
certificate-valid canonical host for the next bounded run.
