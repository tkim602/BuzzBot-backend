# Schedule SQL 150 v1

Frozen Fall 2026 CS structured-retrieval regression recovered from the verified
`buzzbot_schedule_sql_current_150_v1_1` artifact.

- Cases: 150 (50 courses × offering, sections/CRNs, instructor/meeting)
- Term: `202608`
- Data version: `bf5473f7-5d3f-4b3d-a2ba-eb8b84a54a60`
- Manifest SHA-256: `006d740a1a29cbfba1d156ac4f45ece5e33a80f1d2631895d7d613e39d2edbcb`
- Source: local published `buzzbot_v2` PostgreSQL snapshot; no network calls

Run from the repository root:

```bash
LANGSMITH_TRACING=false PYTHONPATH=$PWD python3 -m eval.frozen.schedule_sql_150_v1.runner
```

The runner compares typed retrieval results directly with the manifest. It does
not parse the user-facing answer, call an LLM, mutate the database, or use paid
APIs.
