from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.graph.state import ScheduleQueryType
from app.graph.understanding import understand_query
from app.graph.workflow import WorkflowServices, build_workflow
from db.session import AsyncSessionLocal


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    subject: str | None
    course_number: str | None
    term_code: str | None
    query_type: ScheduleQueryType | None
    context: dict[str, object] | None = None
    clarification: bool = False


def _course_parts(course: str) -> tuple[str, str]:
    subject, number = course.split()
    return subject, number


def load_cases(path: Path) -> tuple[list[EvalCase], list[EvalCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    base_path = Path(payload["base_manifest"])
    digest = hashlib.sha256(base_path.read_bytes()).hexdigest()
    if digest != payload["base_manifest_sha256"]:
        raise ValueError("base schedule manifest hash mismatch")

    base = json.loads(base_path.read_text(encoding="utf-8"))["items"]
    available = {case["course_code"] for case in base}
    courses = payload["courses"]
    if not set(courses) <= available:
        raise ValueError("MVP manifest references an unknown frozen course")

    renderer: list[EvalCase] = []
    nlu: list[EvalCase] = []
    term_code = str(payload["term_code"])
    templates = [*payload["direct_templates"], *payload["context_templates"]]
    for course in courses:
        subject, number = _course_parts(course)
        values = {
            "course": course,
            "compact": course.replace(" ", ""),
            "dashed": course.replace(" ", "-"),
        }
        context = {
            "intent": "course_schedule",
            "subject": subject,
            "course_number": number,
            "term_code": term_code,
        }
        for template in templates:
            query_type = template["query_type"]
            case = EvalCase(
                case_id=f"renderer-{course.replace(' ', '-')}-{template['id']}",
                question=template["question"].format(**values),
                subject=subject,
                course_number=number,
                term_code=term_code,
                query_type=query_type,
                context=context if template in payload["context_templates"] else None,
            )
            renderer.append(case)
            nlu.append(
                EvalCase(
                    **{
                        **case.__dict__,
                        "case_id": case.case_id.replace("renderer-", "nlu-", 1),
                    }
                )
            )

    nlu.extend(
        EvalCase(
            case_id=f"nlu-{item['id']}",
            question=item["question"],
            subject=None,
            course_number=None,
            term_code=None,
            query_type=None,
            clarification=True,
        )
        for item in payload["safety_cases"]
    )
    return renderer, nlu


def score_nlu(cases: list[EvalCase]) -> dict[str, object]:
    failures: list[str] = []
    unsafe: list[str] = []
    clarification_cases = [case for case in cases if case.clarification]
    clarification_passed = 0
    for case in cases:
        result = understand_query(case.question, context=case.context)
        if case.clarification:
            passed = result["intent"] == "course_schedule" and bool(result["needs_clarification"])
            clarification_passed += int(passed)
            if not result["needs_clarification"]:
                unsafe.append(case.case_id)
        else:
            passed = (
                result["intent"] == "course_schedule"
                and result["subject"] == case.subject
                and result["course_number"] == case.course_number
                and result["term_code"] == case.term_code
                and result["schedule_query_type"] == case.query_type
                and not result["needs_clarification"]
            )
        if not passed:
            failures.append(case.case_id)

    count = len(cases)
    return {
        "cases": count,
        "passed": count - len(failures),
        "failed": len(failures),
        "success_rate": (count - len(failures)) / count if count else 0.0,
        "clarification_accuracy": (
            clarification_passed / len(clarification_cases) if clarification_cases else 1.0
        ),
        "unsafe_guess_rate": len(unsafe) / count if count else 0.0,
        "failures": failures,
        "unsafe_guesses": unsafe,
    }


_SECTION_FIELDS = (
    "crn",
    "section_code",
    "campus",
    "schedule_type",
    "instructional_method",
    "instructors",
    "notes",
    "meetings",
)


def score_renderer_result(
    case: EvalCase,
    result: dict[str, object],
    expected: dict[str, object],
    data_version_id: str,
) -> bool:
    evidence = result.get("evidence", [])
    if not isinstance(evidence, list):
        return False
    metadata = [item.get("metadata", {}) for item in evidence if isinstance(item, dict)]
    actual_sections = sorted(
        ({field: row.get(field) for field in _SECTION_FIELDS} for row in metadata),
        key=lambda row: str(row["crn"]),
    )
    expected_sections = sorted(expected["sections"], key=lambda row: str(row["crn"]))
    citations = result.get("citations", [])
    evidence_urls = {item.get("url") for item in evidence if isinstance(item, dict)}
    cited_urls = (
        {item.get("url") for item in citations if isinstance(item, dict)}
        if isinstance(citations, list)
        else set()
    )
    answer = str(result.get("answer", ""))
    unsafe_online_wording = bool(
        case.query_type == "online_availability"
        and any(word in answer.lower().split() for word in ("seat", "seats", "open", "register"))
    )
    return bool(
        result.get("intent") == "course_schedule"
        and result.get("subject") == case.subject
        and result.get("course_number") == case.course_number
        and result.get("term_code") == case.term_code
        and result.get("schedule_query_type") == case.query_type
        and result.get("answer_valid") is True
        and answer.strip()
        and actual_sections == expected_sections
        and metadata
        and {row.get("data_version_id") for row in metadata} == {data_version_id}
        and cited_urls
        and cited_urls <= evidence_urls
        and not unsafe_online_wording
    )


async def score_renderer(path: Path) -> dict[str, object]:
    cases, _ = load_cases(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    base = json.loads(Path(payload["base_manifest"]).read_text(encoding="utf-8"))["items"]
    expected = {
        item["course_code"]: item["expected"]
        for item in base
        if item["question_type"] == "instructor_meeting"
    }
    data_version_id = next(iter({item["snapshot"]["data_version_id"] for item in base}))
    failures: list[str] = []
    async with AsyncSessionLocal() as session:
        graph = build_workflow(WorkflowServices(session))
        for case in cases:
            state = {"query": case.question, **(case.context or {})}
            result = await graph.ainvoke(state)
            course = f"{case.subject} {case.course_number}"
            if not score_renderer_result(case, result, expected[course], data_version_id):
                failures.append(case.case_id)
    return {
        "cases": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "factual_correctness": (len(cases) - len(failures)) / len(cases),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen schedule MVP evaluations")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument("--suite", choices=("nlu", "renderer", "all"), default="all")
    args = parser.parse_args()
    renderer_cases, nlu_cases = load_cases(args.manifest)
    result: dict[str, object] = {}
    if args.suite in {"nlu", "all"}:
        result["nlu"] = score_nlu(nlu_cases)
    if args.suite in {"renderer", "all"}:
        result["renderer"] = asyncio.run(score_renderer(args.manifest))
    print(json.dumps(result, separators=(",", ":")))
    failed = any(isinstance(value, dict) and value.get("failed") for value in result.values())
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
