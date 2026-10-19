import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from usage_meter import meter


def snapshot(used, reset=123):
    return {"rateLimitsByLimitId": {"codex": {"primary": {
        "usedPercent": used, "resetsAt": reset, "windowDurationMins": 300}}}}


class UsageTests(unittest.TestCase):
    def test_unknown_is_not_zero(self):
        self.assertIsNone(meter({})['windows'][0]['usedPercent'])

    def test_alert_dedup_and_reset(self):
        first = meter(snapshot(92))
        self.assertEqual(first['alerts'], [{'window': 'primary', 'threshold': 90}])
        self.assertEqual(meter(snapshot(93), previous=first['state'])['alerts'], [])
        self.assertEqual(meter(snapshot(96), previous=first['state'])['alerts'][0]['threshold'], 95)
        self.assertTrue(meter(snapshot(92, 456), previous=first['state'])['alerts'])

    def test_other_bucket_not_substituted(self):
        self.assertIsNone(meter(snapshot(99), bucket_id='other')['windows'][0]['usedPercent'])

    def test_invalid_readings(self):
        for value in [True, float('nan'), float('inf'), '90', None]:
            self.assertIsNone(meter(snapshot(value))['windows'][0]['usedPercent'])


if __name__ == '__main__':
    unittest.main()
