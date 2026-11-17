"""Earnings scheduling regressions; all events below are synthetic, no network."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from src.tools.buffett_monitoring import (
    RUN_MODE, advance_state, build_plan, daily_context, empty_state, event_key,
)


class EarningsPolicyTests(unittest.TestCase):
    def setUp(self):
        self.scope = ['BKNG', 'LEGACY']
        self.context = {'as_of':'2026-11-03','cutoff':'2026-11-04T02:00:00Z'}
        self.state = empty_state('2026-10-30T02:01:58Z')
        self.baseline = {
            'BKNG': {'research_succeeded':True,'information_cutoff':'2026-10-30T02:01:58Z'},
            'LEGACY': {'migration_needed':True, 'needs_reanalysis':True,
                       'price_alert':True, 'execution_status':'failed', 'quality_score':None},
        }
        self.checks = {t:{'status':'ok','checked_through':self.context['cutoff']} for t in self.scope}
        self.event = {'ticker':'BKNG','event_id':'synthetic-2026Q3','kind':'earnings_release',
                      'status':'released','primary_source_verified':True,
                      'published_at':'2026-11-03T21:00:00Z','report_period':'2026-09-30',
                      'source_url':'https://example.test/issuer/q3'}

    def plan(self, events=None):
        return build_plan(self.scope,self.context,self.state,self.baseline,
                          [self.event] if events is None else events,self.checks)

    def test_bkng_new_q3_triggers_despite_completed_oct29_migration(self):
        self.assertEqual(list(self.plan()['selected']),['BKNG'])
        self.assertEqual(self.plan()['migration_deferred'],['LEGACY'])

    def test_legacy_schema_price_score_failure_alone_never_dispatches(self):
        plan = self.plan([])
        self.assertEqual(plan['selected'],{})
        self.assertEqual(plan['migration_deferred'],['LEGACY'])

    def test_superseded_old_release_never_reenters_queue(self):
        self.state['events'][event_key(self.event)]={'event':deepcopy(self.event),'status':'superseded'}
        self.assertFalse(self.plan()['selected'])

    def test_new_release_naturally_upgrades_legacy_company(self):
        self.event['ticker'] = 'LEGACY'
        self.assertEqual(list(self.plan()['selected']),['LEGACY'])

    def test_scheduled_release_does_not_trigger_even_if_calendar_is_due(self):
        self.event['status'] = 'scheduled'
        self.assertFalse(self.plan()['selected'])

    def test_actual_release_after_cutoff_does_not_trigger(self):
        self.context['cutoff'] = '2026-11-03T20:59:59Z'
        for c in self.checks.values():c['checked_through']=self.context['cutoff']
        self.assertFalse(self.plan()['selected'])

    def test_unverified_or_wrong_kind_is_not_release_evidence(self):
        self.event['primary_source_verified'] = False
        self.assertFalse(self.plan()['selected'])
        self.assertEqual(self.plan()['checks']['BKNG']['status'],'incomplete')
        self.event.update(primary_source_verified=True,kind='material_news')
        self.assertFalse(self.plan()['selected'])

    def test_past_due_schedule_does_not_advance_unconfirmed_watermark(self):
        self.event.update(status='scheduled',scheduled_at='2026-11-03T21:00:00Z')
        state=advance_state(self.state,self.plan(),{},'synthetic-run')
        self.assertNotIn('BKNG',state['tickers'])

    def test_published_production_research_recovers_uncommitted_event(self):
        self.state=advance_state(self.state,self.plan(),{'BKNG':'failed'},'synthetic-run')
        self.baseline['BKNG']['information_cutoff']='2026-11-03T23:00:00Z'
        plan=self.plan([])
        self.assertFalse(plan['selected'])
        state=advance_state(self.state,plan,{},'synthetic-run')
        self.assertEqual(state['events'][event_key(self.event)]['status'],'completed')

    def test_report_period_alone_cannot_trigger_historical_backfill(self):
        self.event['published_at'] = '2026-08-04T20:00:00Z'
        self.assertFalse(self.plan()['selected'])

    def test_missing_timezone_cannot_pass_as_actual_release_time(self):
        self.event['published_at'] = '2026-11-03T16:00:00'
        with self.assertRaisesRegex(ValueError,'timezone'):self.plan()

    def test_successful_research_cutoff_covers_release_calendar_date_does_not(self):
        self.baseline['BKNG']['last_earnings_date'] = '2026-11-03'
        self.assertIn('BKNG',self.plan()['selected'])
        self.baseline['BKNG']['information_cutoff'] = '2026-11-03T23:00:00Z'
        self.assertFalse(self.plan()['selected'])

    def test_completed_event_deduplicates_across_new_daily_runs(self):
        plan = self.plan()
        self.state = advance_state(self.state,plan,{'BKNG':'completed'},'synthetic-run')
        self.assertFalse(self.plan()['selected'])

    def test_failed_new_release_retries_without_restarting_old_migration(self):
        self.state = advance_state(self.state,self.plan(),{'BKNG':'failed'},'synthetic-run')
        plan = self.plan([])
        self.assertEqual(list(plan['selected']),['BKNG'])
        self.assertTrue(plan['selected']['BKNG']['retry'])
        self.assertEqual(plan['migration_deferred'],['LEGACY'])

    def test_incomplete_calendar_keeps_old_watermark_for_late_confirmation(self):
        self.checks['BKNG']['status'] = 'incomplete'
        state = advance_state(self.state,self.plan([]),{},'synthetic-run')
        self.assertNotIn('BKNG',state['tickers'])
        self.assertIn('LEGACY',state['tickers'])
        self.state = state
        self.assertIn('BKNG',self.plan()['selected'])

    def test_latest_report_only_one_job_per_company(self):
        old = dict(self.event,event_id='synthetic-old',published_at='2026-11-02T21:00:00Z')
        selected = self.plan([old,self.event])['selected']
        self.assertEqual(len(selected),1)
        self.assertEqual(selected['BKNG']['event']['event_id'],self.event['event_id'])

    def test_event_identity_cannot_change_timestamp(self):
        self.state = advance_state(self.state,self.plan(),{'BKNG':'failed'},'synthetic-run')
        self.event['published_at'] = '2026-11-03T22:00:00Z'
        with self.assertRaisesRegex(ValueError,'identity changed'):self.plan()

    def test_no_partial_scope_or_backward_watermark(self):
        self.checks.pop('LEGACY')
        with self.assertRaisesRegex(ValueError,'entire scope'):self.plan()
        self.checks['LEGACY'] = {'status':'ok','checked_through':self.context['cutoff']}
        self.state['tickers']['BKNG'] = {'checked_through':'2026-11-05T02:00:00Z'}
        with self.assertRaisesRegex(ValueError,'backwards'):self.plan()

    def test_context_uses_new_york_date_including_dst(self):
        context = daily_context(datetime(2026,11,4,2,tzinfo=timezone.utc))
        self.assertEqual(context['as_of'],'2026-11-03')
        self.assertEqual(self.plan()['run_mode'],RUN_MODE)

    def test_plan_does_not_mutate_history_or_monitoring_state(self):
        state,baseline = deepcopy(self.state),deepcopy(self.baseline)
        self.plan()
        self.assertEqual(self.state,state);self.assertEqual(self.baseline,baseline)


if __name__ == '__main__':unittest.main()
