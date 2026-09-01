from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.db.session import AsyncSessionLocal
from app.retrieval import CourseQuery, lookup_course_offerings

MANIFEST = Path(__file__).with_name("manifest.json")


def _meeting(value: Any) -> dict[str, object]:
    return {
        "meeting_type": value.meeting_type,
        "days": value.days,
        "start_time": value.start_time.strftime("%H:%M") if value.start_time else None,
        "end_time": value.end_time.strftime("%H:%M") if value.end_time else None,
        "start_date": value.start_date.isoformat(),
        "end_date": value.end_date.isoformat(),
        "building": value.building,
        "room": value.room,
        "is_tba": value.is_tba,
    }


def _section(value: Any, *, detailed: bool) -> dict[str, object]:
    section: dict[str, object] = {
        "crn": value.crn,
        "section_code": value.section_code,
        "campus": value.campus,
    }
    if detailed:
        section.update(
            schedule_type=value.schedule_type,
            instructional_method=value.instructional_method,
            instructors=list(value.instructors),
            notes=value.notes,
            meetings=[_meeting(meeting) for meeting in value.meetings],
        )
    return section


def score_case(case: dict[str, Any], offerings: list[Any]) -> bool:
    expected = case["expected"]
    question_type = case["question_type"]
    if question_type == "offering":
        return (
            bool(offerings) is bool(expected["offered"])
            and len(offerings) == expected["section_count"]
            and all(
                value.subject == expected["subject"]
                and value.course_number == expected["course_number"]
                and value.title == expected["title"]
                and value.credits == expected["credits"]
                for value in offerings
            )
        )

    detailed = question_type == "instructor_meeting"
    actual = sorted(
        (_section(value, detailed=detailed) for value in offerings), key=lambda row: row["crn"]
    )
    wanted = sorted(expected["sections"], key=lambda row: row["crn"])
    return actual == wanted


async def run(manifest_path: Path) -> dict[str, object]:
    cases = json.loads(manifest_path.read_text())["items"]
    if len(cases) != 150:
        raise ValueError(f"expected 150 cases, found {len(cases)}")

    expected_versions = {case["snapshot"]["data_version_id"] for case in cases}
    if len(expected_versions) != 1:
        raise ValueError("manifest must reference one data version")

    failures: list[str] = []
    cache: dict[tuple[str, str, str], list[Any]] = {}
    async with AsyncSessionLocal() as session:
        for case in cases:
            key = (case["term_code"], *case["course_code"].split())
            if key not in cache:
                cache[key] = await lookup_course_offerings(session, CourseQuery(*key))
            offerings = cache[key]
            versions = {str(value.data_version_id) for value in offerings}
            if versions != expected_versions or not score_case(case, offerings):
                failures.append(case["id"])

    return {
        "cases": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "data_version_id": next(iter(expected_versions)),
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen schedule SQL regression")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    result = asyncio.run(run(parser.parse_args().manifest))
    print(json.dumps(result, separators=(",", ":")))
    raise SystemExit(1 if result["failed"] else 0)


if __name__ == "__main__":
    main()
