import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('summarize_iq', Path(__file__).resolve().parents[1]/'skills/dradar-dashboard/scripts/summarize_iq.py')
briefing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(briefing)


class BriefingTests(unittest.TestCase):
    def test_sparse_high_iq_does_not_win_recommendation_and_unknown_cost_does_not_win_value(self):
        rows = [dict(model=m, effort='max', iq=iq, price=p, coverage=c, samples=100) for m,iq,p,c in [
            ('gpt-6-astra', 110, 2, '100/112'), ('gpt-5.6-sol', 105, 1, '100/112'),
            ('gpt-6-luna', 150, .1, '1/112'), ('gpt-5.6-terra', 100, 0, '100/112')]]
        data = briefing.summarize({'data': {'iq': rows, 'identity': {'private': 'must-not-appear'}}})
        self.assertEqual(data['highest_observed_iq']['model'], 'gpt-6-luna')
        self.assertEqual(data['recommended_highest_iq']['model'], 'gpt-6-astra')
        self.assertEqual(data['recommended_best_value']['model'], 'gpt-5.6-sol')
        self.assertNotIn('identity', data)

    def test_iq_failure_is_not_an_empty_success(self):
        with self.assertRaises(ValueError):
            briefing.summarize({'data': {'errors': {'iq': 'offline'}}})

    def test_unknown_coverage_and_ultra_are_not_recommended(self):
        data=briefing.summarize({'iq': [dict(model='test', effort='max', iq=100, price=1, coverage='—', samples=100), dict(model='ultra', effort='ultra', iq=150)]})
        self.assertIsNone(data['recommended_highest_iq'])
        self.assertIsNone(data['recommended_best_value'])
        self.assertEqual(len(data['groups']), 1)
