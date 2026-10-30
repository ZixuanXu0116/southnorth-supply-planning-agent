from snm_evals.verifier_mutations import mutations

CASE = "C-US10-40001789-SH-20261117"
RANKING = {
    "rule": "expected_cost",
    "ranked": [
        {
            "option": f"P-1-8-{CASE}",
            "kind": "new_order",
            "coverage_date": "2026-11-12",
            "stockout_risk": "11.88%",
            "meets_service_target": False,
            "missed": [],
            "total_cost": 81.01,
            "expected_total_cost": 286.21,
        },
        {
            "option": f"P-1-10-{CASE}",
            "kind": "new_order",
            "coverage_date": "2026-11-05",
            "stockout_risk": "9.89%",
            "meets_service_target": True,
            "missed": ["freight", "surcharge"],
            "total_cost": 245.48,
            "expected_total_cost": 376.28,
        },
    ],
    "reference": [],
}
REC = {
    "case_id": CASE,
    "options": [
        {"option": f"P-1-8-{CASE}", "evaluation_id": "E-1", "rank": 1},
        {"option": f"P-1-10-{CASE}", "evaluation_id": "E-2", "rank": 2},
    ],
    "recommended": f"P-1-8-{CASE}",
    "rationale_text": "Recommend P-1-8: risk 11.88%, expected total 286.21 USD.",
}


def test_each_mutation_carries_one_false_claim_with_evaluated_figures():
    variants = mutations(REC, RANKING)
    assert set(variants) == {"figure", "mandate_claim", "omission", "cheapest_claim", "target_claim"}
    assert variants["figure"]["rationale_text"] == "Recommend P-1-8: risk 14.88%, expected total 286.21 USD."
    assert variants["figure"]["recommended"] == REC["recommended"]
    mandate = variants["mandate_claim"]
    assert mandate["recommended"] == f"P-1-10-{CASE}"
    assert "inside the mandate" in mandate["rationale_text"] and "9.89%" in mandate["rationale_text"]
    assert "mandate" not in variants["omission"]["rationale_text"]
    assert variants["cheapest_claim"]["recommended"] == f"P-1-10-{CASE}"
    assert "376.28" in variants["cheapest_claim"]["rationale_text"]
    target = variants["target_claim"]
    assert target["recommended"] == f"P-1-8-{CASE}"
    assert "11.88% meets the service target" in target["rationale_text"]
    assert all(v["options"] == REC["options"] for v in variants.values())


def test_mutations_that_do_not_apply_are_left_out():
    inside_only = {**RANKING, "ranked": [{**RANKING["ranked"][0], "meets_service_target": True}]}
    rec = {**REC, "rationale_text": "Recommend P-1-8: it covers the gap."}
    assert mutations(rec, inside_only) == {}
