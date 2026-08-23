# Course Details 120 v1

This frozen retrieval suite expands the 20 manually verified Course Details
gold cases into six fixed realistic English variants per course.

- Cases: 120
- Courses: 20
- Source dataset SHA-256: `8241477b0f886353cd28a1a2c645fccfe9b3ebf8fe29e9f861b5e40d19efb2c3`
- Manifest SHA-256: `fbeefa15b0d1cad67fc3bafc2318d7d7edf16cb10a7781f288dcf0ca85d0910a`
- Gold scope: official title, credits, and description
- Scoring: requested-course chunk rank, not shared Catalog URL equality

```bash
LANGSMITH_TRACING=false PYTHONPATH=$PWD \
  python3 -m eval.frozen.course_details_120_v1.runner
```

The suite does not call the answer model or judge. It measures the diagnosed
candidate/evidence-selection boundary independently.
