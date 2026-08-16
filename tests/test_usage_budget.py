import json

import pytest

from app.core import usage


def test_hard_cap_overrides_legacy_stored_limit(tmp_path, monkeypatch):
    usage_file = tmp_path / "usage.json"
    usage_file.write_text(
        json.dumps({"total_cost": 2.99, "limit": 20.0, "history": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(usage, "USAGE_FILE", usage_file)

    assert usage.get_usage()["limit"] == 3.0
    assert usage.set_limit(10.0) == 3.0

    usage.record_usage("gpt-4o-mini", 20_000, "output")

    with pytest.raises(usage.UsageLimitExceeded):
        usage.check_limit_or_raise()
