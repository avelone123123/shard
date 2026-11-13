import unittest
from shard_engine import telemetry
class TestTelemetry(unittest.TestCase):
    def test_speed_moving_average(self):
        smoother = telemetry.SpeedMovingAverage(window_size=3)
        smoother.add_sample(100.0, 0)
        smoother.add_sample(101.0, 1048576)
        spd = smoother.current_speed()
        self.assertAlmostEqual(spd, 1048576.0, delta=100)
