import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import shutil
import subprocess
import base64
import os
import unittest
from unittest.mock import patch, Mock

from zhongce_radar import radar


def args(**kw):
    defaults = dict(harness='codex', benchmark='deep-swe', period='month', limit=20,
                    model=None, provider=None, effort=None, exclude_effort=['ultra'], min_multiplier=None,
                    plan='example-run-code', plan_id=None, command='run', dry_run=True,
                    concurrency=None, decision_token=None, confirm=False, command_file=None,
                    scope='this-device')
    return SimpleNamespace(**(defaults | kw))


def plan(harness='codex', effort='max'):
    return {'server': radar.SERVER, 'token': 'unit-test-secret', 'plan': {
        'schema_version': 1, 'plan_version': 1, 'plan_id': 'a' * 32, 'harness': harness,
        'expires_at': '2099-01-01T00:00:00Z', 'concurrency': {'mode': 'fixed', 'value': 2},
        'refill': {'enabled': False}, 'assignments': [
            {'assignment_id': 'b' * 32, 'model': 'gpt-test' if harness == 'codex' else 'claude-test', 'effort': effort}]}}


class FakeAPI:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, name, **kw):
        self.calls.append((name, kw))
        return next(self.responses)


class QueryTests(unittest.TestCase):
    def tearDown(self):
        radar.SECRETS.clear()

    def test_month_rank_matches_website_and_excludes_admin(self):
        board = {'contributors': [
            {'nickname': 'admin', 'is_radar_admin': True, 'month_points': 100},
            {'nickname': 'other', 'month_points': 10, 'month_graded': 3, 'points': 15},
            {'nickname': 'old-name', 'avatar_seed': 'mine', 'month_points': 10, 'month_graded': 4, 'points': 12}]}
        result = radar.ranking(None, args(), {'nickname': 'new-name', 'avatar_seed': 'mine'}, board)
        self.assertEqual((result['rank'], result['competitors']), (1, 2))

    def test_all_rank_preserves_server_order(self):
        board = {'contributors': [{'nickname': 'a', 'points': 20}, {'nickname': 'b', 'points': 99}]}
        self.assertEqual(radar.ranking(None, args(period='all'), {'nickname': 'b'}, board)['rank'], 2)

    def test_duplicate_identity_is_not_guessed(self):
        with self.assertRaises(radar.RadarError):
            radar.find_me([{'nickname': 'same'}, {'nickname': 'same'}], {'nickname': 'same'})

    def test_hot_excludes_ultra_and_stale_combo_and_preserves_final_multiplier(self):
        table = {'combos': [{'model': 'gpt-a', 'effort': 'high'}, {'model': 'gpt-a', 'effort': 'ultra'},
                            {'model': 'claude-a', 'effort': 'high', 'agent': 'claude-code'}],
                 'cells': {'task|gpt-a|high': {'st': 'open', 'mult': 3, 'wasteland_multiplier': 1.5},
                           'task|gpt-a|ultra': {'st': 'open', 'mult': 9},
                           'task|gpt-stale|low': {'st': 'open', 'mult': 10},
                           'task|claude-a|high': {'st': 'open', 'mult': 8}}}
        rows = radar.hot(None, args(), table)['items']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['multiplier'], 3)
        self.assertIsNone(rows[0]['api_equivalent_usd'])

    def test_actual_leases_not_my_cells_cooldown(self):
        api = FakeAPI([{'active': [], 'recent_inactive': [{'model': 'gpt-a', 'status': 'expired'}]}])
        result = radar.tasks(api, args())
        self.assertEqual(result['count'], 0)
        self.assertEqual(len(result['recent_inactive']), 1)
        self.assertEqual(api.calls[0][0], 'assignment')
        self.assertEqual(api.calls[0][1]['inventory'], 'true')

    def test_paid_provider_requires_explicit_selection_for_recommendations(self):
        table = {'combos': [{'model': 'deepseek-test', 'effort': 'high', 'provider': 'deepseek', 'billing_mode': 'api'}],
                 'cells': {'task|deepseek-test|high': {'st': 'open', 'mult': 225}}}
        self.assertEqual(radar.hot(None, args(), table)['items'], [])
        self.assertEqual(radar.hot(None, args(provider='all'), table)['items'][0]['multiplier'], 225)

    def test_stale_runner_is_not_running(self):
        self.assertEqual(radar.assignment_state({'heartbeat_running': False, 'started_at': 'date'}), 'stale')
        self.assertEqual(radar.assignment_state({'runner_state': 'resumable'}), 'stale')

    def test_submissions_pages_until_matching_harness(self):
        api = FakeAPI([{'submissions': [{'harness': 'claude-code', 'benchmark_id': 'deep-swe'}], 'next_cursor': 'next'},
                       {'submissions': [{'harness': 'codex', 'benchmark_id': 'deep-swe', 'task_id': 'mine'}], 'next_cursor': None}])
        result = radar.submissions(api, args(limit=1))
        self.assertEqual(result['items'][0]['task_id'], 'mine')
        self.assertEqual(api.calls[1][1]['cursor'], 'next')
        self.assertTrue(result['history_exhausted'])

    def test_pagination_limit_reports_truncation(self):
        api = FakeAPI([{'submissions': [{'harness': 'codex', 'benchmark_id': 'deep-swe'}], 'next_cursor': 'more'}])
        self.assertTrue(radar.submissions(api, args(limit=1))['truncated'])

    def test_account_records_include_newer_other_harness_without_changing_iq_scope(self):
        api = FakeAPI([{'submissions': [
            {'task_id': 'new-claude', 'harness': 'claude-code', 'benchmark_id': 'deep-swe'},
            {'task_id': 'old-codex', 'harness': 'codex', 'benchmark_id': 'deep-swe'}]}])
        options = args(records_scope='all', limit=20)
        result = radar.submissions(api, options)
        self.assertEqual([r['task_id'] for r in result['items']], ['new-claude', 'old-codex'])
        self.assertEqual(result['records_scope'], 'all')
        self.assertEqual(options.harness, 'codex')

    def test_record_time_converts_utc_without_losing_day_or_offset(self):
        from datetime import timezone, timedelta
        self.assertEqual(radar.local_record_time('2026-09-23T08:11:56+00:00', timezone(timedelta(hours=9))),
                         '2026-09-23 17:11:56+09:00')
        self.assertEqual(radar.local_record_time(None), '—')
        self.assertEqual(radar.local_record_time('not-a-time'), '未知')

    def test_plain_records_show_harness_and_both_timestamp_meanings(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            radar.render_section({'records_scope': 'all', 'items': [{'task_id': 'task', 'harness': 'claude-code',
                'submitted_at': '2026-09-23T08:03:27+00:00', 'graded_at': '2026-09-23T08:11:56+00:00', 'grade_status':'graded'}]})
        text = stream.getvalue()
        self.assertIn('claude-code', text)
        self.assertIn('提交', text)
        self.assertIn('判分', text)

    def test_public_query_does_not_read_auth_or_launch(self):
        from zhongce_radar.radar_http import prepare
        with patch('zhongce_radar.radar.read_json', side_effect=AssertionError('auth read')), patch('zhongce_radar.radar.dispatch', side_effect=AssertionError('launch')):
            url, headers = prepare(radar.API(), 'benchmarks')
        self.assertNotIn('Authorization', headers)

    def test_private_auth_sent_only_as_header(self):
        from zhongce_radar.radar_http import prepare
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'config.json').write_text(json.dumps({'server': radar.SERVER, 'token': 'secret-value'}), encoding='utf-8')
            url, headers = prepare(radar.API(home), 'whoami', private=True)
            self.assertEqual(headers['Authorization'], 'Bearer secret-value')
            self.assertNotIn('secret-value', url)

    def test_origin_mismatch_never_sends_auth(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'config.json').write_text(json.dumps({'server': 'https://wrong.example', 'token': 'secret'}), encoding='utf-8')
            with patch('zhongce_radar.radar_http.portal', side_effect=AssertionError('network')):
                with self.assertRaises(radar.RadarError):
                    radar.API(home).get('whoami', private=True)

    def test_non_inventory_request_and_write_rejected(self):
        with self.assertRaises(radar.RadarError):
            radar.API().get('assignment')
        with self.assertRaises(radar.RadarError):
            radar.API().request('whoami', payload={})

    def test_redaction_nested_and_runtime_text(self):
        radar.SECRETS.add('private-run-code')
        data = {'command': 'run', 'token': 'hidden', 'items': [{'message': 'private-run-code', 'decision': 'opaque'}]}
        result = radar.safe(data)
        self.assertEqual(result['command'], 'run')
        self.assertNotIn('hidden', json.dumps(result))
        self.assertNotIn('opaque', json.dumps(result))
        self.assertNotIn('opaque', radar.safe('  "decision": "opaque",'))

    def test_default_local_status_is_offline_and_projection_only(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'run-plans').mkdir()
            (home / 'run-plans/plan-test.json').write_text(json.dumps(plan()), encoding='utf-8')
            (home / 'pending_uploads.json').write_text('[]', encoding='utf-8')
            result = radar.local_status(home, 'codex')
            self.assertEqual(result['pending_uploads_count'], 0)
            self.assertNotIn('unit-test-secret', json.dumps(result))


