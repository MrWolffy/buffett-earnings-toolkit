"""Deterministic earnings-only scheduling for native Codex research.

No network/model calls. Calendars supply leads; the parent supplies verified
release events. Historical migration debt is never an automatic research trigger.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

POLICY_VERSION = 'earnings-only-1'
RUN_MODE = 'daily_earnings'
UNFINISHED = {'pending', 'running', 'failed'}


def instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO string with timezone')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp needs timezone; a scheduled/date-only value is not release proof')
    return result.astimezone(timezone.utc)


def daily_context(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Run clock needs timezone')
    return {'as_of': now.astimezone(ZoneInfo('America/New_York')).date().isoformat(),
            'cutoff': now.astimezone(timezone.utc).isoformat()}


def empty_state(scan_from: str) -> dict:
    instant(scan_from)
    return {'policy_version': POLICY_VERSION, 'initial_scan_from': scan_from,
            'tickers': {}, 'events': {}}


def event_key(event: dict) -> str:
    ticker, identity = event.get('ticker'), event.get('event_id')
    if not isinstance(ticker, str) or not ticker or not isinstance(identity, str) or not identity.strip():
        raise ValueError('Release needs ticker and stable event_id (issuer report/SEC accession)')
    return ticker + '|' + identity


def verified_release(event: dict, scope: set[str], cutoff: datetime) -> bool:
    if event.get('ticker') not in scope:
        raise ValueError('Event outside complete monitoring scope')
    if event.get('kind') != 'earnings_release' or event.get('status') != 'released':
        return False
    if event.get('primary_source_verified') is not True:
        return False
    if not event.get('source_url', '').startswith('https://') or not event.get('report_period'):
        raise ValueError('Verified release needs primary URL and report_period')
    event_key(event)
    return instant(event['published_at']) <= cutoff


def covered_by_research(record: dict, event: dict) -> bool:
    """Only an actual successful research cutoff can cover a new release.

    Refreshing a calendar's last_earnings_date does not mean research used it.
    Framework/score/schema gaps deliberately do not affect dispatch decisions.
    """
    cutoff = record.get('information_cutoff')
    return bool(record.get('research_succeeded') is True and cutoff
                and instant(cutoff) >= instant(event['published_at']))


def build_plan(scope: list[str], context: dict, state: dict, baseline: dict,
               events: list[dict], checks: dict) -> dict:
    if not scope or len(scope) != len(set(scope)):
        raise ValueError('Scope must be complete and unique')
    if state.get('policy_version') != POLICY_VERSION:
        raise ValueError('Unknown monitoring state policy')
    cutoff = instant(context['cutoff'])
    if daily_context(cutoff)['as_of'] != context['as_of']:
        raise ValueError('New York as_of does not match actual cutoff')
    if set(checks) != set(scope):
        raise ValueError('Calendar checks must cover entire scope, including failures')
    initial = instant(state['initial_scan_from'])
    if initial > cutoff:
        raise ValueError('Monitoring window starts after cutoff')
    for ticker, check in checks.items():
        if check.get('status') not in {'ok', 'incomplete'}:
            raise ValueError('Calendar check status must be ok/incomplete')
        if instant(check['checked_through']) != cutoff:
            raise ValueError('Calendar check must describe this exact cutoff')
        previous = state.get('tickers', {}).get(ticker, {}).get('checked_through')
        if previous and instant(previous) > cutoff:
            raise ValueError('Cannot move monitoring watermark backwards')

    prior = state.get('events', {})
    candidates = {}
    covered = []
    checks = deepcopy(checks)
    # A failed NEW release remains retryable even after calendar watermarks move.
    for key, saved in prior.items():
        event = saved['event']
        if saved['status'] in UNFINISHED and event.get('ticker') in scope:
            if not verified_release(event, set(scope), cutoff) or event_key(event) != key:
                raise ValueError('Invalid carried earnings event')
            if covered_by_research(baseline.get(event['ticker'], {}),event):
                covered.append(key)
            else:
                candidates[key] = deepcopy(event)
    ignored = []
    for event in events:
        if not verified_release(event, set(scope), cutoff):
            ignored.append({'ticker': event.get('ticker'), 'reason': 'not_verified_released_by_cutoff'})
            if event.get('kind') == 'earnings_release' and (
                event.get('status') == 'released' and not event.get('primary_source_verified')
                or event.get('status') == 'scheduled' and event.get('scheduled_at')
                and instant(event['scheduled_at']) <= cutoff
            ):
                checks[event['ticker']]['status'] = 'incomplete'
            continue
        ticker = event['ticker']; key = event_key(event)
        old = prior.get(key)
        if old and any(old['event'].get(k) != event.get(k)
                       for k in ('ticker', 'event_id', 'published_at', 'report_period')):
            raise ValueError('Stable event identity changed; review correction explicitly')
        if old and old['status'] in {'completed', 'superseded'}:
            ignored.append({'ticker': ticker, 'event_id': event['event_id'], 'reason': 'already_' + old['status']})
            continue
        since = instant(state.get('tickers', {}).get(ticker, {}).get('checked_through')
                        or state['initial_scan_from'])
        if not old and instant(event['published_at']) <= since:
            ignored.append({'ticker': ticker, 'event_id': event['event_id'], 'reason': 'before_monitoring_window'})
            continue
        if covered_by_research(baseline.get(ticker, {}), event):
            ignored.append({'ticker': ticker, 'event_id': event['event_id'], 'reason': 'covered_by_successful_research'})
            candidates.pop(key,None)
            if old and key not in covered:covered.append(key)
            continue
        candidates[key] = deepcopy(event)

    # One company per child; a later release supersedes an unfinished older one.
    latest = {}
    for key, event in candidates.items():
        ticker = event['ticker']
        rank = (instant(event['published_at']), key)
        if ticker not in latest or rank > latest[ticker][0]:
            latest[ticker] = (rank, key, event)
    selected = {ticker: {'event_key': value[1], 'event': value[2],
                         'reason': 'new_earnings',
                         'retry': prior.get(value[1], {}).get('status') in UNFINISHED}
                for ticker, value in sorted(latest.items())}
    deferred = [ticker for ticker in scope
                if baseline.get(ticker, {}).get('migration_needed') and ticker not in selected]
    return {'policy_version': POLICY_VERSION, 'run_mode': RUN_MODE, **context,
            'monitor_tickers': list(scope), 'selected': selected, 'checks': deepcopy(checks),
            'migration_deferred': deferred, 'ignored': ignored,
            'covered_event_keys': covered,
            'superseded_event_keys': [key for key in candidates
                                      if key not in {x['event_key'] for x in selected.values()}]}


def advance_state(state: dict, plan: dict, outcomes: dict, run_dir: str) -> dict:
    """Call only after full daily publication and validated registry publication."""
    if set(outcomes) != set(plan['selected']):
        raise ValueError('Every selected new release needs an explicit outcome')
    result = deepcopy(state)
    for key in plan['covered_event_keys']:
        result['events'][key].update(status='completed',covered_by_production_research=True)
    for ticker, check in plan['checks'].items():
        if check['status'] == 'ok':
            result['tickers'][ticker] = {'checked_through': plan['cutoff']}
    for key in plan['superseded_event_keys']:
        if key in result['events']:
            result['events'][key]['status'] = 'superseded'
    for selection in plan.get('deferred_earnings', {}).values():
        key = selection['event_key']
        if key not in result['events']:
            result['events'][key] = {'event': deepcopy(selection['event']), 'status': 'pending',
                                     'research_authorized': False, 'deferred_reason': 'outside_authorized_daily_window'}
    for ticker, selection in plan['selected'].items():
        status = outcomes[ticker]
        if status not in {'completed', 'pending', 'failed'}:
            raise ValueError('Unknown earnings outcome')
        result['events'][selection['event_key']] = {
            'event': deepcopy(selection['event']), 'status': status,
            'last_run_dir': run_dir, 'last_attempt_cutoff': plan['cutoff'],
            'research_authorized': True}
    result['last_publication_cutoff'] = plan['cutoff']
    return result
