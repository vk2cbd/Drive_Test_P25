from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OP25Config:
    op25_apps_dir: str
    python_command: str
    device_args: str
    frequency_mhz: float
    sample_rate_hz: int
    gains: str
    fine_tune: int
    nac: str
    modulation: str
    system_name: str
    terminal_url: str
    verbosity: int
    audio_enabled: bool
    plots: str


@dataclass(frozen=True)
class OP25Status:
    wacn: str | None = None
    system_id: str | None = None
    nac: str | None = None
    rfss: str | None = None
    site: str | None = None
    neighbours: tuple[str, ...] = ()


def write_trunk_tsv(config: OP25Config, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    trunk_path = directory / "trunk.tsv"
    frequency_hz = int(round(config.frequency_mhz * 1_000_000.0))
    header = "Sysname\tControl Channel List\tOffset\tNAC\tModulation\tTGID Tags File\tWhitelist\tBlacklist\tCenter Frequency\n"
    row = (
        f"{config.system_name}\t{frequency_hz}\t0\t{config.nac}\t{config.modulation}\t"
        "\t\t\t"
        f"{frequency_hz}\n"
    )
    trunk_path.write_text(header + row, encoding="utf-8")
    return trunk_path


def build_op25_command(config: OP25Config, trunk_path: Path) -> list[str]:
    rx_path = Path(config.op25_apps_dir).expanduser() / "rx.py"
    command = [
        config.python_command,
        str(rx_path),
        "--args",
        config.device_args,
        "-S",
        str(int(config.sample_rate_hz)),
        "-f",
        str(int(round(config.frequency_mhz * 1_000_000.0))),
        "-T",
        str(trunk_path),
        "-U",
        "-l",
        config.terminal_url,
        "-v",
        str(int(config.verbosity)),
        "-q",
        str(int(config.fine_tune)),
    ]
    if config.gains.strip():
        command.extend(("-N", config.gains.strip()))
    if config.plots.strip():
        command.extend(("-P", config.plots.strip()))
    if not config.audio_enabled:
        command.append("-n")
    return command


def parse_op25_status_line(line: str, previous: OP25Status) -> OP25Status:
    text = line.strip()
    wacn = _first_match(text, (r"\bwacn[:=\s]+([0-9a-fA-F]{1,5})",)) or previous.wacn
    system_id = _first_match(text, (r"\bsys(?:tem)?(?:id)?[:=\s]+([0-9a-fA-F]{1,4})", r"\bsysid[:=\s]+([0-9a-fA-F]{1,4})")) or previous.system_id
    nac = _first_match(text, (r"\bnac[:=\s]+([0-9a-fA-F]{1,4})",)) or previous.nac
    rfss = _first_match(text, (r"\brfss[:=\s]+([0-9a-fA-F]{1,3})",)) or previous.rfss
    site = _first_match(text, (r"\bsite[:=\s]+([0-9a-fA-F]{1,3})",)) or previous.site

    neighbours = list(previous.neighbours)
    if re.search(r"\b(adjacent|neighbor|neighbour)\b", text, re.IGNORECASE) and text not in neighbours:
        neighbours.append(text)
    return OP25Status(wacn, system_id, nac, rfss, site, tuple(neighbours[-12:]))


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None
