# Policy answer dev-100 v1

This fixture freezes the accepted PR9 production retrieval output before any
PR10 answer-layer change. Each case stores at most the first five retrieved
official chunks, so answer synthesis, citation selection, and validation can
be compared without rerunning retrieval.

- Baseline git SHA: `b6a44bc435bc02202566b44063494146d52ea4c0`
- Retrieval report SHA-256: `30813af5e1830f9eb99dc8948bc2e5316e9650f4c6d04aba2b891190ab27da59`
- Manifest SHA-256: `58c343d37902a4bbbf4f281509413b701d55cca6cbeab8001404111de6c59562`
- Cases: 100
- Frozen answer-layer taxonomy rows: 21

The taxonomy records one primary observed failure per row. Secondary causes
remain visible in the answer-layer experiment rather than being encoded as
additional labels here.

