"""Seed sources from sources.yaml into the database."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import Source
from app.db.session import SyncSessionLocal


def seed():
    sources_path = Path(__file__).resolve().parent.parent / "ingestion" / "sources.yaml"
    with open(sources_path) as f:
        data = yaml.safe_load(f)

    session = SyncSessionLocal()
    try:
        for s in data.get("sources", []):
            existing = session.query(Source).filter(Source.name == s["name"]).first()
            if existing:
                print(f"  Source '{s['name']}' already exists, updating...")
                existing.base_url = s["base_url"]
                existing.allowed = s.get("allowed", True)
                existing.reason = s.get("reason", "")
            else:
                print(f"  Creating source '{s['name']}'...")
                source = Source(
                    name=s["name"],
                    base_url=s["base_url"],
                    allowed=s.get("allowed", True),
                    reason=s.get("reason", ""),
                    refresh_policy_json=s.get("refresh_policy"),
                )
                session.add(source)
        session.commit()
        print("Sources seeded successfully.")
    except Exception as exc:
        session.rollback()
        print(f"Error: {exc}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    seed()
