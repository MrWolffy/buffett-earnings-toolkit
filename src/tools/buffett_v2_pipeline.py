"""Isolated Buffett v2 artifacts: universe, prepare, validate, publish, status.

Research is performed by native Codex agents. Python never calls a research
model. Legacy data crosses this boundary only through the ticker-only adapter.
Publication commits an immutable generation through one atomic pointer.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import uuid

REPO = next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file())
DEFAULT_ROOT = REPO / "outputs/buffett_v2"
LEGACY_ROOT = REPO / "outputs/buffett"
DEFAULT_UNIVERSE_CSV = REPO / "data/company_pool.csv"
STORAGE_VERSION = "buffett-v2-isolated-1"
LEGACY_TABLES = ("good_companies.csv", "research_results.csv", "research_pending.csv", "execution_failures.csv")


def load_module(path):
    spec = importlib.util.spec_from_file_location("_buffett_" + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load_module(Path(__file__).with_name("buffett_v2_contract.py"))
READABLE_SCHEMAS = ("buffett-research-v2.1", "buffett-research-v2.2", "buffett-research-v2.3",
                    "buffett-research-v2.4", contract.SCHEMA_VERSION)
monitoring = load_module(Path(__file__).with_name("buffett_monitoring.py"))
veto = load_module(Path(__file__).with_name("buffett_veto.py"))
require = contract.require


def parse_json(text):
    def invalid(value):
        raise ValueError("Non-finite JSON constant: " + value)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(text, parse_constant=invalid, object_pairs_hook=unique)


def read(path):
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def value_hash(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(encoded(value), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def within(path, parent):
    return path == parent or parent in path.parents


def separate(first, second):
    require(not within(first, second) and not within(second, first), "v2 and legacy roots must be disjoint")


def storage(root, legacy_root=None):
    root = Path(root).resolve()
    separate(root, LEGACY_ROOT.resolve())
    if legacy_root:
        separate(root, Path(legacy_root).resolve())
    marker = root / "storage.json"
    if marker.exists():
        require(read(marker) == {"storage_version": STORAGE_VERSION}, "Unknown storage layout")
    else:
        require(not root.exists() or not any(root.iterdir()), "Refuse to adopt an existing non-v2 directory")
        write(marker, {"storage_version": STORAGE_VERSION})
    return root


@contextmanager
def locked(root):
    # OS-backed lock: automatically released on crash; no stale-PID heuristics.
    file = (root / ".writer.lock").open("a+b")
    acquired = False
    try:
        file.seek(0, 2)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    except OSError as error:
        if not acquired:
            raise ValueError("Another v2 writer holds the storage lock") from error
        raise
    finally:
        if acquired:
            file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        file.close()


def inside(root, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute(), "Expected relative v2 artifact path")
    path = (root / relative).resolve()
    require(path != root and within(path, root), "Artifact escapes v2 storage")
    return path


def current(root):
    pointer = root / "current.json"
    if not pointer.exists():
        return None, {}, None
    data = read(pointer)
    require(data["storage_version"] == STORAGE_VERSION, "Invalid production pointer")
    generation = inside(root, data["generation"])
    for name, sha in data["files"].items():
        require(digest(inside(generation, name)) == sha, "Production generation changed: " + name)
    registry = read(generation / "registry.json")
    require(registry["schema_version"] in READABLE_SCHEMAS, "Legacy registry cannot enter v2")
    return data, {**registry.get("archived_records", {}), **registry["records"]}, read(generation / "monitoring_state.json")


def legacy_tickers(legacy_root):
    """The ONLY legacy adapter. Never imports old research or event completion."""
    if legacy_root is None:
        return []
    root = Path(legacy_root).resolve()
    found = set()
    for name in LEGACY_TABLES:
        path = root / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                found.add(contract.ticker_name((row.get("ticker") or "").strip().upper()))
    return sorted(found)


def read_universe_csv(path):
    """Read public monitoring symbols only; a pool never authorizes research.

    Unsupported/empty symbols are disclosed rather than silently entering a
    scope that cannot be stored safely on every supported operating system.
    Other columns, including historical valuation fields, are never imported.
    """
    if path is None:
        return None
    path = Path(path).resolve()
    found, skipped = set(), []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        require(reader.fieldnames is not None and reader.fieldnames.count("ticker") == 1,
                "Company pool CSV needs exactly one ticker column")
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            try:
                found.add(contract.ticker_name(ticker))
            except ValueError as error:
                skipped.append({"line": reader.line_num, "ticker": ticker, "reason": str(error)})
    require(found, "Company pool CSV has no supported tickers")
    return {"path": str(path), "import_fields": ["ticker"],
            "tickers": sorted(found), "skipped_rows": skipped}


def universe(root=DEFAULT_ROOT, legacy_root=LEGACY_ROOT, *, universe_csv=None):
    pool = read_universe_csv(universe_csv)
    root = storage(root, legacy_root)
    with locked(root):
        _, records, state = current(root)
        symbols = sorted(set(legacy_tickers(legacy_root)) | set(records)
                         | set((state or {}).get("universe", [])) | set((pool or {}).get("tickers", [])))
        result = {"tickers": symbols, "legacy_import_fields": ["ticker"]}
        if pool is not None:
            result["company_pool"] = pool
        write(root / "inbox/universe.json", result)
    return symbols


def freeze(run):
    source = Path(__file__).parent
    for name in ("buffett_v2_contract.py", "buffett_monitoring.py", "buffett_veto.py", "buffett_v2_pipeline.py"):
        dest = run / "frozen" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, dest)
    for name in ("policy.json", "SUBAGENT_INSTRUCTIONS.md", "PARENT_INSTRUCTIONS.md", "MIGRATION.md"):
        shutil.copy2(REPO / "docs/buffett_v2" / name, run / "frozen" / name)
    skill = REPO / ".agents/skills/buffett"
    require((skill / "SKILL.md").is_file(), "Buffett framework missing")
    for file in skill.rglob("*.md"):
        dest = run / "frozen/framework" / file.relative_to(skill)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest)
    write(run / "frozen/schema.json", contract.SCHEMA)
    return {str(path.relative_to(run)).replace("\\", "/"): digest(path)
            for path in sorted((run / "frozen").rglob("*")) if path.is_file()}


def frozen_manifest(root, run_id):
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,100}", run_id), "Invalid run ID")
    run = inside(root, "runs/" + run_id)
    manifest = read(run / "manifest.json")
    require(manifest["schema_version"] in READABLE_SCHEMAS
            and manifest["root"] == str(root), "Run schema/storage mismatch")
    for name, sha in manifest["frozen_files"].items():
        require(digest(inside(run, name)) == sha, "Frozen input changed: " + name)
    return run, manifest


def runtime(root, run_id):
    run, manifest = frozen_manifest(root, run_id)
    require(digest(__file__) == manifest["frozen_files"]["frozen/buffett_v2_pipeline.py"],
            "Workflow changed: resume with this run's frozen/buffett_v2_pipeline.py")
    c = load_module(run / "frozen/buffett_v2_contract.py")
    m = load_module(run / "frozen/buffett_monitoring.py")
    v = load_module(run / "frozen/buffett_veto.py")
    require(read(run / "frozen/schema.json") == c.SCHEMA, "Schema differs from validator")
    policy = read(run / "frozen/policy.json")
    require(policy["schema_version"] == c.SCHEMA_VERSION and policy["policy_version"] == c.POLICY_VERSION,
            "Policy/schema mismatch")
    for file, sha in manifest["input_hashes"].items():
        require(digest(inside(run, file)) == sha, "Run input changed: " + file)
    plan = read(run / "plan.json")
    require(value_hash(plan) == manifest["plan_sha256"], "Research plan changed")
    return run, manifest, plan, c, m, v, policy


def validate_bundle(root, run_id, ticker):
    run, manifest, plan, c, _, v, policy = runtime(root, run_id)
    require(ticker in plan["selected"], "Company was not explicitly selected for v2 research")
    directory = inside(run, "companies/" + c.ticker_name(ticker))
    data = read(directory / "analysis.json")
    metadata = read(directory / "metadata.json")
    review = read(directory / "parent_review.json")
    require(data["ticker"] == ticker and data["as_of"] == manifest["as_of"]
            and c.timestamp(data["information_cutoff"]) == c.timestamp(manifest["cutoff"]), "Analysis identity/cutoff differs")
    result = c.validate_analysis(data, policy)
    research = read(directory / "research.json")
    decision = read(directory / "parent_entry_decision.json")
    require(decision.get("research_sha256") == digest(directory / "research.json"), "Parent entry decision is not bound to research")
    require(c.apply_parent_entry_policy(research, policy, decision) == data,
            "Final analysis differs from parent policy applied to research")
    v.validate_veto_reason(data)
    require(metadata["route"] == "codex_subagent" and metadata["task_scope"] == "standalone_screen"
            and metadata["ticker"] == ticker and metadata["cutoff"] == manifest["cutoff"], "Research provenance differs")
    framework = metadata["framework_files_read"]
    require("SKILL.md" in framework and all("frozen/framework/" + f in manifest["frozen_files"] for f in framework),
            "Framework read evidence missing")
    selection = plan["selected"][ticker]
    if selection["reason"] == "new_earnings":
        event = selection["event"]
        require(metadata.get("earnings_event_id") == event["event_id"], "Research must identify triggering event")
        require(event["source_url"] in {s["url"] for s in data["sources"]}, "Research must cite triggering release")
    else:
        require(metadata.get("earnings_event_id") is None, "Manual research must not fabricate an earnings event")
    report = (directory / "report.md").read_text(encoding="utf-8-sig")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", report, re.S)
    require(blocks and parse_json(blocks[-1]) == data, "Report final JSON must match analysis exactly")
    require(c.entry_policy_markdown(data) in report, "Report must disclose computed parent policy and four boundaries and five intervals")
    hashes = {name: digest(directory / name) for name in
              ("analysis.json", "metadata.json", "report.md", "research.json", "parent_entry_decision.json")}
    require(review.get("artifact_hashes") == hashes, "Parent review is not bound to these artifacts")
    for key in ("sources_cutoff", "arithmetic", "cash_flow_bridge", "risk_overlap", "reserve_terminal",
                "cross_check", "entry_policy", "business_quality_independent", "anchor_economics", "evidence_survival_gates"):
        require(review.get(key, {}).get("passed") is True and review[key].get("notes", "").strip(), "Missing substantive parent review: " + key)
    require(review.get("veto_reason_reviewed") == data["veto_reason"], "Veto review differs")
    require(review.get("review_flags_acknowledged") == result["review_flags"], "Parent must acknowledge model limitations")
    if data["quality_score"] is not None:
        require(review.get("quality_score_reviewed") == data["quality_score"], "Score review differs")
    hashes["parent_review.json"] = digest(directory / "parent_review.json")
    return {"schema_version": c.SCHEMA_VERSION, "ticker": ticker, "run_id": run_id,
            "artifact_hashes": hashes, "analysis": data, "metadata": metadata,
            "calculations": result, "execution_status": "success"}


def verified_registry(root, records):
    for ticker, record in records.items():
        require(record["schema_version"] in READABLE_SCHEMAS, "Legacy record rejected")
        run, _ = frozen_manifest(root, record["run_id"])
        frozen_workflow = run / "frozen/buffett_v2_pipeline.py"
        checked = load_module(frozen_workflow).validate_bundle(root, record["run_id"], ticker)
        require(all(record[k] == checked[k] for k in ("artifact_hashes", "analysis", "metadata", "calculations")),
                "Cached v2 artifact changed")


def apply_entry(root, run_id, ticker):
    """Parent-only workflow step; the parent supplies its reviewed decision file."""
    root = storage(root)
    with locked(root):
        run, _, plan, c, _, _, policy = runtime(root, run_id)
        require(ticker in plan["selected"], "Company was not selected")
        pointer, _, _ = current(root)
        require(not (run / "publication.json").exists() and (pointer or {}).get("run_id") != run_id,
                "Published runs are immutable")
        d = inside(run, "companies/" + c.ticker_name(ticker))
        research = read(d / "research.json")
        decision = read(d / "parent_entry_decision.json")
        require(decision.get("research_sha256") == digest(d / "research.json"), "Parent decision research hash differs")
        data = c.apply_parent_entry_policy(research, policy, decision)
        # Always rebuild from the preserved research narrative; never append duplicate tiers.
        draft = (d / "research_report.md").read_text(encoding="utf-8-sig")
        blocks = re.findall(r"```json\s*\n(.*?)\n```", draft, re.S)
        require(blocks and parse_json(blocks[-1]) == research, "Research report JSON differs")
        narrative = draft[:draft.rfind("```json")].rstrip()
        report = narrative + "\n\n" + c.entry_policy_markdown(data) + "\n\n```json\n" + encoded(data) + "```\n"
        write(d / "analysis.json", data)
        (d / "report.md").write_text(report, encoding="utf-8")
        return {"ticker": ticker, "policy_id": decision["policy_id"], "tiers": data["entry_policy"]["tiers"]}


def prepare(root=DEFAULT_ROOT, legacy_root=LEGACY_ROOT, mode="daily_earnings", *, events=None,
            checks=None, since=None, tickers=None, reason=None, now=None, cutoff=None, universe_csv=None):
    pool = read_universe_csv(universe_csv)
    root = storage(root, legacy_root)
    context = monitoring.daily_context(now)
    if cutoff is not None:
        frozen_context = monitoring.daily_context(contract.timestamp(cutoff))
        require(contract.timestamp(cutoff) <= contract.timestamp(context["cutoff"])
                and frozen_context["as_of"] == context["as_of"], "Cutoff must be on current New York date, not in the future")
        context = frozen_context
    require(mode in ("daily_earnings", "manual"), "Unknown run mode")
    with locked(root):
        pointer, records, state = current(root)
        verified_registry(root, records)
        imported = legacy_tickers(legacy_root)
        scope = sorted(set(imported) | set(records) | set((state or {}).get("universe", []))
                       | set((pool or {}).get("tickers", [])))
        if mode == "manual":
            require(tickers and len(tickers) == len(set(tickers)) and reason and reason.strip(), "Manual upgrade needs explicit tickers and reason")
            require(events is None and checks is None and since is None, "Manual and earnings triggers cannot be mixed")
            for ticker in tickers:
                contract.ticker_name(ticker)
            scope = sorted(set(scope) | set(tickers))
            plan = {"run_mode": mode, **context, "monitor_tickers": scope,
                    "selected": {t: {"reason": "manual_explicit", "request_reason": reason} for t in sorted(tickers)}}
        else:
            require(not tickers and not reason, "Automatic research cannot accept a manual ticker override")
            require(isinstance(events, list) and isinstance(checks, dict), "Daily prepare requires verified events and full checks")
            if state is None:
                require(since, "First v2 daily run requires explicit --since; do not import legacy completion state")
                state = monitoring.empty_state(since)
            elif since:
                require(since == state["initial_scan_from"], "Cannot reset v2 monitoring window")
            # A manual memo's timestamp alone is not proof that it covered this release.
            pending_events = events + [x["event"] for x in state["events"].values()
                                       if x["status"] in monitoring.UNFINISHED]
            seen = {}
            for event in events:
                if event.get("event_id"):
                    key = monitoring.event_key(event)
                    identity = {k: event.get(k) for k in ("published_at", "report_period", "source_url", "status", "primary_source_verified")}
                    require(key not in seen or seen[key] == identity, "Conflicting duplicate release identity")
                    seen[key] = identity
            baseline = {}
            for t, record in records.items():
                relevant = [e for e in pending_events if e.get("ticker") == t
                            and e.get("kind") == "earnings_release" and e.get("status") == "released"]
                urls = {s["url"] for s in record["analysis"]["sources"]}
                baseline[t] = {"research_succeeded": bool(relevant) and all(e.get("source_url") in urls for e in relevant),
                               "information_cutoff": record["analysis"]["information_cutoff"]}
            plan = monitoring.build_plan(scope, context, state, baseline, events, checks)
            # Discovery is complete; authorization to research is narrower. Keep
            # older startup debt pending even after calendar watermarks advance.
            deferred = {}
            for ticker, selection in list(plan["selected"].items()):
                saved = state["events"].get(selection["event_key"], {})
                released_today = monitoring.daily_context(contract.timestamp(selection["event"]["published_at"]))["as_of"] == context["as_of"]
                authorized_retry = saved.get("research_authorized") is True or saved.get("status") in ("failed", "running")
                previous_check = state.get("tickers", {}).get(ticker, {}).get("checked_through")
                newly_released_since_check = (not saved and previous_check is not None
                    and contract.timestamp(selection["event"]["published_at"]) > contract.timestamp(previous_check))
                if not released_today and not authorized_retry and not newly_released_since_check:
                    deferred[ticker] = plan["selected"].pop(ticker)
            plan["deferred_earnings"] = deferred
            plan["research_window"] = "new_york_today_new_since_check_and_authorized_retries"
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:10]
        run = root / "runs" / run_id
        run.mkdir(parents=True)
        frozen = freeze(run)
        scope_artifact = {"tickers": scope, "legacy_import_fields": ["ticker"]}
        if pool is not None:
            scope_artifact["company_pool"] = pool
        write(run / "scope.json", scope_artifact)
        write(run / "state_before.json", state)
        write(run / "events.json", events)
        write(run / "checks.json", checks)
        write(run / "plan.json", plan)
        for ticker in plan["selected"]:
            (run / "companies" / ticker).mkdir(parents=True)
        manifest = {"schema_version": contract.SCHEMA_VERSION, "storage_version": STORAGE_VERSION,
                    "run_id": run_id, "root": str(root), "run_mode": mode, **context,
                    "parent_generation": pointer["generation"] if pointer else None,
                    "frozen_files": frozen, "config_hash": value_hash(frozen),
                    "input_hashes": {name: digest(run / name) for name in ("scope.json", "state_before.json", "events.json", "checks.json")},
                    "plan_sha256": value_hash(plan)}
        write(run / "manifest.json", manifest)
    return {"run_id": run_id, "run_dir": str(run), "selected": list(plan["selected"]), "scope_count": len(scope)}


def validate(root, run_id, tickers):
    root = storage(root)
    with locked(root):
        run, *_ = runtime(root, run_id)
        require(tickers and len(tickers) == len(set(tickers)), "Validate needs unique tickers")
        records = {ticker: validate_bundle(root, run_id, ticker) for ticker in tickers}
        # Per-company acceptance permits multiple subagent waves without losing earlier successes.
        for ticker, record in records.items():
            write(run / "accepted" / (ticker + ".json"), record)
            write(run / "companies" / ticker / "calculated.json", record["calculations"])
    return {"validated": sorted(records)}


def quote_rows(records, quotes, manifest, policy):
    require(isinstance(quotes, dict) and set(quotes) == set(records), "Quotes must cover exactly the v2 registry, never legacy-only rows")
    rows = []
    for ticker, record in sorted(records.items()):
        quote = quotes[ticker]
        require(set(quote) == {"price", "date", "currency", "source", "observed_at", "status"}, "Invalid quote fields")
        require(quote["status"] in ("active", "missing", "inactive"), "Invalid security status")
        require(isinstance(quote["source"], str) and quote["source"].strip(), "Quote source required")
        observed = contract.timestamp(quote["observed_at"])
        require(observed <= datetime.now(timezone.utc), "Future quote observation")
        if quote["status"] == "active":
            require(type(quote["price"]) in (int, float) and math.isfinite(quote["price"]) and quote["price"] > 0, "Invalid quote price")
            quote_date = datetime.strptime(quote["date"], "%Y-%m-%d").date()
            age = (datetime.strptime(manifest["as_of"], "%Y-%m-%d").date() - quote_date).days
            require(0 <= age <= policy["quote_max_age_calendar_days"], "Future or stale quote")
            require(observed.date() >= quote_date, "Quote cannot be observed before its price date")
        else:
            require(quote["price"] is None and quote["date"] is None, "Missing/inactive quote must not present an old price")
            if quote["status"] == "inactive":
                require(quote["source"].startswith("https://"), "Inactive security needs a primary lifecycle source")
        data = record["analysis"]
        compatible = quote["currency"] == data["currency"]
        current_research = record["execution_status"] == "success"
        usable = bool(current_research and quote["status"] == "active" and compatible
                      and data["entry_policy"]["status"] == "adopted"
                      and data["valuation_status"] in ("supported", "conditional")
                      and data["research_status"] in ("assessed", "conditional")
                      and not data["veto_reason"])
        classification = {"economic_tier": None, "evidence_cap": data["entry_policy"]["gates"]["evidence_cap"],
                          "survival_cap": data["entry_policy"]["gates"]["survival_cap"], "actionable_tier": None,
                          "thesis_break": data["entry_policy"]["gates"]["thesis_break"],
                          "classification_status": "quote_or_research_unavailable"}
        if current_research and quote["status"] == "active" and compatible:
            classification = contract.classify_action(data["entry_policy"], quote["price"])
        if data["veto_reason"]:
            classification["actionable_tier"] = None
            classification["classification_status"] = "vetoed"
        tier_hit = classification["economic_tier"]
        usable = usable and classification["actionable_tier"] is not None
        row = {"ticker": ticker, "schema_version": data["schema_version"],
               "business_quality": data["business_quality"], "research_status": data["research_status"],
               "valuation_status": data["valuation_status"], "quality_score": data["quality_score"],
               "analysis_date": data["as_of"], "price": quote["price"], "price_date": quote["date"],
               "currency": data["currency"], "quote_status": quote["status"],
               "entry_policy_status": data["entry_policy"]["status"],
               "entry_policy_id": data["entry_policy"]["policy_id"], "reference_tier": tier_hit,
               "reference_tier_label": contract.TIER_LABELS.get(tier_hit),
               **classification,
               "actionable_tier_label": contract.TIER_LABELS.get(classification["actionable_tier"]),
               "evidence_reason": data["entry_policy"]["gates"]["evidence_reason"],
               "survival_reason": data["entry_policy"]["gates"]["survival_reason"],
               "thesis_break_reason": data["entry_policy"]["gates"]["thesis_break_reason"],
               "execution_status": record["execution_status"], "usable_entry_reference": usable,
               "price_reference_alert": usable and classification["actionable_tier"] not in (None, "watch"), "actionable": False,
               "requires_review": not usable or tier_hit not in (None, "watch") or data["research_status"] != "assessed"
                                  or classification["classification_status"] != "complete"
                                  or bool(record["calculations"]["review_flags"]),
               "review_flags": ";".join(record["calculations"]["review_flags"]),
               "report": f"reports/{ticker}.md"}
        for boundary in data["entry_policy"]["boundaries"]:
            row[boundary["id"]] = boundary["price"] if current_research else None
        rows.append(row)
    active_or_missing = [r for r in rows if r["quote_status"] != "inactive"]
    if active_or_missing:
        coverage = sum(r["quote_status"] == "active" for r in active_or_missing) / len(active_or_missing)
        require(coverage >= policy["minimum_quote_coverage"], "Insufficient quote coverage; publication refused")
    return rows


def publish(root, run_id, quotes, outcomes, quote_review):
    root = storage(root)
    with locked(root):
        run, manifest, plan, _, m, _, policy = runtime(root, run_id)
        pointer, records, state = current(root)
        receipt = run / "publication.json"
        # Pointer itself is authoritative even if the process died before writing its receipt.
        if pointer and pointer["run_id"] == run_id:
            return {"generation": pointer["generation"], "already_published": True}
        require(not receipt.exists(), "Run was already published; prepare a new run")
        require((pointer["generation"] if pointer else None) == manifest["parent_generation"],
                "Another publication committed first; prepare a fresh run")
        verified_registry(root, records)
        require(isinstance(outcomes, dict) and set(outcomes) == set(plan["selected"]), "Every selected company needs an explicit outcome")
        for ticker, outcome in outcomes.items():
            require(outcome in ("completed", "pending", "failed"), "Unknown outcome")
            if outcome == "completed":
                accepted = read(run / "accepted" / (ticker + ".json"))
                checked = validate_bundle(root, run_id, ticker)
                require(accepted == checked, "Accepted artifacts changed; revalidate")
                records[ticker] = checked
            elif ticker in records:
                records[ticker]["execution_status"] = "new_research_" + outcome
        require(quote_review.get("quotes_sha256") == value_hash(quotes) and quote_review.get("quotes_reviewed") is True
                and quote_review.get("notes", "").strip(), "Quotes need hash-bound parent review")
        # Older v2 research remains traceable but cannot publish ungoverned tiers.
        archived = {t: r for t, r in records.items() if r["schema_version"] != contract.SCHEMA_VERSION}
        records = {t: r for t, r in records.items() if t not in archived}
        rows = quote_rows(records, quotes, manifest, policy)
        if manifest["run_mode"] == "daily_earnings":
            state = m.advance_state(read(run / "state_before.json"), plan, outcomes, str(run))
        # Manual research never advances earnings watermarks or fabricates completion.
        if state is not None:
            state["universe"] = plan["monitor_tickers"]
        generation_name = "publications/" + run_id + "-" + uuid.uuid4().hex[:10]
        generation = inside(root, generation_name)
        generation.mkdir(parents=True)
        write(generation / "registry.json", {"schema_version": contract.SCHEMA_VERSION,
                                             "records": records, "archived_records": archived})
        write(generation / "monitoring_state.json", state)
        write(generation / "migration_status.json", {
            "target_schema": contract.SCHEMA_VERSION,
            "automatic_migration": False,
            "allowed_triggers": ["verified_new_earnings", "manual_explicit"],
            "pending": {t: {"from_schema": r["schema_version"], "source_run_id": r["run_id"],
                             "status": "awaiting_authorized_trigger"} for t, r in sorted(archived.items())},
            "current_contract_tickers": sorted(records)})
        write(generation / "quotes.json", quotes)
        write(generation / "quote_review.json", quote_review)
        write(generation / "operations.json", {"outcomes": outcomes,
            "calendar_incomplete": sorted(t for t, c in plan.get("checks", {}).items() if c["status"] != "ok"),
            "monitored_ticker_count": len(plan["monitor_tickers"]), "published_v2_count": len(rows),
            "deferred_earnings": sorted(plan.get("deferred_earnings", {})),
            "archived_prior_contract_tickers": sorted(archived)})
        fields = list(rows[0]) if rows else ["ticker", "schema_version", "actionable"]
        with (generation / "signals.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        lines = ["# Buffett v2 研究监测", "", f"日期：{manifest['as_of']}；cutoff：{manifest['cutoff']}。",
                 f"运行类型：{manifest['run_mode']}。仅展示通过新版契约验收的 {len(rows)} 家公司。",
                 "四种经济情景定义五档买区；等号归入较低价格一侧。证据与生存上限独立限制档位；actionable 固定为 false。", "",
                 "|公司|现价|中央值|保守值|不利值|条件底部值|经济档位|证据上限|生存上限|可执行研究档位|报告|",
                 "|---|---:|---:|---:|---:|---:|---|---|---|---|---|"]
        for row in rows:
            ticker = row["ticker"]
            prices = "|".join(f"{row[t]:.2f}" if row[t] is not None else "—" for t in contract.BOUNDARIES)
            labels = [contract.TIER_LABELS.get(row[k]) or '—' for k in ('economic_tier', 'evidence_cap', 'survival_cap', 'actionable_tier')]
            lines.append(f"|{ticker}|{row['price']}|{prices}|{'|'.join(labels)}|[研究](reports/{ticker}.md)|")
            source = inside(root, "runs/" + records[ticker]["run_id"] + "/companies/" + ticker)
            (generation / "reports").mkdir(exist_ok=True)
            report = (source / "report.md").read_text(encoding="utf-8-sig")
            if records[ticker]["execution_status"] != "success":
                report = "> 新研究尚未成功；以下为已过期的 v2 研究存档，价格条件不可用。\n\n" + report
            (generation / "reports" / (ticker + ".md")).write_text(report, encoding="utf-8")
        if not rows:
            lines.append("\n本轮暂无已验收的 v2 研究；事件处理情况保存在 operations.json。")
        (generation / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        files = {str(p.relative_to(generation)).replace("\\", "/"): digest(p)
                 for p in generation.rglob("*") if p.is_file()}
        new_pointer = {"storage_version": STORAGE_VERSION, "run_id": run_id, "generation": generation_name,
                       "files": files, "last_daily_report": (pointer or {}).get("last_daily_report"),
                       "last_manual_report": (pointer or {}).get("last_manual_report")}
        report_key = "last_daily_report" if manifest["run_mode"] == "daily_earnings" else "last_manual_report"
        new_pointer[report_key] = generation_name + "/summary.md"
        # Registry, reports and event completion become visible together, or not at all.
        write(root / "current.json", new_pointer)
        write(receipt, {"generation": generation_name, "files": files})
    return {"generation": generation_name, "v2_rows": len(rows), "outcomes": outcomes}


def status(root):
    root = storage(root)
    with locked(root):
        pointer, records, state = current(root)
    return {"root": str(root), "current": pointer, "v2_tickers": sorted(records),
            "current_contract_tickers": sorted(t for t, r in records.items() if r["schema_version"] == contract.SCHEMA_VERSION),
            "migration_pending_tickers": sorted(t for t, r in records.items() if r["schema_version"] != contract.SCHEMA_VERSION),
            "initial_scan_from": (state or {}).get("initial_scan_from")}


def context(root=DEFAULT_ROOT, now=None):
    """Explicit first-run lookback, without importing v1 research completion."""
    result = monitoring.daily_context(now)
    root = Path(root).resolve()
    state = None
    if (root / "storage.json").exists():
        root = storage(root)
        with locked(root):
            _, _, state = current(root)
    if state is not None:
        result.update(initial_scan_from=state["initial_scan_from"], scan_window_source="v2_state")
    else:
        policy = read(REPO / "docs/buffett_v2/policy.json")
        days = policy["bootstrap_lookback_days"]
        require(type(days) is int and days > 0, "Invalid bootstrap lookback")
        result.update(initial_scan_from=(contract.timestamp(result["cutoff"]) - timedelta(days=days)).isoformat(),
                      scan_window_source="explicit_bootstrap_lookback", bootstrap_lookback_days=days)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    u = sub.add_parser("universe")
    u.add_argument("--legacy-root", type=Path, default=LEGACY_ROOT)
    u.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE_CSV,
                   help="Public monitoring pool; imports ticker only and grants no research authorization")
    p = sub.add_parser("prepare")
    p.add_argument("--mode", choices=["daily_earnings", "manual"], default="daily_earnings")
    p.add_argument("--legacy-root", type=Path, default=LEGACY_ROOT)
    p.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE_CSV,
                   help="Public monitoring pool; daily checks must cover its valid tickers")
    p.add_argument("--events", type=Path); p.add_argument("--checks", type=Path); p.add_argument("--since")
    p.add_argument("--cutoff", help="Freeze the timestamp returned by context before gathering evidence/checks")
    p.add_argument("--tickers", nargs="+"); p.add_argument("--reason")
    v = sub.add_parser("validate"); v.add_argument("--run-id", required=True); v.add_argument("--tickers", nargs="+", required=True)
    e = sub.add_parser("apply-entry"); e.add_argument("--run-id", required=True); e.add_argument("--ticker", required=True)
    p = sub.add_parser("publish"); p.add_argument("--run-id", required=True)
    p.add_argument("--quotes", type=Path, required=True); p.add_argument("--outcomes", type=Path, required=True)
    p.add_argument("--quote-review", type=Path, required=True)
    sub.add_parser("status")
    sub.add_parser("context")
    args = parser.parse_args()
    if args.command == "context":
        result = context(args.root)
    elif args.command == "universe":
        symbols = universe(args.root, args.legacy_root, universe_csv=args.universe_csv)
        universe_path = args.root.resolve() / "inbox/universe.json"
        pool = read(universe_path).get("company_pool", {})
        result = {"ticker_count": len(symbols), "skipped_row_count": len(pool.get("skipped_rows", [])),
                  "path": str(universe_path)}
    elif args.command == "prepare":
        result = prepare(args.root, args.legacy_root, args.mode,
                         events=read(args.events) if args.events else None, checks=read(args.checks) if args.checks else None,
                         since=args.since, tickers=args.tickers, reason=args.reason, cutoff=args.cutoff,
                         universe_csv=args.universe_csv)
    elif args.command == "validate":
        result = validate(args.root, args.run_id, args.tickers)
    elif args.command == "apply-entry":
        result = apply_entry(args.root, args.run_id, args.ticker)
    elif args.command == "publish":
        result = publish(args.root, args.run_id, read(args.quotes), read(args.outcomes), read(args.quote_review))
    else:
        result = status(args.root)
    print(encoded(result))


if __name__ == "__main__":
    main()