class ControlTests(unittest.TestCase):
    def test_wrong_harness_and_ultra_fail(self):
        with self.assertRaises(radar.RadarError):
            radar.validate_plan(plan(), 'claude-code', execution=True)
        with self.assertRaises(radar.RadarError):
            radar.validate_plan(plan(effort='ultra'), 'codex', execution=True)

    def test_expired_run_rejected_but_stop_permitted(self):
        state = plan()
        state['plan']['expires_at'] = '2000-01-01T00:00:00Z'
        with self.assertRaises(radar.RadarError):
            radar.validate_plan(state, 'codex', execution=True)
        radar.validate_plan(state, 'codex')

    def test_refill_must_be_bounded_and_exact(self):
        state = plan()
        state['plan']['refill'] = {'enabled': True}
        with self.assertRaises(radar.RadarError):
            radar.validate_plan(state, 'codex', execution=True)
        state['plan']['refill']['max_tasks'] = 3
        radar.validate_plan(state, 'codex', execution=True)
        state['plan']['assignments'].append({'model': 'gpt-other', 'effort': 'high', 'assignment_id': 'x'})
        with self.assertRaises(radar.RadarError):
            radar.validate_plan(state, 'codex', execution=True)

    def test_dry_run_never_dispatches_or_prepares(self):
        with patch('zhongce_radar.radar.saved_plan', return_value=plan()), patch('zhongce_radar.radar.dispatch', side_effect=AssertionError('launch')), patch('zhongce_radar.radar.prepare', side_effect=AssertionError('exchange')):
            result = radar.control(args())
            self.assertFalse(result['started'])
            self.assertEqual(result['concurrency']['value'], 2)

    def test_execution_uses_project_route_and_keeps_effort(self):
        with patch('zhongce_radar.radar.prepare', return_value=plan(effort='low')), patch('zhongce_radar.radar.dispatch', return_value=(0, '')) as dispatch:
            radar.control(args(dry_run=False))
        self.assertEqual(dispatch.call_args.args[0], 'control')
        self.assertEqual(dispatch.call_args.args[1], ['run', '--plan', 'example-run-code', '--json'])

    def test_upload_only_never_adds_a_new_run(self):
        with patch('zhongce_radar.radar.prepare', return_value=plan(effort='ultra')), patch('zhongce_radar.radar.dispatch', return_value=(0, '')) as dispatch:
            radar.control(args(dry_run=False, command='upload'))
        self.assertIn('--upload-only', dispatch.call_args.args[1])

    def test_route_failure_propagates_without_retry_or_fallback(self):
        with patch('zhongce_radar.radar.prepare', return_value=plan()), patch('zhongce_radar.radar.dispatch', return_value=(7, '')) as dispatch:
            result = radar.control(args(dry_run=False))
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(dispatch.call_args.args[0], 'control')

    def test_exact_website_arguments_preserved(self):
        values = ['--offline', '--from', 'pinned-package', 'dradar', 'run', '--plan', 'example-run-code', '--concurrency', '2', '--json']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'command.json'
            path.write_text(json.dumps({'FilePath': 'uvx.exe', 'ArgumentList': values}), encoding='utf-8')
            with patch('zhongce_radar.radar.prepare', return_value=plan()), patch('zhongce_radar.radar.dispatch', return_value=(0, '')) as dispatch:
                radar.control(args(dry_run=False, command_file=str(path)))
            self.assertEqual(dispatch.call_args.args, ('exact', values, 'uvx.exe'))

    def test_confirm_requires_matching_previous_operation(self):
        with patch('zhongce_radar.radar.saved_plan', return_value=plan()):
            with self.assertRaises(radar.RadarError):
                radar.control(args(confirm=True))

    def test_watch_write_is_rejected(self):
        with self.assertRaises(radar.RadarError):
            radar.main(['codex', 'run', '--plan', 'test-code', '--watch'])

    def test_unknown_progress_does_not_register_a_code(self):
        with patch('zhongce_radar.radar.saved_plan', return_value=None), patch('zhongce_radar.radar.dispatch', side_effect=AssertionError('launch')):
            with self.assertRaises(radar.RadarError):
                radar.progress(None, args(command='progress'))


