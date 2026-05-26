import math

import numpy as np

from radio_survey.sdr import SoapySdrplayLevelMeter


def _meter_for_bandwidth(measurement_bandwidth_hz: float) -> SoapySdrplayLevelMeter:
    meter = SoapySdrplayLevelMeter()
    meter._np = np
    meter._sample_rate_hz = 200_000.0
    meter._bandwidth_hz = 200_000.0
    meter._measurement_bandwidth_hz = measurement_bandwidth_hz
    meter._dbm_offset = 0.0
    meter._last_level_dbm = None
    return meter


def test_channel_power_increases_with_measurement_bandwidth_for_noise() -> None:
    rng = np.random.default_rng(1234)
    samples = (
        rng.normal(0.0, 1.0, 8192)
        + 1j * rng.normal(0.0, 1.0, 8192)
    ).astype(np.complex64)

    narrow = _meter_for_bandwidth(10_000.0)._measure_channel_power(samples)
    wide = _meter_for_bandwidth(80_000.0)._measure_channel_power(samples)

    expected_delta_db = 10.0 * math.log10(8.0)
    assert abs((wide - narrow) - expected_delta_db) <= 1.5
