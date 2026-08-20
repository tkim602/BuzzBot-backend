# v2 Source Policy

BuzzBot v2 uses exact public sources and stops before authentication. It does not perform a generic
`*.gatech.edu` crawl.

## Controlled document registry

| Source | Type | Authority | Initial seed cap |
|---|---|---|---:|
| `gt-registrar` | registration policy | Registrar | 10 |
| `gt-academic-calendar` | exact dates | Academic Calendar | 5 |
| `gt-catalog` | course Catalog | Catalog | 15 |
| `gt-omscs` | OMSCS policy | OMSCS | 10 |
| `gt-admission` | undergraduate admissions | Admissions | 5 |

The cap is a registry ceiling; the initial bounded run fetches at most one seed. Every source must
pass a one-request probe first. HTTPS root membership, redirect destination, response status, content
type, and extracted body shape are checked before synchronization.

## Structured schedule source

Public OSCAR schedule pages are treated as structured data, not RAG documents. One representative
course request gates a subject sync. The collection must reconcile subjects, fetched/parsed counts,
required fields, course references, duplicate CRNs, meeting/TBA invariants, and freshness before it
can be published.

OSCAR authentication redirects stop the run. BuzzBot never signs in or submits registration actions.

## Blocked and unsupported sources

- RateMyProfessors: never crawled; legacy user-provided excerpt mode only.
- Common Crawl: not a v2 fallback.
- BuzzPort/SSO/student records: out of scope.
- Arbitrary URLs supplied by a user or model: never fetched by the v2 graph.

## Adding a source

1. Establish that the information is official, public, and needed by a typed retrieval intent.
2. Add exact HTTPS roots and seeds to `ingestion/sources.yaml` with a small hard cap.
3. Add registry/probe tests for host and redirect behavior.
4. Run `probe` once. Do not sync a source that does not report READY.
5. Sync one seed and inspect only counts/metadata before expanding its explicit registry entry.
