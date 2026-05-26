from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CALIBRATION_PATH = Path.home() / ".config" / "radio_survey" / "calibration_vhf_100mhz.json"


@dataclass(frozen=True)
class CalibrationPoint:
    label: str
    measured_dbm: float
    input_dbm: float | None = None


@dataclass(frozen=True)
class CalibrationProfile:
    name: str
    created_utc: str
    metadata: dict[str, object]
    points: tuple[CalibrationPoint, ...]

    @property
    def signal_points(self) -> tuple[CalibrationPoint, ...]:
        points = [point for point in self.points if point.input_dbm is not None]
        return tuple(sorted(points, key=lambda point: point.measured_dbm))

    def has_points(self, labels: tuple[str, ...]) -> bool:
        available = {point.label for point in self.points}
        return all(label in available for label in labels)

    def apply(self, measured_dbm: float) -> float:
        points = self.signal_points
        if len(points) < 2:
            return measured_dbm
        lower = points[0]
        upper = points[1]
        if measured_dbm > points[-1].measured_dbm:
            lower = points[-2]
            upper = points[-1]
        for index in range(len(points) - 1):
            left = points[index]
            right = points[index + 1]
            if left.measured_dbm <= measured_dbm <= right.measured_dbm:
                lower = left
                upper = right
                break
        return _interpolate(measured_dbm, lower.measured_dbm, upper.measured_dbm, lower.input_dbm, upper.input_dbm)

    def metadata_mismatches(self, current: dict[str, object]) -> tuple[str, ...]:
        mismatches: list[str] = []
        for key, expected in self.metadata.items():
            actual = current.get(key)
            if not _values_match(expected, actual):
                mismatches.append(key)
        return tuple(mismatches)

    def upsert_point(self, point: CalibrationPoint) -> "CalibrationProfile":
        points = [existing for existing in self.points if existing.label != point.label]
        points.append(point)
        return CalibrationProfile(self.name, self.created_utc, self.metadata, tuple(points))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "created_utc": self.created_utc,
            "metadata": self.metadata,
            "points": [
                {
                    "label": point.label,
                    "input_dbm": point.input_dbm,
                    "measured_dbm": point.measured_dbm,
                }
                for point in self.points
            ],
        }


def new_vhf_broadcast_profile(metadata: dict[str, object]) -> CalibrationProfile:
    return CalibrationProfile(
        name="VHF broadcast 100 MHz",
        created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        metadata=dict(metadata),
        points=(),
    )


def load_calibration(path: Path = CALIBRATION_PATH) -> CalibrationProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    try:
        points = tuple(
            CalibrationPoint(
                label=str(point["label"]),
                input_dbm=None if point.get("input_dbm") is None else float(point["input_dbm"]),
                measured_dbm=float(point["measured_dbm"]),
            )
            for point in data.get("points", ())
        )
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return CalibrationProfile(
            name=str(data.get("name", "VHF broadcast 100 MHz")),
            created_utc=str(data.get("created_utc", "")),
            metadata=metadata,
            points=points,
        )
    except (KeyError, TypeError, ValueError):
        return None


def save_calibration(profile: CalibrationProfile, path: Path = CALIBRATION_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _interpolate(x: float, x0: float, x1: float, y0: float | None, y1: float | None) -> float:
    if y0 is None or y1 is None:
        return x
    if abs(x1 - x0) < 1e-9:
        return y0
    fraction = (x - x0) / (x1 - x0)
    return y0 + fraction * (y1 - y0)


def _values_match(expected: object, actual: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) == bool(actual)
    try:
        return abs(float(expected) - float(actual)) <= 1e-6
    except (TypeError, ValueError):
        return str(expected) == str(actual)
