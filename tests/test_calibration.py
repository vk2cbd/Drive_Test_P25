from radio_survey.calibration import CalibrationPoint, CalibrationProfile, new_vhf_broadcast_profile


def test_calibration_interpolates_and_extrapolates() -> None:
    profile = CalibrationProfile(
        name="test",
        created_utc="2026-05-26T00:00:00+00:00",
        metadata={},
        points=(
            CalibrationPoint("-100 dBm", input_dbm=-100.0, measured_dbm=-110.0),
            CalibrationPoint("-80 dBm", input_dbm=-80.0, measured_dbm=-88.0),
            CalibrationPoint("-60 dBm", input_dbm=-60.0, measured_dbm=-66.0),
        ),
    )

    assert round(profile.apply(-99.0), 3) == -90.0
    assert round(profile.apply(-121.0), 3) == -110.0
    assert round(profile.apply(-55.0), 3) == -50.0


def test_calibration_metadata_mismatch() -> None:
    profile = new_vhf_broadcast_profile({"center_frequency_mhz": 100.0, "antenna": "A", "fm_notch": False})

    assert profile.metadata_mismatches({"center_frequency_mhz": 88.0, "antenna": "A", "fm_notch": False}) == ()
    assert profile.metadata_mismatches({"center_frequency_mhz": 108.0, "antenna": "A", "fm_notch": False}) == ()
    assert profile.metadata_mismatches({"center_frequency_mhz": 87.99, "antenna": "A", "fm_notch": False}) == ("center_frequency_mhz",)
    assert profile.metadata_mismatches({"center_frequency_mhz": 108.01, "antenna": "A", "fm_notch": False}) == ("center_frequency_mhz",)
    assert profile.metadata_mismatches({"center_frequency_mhz": 100.0, "antenna": "B", "fm_notch": True}) == ()


def test_calibration_ignores_operational_metadata() -> None:
    profile = new_vhf_broadcast_profile(
        {
            "antenna": "A",
            "bias_t": False,
            "dab_notch": False,
            "fm_notch": False,
            "hdr_mode": False,
            "mw_notch": False,
            "tuner": "A",
        }
    )

    assert (
        profile.metadata_mismatches(
            {
                "antenna": "C",
                "bias_t": True,
                "dab_notch": True,
                "fm_notch": True,
                "hdr_mode": True,
                "mw_notch": True,
                "tuner": "B",
            }
        )
        == ()
    )


def test_calibration_upsert_replaces_point() -> None:
    profile = new_vhf_broadcast_profile({})
    profile = profile.upsert_point(CalibrationPoint("-80 dBm", input_dbm=-80.0, measured_dbm=-90.0))
    profile = profile.upsert_point(CalibrationPoint("-80 dBm", input_dbm=-80.0, measured_dbm=-88.0))

    assert len(profile.points) == 1
    assert profile.points[0].measured_dbm == -88.0


def test_calibration_has_required_points() -> None:
    profile = new_vhf_broadcast_profile({}).upsert_point(
        CalibrationPoint("Noise floor", input_dbm=None, measured_dbm=-120.0)
    )

    assert profile.has_points(("Noise floor",))
    assert not profile.has_points(("Noise floor", "-100 dBm"))