class ErrorSurfaceTests(unittest.TestCase):
    def test_overview_reports_both_rankings_from_same_snapshot(self):
        board = {'contributors': [{'nickname': 'other', 'month_points': 1, 'points': 20},
                                  {'nickname': 'me', 'month_points': 10, 'points': 10}]}
        with patch('zhongce_radar.radar.parallel', return_value={'identity': {'nickname': 'me'}, 'board': board, 'tasks': {'items': []}, 'submissions': {'items': []}}), patch('zhongce_radar.radar.hot', return_value={'items': []}), patch('zhongce_radar.radar.local_status', return_value={}):
            data = radar.overview(None, args())
        self.assertEqual(data['rank']['rank'], 1)
        self.assertEqual(data['other_rank']['rank'], 2)

    def test_partial_overview_never_reports_failed_section_as_zero(self):
        with patch('zhongce_radar.radar.parallel', return_value={'identity': {'error': 'offline'}, 'board': {}, 'tasks': {'error': 'offline'}, 'submissions': {'items': []}}), patch('zhongce_radar.radar.hot', return_value={'items': []}), patch('zhongce_radar.radar.local_status', return_value={}):
            data = radar.overview(None, args())
        self.assertTrue(data['partial'])
        self.assertEqual(data['tasks'], {'error': 'offline'})

    def test_watch_json_is_one_line_per_snapshot(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            radar.render({'data': {'nickname': '日本語'}}, True, compact=True)
        self.assertEqual(len(out.getvalue().splitlines()), 1)


if __name__ == '__main__':
    unittest.main()
