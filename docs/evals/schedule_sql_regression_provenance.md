# Schedule SQL Regression Provenance

- Benchmark: `buzzbot_schedule_sql_current_150_v1_1`
- Frozen manifest: `eval/frozen/schedule_sql_150_v1/manifest.json`
- Manifest SHA-256: `006d740a1a29cbfba1d156ac4f45ece5e33a80f1d2631895d7d613e39d2edbcb`
- Cases: 150
- Scope: Fall 2026 (`202608`), 50 CS courses, 3 checks per course
- Data version: `bf5473f7-5d3f-4b3d-a2ba-eb8b84a54a60`
- Baseline git SHA: `acc17ca`

## Provenance

The manifest was recovered without modification from:

```text
~/Desktop/personal_project/buzzbot_full_domain_500_dataset/sql_current_150.json
```

The historical HTTP evaluation completed 150/150 cases with structured exact
rate 1.0. Its preserved summary SHA-256 is:

```text
ddb87f15175942c104c51104da88f0b845cf903d1965b4da1f3d846414a9d68c
```

That historical runner parsed the old semicolon-delimited response, so it is
not the ongoing regression gate. The committed runner queries
`lookup_course_offerings` and compares typed values directly against the frozen
manifest.

## Current baseline

Command:

```bash
LANGSMITH_TRACING=false PYTHONPATH=$PWD python3 -m eval.frozen.schedule_sql_150_v1.runner
```

Result:

```json
{"cases":150,"passed":150,"failed":0,"data_version_id":"bf5473f7-5d3f-4b3d-a2ba-eb8b84a54a60","failures":[]}
```

This benchmark performs no network requests, LLM calls, or database writes.
