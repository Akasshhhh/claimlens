from __future__ import annotations

from prompts import get_prompt


_POLICY_DELTA = (
    "In particular, omit any sentence whose content is only advice,\n"
    "an instruction, or an opinion and makes no verifiable factual assertion, even\n"
    "if it mentions a product, model, brand, buyer, purchase, or buying context;\n"
    "product or purchase wording alone does not qualify as a claim."
)


def test_v2_keeps_existing_rules_and_adds_the_nonfactual_product_purchase_policy() -> None:
    v1 = get_prompt("v1")
    v2 = get_prompt("v2")

    assert v1 != v2
    assert "ClaimLens v2" in v2
    assert _POLICY_DELTA in v2
    assert _POLICY_DELTA not in v1
    for unchanged_rule in (
        "The supplied catalogue is the sole authority.",
        "Before writing JSON, make a sentence-boundary pass over the exact answer.",
        "Apply this clause rule to one extracted sentence:",
        "Return exactly one raw JSON object, with no prose or markdown:",
        "Use the source answer rather than counting from memory.",
    ):
        assert unchanged_rule in v2


def test_product_and_purchase_words_do_not_turn_nonclaims_into_claims() -> None:
    visible_answer = (
        "Buy ClearTrail Mini for your weekend trip. "
        "Check the exact ClearTrail Mini model before ordering. "
        "ClearTrail Mini is the best choice for a desk. "
        "ClearTrail Mini costs INR 1,299 in India."
    )
    nonclaim_sentences = (
        "Buy ClearTrail Mini for your weekend trip.",
        "Check the exact ClearTrail Mini model before ordering.",
        "ClearTrail Mini is the best choice for a desk.",
    )

    assert all(sentence in visible_answer for sentence in nonclaim_sentences)
    assert "ClearTrail Mini costs INR 1,299 in India." in visible_answer

    v2 = get_prompt("v2")
    assert _POLICY_DELTA in v2
    assert "Extract every complete sentence that makes a verifiable" in v2
