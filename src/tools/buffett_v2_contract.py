"""Standalone v2 research contract. No legacy parser, network or model calls.

This file and the policy are frozen into each run. All prices describe equity
cash flows per economic share, after reconciling financing and other claims.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from copy import deepcopy
import math
import re

SCHEMA_VERSION = "buffett-research-v2.5"
POLICY_VERSION = "buffett-valuation-6"
TIERS = ("watch", "research", "starter", "add", "fat_pitch")
BOUNDARIES = ("V_central", "V_conservative", "V_adverse", "V_floor")
SCENARIO_LABELS = ("central", "conservative", "adverse", "floor")
TIER_LABELS = dict(zip(TIERS, ("仅观望", "认真研究 / 最多极小仓", "开始建仓", "明显加仓", "Buffett 式胖球候选")))
INTERVALS = dict(zip(TIERS, ((BOUNDARIES[0], None), (BOUNDARIES[1], BOUNDARIES[0]),
    (BOUNDARIES[2], BOUNDARIES[1]), (BOUNDARIES[3], BOUNDARIES[2]), (None, BOUNDARIES[3]))))
FAT_PITCH_CHECKS = ("floor_independent", "no_specific_equity_destroying_tail", "financing_viable",
                    "no_major_dilution", "capital_distributable", "cross_check_resolved")
SEVERE_SURVIVAL_CHECKS = ("no_specific_equity_destroying_tail", "financing_viable", "no_major_dilution")
SURVIVAL_CHECKS = (*SEVERE_SURVIVAL_CHECKS, "capital_distributable")


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def arr(items):
    return {"type": "array", "items": items}


def enum(*values):
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string"}
NUMBER = {"type": "number"}
NULL_NUMBER = {"type": ["number", "null"]}
NULL_TEXT = {"type": ["string", "null"]}
STRINGS = arr(TEXT)
GATES = obj({
    "evidence_cap": enum(*TIERS), "evidence_reason": TEXT,
    "survival_cap": enum(*TIERS), "survival_reason": TEXT,
    "thesis_break": enum("none", "research_cap", "reject"), "thesis_break_reason": TEXT,
    "floor_cross_check_resolution": obj({"status": enum("not_needed", "resolved", "unresolved"),
        "reason": TEXT, "source_ids": STRINGS}),
    "fat_pitch_checks": obj({name: obj({"passed": {"type": "boolean"}, "reason": TEXT})
                              for name in FAT_PITCH_CHECKS}),
})
NULL_GATES = dict(GATES, type=["object", "null"])
RATE = obj({
    "id": TEXT, "role": enum("reference", "model_discount", "entry_sensitivity", "investor_required_return"),
    "value": NUMBER, "currency": TEXT, "horizon_years": NUMBER, "basis": TEXT,
    "source_kind": enum("market_evidence", "analyst_assumption", "project_policy", "user_policy"),
    "source_ids": STRINGS,
})
SCENARIO = obj({
    "id": TEXT, "label": enum(*SCENARIO_LABELS),
    "method": TEXT, "assumptions": STRINGS, "discount_rate_id": TEXT,
    "assumption_changes": arr(obj({"variable": TEXT, "from_central": TEXT,
        "scenario_value": TEXT, "basis": TEXT, "source_ids": STRINGS})),
    "tolerable_errors": STRINGS, "survival_assessment": TEXT,
    "cash_flows": arr(obj({"year": NUMBER, "distribution_per_share": NUMBER})),
    "terminal": obj({"year": NUMBER, "value_per_share": NUMBER, "basis": TEXT,
                     "capital_treatment": TEXT}),
    "equity_value_per_share": NUMBER, "source_ids": STRINGS,
})
CROSS_CHECK = obj({
    "scenario_id": TEXT, "method": TEXT, "status": enum("available", "unavailable"),
    "value_per_share": NULL_NUMBER, "explanation": TEXT, "source_ids": STRINGS,
})
# Optional for existing producers: omission asserts the same economic premise.
# A recovery reference cannot independently substantiate a going-concern floor.
CROSS_CHECK["properties"]["premise"] = enum("same_as_scenario", "different_state_or_right")
SCHEMA = obj({
    "schema_version": enum(SCHEMA_VERSION), "ticker": TEXT,
    "task_scope": enum("standalone_screen"), "as_of": TEXT, "information_cutoff": TEXT,
    "currency": TEXT,
    "research_status": enum("assessed", "conditional", "insufficient_evidence"),
    "business_quality": enum("good", "mixed", "poor", "unknown"),
    "valuation_status": enum("supported", "conditional", "unavailable", "not_attempted"),
    "business_summary": TEXT, "moat_assessment": TEXT, "veto_reason": TEXT,
    "quality_score": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
    "quality_assessment": {"type": ["object", "null"], **{
        k: v for k, v in obj({"rubric_id": TEXT, "confidence": enum("high", "medium", "low"),
            "dimensions": arr(obj({"name": TEXT, "score": NUMBER, "maximum": NUMBER,
                                   "rationale": TEXT, "source_ids": STRINGS}))}).items() if k != "type"}},
    "missing_evidence": STRINGS, "strongest_counterargument": TEXT,
    "entry_conditions": STRINGS, "invalidation_triggers": STRINGS,
    "opportunity_cost_status": enum("not_required"),
    "sources": arr(obj({"id": TEXT, "title": TEXT, "url": TEXT,
                        "published_at": NULL_TEXT, "supports": STRINGS})),
    "cash_flow_bridge": TEXT, "floor_limitation": TEXT,
    "rates": arr(RATE), "valuation_scenarios": arr(SCENARIO),
    "return_sensitivity": arr(obj({"scenario_id": TEXT, "rate_id": TEXT,
                                    "price_per_share": NUMBER})),
    "entry_policy": obj({
        "status": enum("pending_parent", "adopted"), "policy_id": NULL_TEXT,
        "defined_by": enum("unassigned", "parent"),
        "basis": TEXT, "gates": NULL_GATES,
        "boundaries": arr(obj({"id": enum(*BOUNDARIES), "price": NULL_NUMBER,
                                "scenario_id": NULL_TEXT, "rate_id": NULL_TEXT,
                                "status": enum("available", "unavailable"), "basis": TEXT})),
        "tiers": arr(obj({"tier": enum(*TIERS), "label": TEXT,
                           "lower_boundary": NULL_TEXT, "upper_boundary": NULL_TEXT,
                           "lower_inclusive": {"type": "boolean"}, "upper_inclusive": {"type": "boolean"},
                           "conditions": STRINGS, "basis": TEXT})),
    }),
    "reserves": arr(obj({
        "id": TEXT, "scenario_id": TEXT, "kind": enum("operating", "regulatory", "contingent"),
        "currency": TEXT, "amount": NUMBER, "consumed": NUMBER, "released": NUMBER,
        "terminal_balance": NUMBER, "release_condition": TEXT,
        "terminal_treatment": enum("included_in_operating_value", "added_separately", "excluded_with_reason"),
        "basis": TEXT,
    })),
    "risk_ledger": arr(obj({
        "id": TEXT, "description": TEXT, "cash_flow_treatment": TEXT,
        "rate_treatment": TEXT, "entry_treatment": TEXT, "reserve_ids": STRINGS,
        "overlap_resolution": TEXT,
    })),
    "cross_checks": arr(CROSS_CHECK),
})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    require(isinstance(value, str), "Timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def ticker_name(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]*", value), "Invalid ticker")
    require(value not in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                          *[f"LPT{i}" for i in range(1, 10)]}, "Reserved ticker filename")
    require(not value.endswith("."), "Invalid ticker filename")
    return value


def validate_schema(value, schema=SCHEMA, path="$"):
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    valid = {"null": value is None, "object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "boolean": type(value) is bool,
             "integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value)}
    require(not types or any(valid.get(t, False) for t in types), f"{path}: invalid type")
    if "enum" in schema:
        require(value in schema["enum"], f"{path}: invalid enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        require(set(schema.get("required", [])) <= value.keys(), f"{path}: missing fields")
        require(schema.get("additionalProperties") is not False or value.keys() <= properties.keys(),
                f"{path}: unexpected fields (legacy payloads are not v2)")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], path + "." + key)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            validate_schema(child, schema.get("items", {}), f"{path}[{i}]")
    elif type(value) in (int, float):
        require(value >= schema.get("minimum", -math.inf) and value <= schema.get("maximum", math.inf),
                f"{path}: outside allowed range")


def keyed(items, name):
    result = {item["id"]: item for item in items}
    require(len(result) == len(items) and all(result), f"Duplicate/empty {name} IDs")
    return result


def price_at(scenario, rate):
    value = sum(c["distribution_per_share"] / (1 + rate) ** c["year"] for c in scenario["cash_flows"])
    terminal = scenario["terminal"]
    return value + terminal["value_per_share"] / (1 + rate) ** terminal["year"]


def close(actual, expected, label):
    require(math.isclose(actual, expected, rel_tol=0.001, abs_tol=0.011), f"{label}: arithmetic mismatch")


def validate_analysis(data, policy, stage="final"):
    require(stage in ("research", "final"), "Unknown validation stage")
    validate_schema(data)
    require(policy["policy_version"] == POLICY_VERSION and policy["schema_version"] == SCHEMA_VERSION,
            "Unknown valuation policy/schema")
    ticker_name(data["ticker"])
    date.fromisoformat(data["as_of"])
    cutoff = timestamp(data["information_cutoff"])
    require(re.fullmatch(r"[A-Z]{3}", data["currency"]), "Currency must be ISO-style code")
    for field in ("business_summary", "moat_assessment", "strongest_counterargument"):
        require(data[field].strip(), f"Missing {field}")
    require(data["invalidation_triggers"], "Missing thesis invalidation triggers")
    sources = keyed(data["sources"], "source")
    require(sources, "Research needs sources")
    for source in sources.values():
        require(source["url"].startswith("https://") and source["title"].strip() and source["supports"],
                "Source must be traceable")
        if source["published_at"]:
            require(timestamp(source["published_at"]) <= cutoff, "Post-cutoff source")

    def refs(ids):
        require(bool(ids) and set(ids) <= sources.keys(), "Missing or unknown source references")

    score = data["quality_score"]
    assessment = data["quality_assessment"]
    if score is None:
        require(assessment is None, "Unscored research must not invent a scoring worksheet")
    else:
        require(assessment is not None, "Score requires a rubric")
        rubric = policy["quality_rubrics"].get(assessment["rubric_id"])
        require(rubric is not None, "Scoring rubric not declared in frozen policy")
        dimensions = assessment["dimensions"]
        require(len({d["name"] for d in dimensions}) == len(dimensions)
                and {d["name"] for d in dimensions} == set(rubric), "Rubric dimensions differ")
        for item in dimensions:
            require(item["maximum"] == rubric[item["name"]] and 0 <= item["score"] <= item["maximum"]
                    and item["rationale"].strip(), "Invalid score evidence/range")
            refs(item["source_ids"])
        close(score, sum(d["score"] for d in dimensions), "Quality total")
    rates = keyed(data["rates"], "rate")
    for rate in rates.values():
        require(-1 < rate["value"] <= 1 and rate["horizon_years"] > 0 and rate["basis"].strip(),
                "Rate needs value, horizon and justification")
        require(rate["currency"] == data["currency"], "Convert cash flows and rates to one currency first")
        if rate["source_kind"] == "market_evidence":
            refs(rate["source_ids"])
        elif rate["source_kind"] in ("user_policy", "project_policy"):
            approved = policy["approved_rates"].get(rate["id"])
            require(approved is not None and all(approved.get(k) == rate[k] for k in
                    ("value", "currency", "role", "source_kind")) and
                    (approved.get("horizon_years") == rate["horizon_years"] or
                     approved.get("horizon_years") == "selected_scenario"), "Undeclared return policy")
        if rate["role"] == "investor_required_return":
            require(rate["source_kind"] == "user_policy", "Cannot invent investor return requirements")
        require(set(rate["source_ids"]) <= sources.keys(), "Unknown rate source")
    scenarios = keyed(data["valuation_scenarios"], "scenario")
    available = data["valuation_status"] in ("supported", "conditional")
    require(bool(scenarios) == available, "Valuation status and models disagree")
    calculations = {"schema_version": SCHEMA_VERSION, "scenarios": {}, "review_flags": []}
    if available:
        require(data["cash_flow_bridge"].strip(), "Valuation needs economic cash-flow bridge")
        labels = [s["label"] for s in scenarios.values()]
        require(len(labels) == len(set(labels)) and set(labels) >= {"central", "conservative", "adverse"},
                "Need distinct central, conservative and adverse economic paths")
        require(("floor" in labels) != bool(data["floor_limitation"].strip()),
                "Estimate floor or explicitly explain why it cannot be bounded")
        require(len({s["discount_rate_id"] for s in scenarios.values()}) == 1,
                "Economic anchors must use the same discount framework/rate, not rate sensitivity")
    else:
        require(data["missing_evidence"], "Unavailable valuation needs evidence limitations")
    for sid, scenario in scenarios.items():
        rate = rates.get(scenario["discount_rate_id"])
        require(rate is not None and rate["role"] == "model_discount", "Scenario requires model discount rate")
        require(scenario["method"].strip() and scenario["assumptions"], "Scenario assumptions missing")
        refs(scenario["source_ids"])
        require(scenario["tolerable_errors"] and all(x.strip() for x in scenario["tolerable_errors"])
                and scenario["survival_assessment"].strip(), "Scenario needs error tolerance and survival evidence")
        require(scenario["label"] == "central" or scenario["assumption_changes"],
                "Non-central anchor needs economic assumption changes, not a price haircut")
        for change in scenario["assumption_changes"]:
            require(all(change[k].strip() for k in ("variable", "from_central", "scenario_value", "basis")),
                    "Economic assumption change needs values and evidence")
            refs(change["source_ids"])
        terminal = scenario["terminal"]
        require(terminal["year"] > 0 and terminal["basis"].strip() and terminal["capital_treatment"].strip(),
                "Terminal value needs horizon, basis and retained-capital treatment")
        years = [flow["year"] for flow in scenario["cash_flows"]]
        require(years and len(years) == len(set(years)) and years == sorted(years)
                and all(0 <= year <= terminal["year"] for year in years), "Invalid cash-flow timeline")
        require(terminal["value_per_share"] >= 0, "Negative terminal ordinary equity")
        value = price_at(scenario, rate["value"])
        require(value >= 0, "Negative modeled equity: use unavailable valuation or an explicit failure case")
        close(scenario["equity_value_per_share"], value, "Scenario value")
        terminal_pv = terminal["value_per_share"] / (1 + rate["value"]) ** terminal["year"]
        calculations["scenarios"][sid] = {"value_per_share": value,
            "terminal_pv_weight": terminal_pv / value if value else None}
    seen_sensitivities = set()
    for item in data["return_sensitivity"]:
        key = (item["scenario_id"], item["rate_id"])
        require(key not in seen_sensitivities, "Duplicate sensitivity")
        seen_sensitivities.add(key)
        require(key[0] in scenarios and key[1] in rates, "Unknown sensitivity input")
        close(item["price_per_share"], price_at(scenarios[key[0]], rates[key[1]]["value"]), "Return sensitivity")

    entry = data["entry_policy"]
    require(stage != "research" or entry["status"] == "pending_parent",
            "Subagent research cannot define action tiers; parent assignment required")
    require(stage != "final" or (available and entry["status"] == "adopted"),
            "Final publication requires a valuation and four parent-policy boundaries; continue research")
    if entry["status"] == "pending_parent":
        require(entry["policy_id"] is None and entry["defined_by"] == "unassigned"
                and not entry["boundaries"] and not entry["tiers"] and entry["gates"] is None,
                "No action-tier mapping without a parent policy_id")
    else:
        adopted = policy["approved_entry_policies"].get(entry["policy_id"])
        require(adopted is not None and adopted.get("defined_by") == "parent"
                and entry["defined_by"] == "parent", "Entry policy has not been adopted by parent in frozen policy")
        require([b["id"] for b in entry["boundaries"]] == list(BOUNDARIES), "All four ordered boundaries required")
        previous = math.inf
        for boundary, label in zip(entry["boundaries"], SCENARIO_LABELS):
            price, sid, rid = boundary["price"], boundary["scenario_id"], boundary["rate_id"]
            require(boundary["basis"].strip(), "Anchor needs parent rationale")
            if boundary["status"] == "unavailable":
                require(label == "floor" and price is None and sid is None and rid is None
                        and data["floor_limitation"].strip(), "Only explicitly unbounded floor may be unavailable")
                continue
            require(available and price is not None and 0 <= price <= previous, "Invalid boundary ordering/value")
            previous = price
            require(sid in scenarios and scenarios[sid]["label"] == label and rid in rates
                    and rid == scenarios[sid]["discount_rate_id"] and rates[rid]["role"] == "model_discount",
                    "Anchor needs its own matching economic scenario and shared model discount rate")
            close(price, price_at(scenarios[sid], rates[rid]["value"]), "Boundary price")
        active = [b for b in entry["boundaries"] if b["status"] == "available"]
        require(len({b["scenario_id"] for b in active}) == len(active), "Anchors must use distinct economic paths")
        require(len({b["rate_id"] for b in active}) == 1, "Anchors must use a common model rate")
        validate_gates(entry)
        require([t["tier"] for t in entry["tiers"]] == list(TIERS), "Five ordered intervals required")
        for tier in entry["tiers"]:
            lo, hi = INTERVALS[tier["tier"]]
            require((tier["lower_boundary"], tier["upper_boundary"]) == (lo, hi)
                    and tier["lower_inclusive"] is False and tier["upper_inclusive"] is (hi is not None)
                    and tier["label"] == TIER_LABELS[tier["tier"]], "Intervals must cover prices without gaps or overlaps; equality belongs to lower-price tier")
            require(tier["conditions"] and tier["basis"].strip() and data["entry_conditions"], "Entry needs conditions and basis")
        require(entry["basis"].strip(), "Entry policy needs provenance/basis")

    reserves = keyed(data["reserves"], "reserve")
    for reserve in reserves.values():
        require(reserve["scenario_id"] in scenarios and reserve["currency"] == data["currency"], "Reserve scenario/currency mismatch")
        require(all(reserve[k] >= 0 for k in ("amount", "consumed", "released", "terminal_balance")), "Negative reserve")
        close(reserve["amount"], reserve["consumed"] + reserve["released"] + reserve["terminal_balance"], "Reserve roll-forward")
        require(reserve["basis"].strip() and reserve["release_condition"].strip(), "Reserve needs use/release/terminal explanation")
    keyed(data["risk_ledger"], "risk")
    require(not available or data["risk_ledger"], "Valuation needs a risk ledger")
    for risk in data["risk_ledger"]:
        require(set(risk["reserve_ids"]) <= reserves.keys(), "Unknown risk reserve")
        treatments = sum(bool(risk[k].strip()) for k in ("cash_flow_treatment", "rate_treatment", "entry_treatment"))
        treatments += bool(risk["reserve_ids"])
        require(risk["description"].strip(), "Missing risk description")
        require(treatments <= 1 or risk["overlap_resolution"].strip(), "Risk repeated across cash/rates/reserves without reconciliation")
    require({r for risk in data["risk_ledger"] for r in risk["reserve_ids"]} == set(reserves), "Unmapped reserve risk")
    checked = set()
    for check in data["cross_checks"]:
        require(check["scenario_id"] in scenarios and check["method"].strip()
                and check["explanation"].strip(), "Cross-check needs method and explanation")
        require(check["method"] != scenarios[check["scenario_id"]]["method"], "Cross-check must use another method")
        checked.add(check["scenario_id"])
        if check["status"] == "available":
            require(check["value_per_share"] is not None and check["value_per_share"] >= 0, "Invalid cross-check value")
            refs(check["source_ids"])
            primary = scenarios[check["scenario_id"]]["equity_value_per_share"]
            if abs(primary - check["value_per_share"]) / max(abs(primary), 0.01) > policy["cross_check_review_fraction"]:
                calculations["review_flags"].append("material_cross_check_gap")
        else:
            require(check["value_per_share"] is None, "Unavailable cross-check cannot contain a value")
            calculations["review_flags"].append("cross_check_unavailable")
    require(all(s["id"] in checked for s in scenarios.values() if s["label"] in ("central", "floor")),
            "Central and floor need independent cross-checks or explicit unavailability")
    if entry["status"] == "adopted":
        checks = entry["gates"]["fat_pitch_checks"]
        floor = next((s for s in scenarios.values() if s["label"] == "floor"), None)
        independent = [c for c in data["cross_checks"] if floor and c["scenario_id"] == floor["id"]
                       and c["status"] == "available"]
        floor_support = [c for c in independent if c.get("premise", "same_as_scenario") == "same_as_scenario"]
        if checks["floor_independent"]["passed"]:
            require(floor_support, "Independent floor gate requires support for the floor premise; recovery-only evidence is insufficient")
        if checks["cross_check_resolved"]["passed"]:
            require(independent, "Resolved floor cross-check gate needs an available independent check")
            lower = any(item["value_per_share"] < floor["equity_value_per_share"]
                * (1 - policy["cross_check_review_fraction"]) for item in independent)
            require(not lower or entry["gates"]["floor_cross_check_resolution"]["status"] == "resolved",
                    "Material independent floor contradiction requires evidence-bound parent resolution")
        resolution = entry["gates"]["floor_cross_check_resolution"]
        if resolution["status"] == "resolved":
            refs(resolution["source_ids"])
        else:
            require(set(resolution["source_ids"]) <= sources.keys(), "Unknown resolution source")
    calculations["review_flags"] = sorted(set(calculations["review_flags"]))
    return calculations


def apply_parent_entry_policy(research, policy, decision):
    """Deterministic calculation after the parent selects four economic scenarios.

    The pipeline binds the separate parent decision to the unmodified research
    artifact. This function neither selects economic assumptions nor invents data.
    """
    validate_analysis(research, policy, stage="research")
    require(decision.get("defined_by") == "parent", "Entry decision must be made by parent")
    pid = decision.get("policy_id")
    adopted = policy["approved_entry_policies"].get(pid)
    require(adopted is not None and adopted.get("defined_by") == "parent", "Unknown parent policy_id")
    require(all(isinstance(decision.get(k), str) and decision[k].strip()
                for k in ("basis", "risk_overlap_resolution")), "Parent must justify scenario and risk treatment")
    scenarios = keyed(research["valuation_scenarios"], "scenario")
    selection = decision.get("anchor_scenarios", {})
    rationales = decision.get("anchor_rationales", {})
    require(set(selection) == set(BOUNDARIES) and set(rationales) == set(BOUNDARIES)
            and all(isinstance(v, str) and v.strip() for v in rationales.values()),
            "Parent needs four explicit economic-anchor selections and rationales")
    validate_schema(decision.get("gates"), GATES, "$.gates")
    conditions = decision.get("tier_conditions", {})
    require(set(conditions) == set(TIERS) and all(isinstance(v, list) and v and
            all(isinstance(s, str) and s.strip() for s in v) for v in conditions.values()),
            "Parent must define conditions for each of five tiers")
    data = deepcopy(research)
    boundaries = []
    rates = keyed(research["rates"], "rate")
    for bid, label in zip(BOUNDARIES, SCENARIO_LABELS):
        sid = selection[bid]
        if sid is None:
            require(label == "floor" and research["floor_limitation"].strip(),
                    "Only unbounded floor may be omitted with a limitation")
            boundaries.append(dict(id=bid, price=None, scenario_id=None, rate_id=None,
                                   status="unavailable", basis=rationales[bid]))
            continue
        require(sid in scenarios and scenarios[sid]["label"] == label,
                "Parent must select the matching economic scenario for every anchor")
        rid = scenarios[sid]["discount_rate_id"]
        price = price_at(scenarios[sid], rates[rid]["value"])
        boundaries.append(dict(id=bid, price=price, scenario_id=sid, rate_id=rid,
                               status="available", basis=rationales[bid]))
    tiers = [dict(tier=t, label=TIER_LABELS[t], lower_boundary=INTERVALS[t][0], upper_boundary=INTERVALS[t][1],
                  lower_inclusive=False, upper_inclusive=INTERVALS[t][1] is not None,
                  conditions=conditions[t], basis=decision["basis"]) for t in TIERS]
    data["entry_policy"] = dict(status="adopted", policy_id=pid, defined_by="parent",
                                basis=decision["basis"] + " " + decision["risk_overlap_resolution"],
                                boundaries=boundaries, tiers=tiers, gates=deepcopy(decision["gates"]))
    validate_analysis(data, policy, stage="final")
    return data


def validate_gates(entry):
    """Policy gates are parent decisions, with executable limits on their claims."""
    gates = entry["gates"]
    validate_schema(gates, GATES, "$.entry_policy.gates")
    require(all(gates[k].strip() for k in ("evidence_reason", "survival_reason", "thesis_break_reason")),
            "Parent gates need evidence, consequences and closure reasons")
    checks = gates["fat_pitch_checks"]
    resolution = gates["floor_cross_check_resolution"]
    require(resolution["reason"].strip(), "Floor cross-check resolution needs a substantive explanation")
    require(resolution["status"] != "unresolved" or not checks["cross_check_resolved"]["passed"],
            "Unresolved independent contradiction cannot pass fat-pitch gate")
    require(all(c["reason"].strip() for c in checks.values()), "Every fat-pitch check needs evidence/reason")
    floor_available = entry["boundaries"][-1]["status"] == "available"
    if not floor_available:
        require(not checks["floor_independent"]["passed"] and not checks["cross_check_resolved"]["passed"],
                "Unavailable floor cannot pass independent-floor checks")
    if not floor_available or not all(c["passed"] for c in checks.values()):
        require(min(TIERS.index(gates["evidence_cap"]), TIERS.index(gates["survival_cap"])) <= TIERS.index("add")
                or gates["thesis_break"] != "none", "Incomplete fat-pitch evidence must cap action below fat_pitch")
    if not all(checks[name]["passed"] for name in SEVERE_SURVIVAL_CHECKS):
        require(TIERS.index(gates["survival_cap"]) <= TIERS.index("research")
                or gates["thesis_break"] in ("research_cap", "reject"),
                "Specific equity-destroying tail, financing failure or major dilution requires research survival cap or rejection")
    elif not checks["capital_distributable"]["passed"]:
        require(TIERS.index(gates["survival_cap"]) <= TIERS.index("add")
                or gates["thesis_break"] in ("research_cap", "reject"),
                "Distribution restrictions require survival cap at add or below, or thesis-break override")


def classify_price(entry, price):
    require(type(price) in (int, float) and math.isfinite(price) and price > 0, "Price must be finite and positive")
    require(entry["status"] == "adopted" and [b["id"] for b in entry["boundaries"]] == list(BOUNDARIES),
            "Classification requires adopted four-boundary policy")
    values = [b["price"] for b in entry["boundaries"]]
    require(all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values[:3])
            and (values[-1] is None or (type(values[-1]) in (int, float)
                 and math.isfinite(values[-1]) and values[-1] >= 0)), "Invalid boundary value")
    require(all(a >= b for a, b in zip(values, values[1:]) if b is not None), "Invalid boundary ordering/value")
    for tier, boundary in zip(TIERS, values):
        if boundary is None:
            return None
        if price > boundary:
            return tier
    return "fat_pitch"


def classify_action(entry, price):
    """Highest research tier justified by price and evidence; never trade authority.

    With an unavailable floor, a price at/below adverse has at least the add
    economics. It cannot distinguish add/fat_pitch, so the conservative known
    research tier is add, then subject to both caps and thesis-break override.
    """
    economic = classify_price(entry, price)
    validate_gates(entry)
    gates = entry["gates"]
    tier = min((economic or "add", gates["evidence_cap"], gates["survival_cap"]), key=TIERS.index)
    if gates["thesis_break"] == "research_cap":
        tier = min(tier, "research", key=TIERS.index)
    elif gates["thesis_break"] == "reject":
        tier = None
    return dict(economic_tier=economic, evidence_cap=gates["evidence_cap"], survival_cap=gates["survival_cap"],
                actionable_tier=tier, thesis_break=gates["thesis_break"],
                classification_status="complete" if economic is not None else "floor_unavailable")


def entry_policy_markdown(data):
    entry = data["entry_policy"]
    boundaries = {b["id"]: b["price"] for b in entry["boundaries"]}
    lines = ["## 父层四经济锚、五档价格区间", "", f"政策 `{entry['policy_id']}`；定义者：父层。币种：{data['currency']}。",
             entry["basis"], "", "四锚分别来自正常经济、普通多项误差、单一核心论点失败（最多两项直接因果相连假设）、持续经营经济底部。floor 去除增长与质量溢价并计全成本，不自动清空客户或资产；有结构性衰退/有限寿命证据才用有限期 floor。清算回收仅为另列风险参考。分类采用未舍入数值；等值锚允许形成空区间。", "",
             "| 经济锚 | 估计价格 |", "|---|---:|"]
    for bid, price in boundaries.items():
        displayed = f"{price:.2f}" if price is not None else "无法可靠界定"
        lines.append(f"| {bid} | {displayed} |")
    if data["floor_limitation"].strip():
        lines += ["", "Floor 限制：" + data["floor_limitation"]]
    lines += ["", "| 价格区间 | 档位 | 条件 |", "|---|---|---|"]
    for tier in entry["tiers"]:
        lo, hi = tier["lower_boundary"], tier["upper_boundary"]
        interval = f"P > {lo}" if hi is None else (f"P ≤ {hi}" if lo is None else f"{lo} < P ≤ {hi}")
        conditions = "；".join(tier["conditions"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| {interval} | {tier['label']} | {conditions} |")
    gates = entry["gates"]
    lines += ["", "### 父层证据与生存闸门", "",
              f"证据上限：{TIER_LABELS[gates['evidence_cap']]}。{gates['evidence_reason']}", "",
              f"生存上限：{TIER_LABELS[gates['survival_cap']]}。{gates['survival_reason']}", "",
              f"逻辑破坏：{gates['thesis_break']}。{gates['thesis_break_reason']}", "",
              "| 胖球必要条件 | 通过 | 证据与待关闭问题 |", "|---|---|---|"]
    for name, check in gates["fat_pitch_checks"].items():
        reason = check["reason"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {name} | {'是' if check['passed'] else '否'} | {reason} |")
    resolution = gates["floor_cross_check_resolution"]
    lines += ["", f"持续经营 floor 交叉验证解决状态：{resolution['status']}。{resolution['reason']}",
              "来源：" + (", ".join(resolution["source_ids"]) or "无须额外来源；见独立验证"),
              "cross_check_resolved 允许有来源和理由解决的不同经济状态/权利差异；同前提较低估计的未解决反证禁止胖球。",
              "no_specific_equity_destroying_tail 只否决具体、可信、未解决且无法合理界定或吸收的普通股毁损风险；一般未知不构成失败，预算不等于法律赔偿上限。",
              "融资、重大稀释或上述尾部失败须 survival_cap≤research（或逻辑破坏限制）；仅资本分配受限须 survival_cap≤add。"]
    lines += ["", "允许行动档位取经济档位、证据上限、生存上限中的最低档，并应用逻辑破坏限制。Floor 是有条件估计，不是保本价；胖球是候选标签，actionable_tier 不是交易或仓位授权，独立筛选 actionable 始终为 false。"]
    return "\n".join(lines)
