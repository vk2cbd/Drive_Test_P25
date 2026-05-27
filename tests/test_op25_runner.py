from pathlib import Path

from radio_survey.op25_runner import OP25Config, OP25Status, build_op25_command, parse_op25_status_line, write_trunk_tsv


def _config() -> OP25Config:
    return OP25Config(
        op25_apps_dir="~/op25/op25/gr-op25_repeater/apps",
        python_command="python3",
        device_args="soapy=0,driver=sdrplay",
        frequency_mhz=468.5125,
        sample_rate_hz=1_000_000,
        gains="RFGR:20,IFGR:30",
        fine_tune=0,
        nac="0x0",
        modulation="C4FM",
        system_name="P25",
        terminal_url="http:127.0.0.1:8080",
        verbosity=5,
        audio_enabled=False,
        plots="symbol,constellation",
    )


def test_write_trunk_tsv(tmp_path: Path) -> None:
    path = write_trunk_tsv(_config(), tmp_path)
    text = path.read_text(encoding="utf-8")

    assert "Control Channel List" in text
    assert "468512500" in text
    assert "C4FM" in text


def test_build_op25_command() -> None:
    trunk_path = Path("/tmp/trunk.tsv")
    command = build_op25_command(_config(), trunk_path)

    assert command[:2] == ["python3", str(Path("~/op25/op25/gr-op25_repeater/apps").expanduser() / "rx.py")]
    assert "--args" in command
    assert "soapy=0,driver=sdrplay" in command
    assert "-T" in command
    assert str(trunk_path) in command
    assert "-n" in command


def test_parse_op25_status_line() -> None:
    status = parse_op25_status_line("wacn BEE00 sysid 123 nac 293 rfss 4 site 12", OP25Status())
    status = parse_op25_status_line("adjacent neighbor rfss 4 site 13 channel 1-77", status)

    assert status.wacn == "BEE00"
    assert status.system_id == "123"
    assert status.nac == "293"
    assert status.rfss == "4"
    assert status.site == "13"
    assert status.neighbours
