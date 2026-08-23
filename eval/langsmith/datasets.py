from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATASET_NAME = "buzzbot-course-details-20-full-domain-v1"
_EXAMPLE_NAMESPACE = uuid.UUID("b8469f9f-a034-4ff6-b46a-35c218daf6ce")


@dataclass(frozen=True)
class CourseDetailsCase:
    case_id: str
    question: str
    gold_answer: str
    gold_urls: tuple[str, ...]
    gold_sources: tuple[str, ...]
    expected_route: str
    expected_subject: str
    expected_course_number: str
    metadata: dict[str, object]


def load_course_details(path: Path) -> list[CourseDetailsCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", []) if isinstance(payload, dict) else []
    selected = [item for item in items if item.get("subsystem") == "course_details"]
    if len(selected) != 20:
        raise ValueError(f"expected 20 course_details cases, found {len(selected)}")

    cases = []
    for item in selected:
        course_code = str(item["expected"]["course_code"]).split()
        if len(course_code) != 2:
            raise ValueError(f"{item['id']}: expected course_code as SUBJECT NUMBER")
        cases.append(
            CourseDetailsCase(
                case_id=str(item["id"]),
                question=str(item["question"]),
                gold_answer=str(item["gold_answer"]),
                gold_urls=tuple(str(url) for url in item["gold_urls"]),
                gold_sources=tuple(str(source) for source in item["gold_sources"]),
                expected_route=str(item["expected_route"]),
                expected_subject=course_code[0],
                expected_course_number=course_code[1],
                metadata={
                    "subsystem": "course_details",
                    "benchmark_version": "full-domain-v1",
                    "variant_group": str(item.get("variant_group", item["id"])),
                    "vertical": str(item.get("gold_vertical", "unknown")),
                    "question_type": str(item.get("question_type", "unknown")),
                    "difficulty": str(item.get("difficulty", "unknown")),
                    "style": str(item.get("style", "unknown")),
                    "time_sensitive": bool(item.get("time_sensitive", False)),
                },
            )
        )
    if len({case.case_id for case in cases}) != 20:
        raise ValueError("course_details case ids must be unique")
    return cases


def _example(case: CourseDetailsCase) -> dict[str, object]:
    return {
        "id": uuid.uuid5(_EXAMPLE_NAMESPACE, f"{DATASET_NAME}:{case.case_id}"),
        "inputs": {"case_id": case.case_id, "question": case.question},
        "outputs": {
            "answer": case.gold_answer,
            "gold_urls": list(case.gold_urls),
            "gold_sources": list(case.gold_sources),
            "expected_route": case.expected_route,
            "expected_subject": case.expected_subject,
            "expected_course_number": case.expected_course_number,
        },
        "metadata": case.metadata,
    }


def ensure_dataset(client: Any, cases: list[CourseDetailsCase]):
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        count = sum(1 for _ in client.list_examples(dataset_id=dataset.id))
        if count != len(cases):
            raise ValueError(f"{DATASET_NAME}: expected {len(cases)} examples, found {count}")
        return dataset

    dataset = client.create_dataset(
        DATASET_NAME,
        description="Frozen BuzzBot full-domain-v1 course_details slice (20 cases).",
        metadata={"benchmark_version": "full-domain-v1", "subsystem": "course_details"},
    )
    client.create_examples(dataset_id=dataset.id, examples=[_example(case) for case in cases])
    return dataset
