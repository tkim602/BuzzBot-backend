# Schedule MVP v1

This frozen suite derives from `schedule_sql_150_v1` without changing its
authoritative Fall 2026 snapshot.

- Base snapshot: `bf5473f7-5d3f-4b3d-a2ba-eb8b84a54a60`
- Base manifest SHA-256: `006d740a1a29cbfba1d156ac4f45ece5e33a80f1d2631895d7d613e39d2edbcb`
- MVP manifest SHA-256: `a459448c88ac7969ffc45f76db0a10dd02f1ddc34a7e400b2e6de35246876e89`
- Renderer cases: 140 (20 courses × 7 query types)
- English NLU cases: 150 (140 realistic direct/context cases + 10 safety cases)

Run locally without creating LangSmith traces or OpenAI usage:

```bash
LANGSMITH_TRACING=false PYTHONPATH=$PWD \
  python3 -m eval.frozen.schedule_mvp_v1.runner --suite all
```

The renderer score requires exact typed sections, snapshot identity, a valid
deterministic answer, authoritative citations, and safe online wording. The NLU
score requires exact route/query type/course/term or a safe clarification.
