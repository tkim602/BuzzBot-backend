# BuzzBot Rubric (Human + Automated)

Each answer should be scored on:

1) Correctness (0-2)
- 2: Correct and complete per cited official source
- 1: Partially correct or missing an important detail
- 0: Incorrect or fabricated

2) Grounding / Faithfulness (0-2)
- 2: All factual statements supported by retrieved contexts
- 1: Minor overreach / unclear support
- 0: Hallucinated key facts

3) Citations Quality (0-2)
- 2: Right URL + right section + fetched_at relevant
- 1: Cites correct domain but weak/unclear section linkage
- 0: No citations or irrelevant citations

4) Freshness Handling (0-2) [only for sensitive queries]
- 2: Mentions verification time; uses live_fetch or fresh cache
- 1: Answer ok but freshness uncertain
- 0: Uses stale info or fails to address freshness

Overall: 0-8
