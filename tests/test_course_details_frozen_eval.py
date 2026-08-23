from collections import Counter
from pathlib import Path

from app.retrieval.documents import DocumentEvidence
from eval.frozen.course_details_120_v1.runner import load_cases, target_course_rank

MANIFEST = Path("eval/frozen/course_details_120_v1/manifest.json")


def test_course_details_manifest_freezes_six_variants_for_twenty_gold_courses():
    cases = load_cases(MANIFEST)

    assert len(cases) == 120
    assert len({case.case_id for case in cases}) == 120
    assert set(Counter(case.course_code for case in cases).values()) == {6}
    assert len({case.course_code for case in cases}) == 20


def test_target_rank_requires_the_requested_course_marker():
    evidence = [
        DocumentEvidence(
            chunk_id="wrong",
            text="CS 2316. Data Input and Manipulation.",
            title="Computer Science",
            canonical_url="https://catalog.gatech.edu/coursesaz/cs/",
            source_name="gt-catalog",
            source_type="course_catalog",
            authority="catalog",
            fetched_at=None,
            edition=None,
            score=1,
            retrieval_method="hybrid_rrf",
        ),
        DocumentEvidence(
            chunk_id="right",
            text="CS 6300. Software Development Process. 3 Credit Hours.",
            title="Computer Science",
            canonical_url="https://catalog.gatech.edu/coursesaz/cs/",
            source_name="gt-catalog",
            source_type="course_catalog",
            authority="catalog",
            fetched_at=None,
            edition=None,
            score=0.9,
            retrieval_method="exact_code",
        ),
    ]

    assert target_course_rank("CS 6300", evidence) == 2
    assert target_course_rank("CS 7750", evidence) is None
