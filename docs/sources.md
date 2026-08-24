# Source Policy

BuzzBot uses exact public sources and stops before authentication. It does not perform a generic
`*.gatech.edu` crawl.

## Controlled document registry

Run 3 covers academics, finance, housing/dining, health/support, international students, career,
campus operations, student life, and admissions. Every entry declares its vertical, adapter, HTTPS
roots, seed URLs, accepted path prefixes, accepted content types, freshness class, and fail-closed
URL ceiling in `ingestion/sources.yaml`.

The 2026-08-21 read-only discovery verification observed:

| Source group | Sources | Discovered URLs / ceiling |
|---|---|---|
| Existing academics/admissions | Registrar, Catalog courses, OMSCS, First-Year | 24/50, 91/150, 6/10, 8/30 |
| Academic expansion | Catalog programs, Catalog rules, Registrar lifecycle | 377/500, 47/100, 16/80 |
| Finance | Bursar, Financial Aid | 5/60, 28/100 |
| Housing/dining | Housing, Dining | 12/100, 5/50 |
| Health/support | Stamps, Disability Services, Student Life support | 5/60, 10/80, 4/30 |
| International/career | OIE/ISSS, Career Center | 16/120, 10/80 |
| Campus/student life | Transportation, Academic Success, Student Engagement | 5/60, 9/60, 4/100 |
| Admissions expansion | Transfer, Graduate | 5/60, 4/100 |

These are discovery counts, not proof that full ingestion has run. `max_urls` is a safety ceiling,
never a truncation limit. The separate global verification limit is applied only after full bounded
discovery succeeds. HTTPS root/path membership, redirect destination, response status, content type,
and extracted body shape are checked before a URL can publish.

## Structured schedule source

Public OSCAR schedule pages are treated as structured data, not RAG documents. One representative
course request gates a subject sync. The collection must reconcile subjects, fetched/parsed counts,
required fields, course references, duplicate CRNs, meeting/TBA invariants, and freshness before it
can be published.

OSCAR authentication redirects stop the run. BuzzBot never signs in or submits registration actions.

## Blocked and unsupported sources

- RateMyProfessors: unsupported and never crawled.
- Common Crawl: not a fallback.
- BuzzPort/SSO/student records: out of scope.
- Arbitrary URLs supplied by a user or model: never fetched by the production graph.

## Adding a source

1. Establish that the information is official, public, and needed by a typed retrieval intent.
2. Add exact HTTPS roots, seeds, path prefixes, content types, freshness, and profile to
   `ingestion/sources.yaml` with an evidence-based hard ceiling.
3. Add registry/probe tests for host and redirect behavior.
4. Run `probe` once. Do not sync a source that does not report READY.
5. Sync one seed and inspect only counts/metadata before running the full immutable profile.
