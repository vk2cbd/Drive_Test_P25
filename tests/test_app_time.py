from datetime import datetime, timezone

from radio_survey.app import _format_local_time


def test_format_local_time_uses_system_timezone() -> None:
    timestamp = datetime(2026, 5, 22, 10, 30, tzinfo=timezone.utc)

    assert _format_local_time(timestamp) == timestamp.astimezone().strftime("%H:%M:%S %Z")
