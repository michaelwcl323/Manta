import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark.logs import LogParser, _parse_round_timestamps


class RoundWaveTimingTests(unittest.TestCase):
    def test_parse_round_timestamps_prefers_earliest_event(self):
        log = """
[2026-08-20T01:02:03.100Z INFO primary::proposer] HEADER_SIZE round=1 payload_bytes=0
[2026-08-20T01:02:03.200Z INFO primary::proposer] Created B1(node) -> digest=
[2026-08-20T01:02:03.300Z INFO primary::proposer] HEADER_METADATA round=2 tusk_base_bytes=10
"""

        timestamps = _parse_round_timestamps(log)

        self.assertEqual(set(timestamps), {1, 2})
        self.assertAlmostEqual(timestamps[2] - timestamps[1], 0.2, places=6)

    def test_export_contains_round_and_wave_rows(self):
        parser = LogParser.__new__(LogParser)
        parser.round_timestamps = (
            {1: 10.0, 2: 11.0, 3: 12.0, 4: 13.0, 5: 14.0},
            {1: 10.2, 2: 11.3, 3: 12.1, 4: 13.4, 5: 14.5},
        )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'round_wave_timing.csv'
            parser.export_round_wave_timing_csv(str(output), wave_length=4)
            with output.open(newline='') as f:
                rows = list(csv.DictReader(f))

        round_one = next(
            row for row in rows if row['scope'] == 'round' and row['index'] == '1'
        )
        wave_one = next(
            row for row in rows if row['scope'] == 'wave' and row['index'] == '1'
        )
        wave_two = next(
            row for row in rows if row['scope'] == 'wave' and row['index'] == '2'
        )

        self.assertEqual(round_one['duration_ms'], '200.0')
        self.assertEqual(round_one['min_observed_primaries'], '2')
        self.assertEqual(wave_one['round_start'], '1')
        self.assertEqual(wave_one['round_end'], '4')
        self.assertEqual(wave_one['duration_ms'], '3400.0')
        self.assertEqual(wave_one['complete'], 'True')
        self.assertEqual(wave_two['observed_rounds'], '1')
        self.assertEqual(wave_two['complete'], 'False')


if __name__ == '__main__':
    unittest.main()
