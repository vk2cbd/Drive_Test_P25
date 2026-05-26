from radio_survey.app import SurveyApp
from radio_survey.config import SDR_PARAMETER_DEFS


class DummyVar:
    def __init__(self, value: object) -> None:
        self._value = value

    def get(self) -> object:
        return self._value


def test_collect_sdr_params_converts_display_units() -> None:
    app = SurveyApp.__new__(SurveyApp)
    app._vars = {
        "center_frequency_mhz": DummyVar("146.500000"),
        "sample_rate_msps": DummyVar("0.5"),
        "bandwidth_mhz": DummyVar("0.2"),
        "measurement_bandwidth_khz": DummyVar("12.5"),
    }

    params = app._collect_sdr_params()

    assert params["center_frequency_hz"] == 146_500_000.0
    assert params["sample_rate_hz"] == 500_000.0
    assert params["bandwidth_hz"] == 200_000.0
    assert params["measurement_bandwidth_khz"] == 12.5


def test_spectrum_y_axis_accepts_minus_150() -> None:
    app = SurveyApp.__new__(SurveyApp)

    assert app._parse_spectrum_y_value("-150") == -150.0
    assert app._parse_spectrum_y_value("-151") is None
    assert app._clamp_spectrum_y_value(-200.0) == -150.0


def test_rf_fm_notch_duplicate_control_removed() -> None:
    keys = {param.key for param in SDR_PARAMETER_DEFS}

    assert "rf_notch" not in keys
    assert "fm_notch" in keys
