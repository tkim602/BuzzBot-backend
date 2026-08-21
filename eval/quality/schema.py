from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldCase:
    id: str
    variant_group: str
    question: str
    gold_answer: str
    gold_urls: tuple[str, ...]
    gold_sources: tuple[str, ...]
    gold_vertical: str
    gold_locator: str
    question_type: str
    time_sensitive: bool
    style: str


_STYLES = (
    ("direct", lambda topic, direct: direct),
    ("keyword", lambda topic, direct: f"GT {topic}"),
    ("search_bar", lambda topic, direct: f"Georgia Tech {topic} official policy"),
    ("fragment", lambda topic, direct: f"{topic.capitalize()} — what's the rule?"),
    (
        "casual",
        lambda topic, direct: (
            f"Quick question: what do I actually need to know about {topic} at Tech?"
        ),
    ),
    (
        "formal",
        lambda topic, direct: (
            f"What is Georgia Tech's official rule or procedure regarding {topic}?"
        ),
    ),
    (
        "scenario",
        lambda topic, direct: (
            f"I'm dealing with {topic} right now. What's the Georgia Tech rule I should follow?"
        ),
    ),
    (
        "confirmation",
        lambda topic, direct: (
            f"Just to confirm, what does Georgia Tech officially say about {topic}?"
        ),
    ),
    ("minimal", lambda topic, direct: f"{topic} at GT?"),
    (
        "support_desk",
        lambda topic, direct: f"Where can I find the official Georgia Tech guidance on {topic}?",
    ),
)


def _expand_fact(raw: dict[str, object]) -> list[GoldCase]:
    group = str(raw["fact_id"])
    topic = str(raw["gold_locator"]).strip().rstrip(".?")
    direct = str(raw["direct_question"])
    common = dict(
        variant_group=group,
        gold_answer=str(raw.get("gold_answer", "")),
        gold_urls=tuple(str(url) for url in raw.get("gold_urls", [])),
        gold_sources=tuple(str(source) for source in raw.get("gold_sources", [])),
        gold_vertical=str(raw.get("gold_vertical", "unknown")),
        gold_locator=topic,
        question_type=str(raw.get("question_type", "unknown")),
        time_sensitive=bool(raw.get("time_sensitive", False)),
    )
    if not common["gold_urls"]:
        raise ValueError(f"{group}: gold_urls is required")
    if not common["gold_sources"]:
        raise ValueError(f"{group}: gold_sources is required")
    return [
        GoldCase(
            id=f"{group}-v{index}",
            question=builder(topic, direct),
            style=style,
            **common,
        )
        for index, (style, builder) in enumerate(_STYLES, start=1)
    ]


def load_cases(path: Path) -> list[GoldCase]:
    cases: list[GoldCase] = []
    groups: set[str] = set()
    files = sorted(path.glob("gold_facts_part*.jsonl")) if path.is_dir() else [path]
    if not files:
        raise ValueError(f"no gold fact files found at {path}")
    for file_path in files:
        with file_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                raw = json.loads(line)
                group = str(raw.get("fact_id", ""))
                if not group:
                    raise ValueError(f"{file_path}:{line_number}: fact_id is required")
                if group in groups:
                    raise ValueError(f"{file_path}:{line_number}: duplicate fact_id {group}")
                groups.add(group)
                cases.extend(_expand_fact(raw))
    if not cases:
        raise ValueError("dataset is empty")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("duplicate case ids detected")
    return cases
