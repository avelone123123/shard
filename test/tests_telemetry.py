import unittest
from shard_engine import telemetry
class TestTelemetry(unittest.TestCase):
    def test_speed_moving_average(self):
        smoother = telemetry.SpeedMovingAverage(window_size=3)
        smoother.add_sample(100.0, 0)
        smoother.add_sample(101.0, 1048576)
        spd = smoother.current_speed()
        self.assertAlmostEqual(spd, 1048576.0, delta=100)
    def test_transfer_progress_tracker(self):
        tracker = telemetry.TransferProgressTracker(total_bytes=10000000)
        tracker.update(5000000)
        snap = tracker.snapshot(stage="downloading")
        self.assertEqual(snap.downloaded_bytes, 5000000)
        self.assertEqual(snap.total_bytes, 10000000)
        self.assertEqual(snap.stage, "downloading")
        d = snap.to_dict()
        self.assertIn("speed_bps", d)
if __name__ == "__main__":
    unittest.main()
