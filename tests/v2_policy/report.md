# v2 policy hypothesis

**Hypothesis.** The v1 model over-extracts advice, instructions, and opinions
when they mention a product or purchase. Explicitly requiring a verifiable
factual assertion, and saying that product/purchase wording alone is
insufficient, should remove those false-positive spans without changing the
catalogue-grounded claim rules.

**Offline baseline.** The saved v1 development analysis reports 261 unmatched
predictions: 254 advice/instruction spans, 3 meta-instructions, 2
unsupported/advice-like spans, and 2 purchase-paperwork instructions. The
representative visible answer text includes “Buy/check/compare” guidance next
to otherwise factual product sentences.

**Expected failure reduction.** At least 254 of 261 known unmatched spans
(97.3%) should disappear; if the two purchase-paperwork instructions and the
three meta-instructions are also consistently excluded, the reduction is
261/261 (100%). Factual sentence extraction and all v1 classification rules
should remain unchanged. This is a prompt-only candidate; no provider
experiment was launched and no gold labels or spans were supplied to
inference.
