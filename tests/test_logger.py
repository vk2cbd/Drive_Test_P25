from datetime import datetime, timezone

from radio_survey.logger import CsvSurveyLogger
from radio_survey.nmea import GpsFix


def test_csv_logger_writes_requested_columns(tmp_path) -> None:
    path = tmp_path / "survey.csv"
    fix = GpsFix(datetime(2026, 5, 22, 10, 30, tzinfo=timezone.utc), -33.8688, 151.2093)

    with CsvSurveyLogger(path) as logger:
        logger.write(fix, -87.456)

    local_timestamp = fix.timestamp_utc.astimezone()
    assert path.read_text(encoding="utf-8").splitlines() == [
        "date_local,time_local,latitude,longitude,received_level_dbm",
        f"{local_timestamp:%Y-%m-%d},{local_timestamp:%H:%M:%S.%f},33:52:07.680S,151:12:33.480E,-87.46",
    ]
