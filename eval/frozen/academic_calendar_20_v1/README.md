# Academic Calendar 20 v1

Twenty fixed, source-consistent events from the published Georgia Tech
2026–2027 Academic Calendar. The suite checks deterministic routing and exact
event retrieval; it does not mix Registrar time-ticket pages into the Calendar
contract.

```bash
LANGSMITH_TRACING=false PYTHONPATH=$PWD \
python3 -m eval.frozen.academic_calendar_20_v1.runner
```

Manifest SHA-256: `5fb4ead9d22267935aa2b8c343a753034c77e81582d38fa33fa08cac88308114`

