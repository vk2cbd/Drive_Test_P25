from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CALIBRATION_DIR = Path.home() / ".config" / "radio_survey"
LEGACY_CALIBRATION_PATH = CALIBRATION_DIR / "calibration_vhf_100mhz.json"
CALIBRATION_IGNORED_METADATA_KEYS = {
    "antenna",
    "bias_t",
    "dab_notch",
    "fm_notch",
    "hdr_mode",
    "mw_notch",
    "tuner",
}


@dataclass(frozen=True)
class CalibrationBand:
    key: str
    label: str
    minimum_mhz: float
    maximum_mhz: float
    file_name: str

    @property
    def path(self) -> Path:
        return CALIBRATION_DIR / self.file_name

    def contains(self, value: object) -> bool:
        try:
            frequency_mhz = float(value)
        except (TypeError, ValueError):
            return False
        return self.minimum_mhz <= frequency_mhz <= self.maximum_mhz


CALIBRATION_BANDS: tuple[CalibrationBand, ...] = (
    CalibrationBand("vhf_broadcast", "VHF broadcast 88-108 MHz", 88.0, 108.0, "calibration_vhf_broadcast.json"),
    CalibrationBand("vhf_high", "VHF high 108-174 MHz", 108.0, 174.0, "calibration_vhf_high.json"),
    CalibrationBand("uhf_low", "UHF low 403-440 MHz", 403.0, 440.0, "calibration_uhf_low.json"),
    CalibrationBand("uhf_high", "UHF high 440-520 MHz", 440.0, 520.0, "calibration_uhf_high.json"),
)
DEFAULT_CALIBRATION_BAND_KEY = "vhf_broadcast"


def calibration_band_for_key(key: str) -> CalibrationBand:
    for band in CALIBRATION_BANDS:
        if band.key == key:
            return band
    return CALIBRATION_BANDS[0]


def calibration_band_for_label(label: str) -> CalibrationBand:
    for band in CALIBRATION_BANDS:
        if band.label == label:
            return band
    return CALIBRATION_BANDS[0]


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
    band_key: str = DEFAULT_CALIBRATION_BAND_KEY
    locked: bool = False

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
            if key in CALIBRATION_IGNORED_METADATA_KEYS:
                continue
            actual = current.get(key)
            if key == "center_frequency_mhz":
                if calibration_band_for_key(self.band_key).contains(actual):
                    continue
            if not _values_match(expected, actual):
                mismatches.append(key)
        return tuple(mismatches)

    def upsert_point(self, point: CalibrationPoint) -> "CalibrationProfile":
        points = [existing for existing in self.points if existing.label != point.label]
        points.append(point)
        return CalibrationProfile(self.name, self.created_utc, self.metadata, tuple(points), self.band_key, self.locked)

    def with_locked(self, locked: bool) -> "CalibrationProfile":
        return CalibrationProfile(self.name, self.created_utc, self.metadata, self.points, self.band_key, locked)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "created_utc": self.created_utc,
            "band_key": self.band_key,
            "locked": self.locked,
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


def new_calibration_profile(band_key: str, metadata: dict[str, object]) -> CalibrationProfile:
    band = calibration_band_for_key(band_key)
    return CalibrationProfile(
        name=band.label,
        created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        metadata=dict(metadata),
        points=(),
        band_key=band.key,
        locked=False,
    )


def new_vhf_broadcast_profile(metadata: dict[str, object]) -> CalibrationProfile:
    return new_calibration_profile(DEFAULT_CALIBRATION_BAND_KEY, metadata)


def load_calibration(band_key: str = DEFAULT_CALIBRATION_BAND_KEY, path: Path | None = None) -> CalibrationProfile | None:
    band = calibration_band_for_key(band_key)
    path = path or band.path
    if band.key == DEFAULT_CALIBRATION_BAND_KEY and not path.exists() and LEGACY_CALIBRATION_PATH.exists():
        path = LEGACY_CALIBRATION_PATH
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
            name=str(data.get("name", band.label)),
            created_utc=str(data.get("created_utc", "")),
            metadata=metadata,
            points=points,
            band_key=str(data.get("band_key", band.key)),
            locked=bool(data.get("locked", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_calibrations() -> dict[str, CalibrationProfile]:
    profiles: dict[str, CalibrationProfile] = {}
    for band in CALIBRATION_BANDS:
        profile = load_calibration(band.key)
        if profile is not None:
            profiles[band.key] = profile
    return profiles


def save_calibration(profile: CalibrationProfile, path: Path | None = None) -> None:
    path = path or calibration_band_for_key(profile.band_key).path
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
