# Course Details LangSmith Baseline

- Dataset: buzzbot-course-details-20-full-domain-v1
- Git SHA: `c33866a5409962ac31de1eff6f2535f4d1ff2988`
- Generated: 2026-08-23T18:25:45.104751+00:00
- Experiment: https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3
- Cases: 20
- Task success: 20.0%
- Route accuracy: 100.0%
- Slot accuracy: 100.0%
- Gold URL Hit@5: 100.0%
- Gold URL Hit@8: 100.0%
- MRR@8: 1.0000
- Evidence valid: 100.0%
- Answer correctness: 20.0%
- Support: 20.0%
- Gold citation hit: 30.0%
- Abstention: 70.0%
- App cost: $0.011981
- Judge cost: $0.000600
- Latency p50/p95: 4561.9 / 5522.7 ms
- Failure stages: `{"ANSWER_VALIDATION_REJECT": 14, "PASS": 4, "SYNTHESIS_WRONG": 2}`
- Interpretation: catalog chunks share one canonical subject URL, so URL Hit@5 does not prove that the requested course chunk was retrieved; inspect each linked retrieval span.

| Case | Route | Gold rank | Evidence | Abstained | Correct | Supported | Stage | Trace |
|---|---:|---:|---:|---:|---:|---:|---|---|
| fd-course-001 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | PASS | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-8f8d-7cf3-9ac9-cf175c0aa0b7?trace_id=01a02fd9-8f8d-7cf3-9ac9-cf175c0aa0b7&start_time=2026-08-23T18:19:42.349938) |
| fd-course-002 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | PASS | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-5722-7da0-bec1-9681ad5e0ce7?trace_id=01a02fd9-5722-7da0-bec1-9681ad5e0ce7&start_time=2026-08-23T18:19:27.906903) |
| fd-course-003 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fda-138e-7ce3-a633-61cd77078eaa?trace_id=01a02fda-138e-7ce3-a633-61cd77078eaa&start_time=2026-08-23T18:20:16.142330) |
| fd-course-004 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-4850-7d01-966e-6b665f5f4146?trace_id=01a02fd9-4850-7d01-966e-6b665f5f4146&start_time=2026-08-23T18:19:24.112035) |
| fd-course-005 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fda-02cd-7463-804f-d4cc46f6e918?trace_id=01a02fda-02cd-7463-804f-d4cc46f6e918&start_time=2026-08-23T18:20:11.853643) |
| fd-course-006 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-7ccd-7633-8c5b-1da0dc811ab4?trace_id=01a02fd9-7ccd-7633-8c5b-1da0dc811ab4&start_time=2026-08-23T18:19:37.549226) |
| fd-course-007 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-b8d7-7d53-85a2-5a94267923af?trace_id=01a02fd9-b8d7-7d53-85a2-5a94267923af&start_time=2026-08-23T18:19:52.919514) |
| fd-course-008 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-6b39-72d2-9afc-50d982606d47?trace_id=01a02fd9-6b39-72d2-9afc-50d982606d47&start_time=2026-08-23T18:19:33.049463) |
| fd-course-009 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd8-d1b1-74b3-978c-4a458de1c47e?trace_id=01a02fd8-d1b1-74b3-978c-4a458de1c47e&start_time=2026-08-23T18:18:53.745226) |
| fd-course-010 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | PASS | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fda-34c9-7022-9213-7e0b87328967?trace_id=01a02fda-34c9-7022-9213-7e0b87328967&start_time=2026-08-23T18:20:24.649809) |
| fd-course-011 | 1.0 | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | PASS | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-3155-73a1-966c-bd21fd009eaa?trace_id=01a02fd9-3155-73a1-966c-bd21fd009eaa&start_time=2026-08-23T18:19:18.229455) |
| fd-course-012 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-1bba-7293-8264-b8218c1ab4ac?trace_id=01a02fd9-1bba-7293-8264-b8218c1ab4ac&start_time=2026-08-23T18:19:12.698592) |
| fd-course-013 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-c96a-77b3-9b4f-2c7c171cecf1?trace_id=01a02fd9-c96a-77b3-9b4f-2c7c171cecf1&start_time=2026-08-23T18:19:57.162099) |
| fd-course-014 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-dc30-7653-b7c2-c137639fe8a9?trace_id=01a02fd9-dc30-7653-b7c2-c137639fe8a9&start_time=2026-08-23T18:20:01.968212) |
| fd-course-015 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fda-4b58-7b43-8716-8a7054f563f1?trace_id=01a02fda-4b58-7b43-8716-8a7054f563f1&start_time=2026-08-23T18:20:30.424802) |
| fd-course-016 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fda-2466-72e1-87c9-f6200a07fb97?trace_id=01a02fda-2466-72e1-87c9-f6200a07fb97&start_time=2026-08-23T18:20:20.454995) |
| fd-course-017 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-ef52-7681-bc58-b2af9fd33bb7?trace_id=01a02fd9-ef52-7681-bc58-b2af9fd33bb7&start_time=2026-08-23T18:20:06.866597) |
| fd-course-018 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | SYNTHESIS_WRONG | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-a59a-7f73-b89b-71401c45e878?trace_id=01a02fd9-a59a-7f73-b89b-71401c45e878&start_time=2026-08-23T18:19:47.994932) |
| fd-course-019 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | SYNTHESIS_WRONG | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fda-5f68-77d0-8952-af757c52156c?trace_id=01a02fda-5f68-77d0-8952-af757c52156c&start_time=2026-08-23T18:20:35.560906) |
| fd-course-020 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | ANSWER_VALIDATION_REJECT | [trace](https://smith.langchain.com/o/bf3ea726-f9cc-4c04-84f2-0ee66ced13e2/projects/p/49345c5f-0e98-4e42-85cb-d42e61d0d4e3/r/01a02fd9-0b6e-7863-b71a-74e10351777b?trace_id=01a02fd9-0b6e-7863-b71a-74e10351777b&start_time=2026-08-23T18:19:08.526159) |
