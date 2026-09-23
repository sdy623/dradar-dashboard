"""Dashboard semantics and actual scroll state, independent of live credentials."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import contextlib
import io
from zhongce_radar import radar
import threading
import time


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('zhongce_radar.radar_tui'), 'Scrollable IQ dashboard is not implemented')
        from zhongce_radar import radar_tui
        self.ui = radar_tui

    def table(self):
        return {'benchmark_id': 'deep-swe', 'tasks': [{'id': 'a'}, {'id': 'b'}],
                'combos': [{'model': 'gpt-a', 'effort': 'low'}, {'model': 'gpt-a', 'effort': 'ultra'},
                           {'model': 'gpt-unavailable', 'effort': 'max'}],
                'cells': {'a|gpt-a|low': {'n': 2, 'st': 'running', 'holders': [{'running': True}, {'running': True}], 'q': 1},
                          'b|gpt-a|low': {'n': 0, 'st': 'leased', 'holders': [{'running': False}]},
                          'a|gpt-a|ultra': {'n': 5, 'holders': [{'running': True}], 'q': 0}}}

    def test_authoritative_iq_not_majority_or_recomputed_submission_rate(self):
        feed = {'mode': 'equal_latest_3', 'points': [
            {'model': 'gpt-a', 'effort': 'low', 'iq': 98.04, 'passed': 100, 'total': 153, 'average_price_usd': 1.5, 'average_minutes': 8.64},
            {'model': 'gpt-a', 'effort': 'ultra', 'iq': 140},
            {'model': 'gpt-unavailable', 'effort': 'max', 'iq': 150}]}
        rows = self.ui.iq_rows(self.table(), feed, {'gpt-a'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['iq'], 98.04)
        self.assertEqual(rows[0]['coverage'], '1/2')
        self.assertEqual(rows[0]['samples'], 153)

    def test_missing_iq_not_presented_as_zero(self):
        rows = self.ui.iq_rows(self.table(), {'mode': 'equal_latest_3', 'points': []}, {'gpt-a'})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['iq'])

    def test_wrong_iq_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.ui.iq_rows(self.table(), {'mode': 'majority', 'points': []}, {'gpt-a'})

    def test_concurrency_counts_workers_not_running_cells(self):
        counts = self.ui.pipeline(self.table())
        self.assertEqual(counts, {'running': 3, 'waiting': 1, 'grading': 1})
        own = self.ui.pipeline(self.table(), {'gpt-a'})
        self.assertEqual(own['running'], 2)  # ultra is removed only from the local model slice

    def test_online_and_riding_and_workers_are_distinct(self):
        live = {'online_volunteers': 8, 'riders': [
            {'riding_open': True, 'riding_since': 'today', 'riding_workers': 3},
            {'riding_open': False, 'riding_since': 'yesterday', 'riding_workers': 9},
            {'riding_open': True, 'riding_since': 'today', 'riding_workers': 2}]}
        data = self.ui.live_counts(live)
        self.assertEqual(data, {'online': 8, 'riders': 2, 'workers': 5})

    def test_unknown_worker_count_remains_unknown(self):
        counts = self.ui.live_counts({'riders': [{'riding_open': True, 'riding_since': 'today'}]})
        self.assertIsNone(counts['online'])
        self.assertIsNone(counts['workers'])

    def test_models_follow_local_relay_without_connecting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'tenbin-route').mkdir()
            (root/'tenbin-route/relay.mjs').write_text("export const MODELS = ['gpt-a', 'gpt-b'];", encoding='utf-8')
            allowed = self.ui.supported_models('codex', self.table(), root=root)
        self.assertEqual(allowed, {'gpt-a'})

    def test_scroll_clamps_resize_and_pages(self):
        view = self.ui.View()
        view.handle('down', 100, 10)
        self.assertEqual(view.offset, 1)
        view.handle('pgdn', 100, 10)
        self.assertEqual(view.offset, 10)
        view.handle('end', 100, 10)
        self.assertEqual(view.offset, 90)
        view.handle('down', 100, 10)
        self.assertEqual(view.offset, 90)
        view.handle('resize', 100, 95)
        self.assertEqual(view.offset, 5)
        view.handle('home', 100, 10)
        self.assertEqual(view.offset, 0)

    def test_search_finds_personal_section_and_wraps(self):
        view = self.ui.View()
        lines = ['IQ', 'x', '我的跑题', 'x', '我的判分']
        self.assertTrue(view.search('我的', lines))
        self.assertEqual(view.offset, 2)
        self.assertTrue(view.search('我的', lines))
        self.assertEqual(view.offset, 4)
        view.search('我的', lines)
        self.assertEqual(view.offset, 2)

    def test_terminal_display_width_handles_cjk(self):
        self.assertEqual(self.ui.display_width('月見IQ'), 6)
        self.assertEqual(self.ui.clip('月見IQ', 5), '月見I')
        self.assertEqual(self.ui.clip('a\u0301b', 1), 'a\u0301')

    def test_document_starts_with_iq_and_retains_personal_history(self):
        data = {'harness': 'codex', 'benchmark': 'deep-swe', 'iq': [{'model': 'gpt-a', 'effort': 'low', 'iq': 100, 'samples': 10, 'coverage': '2/2'}],
                'traffic': {}, 'identity': {'nickname': '我'}, 'rank': {}, 'other_rank': {},
                'personal': {'items': [{'task_id': 'historical', 'model': 'gpt-old', 'effort': 'ultra', 'grade_status': 'graded'}]},
                'tasks': {'items': []}, 'hot': [], 'matrix': [], 'leaders': [], 'errors': {}}
        lines, sections = self.ui.document(data, 90)
        text = '\n'.join(line[1] for line in lines)
        self.assertLess(text.index('模型 IQ'), text.index('我的跑题'))
        self.assertIn('gpt-old', text)
        self.assertIn('ultra', text)
        self.assertTrue(all(self.ui.display_width(line[1]) <= 90 for line in lines))
        self.assertIn('3', sections)

    def test_mouse_wheel_and_arrows_decode_to_scroll_actions(self):
        self.assertEqual(self.ui.decode_escape('\x1b[A'), 'up')
        self.assertEqual(self.ui.decode_escape('\x1b[6~'), 'pgdn')
        self.assertEqual(self.ui.decode_escape('\x1b[<65;30;10M'), 'wheel_down')
        self.assertEqual(self.ui.decode_escape('\x1b[<64;30;10M'), 'wheel_up')

    def test_cli_exposes_dashboard_and_tui(self):
        for command in ['dashboard', 'tui']:
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    parsed = radar.parser().parse_args(['codex', command])
            except SystemExit:
                self.fail(f'{command} is not available from the CLI')
            self.assertEqual(parsed.command, command)

    def test_default_interactive_mode_and_machine_output_do_not_conflict(self):
        self.assertTrue(callable(getattr(radar, 'select_mode', None)), 'automatic terminal mode is not implemented')
        self.assertEqual(radar.select_mode('auto', True, True, False, False), 'tui')
        self.assertEqual(radar.select_mode('auto', True, True, True, False), 'dashboard')
        self.assertEqual(radar.select_mode('auto', False, False, False, False), 'dashboard')
        self.assertEqual(radar.select_mode('overview', True, True, False, False), 'overview')



if __name__ == '__main__':
    unittest.main()
