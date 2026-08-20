from eval.agentic_rag_eval import evaluate_cases, load_cases


def test_offline_golden_set_routes_and_extracts_required_fields_without_api():
    report = evaluate_cases(load_cases())

    assert report["mode"] == "offline_no_api"
    assert report["cases"] >= 10
    assert report["routing_accuracy"] == 1.0
    assert report["required_field_accuracy"] == 1.0
    assert report["passed"] is True
