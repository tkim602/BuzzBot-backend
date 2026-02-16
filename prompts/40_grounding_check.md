# Grounding Check (JSON Only)

You are given:
- draft_answer_json (produced by the generator)
- retrieved_contexts (same as used for generation)

Goal:
Detect any unsupported or overstated claims.

Return ONLY JSON:
{
  "verdict": "pass" | "fail",
  "unsupported_claims": [
    {"claim": "...", "reason": "..."}
  ],
  "citation_issues": [
    {"issue": "...", "fix": "..."}
  ],
  "suggested_edits": "short instruction to fix"
}

Rules:
- A claim is unsupported if it is not directly stated or clearly implied by the retrieved_contexts.
- If the answer says a date/deadline, it must be found in contexts with a citation pointing to it.
- If contexts conflict, the answer must acknowledge the conflict; otherwise fail.
- If verdict=fail, suggested_edits must be actionable (e.g., remove claim, lower confidence, add follow-up).
