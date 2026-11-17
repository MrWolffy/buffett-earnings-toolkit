"""Offline v2 contract and storage regressions. All issuers/numbers are synthetic."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.tools import buffett_v2_pipeline as p

c = p.contract
NOW = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)
CUTOFF = NOW.isoformat()
AS_OF = "2026-11-04"
SINCE = "2026-11-01T00:00:00+00:00"
URL = "https://example.test/issuer/q3"


def analysis(ticker="AAA", unavailable=False):
    data = {
        "schema_version": c.SCHEMA_VERSION, "ticker": ticker, "task_scope": "standalone_screen",
        "as_of": AS_OF, "information_cutoff": CUTOFF, "currency": "USD",
        "research_status": "conditional", "business_quality": "good",
        "valuation_status": "conditional", "business_summary": "Synthetic business",
        "moat_assessment": "Evidence-limited synthetic moat", "veto_reason": "",
        "quality_score": None, "quality_assessment": None, "missing_evidence": ["Uncertain long-run renewal"],
        "strongest_counterargument": "Renewals may fail", "entry_conditions": ["Verify renewal economics"],
        "invalidation_triggers": ["Loss of license"], "opportunity_cost_status": "not_required",
        "sources": [{"id": "release", "title": "Synthetic release", "url": URL,
                     "published_at": "2026-11-04T20:00:00Z", "supports": ["Historical cash"]}],
        "cash_flow_bridge": "After maintenance, growth, economic SBC and all senior claims per economic share",
        "rates": [{"id": "discount", "role": "model_discount", "value": 0.1, "currency": "USD",
                   "horizon_years": 5, "basis": "Synthetic assumption for testing", "source_kind": "analyst_assumption",
                   "source_ids": ["release"]}],
        "valuation_scenarios": [], "return_sensitivity": [], "floor_limitation": "",
        "entry_policy": {"status": "pending_parent", "policy_id": None, "defined_by": "unassigned", "basis": "Awaiting parent policy",
                         "boundaries": [], "tiers": [], "gates": None},
        "reserves": [], "risk_ledger": [{"id": "renewal", "description": "Renewal risk",
            "cash_flow_treatment": "Downside path", "rate_treatment": "", "entry_treatment": "",
            "reserve_ids": [], "overlap_resolution": ""}], "cross_checks": [],
    }
    for label, distribution, terminal in (("central", 10, 100), ("conservative", 8, 80), ("adverse", 5, 50), ("floor", 2, 20)):
        scenario = {"id": label, "label": label, "method": "Equity DCF", "assumptions": ["Fixed cash path"],
                    "discount_rate_id": "discount", "cash_flows": [{"year": i, "distribution_per_share": distribution} for i in range(1, 6)],
                    "terminal": {"year": 5, "value_per_share": terminal, "basis": "Synthetic exit",
                                 "capital_treatment": "Operating cash included in franchise"},
                    "equity_value_per_share": 0, "source_ids": ["release"],
                    "assumption_changes": [] if label == "central" else [{"variable": "annual distribution", "from_central": "10", "scenario_value": str(distribution), "basis": "Observed renewal and maintenance bounds", "source_ids": ["release"]}],
                    "tolerable_errors": ["Stated cash path survives lower renewal demand"],
                    "survival_assessment": "Fully funded operations; no refinancing or new equity required"}
        scenario["equity_value_per_share"] = c.price_at(scenario, 0.1)
        data["valuation_scenarios"].append(scenario)
    data["cross_checks"] = [{"scenario_id": "central", "method": "Normalized earnings capitalization",
                             "status": "available", "value_per_share": 100,
                             "explanation": "Independent normalized earnings check", "source_ids": ["release"]},
                            {"scenario_id": "floor", "method": "Normalized low-state earnings capitalization", "status": "available", "value_per_share": 20,
                             "explanation": "Independent going-concern low-state earnings after full maintenance, SBC, tax, working capital and senior claims", "source_ids": ["release"]}]
    if unavailable:
        data.update(valuation_status="unavailable", valuation_scenarios=[], rates=[], cross_checks=[], risk_ledger=[], floor_limitation="No supportable distributable cash or recovery values")
    return data


def parent_decision(policy):
    return {"defined_by": "parent", "policy_id": policy["default_entry_policy_id"],
            "anchor_scenarios": {"V_" + label: label for label in ("central", "conservative", "adverse", "floor")},
            "anchor_rationales": {key: "Parent checked economic assumptions and survival" for key in c.BOUNDARIES},
            "gates": {"evidence_cap": "fat_pitch", "evidence_reason": "Primary source and independent asset evidence reconciled", "survival_cap": "fat_pitch", "survival_reason": "No required refinancing, restricted capital or material dilution", "thesis_break": "none", "thesis_break_reason": "No thesis break identified", "floor_cross_check_resolution": {"status": "not_needed", "reason": "Comparable methods support the conditional floor", "source_ids": []}, "fat_pitch_checks": {key: {"passed": True, "reason": "Synthetic evidence independently verified"} for key in ("floor_independent", "no_specific_equity_destroying_tail", "financing_viable", "no_major_dilution", "capital_distributable", "cross_check_resolved")}},
            "basis": "Parent selected four independently supported economic cash paths", "risk_overlap_resolution": "No second haircut",
            "tier_conditions": {t: ["Renewal economics checked; update on evidence change"] for t in c.TIERS}}


def adopted(data):
    policy = p.read(p.REPO / "docs/buffett_v2/policy.json")
    return c.apply_parent_entry_policy(data, policy, parent_decision(policy))


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.policy = p.read(p.REPO / "docs/buffett_v2/policy.json")

    def test_current_contract_policy_and_schema_are_synchronized(self):
        self.assertEqual(c.SCHEMA_VERSION, 'buffett-research-v2.5')
        self.assertEqual(c.POLICY_VERSION, 'buffett-valuation-6')
        self.assertEqual(self.policy['schema_version'], c.SCHEMA_VERSION)
        self.assertEqual(self.policy['policy_version'], c.POLICY_VERSION)
        self.assertEqual(self.policy['default_entry_policy_id'], 'parent-assumption-tolerance-2026-11-v3')
        self.assertIn(self.policy['default_entry_policy_id'], self.policy['approved_entry_policies'])
        self.assertEqual(p.read(p.REPO / 'docs/buffett_v2/schema.json'), c.SCHEMA)
        self.assertIn('buffett-research-v2.4', p.READABLE_SCHEMAS)
        self.assertEqual(c.FAT_PITCH_CHECKS, ('floor_independent', 'no_specific_equity_destroying_tail',
            'financing_viable', 'no_major_dilution', 'capital_distributable', 'cross_check_resolved'))

    def test_prompt_regressions_preserve_adverse_floor_and_zero_floor_semantics(self):
        for name in ('SUBAGENT_INSTRUCTIONS.md', 'PARENT_INSTRUCTIONS.md', 'automation_prompt.txt'):
            text = (p.REPO / 'docs/buffett_v2' / name).read_text(encoding='utf-8')
            compact = ''.join(text.split())
            with self.subTest(document=name):
                self.assertIn(c.SCHEMA_VERSION, text)
                self.assertIn(c.POLICY_VERSION, text)
                self.assertIn('单一核心论点显著失败', text)
                self.assertIn('最多两项直接因果相连的假设失败', text)
                self.assertIn('独立驱动维持', compact)
                self.assertNotIn('其他参数也转弱', text)
                self.assertRegex(text, r'禁止(?:把|叠加)无关诉讼')
                self.assertIn('退回/修订多因素灾难包', compact)
                self.assertIn('GOING-CONCERN ECONOMIC FLOOR', text)
                self.assertIn('低位但有证据支持的正常化经营', text)
                self.assertIn('不得因floor标签自动摧毁现有客户', compact)
                self.assertIn('结构性衰退/有限寿命证据', text)
                self.assertIn('仅清算价值为零不足以推出零floor', compact)
                self.assertIn('持续经营普通股经济价值实际≤0', compact)
                self.assertIn('优先索偿后无普通股回收', compact)
                self.assertIn('JBSS/EVRG/ED/GMED', text)
                self.assertIn('EXPE/FICO/APP', text)
                self.assertIn('floor_independent', text)
                self.assertIn('different_state_or_right', text)
                self.assertIn('cross_check_resolved', text)
                self.assertRegex(compact, r'预算(?:不是|不等于|冒充)法律赔偿上限')
        for name in ('README.md', 'MIGRATION.md'):
            text = (p.REPO / 'docs/buffett_v2' / name).read_text(encoding='utf-8')
            with self.subTest(document=name):
                self.assertIn(c.SCHEMA_VERSION, text)
                self.assertIn(c.POLICY_VERSION, text)
                self.assertIn(self.policy['default_entry_policy_id'], text)
                self.assertIn('持续经营经济底部', text)
                self.assertIn('cross_check_resolved', text)

    def test_parent_template_has_no_default_caps_or_failed_checks(self):
        text = (p.REPO / 'docs/buffett_v2/PARENT_INSTRUCTIONS.md').read_text(encoding='utf-8')
        template = text.split('```json\n', 1)[1].split('```', 1)[0]
        self.assertIn('解释性伪值', text)
        self.assertIn('"evidence_cap":"<', template)
        self.assertIn('"survival_cap":"<', template)
        self.assertNotIn('"passed":false', template)

    def test_good_with_unavailable_valuation_and_no_score_is_valid(self):
        self.assertEqual(c.validate_analysis(analysis(unavailable=True), self.policy, stage="research")["scenarios"], {})
        with self.assertRaisesRegex(ValueError, "Final publication requires"):
            c.validate_analysis(analysis(unavailable=True), self.policy)

    def test_parent_policy_requires_four_boundaries_five_intervals(self):
        data = adopted(analysis())
        self.assertEqual(c.validate_analysis(data, self.policy)["review_flags"], [])
        data['entry_policy']['boundaries'].pop()
        with self.assertRaisesRegex(ValueError, "All four"):
            c.validate_analysis(data, self.policy)

    def test_subagent_cannot_map_sensitivity_to_action_without_policy(self):
        data = analysis()
        data['entry_policy']['boundaries'].append(dict(id='V_central',price=100,scenario_id='central',rate_id='discount',status='available',basis='Unauthorized child mapping'))
        with self.assertRaisesRegex(ValueError, "No action-tier mapping"):
            c.validate_analysis(data, self.policy, stage='research')
        with self.assertRaisesRegex(ValueError, "Subagent research cannot define"):
            c.validate_analysis(adopted(analysis()), self.policy, stage='research')
        data=analysis(); data['entry_policy']['status']='proposed'
        with self.assertRaisesRegex(ValueError, "invalid enum"):
            c.validate_analysis(data, self.policy, stage='research')

    def test_pending_estimates_cannot_be_published_as_final(self):
        with self.assertRaisesRegex(ValueError, "Final publication requires"):
            c.validate_analysis(analysis(), self.policy)

    def test_interval_equalities_and_adjacent_prices(self):
        import math
        entry=adopted(analysis())['entry_policy']
        for i,b in enumerate(entry['boundaries']):
            price=b['price']
            self.assertEqual(c.classify_price(entry,math.nextafter(price,math.inf)),c.TIERS[i])
            self.assertEqual(c.classify_price(entry,price),c.TIERS[i+1])
            self.assertEqual(c.classify_price(entry,math.nextafter(price,0)),c.TIERS[i+1])
        self.assertEqual(c.classify_price(entry,1e12),'watch')
        self.assertEqual(c.classify_price(entry,1e-12),'fat_pitch')
        for bad in [0,-1,float('inf'),float('nan')]:
            with self.assertRaises(ValueError):c.classify_price(entry,bad)

    def test_interval_topology_cannot_be_redefined_by_subagent(self):
        data=adopted(analysis())
        data['entry_policy']['tiers'][1]['lower_inclusive']=True
        with self.assertRaisesRegex(ValueError,'without gaps or overlaps'):
            c.validate_analysis(data,self.policy)
        data=adopted(analysis())
        data['entry_policy']['tiers'][0]['upper_boundary']='V_central'
        with self.assertRaisesRegex(ValueError,'without gaps or overlaps'):
            c.validate_analysis(data,self.policy)

    def test_p18_is_not_an_entry_boundary(self):
        data=adopted(analysis())
        self.assertEqual(len(data['entry_policy']['boundaries']),4)
        self.assertEqual(len(data['entry_policy']['tiers']),5)
        self.assertNotIn(.18,[r['value'] for r in data['rates'] if r['source_kind']=='project_policy'])
        data['entry_policy']['boundaries'][-1]['id']='P18'
        with self.assertRaisesRegex(ValueError,'invalid enum'):
            c.validate_analysis(data,self.policy)

    def test_old_schema_even_named_v2_is_rejected(self):
        data = analysis(); data["buy_zone"] = {"lower": 60, "upper": 70}
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            c.validate_analysis(data, self.policy)

    def test_cannot_relabel_analyst_rate_as_project_policy(self):
        data = adopted(analysis()); data["rates"][0]["source_kind"] = 'project_policy'
        with self.assertRaisesRegex(ValueError, "Undeclared return policy"):
            c.validate_analysis(data, self.policy)

    def test_cannot_claim_adopted_policy(self):
        data = adopted(analysis()); data["entry_policy"].update(status="adopted", policy_id="invented")
        with self.assertRaisesRegex(ValueError, "not been adopted"):
            c.validate_analysis(data, self.policy)

    def test_numeric_score_without_declared_rubric_rejected(self):
        data = analysis(); data.update(quality_score=50, quality_assessment={"rubric_id": "invented", "confidence": "low", "dimensions": []})
        with self.assertRaisesRegex(ValueError, "rubric not declared"):
            c.validate_analysis(data, self.policy)

    def test_wrong_price_and_sensitivity_rejected(self):
        data = adopted(analysis()); data["entry_policy"]["boundaries"][2]["price"] += 1
        with self.assertRaisesRegex(ValueError, "Boundary price: arithmetic"):
            c.validate_analysis(data, self.policy)
        data = adopted(analysis()); data["return_sensitivity"].append({'scenario_id':'central','rate_id':'discount','price_per_share':105})
        with self.assertRaisesRegex(ValueError, "Return sensitivity: arithmetic"):
            c.validate_analysis(data, self.policy)

    def test_cash_flow_timeline_and_nan_rejected(self):
        data = analysis(); data["valuation_scenarios"][0]["cash_flows"][0]["year"] = 10
        with self.assertRaisesRegex(ValueError, "timeline"):
            c.validate_analysis(data, self.policy)
        data = analysis(); data["rates"][0]["value"] = float("nan")
        with self.assertRaisesRegex(ValueError, "invalid type"):
            c.validate_analysis(data, self.policy)

    def test_risk_double_count_requires_resolution(self):
        data = adopted(analysis()); data["risk_ledger"][0]["rate_treatment"] = "Also increased discount rate"
        with self.assertRaisesRegex(ValueError, "without reconciliation"):
            c.validate_analysis(data, self.policy)

    def test_reserve_cannot_disappear(self):
        data = adopted(analysis())
        data["reserves"] = [{"id": "lawsuit", "scenario_id": "central", "kind": "contingent", "currency": "USD",
                             "amount": 20, "consumed": 0, "released": 0, "terminal_balance": 0,
                             "release_condition": "Case resolved", "terminal_treatment": "added_separately",
                             "basis": "Residual cash modeled once"}]
        data["risk_ledger"][0].update(reserve_ids=["lawsuit"], overlap_resolution="Separate risk")
        with self.assertRaisesRegex(ValueError, "Reserve roll-forward"):
            c.validate_analysis(data, self.policy)

    def test_cross_check_gap_requires_review_not_automatic_average(self):
        data = adopted(analysis()); data["cross_checks"][0]["value_per_share"] = 200
        result = c.validate_analysis(data, self.policy)
        self.assertEqual(result["review_flags"], ["material_cross_check_gap"])
        self.assertAlmostEqual(result["scenarios"]["central"]["value_per_share"], 100)

    def test_second_method_cannot_be_silently_omitted(self):
        data = adopted(analysis()); data["cross_checks"] = []
        with self.assertRaisesRegex(ValueError, "independent cross-check"):
            c.validate_analysis(data, self.policy)

    def test_future_source_and_currency_mismatch_rejected(self):
        data = analysis(); data["sources"][0]["published_at"] = "2027-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "Post-cutoff"):
            c.validate_analysis(data, self.policy)
        data = analysis(); data["rates"][0]["currency"] = "BRL"
        with self.assertRaisesRegex(ValueError, "one currency"):
            c.validate_analysis(data, self.policy)

    def test_four_anchors_change_economics_not_discount_rates(self):
        data = adopted(analysis())
        for boundary, expected in zip(data['entry_policy']['boundaries'], [100, 80, 50, 20]):
            self.assertAlmostEqual(boundary['price'], expected)
        self.assertEqual(len(data['rates']), 1)
        self.assertEqual({b['rate_id'] for b in data['entry_policy']['boundaries']}, {'discount'})
        research = analysis()
        research['rates'].append(dict(research['rates'][0], id='higher_rate', value=.2))
        scenario = research['valuation_scenarios'][-1]
        scenario['discount_rate_id'] = 'higher_rate'
        scenario['equity_value_per_share'] = c.price_at(scenario, .2)
        with self.assertRaises(ValueError):
            adopted(research)

    def test_equal_anchors_leave_empty_intervals_without_invented_spread(self):
        research = analysis()
        scenario = research['valuation_scenarios'][1]
        scenario['cash_flows'] = deepcopy(research['valuation_scenarios'][0]['cash_flows'])
        scenario['terminal'] = deepcopy(research['valuation_scenarios'][0]['terminal'])
        scenario['equity_value_per_share'] = 100
        data = adopted(research)
        boundary = data['entry_policy']['boundaries'][0]['price']
        self.assertEqual(c.classify_price(data['entry_policy'], boundary), 'starter')
        self.assertEqual(c.classify_price(data['entry_policy'], 100.01), 'watch')

    def test_zero_floor_does_not_create_positive_price_fat_pitch(self):
        research = analysis()
        floor = research['valuation_scenarios'][-1]
        for flow in floor['cash_flows']:
            flow['distribution_per_share'] = 0
        floor['terminal']['value_per_share'] = 0
        floor['terminal']['basis'] = 'No residual ordinary-equity economic surplus after senior claims'
        floor['equity_value_per_share'] = 0
        floor['assumptions'] = ['Funded low-state operations have zero normalized ordinary-equity surplus after full economic costs']
        floor['assumption_changes'][0]['scenario_value'] = '0 after full maintenance, SBC, tax, working capital and senior claims'
        research['cross_checks'][-1]['value_per_share'] = 0
        research['cross_checks'][-1]['explanation'] = 'Independent capitalization of zero low-state ordinary-equity earnings, not asset liquidation'
        data = adopted(research)
        self.assertEqual(data['entry_policy']['boundaries'][-1]['price'], 0)
        self.assertEqual(c.classify_price(data['entry_policy'], 1e-12), 'add')

    def test_missing_floor_is_explicit_and_cannot_manufacture_fat_pitch(self):
        research = analysis()
        research['valuation_scenarios'].pop()
        research['cross_checks'].pop()
        research['floor_limitation'] = 'Asset recoverability is not currently bounded'
        decision = parent_decision(self.policy)
        decision['anchor_scenarios']['V_floor'] = None
        decision['gates']['evidence_cap'] = 'add'
        decision['gates']['fat_pitch_checks']['floor_independent'] = {'passed': False, 'reason': research['floor_limitation']}
        decision['gates']['fat_pitch_checks']['cross_check_resolved'] = {'passed': False, 'reason': research['floor_limitation']}
        data = c.apply_parent_entry_policy(research, self.policy, decision)
        result = c.classify_action(data['entry_policy'], 1)
        self.assertIsNone(result['economic_tier'])
        self.assertEqual(result['classification_status'], 'floor_unavailable')
        self.assertEqual(result['actionable_tier'], 'add')
        self.assertEqual(c.classify_price(data['entry_policy'], 60), 'starter')
        self.assertIsNone(data['entry_policy']['boundaries'][-1]['price'])

    def test_economic_tier_and_two_caps_are_separate(self):
        decision = parent_decision(self.policy)
        decision['gates'].update(evidence_cap='starter', survival_cap='research')
        data = c.apply_parent_entry_policy(analysis(), self.policy, decision)
        result = c.classify_action(data['entry_policy'], 10)
        self.assertEqual(result['economic_tier'], 'fat_pitch')
        self.assertEqual(result['evidence_cap'], 'starter')
        self.assertEqual(result['survival_cap'], 'research')
        self.assertEqual(result['actionable_tier'], 'research')

    def test_thesis_break_caps_or_rejects_without_sixth_price_tier(self):
        for state, expected in [('research_cap', 'research'), ('reject', None)]:
            decision = parent_decision(self.policy)
            decision['gates'].update(thesis_break=state, thesis_break_reason='Potential loss of operating license')
            data = c.apply_parent_entry_policy(analysis(), self.policy, decision)
            result = c.classify_action(data['entry_policy'], 10)
            self.assertEqual(result['economic_tier'], 'fat_pitch')
            self.assertEqual(result['actionable_tier'], expected)
            self.assertEqual(len(data['entry_policy']['tiers']), 5)

    def test_failed_fat_pitch_check_cannot_claim_unrestricted_caps(self):
        for check in ('floor_independent', 'no_specific_equity_destroying_tail', 'financing_viable', 'no_major_dilution', 'capital_distributable', 'cross_check_resolved'):
            decision = parent_decision(self.policy)
            decision['gates']['fat_pitch_checks'][check] = {'passed': False, 'reason': 'Unresolved evidence'}
            with self.subTest(check=check), self.assertRaises(ValueError):
                c.apply_parent_entry_policy(analysis(), self.policy, decision)

    def test_capital_distributable_only_failure_allows_add_survival_cap(self):
        decision = parent_decision(self.policy)
        gates = decision['gates']
        gates['fat_pitch_checks']['capital_distributable'] = {
            'passed': False, 'reason': 'Temporary regulatory distribution restriction; funded ordinary equity survives'}
        gates['survival_cap'] = 'add'
        gates['survival_reason'] = 'Only distributions are restricted; financing, dilution and specific tail checks pass'
        data = c.apply_parent_entry_policy(analysis(), self.policy, decision)
        result = c.classify_action(data['entry_policy'], 10)
        self.assertEqual(result['economic_tier'], 'fat_pitch')
        self.assertEqual(result['survival_cap'], 'add')
        self.assertEqual(result['evidence_cap'], 'fat_pitch')
        self.assertEqual(result['actionable_tier'], 'add')
        # Lowering evidence alone cannot leave the distribution survival cap unrestricted.
        gates.update(evidence_cap='add', survival_cap='fat_pitch')
        with self.assertRaisesRegex(ValueError, 'Distribution restrictions'):
            c.apply_parent_entry_policy(analysis(), self.policy, decision)

    def test_severe_survival_failure_cannot_exceed_research_without_thesis_break(self):
        for check in ('no_specific_equity_destroying_tail', 'financing_viable', 'no_major_dilution'):
            decision = parent_decision(self.policy)
            gates = decision['gates']
            gates['fat_pitch_checks'][check] = {'passed': False, 'reason': 'Specific credible unresolved threat to ordinary equity'}
            gates['evidence_cap'] = 'research'
            for cap in ('starter', 'add', 'fat_pitch'):
                gates['survival_cap'] = cap
                with self.subTest(check=check, cap=cap), self.assertRaisesRegex(ValueError, 'research survival cap'):
                    c.apply_parent_entry_policy(analysis(), self.policy, decision)
            gates['survival_cap'] = 'research'
            data = c.apply_parent_entry_policy(analysis(), self.policy, decision)
            self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], 'research')
            for state, expected in (('research_cap', 'research'), ('reject', None)):
                gates.update(survival_cap='fat_pitch', thesis_break=state)
                data = c.apply_parent_entry_policy(analysis(), self.policy, decision)
                self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], expected)

    def test_non_survival_fat_pitch_failure_can_cap_at_add(self):
        for check in ('floor_independent', 'cross_check_resolved'):
            decision = parent_decision(self.policy)
            gates = decision['gates']
            gates['fat_pitch_checks'][check] = {'passed': False, 'reason': 'Specific evidence still requires reconciliation'}
            gates['evidence_cap'] = 'add'
            with self.subTest(check=check):
                data = c.apply_parent_entry_policy(analysis(), self.policy, decision)
                result = c.classify_action(data['entry_policy'], 10)
                self.assertEqual(result['survival_cap'], 'fat_pitch')
                self.assertEqual(result['actionable_tier'], 'add')

    def test_mixed_quality_and_ordinary_uncertainty_do_not_fail_fat_pitch_checks(self):
        research = analysis()
        research['business_quality'] = 'mixed'
        decision = parent_decision(self.policy)
        decision['gates']['fat_pitch_checks']['no_specific_equity_destroying_tail']['reason'] = (
            'Ordinary demand uncertainty has no mathematical maximum loss; no specific credible unresolved equity-destroying tail')
        data = c.apply_parent_entry_policy(research, self.policy, decision)
        self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], 'fat_pitch')

    def test_floor_check_cannot_be_claimed_when_independent_method_is_missing(self):
        research = analysis()
        research['cross_checks'].pop()
        with self.assertRaises(ValueError):
            adopted(research)

    def test_higher_independent_estimate_flags_review_without_invalidating_floor(self):
        research = analysis()
        research['cross_checks'][-1]['value_per_share'] = 100
        data = adopted(research)
        calculations = c.validate_analysis(data, self.policy)
        self.assertIn('material_cross_check_gap', calculations['review_flags'])
        self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], 'fat_pitch')

    def test_lower_same_premise_estimate_requires_resolution(self):
        research = analysis()
        research['cross_checks'][-1]['value_per_share'] = 5
        with self.assertRaisesRegex(ValueError, 'floor contradiction'):
            adopted(research)

    def test_old_rate_boundaries_cannot_be_renamed_as_new_scenarios(self):
        research = analysis()
        research['schema_version'] = 'buffett-research-v2.3'
        with self.assertRaisesRegex(ValueError, 'invalid enum'):
            c.validate_analysis(research, self.policy, stage='research')
        decision = parent_decision(self.policy)
        decision['anchor_scenarios'] = {key: 'central' for key in c.BOUNDARIES}
        with self.assertRaises(ValueError):
            c.apply_parent_entry_policy(analysis(), self.policy, decision)

    def test_floor_method_gap_requires_traceable_parent_resolution(self):
        research = analysis()
        research['cross_checks'].append({
            'scenario_id': 'floor', 'method': 'Orderly liquidation after senior claims',
            'status': 'available', 'value_per_share': 5, 'premise': 'different_state_or_right',
            'explanation': 'Liquidation excludes viable continuing contracts and measures exit recovery only',
            'source_ids': ['release']})
        original_floor = research['valuation_scenarios'][-1]['equity_value_per_share']
        decision = parent_decision(self.policy)
        resolution = {'status': 'resolved', 'reason': 'Asset liquidation omits viable continuing contracts; issuer evidence reconciles the different premise', 'source_ids': []}
        decision['gates']['floor_cross_check_resolution'] = resolution
        with self.assertRaises(ValueError):
            c.apply_parent_entry_policy(research, self.policy, decision)
        resolution['source_ids'] = [research['sources'][0]['id']]
        data = c.apply_parent_entry_policy(research, self.policy, decision)
        self.assertIn('material_cross_check_gap', c.validate_analysis(data, self.policy)['review_flags'])
        self.assertEqual(data['entry_policy']['boundaries'][-1]['price'], original_floor)
        self.assertEqual(data['valuation_scenarios'], research['valuation_scenarios'])
        self.assertEqual(len(data['entry_policy']['boundaries']), 4)
        self.assertTrue(data['entry_policy']['gates']['fat_pitch_checks']['cross_check_resolved']['passed'])
        self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], 'fat_pitch')
        report = c.entry_policy_markdown(data)
        self.assertIn('cross_check_resolved', report)
        self.assertIn('来源：release', report)
        self.assertIn(resolution['reason'], report)
        resolution['status'] = 'unresolved'
        with self.assertRaises(ValueError):
            c.apply_parent_entry_policy(research, self.policy, decision)

    def test_unresolved_lower_same_premise_check_blocks_fat_pitch_but_allows_add(self):
        research = analysis()
        research['cross_checks'][-1].update(value_per_share=5, premise='same_as_scenario',
            explanation='Independent low-state going-concern earnings are lower; discrepancy is unexplained')
        decision = parent_decision(self.policy)
        gates = decision['gates']
        gates['floor_cross_check_resolution'] = {'status': 'unresolved',
            'reason': 'Lower independent estimate under the same floor premise has not been reconciled',
            'source_ids': ['release']}
        with self.assertRaisesRegex(ValueError, 'Unresolved independent contradiction'):
            c.apply_parent_entry_policy(research, self.policy, decision)
        gates['fat_pitch_checks']['cross_check_resolved'] = {'passed': False, 'reason': 'Unexplained lower same-premise estimate'}
        with self.assertRaisesRegex(ValueError, 'Incomplete fat-pitch evidence'):
            c.apply_parent_entry_policy(research, self.policy, decision)
        gates['evidence_cap'] = 'add'
        data = c.apply_parent_entry_policy(research, self.policy, decision)
        self.assertFalse(data['entry_policy']['gates']['fat_pitch_checks']['cross_check_resolved']['passed'])
        self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], 'add')

    def test_recovery_only_cross_check_cannot_independently_support_going_concern_floor(self):
        research = analysis()
        research['cross_checks'][-1].update(method='Liquidation recovery', value_per_share=5,
            premise='different_state_or_right', explanation='Separate exit recovery excludes the continuing franchise')
        decision = parent_decision(self.policy)
        gates = decision['gates']
        gates['floor_cross_check_resolution'] = {'status': 'resolved',
            'reason': 'Liquidation measures exit recovery, not the viable going-concern floor; operating evidence supports continued viability',
            'source_ids': ['release']}
        with self.assertRaisesRegex(ValueError, 'recovery-only evidence is insufficient'):
            c.apply_parent_entry_policy(research, self.policy, decision)
        gates['fat_pitch_checks']['floor_independent'] = {'passed': False, 'reason': 'Separate independent going-concern method still needed'}
        gates['evidence_cap'] = 'add'
        data = c.apply_parent_entry_policy(research, self.policy, decision)
        self.assertEqual(c.classify_action(data['entry_policy'], 10)['actionable_tier'], 'add')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        parent = p.REPO / ".cache/buffett_v2_tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "v2"
        self.legacy = self.base / "v1"
        self.legacy.mkdir()
        (self.legacy / "good_companies.csv").write_text("ticker,buy_zone_upper,quality_score\nAAA,999999,99\nOLD,888888,88\n", encoding="utf-8")
        self.legacy_hash = p.digest(self.legacy / "good_companies.csv")

    def event(self, ticker="AAA", identity="q3"):
        return {"ticker": ticker, "event_id": identity, "kind": "earnings_release", "status": "released",
                "primary_source_verified": True, "published_at": "2026-11-04T20:00:00Z",
                "report_period": "2026-09-30", "source_url": URL}

    def daily(self, events=None, checks=None):
        return p.prepare(self.root, self.legacy, events=[self.event()] if events is None else events,
                         checks=checks or {t: {"status": "ok", "checked_through": CUTOFF} for t in ("AAA", "OLD")},
                         since=SINCE, now=NOW)

    def manual(self, tickers=None):
        return p.prepare(self.root, self.legacy, "manual", tickers=tickers or ["AAA"], reason="Explicit test request", now=NOW)

    def bundle(self, run, ticker="AAA", data=None):
        data = data or analysis(ticker)
        directory = Path(run["run_dir"]) / "companies" / ticker
        plan = p.read(Path(run["run_dir"]) / "plan.json")
        event = plan["selected"][ticker].get("event")
        metadata = {"ticker": ticker, "route": "codex_subagent", "task_scope": "standalone_screen", "cutoff": CUTOFF,
                    "earnings_event_id": event["event_id"] if event else None,
                    "framework_files_read": ["SKILL.md", "references/06-valuation-capital.md"]}
        p.write(directory / "research.json", data)
        p.write(directory / "metadata.json", metadata)
        (directory / "research_report.md").write_text("# Synthetic v2\n\n```json\n" + p.encoded(data) + "```\n", encoding="utf-8")
        decision=parent_decision(p.read(Path(run['run_dir'])/'frozen/policy.json'))
        decision['research_sha256']=p.digest(directory/'research.json')
        p.write(directory/'parent_entry_decision.json',decision)
        p.apply_entry(self.root,run['run_id'],ticker)
        data=p.read(directory/'analysis.json')
        review = {key: {"passed": True, "notes": "Synthetic test review"} for key in
                  ("sources_cutoff", "arithmetic", "cash_flow_bridge", "risk_overlap", "reserve_terminal",
                   "cross_check", "entry_policy", "business_quality_independent", "anchor_economics", "evidence_survival_gates")}
        review.update(artifact_hashes={name: p.digest(directory / name) for name in ("analysis.json", "metadata.json", "report.md", "research.json", "parent_entry_decision.json")},
                      veto_reason_reviewed=data["veto_reason"],
                      review_flags_acknowledged=c.validate_analysis(data, p.read(Path(run["run_dir"]) / "frozen/policy.json"))["review_flags"])
        p.write(directory / "parent_review.json", review)
        p.validate(self.root, run["run_id"], [ticker])

    def quotes(self, tickers=("AAA",)):
        return {t: {"price": 50, "date": AS_OF, "currency": "USD", "source": "synthetic fixture",
                    "observed_at": CUTOFF, "status": "active"} for t in tickers}

    def publish(self, run, outcomes=None, quotes=None):
        quotes = self.quotes() if quotes is None else quotes
        return p.publish(self.root, run["run_id"], quotes, outcomes if outcomes is not None else {"AAA": "completed"},
                         {"quotes_sha256": p.value_hash(quotes), "quotes_reviewed": True, "notes": "Synthetic quote check"})

    def test_bridge_only_imports_ticker_and_does_not_research_legacy_backlog(self):
        run = self.daily(events=[])
        self.assertEqual(run["selected"], [])
        self.publish(run, {}, {})
        pointer, records, state = p.current(self.root)
        self.assertEqual(records, {})
        summary = (self.root / pointer["generation"] / "summary.md").read_text(encoding="utf-8")
        self.assertNotIn("OLD", summary)
        self.assertNotIn("999999", summary)
        self.assertEqual(p.digest(self.legacy / "good_companies.csv"), self.legacy_hash)

    def test_new_release_upgrades_one_company_without_legacy_rows(self):
        run = self.daily(); self.assertEqual(run["selected"], ["AAA"])
        self.bundle(run); result = self.publish(run)
        pointer, records, state = p.current(self.root)
        self.assertEqual(set(records), {"AAA"})
        self.assertEqual(state["events"]["AAA|q3"]["status"], "completed")
        signals = (self.root / result["generation"] / "signals.csv").read_text(encoding="utf-8-sig")
        self.assertNotIn("OLD", signals)
        self.assertNotIn("buy_zone", signals)
        self.assertNotIn(",999999,", signals)
        self.assertIn("parent-assumption-tolerance-2026-11-v3", signals)
        self.assertEqual(self.daily()["selected"], [])
        self.assertTrue(self.publish(run)["already_published"])

    def test_manual_trigger_is_explicit_and_does_not_advance_state(self):
        with self.assertRaisesRegex(ValueError, "explicit tickers"):
            p.prepare(self.root, self.legacy, "manual", now=NOW)
        run = self.manual(); self.bundle(run); self.publish(run)
        pointer, _, state = p.current(self.root)
        self.assertIsNone(state)
        self.assertIsNone(pointer["last_daily_report"])
        self.assertIsNotNone(pointer["last_manual_report"])

    def test_manual_memo_without_release_source_cannot_suppress_event(self):
        run = self.manual(); data = analysis(); data["sources"][0]["url"] = "https://example.test/other"
        self.bundle(run, data=data); self.publish(run)
        self.assertEqual(self.daily()["selected"], ["AAA"])

    def test_scheduled_and_future_release_do_not_upgrade(self):
        event = self.event(); event.update(status="scheduled", scheduled_at="2026-11-04T20:00:00Z")
        run = self.daily([event]); self.assertEqual(run["selected"], [])
        self.publish(run, {}, {})
        _, _, state = p.current(self.root)
        self.assertNotIn("AAA", state["tickers"])
        event = self.event(); event["published_at"] = "2026-11-06T20:00:00Z"
        self.assertEqual(self.daily([event])["selected"], [])

    def test_failure_retries_and_does_not_publish_legacy_company(self):
        run = self.daily(); self.publish(run, {"AAA": "failed"}, {})
        _, records, state = p.current(self.root)
        self.assertFalse(records)
        self.assertEqual(state["events"]["AAA|q3"]["status"], "failed")
        self.assertEqual(self.daily([])["selected"], ["AAA"])

    def test_older_startup_events_remain_pending_without_automatic_research(self):
        older = self.event(identity='older-release')
        older['published_at'] = '2026-11-02T20:00:00Z'
        run = self.daily([older])
        self.assertEqual(run['selected'], [])
        plan = p.read(Path(run['run_dir']) / 'plan.json')
        self.assertTrue(plan['deferred_earnings'])
        self.publish(run, {}, {})
        _, _, state = p.current(self.root)
        deferred = state['events']['AAA|older-release']
        self.assertEqual(deferred['status'], 'pending')
        self.assertFalse(deferred['research_authorized'])
        self.assertEqual(self.daily([])['selected'], [])

    def test_explicitly_selected_failure_can_retry_on_later_new_york_day(self):
        run = self.daily()
        self.publish(run, {'AAA': 'failed'}, {})
        later = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
        retry = p.prepare(self.root, self.legacy, events=[],
                          checks={t: {'status': 'ok', 'checked_through': later.isoformat()} for t in ('AAA', 'OLD')}, now=later)
        self.assertEqual(retry['selected'], ['AAA'])

    def test_release_between_checks_is_not_lost_at_new_york_midnight(self):
        first = self.daily([])
        self.publish(first, {}, {})
        # 23:00 NY after the 21:00 previous check, discovered on the next NY day.
        event = self.event(identity='late-release')
        event['published_at'] = '2026-11-05T04:00:00Z'
        later = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
        run = p.prepare(self.root, self.legacy, events=[event],
            checks={t: {'status': 'ok', 'checked_through': later.isoformat()} for t in ('AAA', 'OLD')}, now=later)
        self.assertEqual(run['selected'], ['AAA'])

    def test_cannot_publish_unvalidated_or_changed_analysis(self):
        run = self.daily()
        with self.assertRaises(FileNotFoundError):
            self.publish(run)
        self.bundle(run)
        directory = Path(run["run_dir"]) / "companies/AAA"
        (directory / "report.md").write_text("Tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Report final JSON"):
            self.publish(run)
        self.assertFalse((self.root / "current.json").exists())

    def test_parent_decision_is_required_and_hash_bound(self):
        run=self.manual(); self.bundle(run)
        directory=Path(run['run_dir'])/'companies/AAA'
        decision=p.read(directory/'parent_entry_decision.json')
        decision['research_sha256']='unreviewed'
        p.write(directory/'parent_entry_decision.json',decision)
        with self.assertRaisesRegex(ValueError,'not bound to research'):
            self.publish(run)
        self.assertFalse((self.root/'current.json').exists())

    def test_final_cash_cannot_change_after_parent_policy_application(self):
        run=self.manual(); self.bundle(run)
        directory=Path(run['run_dir'])/'companies/AAA'
        data=p.read(directory/'analysis.json')
        data['business_summary']='Unreviewed alteration'
        p.write(directory/'analysis.json',data)
        with self.assertRaisesRegex(ValueError,'differs from parent policy'):
            self.publish(run)

    def test_mixed_company_retains_five_estimates_and_review_flag(self):
        run=self.manual(); data=analysis(); data['business_quality']='mixed'
        data['cross_checks'][0]['value_per_share']=200
        self.bundle(run,data=data); result=self.publish(run)
        import csv
        with (self.root/result['generation']/'signals.csv').open(encoding='utf-8-sig') as f:
            row=next(csv.DictReader(f))
        self.assertTrue(all(float(row[t])>0 for t in c.BOUNDARIES))
        self.assertEqual(row['usable_entry_reference'],'True')
        self.assertEqual(row['requires_review'],'True')
        self.assertEqual(row['actionable'],'False')

    def test_above_central_is_watch_without_price_alert(self):
        run=self.manual(); data=analysis(); data['research_status']='assessed'
        self.bundle(run,data=data)
        quotes=self.quotes(); quotes['AAA']['price']=10000
        result=self.publish(run,quotes=quotes)
        import csv
        with (self.root/result['generation']/'signals.csv').open(encoding='utf-8-sig') as f:
            row=next(csv.DictReader(f))
        self.assertEqual(row['reference_tier'],'watch')
        self.assertEqual(row['price_reference_alert'],'False')
        self.assertEqual(row['requires_review'],'False')
        self.assertFalse(any(k.endswith('_ceiling') for k in row))

    def test_nonselected_company_cannot_be_validated(self):
        run = self.daily([])
        with self.assertRaisesRegex(ValueError, "not explicitly selected"):
            p.validate(self.root, run["run_id"], ["OLD"])

    def test_conflicting_duplicate_release_rejected(self):
        other = self.event(); other["published_at"] = "2026-11-04T21:00:00Z"
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            self.daily([self.event(), other])

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            p.parse_json('{"ticker":"AAA","ticker":"OLD"}')

    def test_cutoff_can_be_frozen_before_gathering_inputs(self):
        later = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
        run = p.prepare(self.root, self.legacy, events=[self.event()], since=SINCE, now=later, cutoff=CUTOFF,
                        checks={t: {"status": "ok", "checked_through": CUTOFF} for t in ("AAA", "OLD")})
        self.assertEqual(p.read(Path(run["run_dir"]) / "manifest.json")["cutoff"], CUTOFF)

    def test_bootstrap_window_is_explicit_and_state_wins_after_publish(self):
        initial = p.context(self.root, NOW)
        self.assertEqual(initial["bootstrap_lookback_days"], 7)
        self.assertEqual(initial["initial_scan_from"], "2026-10-29T02:00:00+00:00")
        run = self.daily([]); self.publish(run, {}, {})
        after = p.context(self.root, NOW)
        self.assertEqual(after["initial_scan_from"], SINCE)
        self.assertEqual(after["scan_window_source"], "v2_state")

    def test_rejects_changed_frozen_policy(self):
        run = self.manual()
        file = Path(run["run_dir"]) / "frozen/policy.json"
        policy = p.read(file); policy["approved_rates"] = {"x": .1}; p.write(file, policy)
        with self.assertRaisesRegex(ValueError, "Frozen input changed"):
            p.validate(self.root, run["run_id"], ["AAA"])

    def test_rejects_v1_quotes_and_low_coverage(self):
        run = self.manual(); self.bundle(run)
        with self.assertRaisesRegex(ValueError, "never legacy-only"):
            self.publish(run, quotes=self.quotes(("AAA", "OLD")))
        quotes = self.quotes(); quotes["AAA"].update(price=None, date=None, status="missing")
        with self.assertRaisesRegex(ValueError, "quote coverage"):
            self.publish(run, quotes=quotes)

    def test_quote_observation_cannot_predate_price(self):
        run = self.manual(); self.bundle(run)
        quotes = self.quotes(); quotes["AAA"]["observed_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "before its price date"):
            self.publish(run, quotes=quotes)

    def test_publish_failure_keeps_old_pointer_and_is_retryable(self):
        run = self.daily([]); self.publish(run, {}, {})
        old = (self.root / "current.json").read_bytes()
        run = self.manual(); self.bundle(run)
        original = p.write
        def fail_pointer(path, value):
            if Path(path) == self.root / "current.json":
                raise PermissionError("Synthetic locked pointer")
            return original(path, value)
        with patch.object(p, "write", side_effect=fail_pointer):
            with self.assertRaises(PermissionError):
                self.publish(run)
        self.assertEqual(old, (self.root / "current.json").read_bytes())
        self.publish(run)
        self.assertEqual(set(p.current(self.root)[1]), {"AAA"})

    def test_two_runs_cannot_overwrite_each_other(self):
        first = self.manual(); second = self.manual()
        self.bundle(first); self.bundle(second); self.publish(first)
        with self.assertRaisesRegex(ValueError, "committed first"):
            self.publish(second)

    def test_lock_prevents_simultaneous_writer(self):
        p.storage(self.root)
        with p.locked(self.root):
            with self.assertRaisesRegex(ValueError, "Another v2 writer"):
                self.manual()

    def test_storage_disallows_legacy_overlap_and_existing_directory(self):
        with self.assertRaisesRegex(ValueError, "disjoint"):
            p.storage(self.legacy / "v2", self.legacy)
        with self.assertRaisesRegex(ValueError, "disjoint"):
            p.storage(p.LEGACY_ROOT)
        with self.assertRaisesRegex(ValueError, "non-v2"):
            p.storage(self.legacy)

    def test_daily_and_manual_report_pointers_are_separate(self):
        run = self.daily(); self.bundle(run); self.publish(run)
        daily_report = p.current(self.root)[0]["last_daily_report"]
        run = self.manual(); self.bundle(run); self.publish(run)
        pointer = p.current(self.root)[0]
        self.assertEqual(pointer["last_daily_report"], daily_report)
        self.assertNotEqual(pointer["last_manual_report"], daily_report)

    def test_prior_contract_is_archived_without_resetting_completed_events(self):
        run = self.daily(); self.bundle(run); self.publish(run)
        pointer, _, state_before = p.current(self.root)
        prior = self.root / pointer['generation']
        registry = p.read(prior / 'registry.json')
        # Synthetic legacy fixture: integrity of real frozen runs is exercised separately.
        registry['schema_version'] = 'buffett-research-v2.4'
        registry['records']['AAA']['schema_version'] = 'buffett-research-v2.4'
        registry['records']['AAA']['analysis']['schema_version'] = 'buffett-research-v2.4'
        p.write(prior / 'registry.json', registry)
        pointer['files']['registry.json'] = p.digest(prior / 'registry.json')
        p.write(self.root / 'current.json', pointer)
        prior_bytes = (prior / 'registry.json').read_bytes()
        with patch.object(p, 'verified_registry'):
            next_run = self.daily([])
            self.assertEqual(next_run['selected'], [])
            result = self.publish(next_run, {}, {})
        generation = self.root / result['generation']
        latest = p.read(generation / 'registry.json')
        self.assertEqual(latest['records'], {})
        self.assertIn('AAA', latest['archived_records'])
        migration = p.read(generation / 'migration_status.json')
        self.assertEqual(migration['pending']['AAA']['status'], 'awaiting_authorized_trigger')
        self.assertFalse(migration['automatic_migration'])
        self.assertEqual(p.current(self.root)[2]['events'], state_before['events'])
        self.assertEqual(p.current(self.root)[2]['initial_scan_from'], state_before['initial_scan_from'])
        self.assertEqual((prior / 'registry.json').read_bytes(), prior_bytes)
        summary = (generation / 'summary.md').read_text(encoding='utf-8')
        self.assertNotIn('|AAA|', summary)

    def test_new_summary_discloses_gates_and_never_rate_boundaries(self):
        run = self.manual(); self.bundle(run); result = self.publish(run)
        generation = self.root / result['generation']
        import csv
        with (generation / 'signals.csv').open(encoding='utf-8-sig') as file:
            row = next(csv.DictReader(file))
        for key in ('economic_tier', 'evidence_cap', 'survival_cap', 'actionable_tier', 'thesis_break'):
            self.assertIn(key, row)
        self.assertFalse({'P8', 'P10', 'P12', 'P15'} & set(row))
        self.assertEqual(row['actionable'], 'False')
        frozen = p.read(Path(run['run_dir']) / 'manifest.json')['frozen_files']
        self.assertIn('frozen/PARENT_INSTRUCTIONS.md', frozen)

    def test_prior_v2_disabled_after_unprocessed_new_release(self):
        run = self.daily(); self.bundle(run); self.publish(run)
        event = self.event(identity="q3-correction"); event["published_at"] = "2026-11-05T02:30:00Z"
        later = datetime(2026, 11, 5, 3, 0, tzinfo=timezone.utc)
        run = p.prepare(self.root, self.legacy, now=later, events=[event],
                        checks={t: {"status": "ok", "checked_through": later.isoformat()} for t in ("AAA", "OLD")})
        self.publish(run, {"AAA": "failed"})
        pointer, records, _ = p.current(self.root)
        self.assertEqual(records["AAA"]["execution_status"], "new_research_failed")
        with (self.root / pointer["generation"] / "signals.csv").open(encoding="utf-8-sig") as file:
            import csv
            row = next(csv.DictReader(file))
        self.assertEqual(row["V_conservative"], "")
        self.assertEqual(row["usable_entry_reference"], "False")


if __name__ == "__main__":
    unittest.main()
