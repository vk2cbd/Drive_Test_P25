from __future__ import annotations

import math
import random
import time
from typing import Protocol


class LevelMeter(Protocol):
    def configure(self, params: dict[str, object]) -> None: ...
    def read_level_dbm(self) -> float: ...
    def close(self) -> None: ...


class SimulatedLevelMeter:
    def __init__(self) -> None:
        self._start = time.monotonic()
        self._offset = -82.0

    def configure(self, params: dict[str, object]) -> None:
        self._offset = float(params.get("simulated_level_dbm", -82.0))

    def read_level_dbm(self) -> float:
        elapsed = time.monotonic() - self._start
        slow_fade = math.sin(elapsed / 14.0) * 5.0
        flutter = math.sin(elapsed * 2.7) * 1.5
        noise = random.gauss(0.0, 0.8)
        return self._offset + slow_fade + flutter + noise

    def close(self) -> None:
        return


class SoapySdrplayLevelMeter:
    def __init__(self) -> None:
        self._sdr = None
        self._stream = None
        self._stream_active = False
        self._samples_per_level = 8192
        self._dbm_offset = -30.0
        self._last_level_dbm: float | None = None

    def configure(self, params: dict[str, object]) -> None:
        try:
            import numpy as np
            import SoapySDR
            from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX
        except ImportError as exc:
            raise RuntimeError("SoapySDR and numpy are required for the soapy_sdrplay backend") from exc

        self._np = np
        self._SoapySDR = SoapySDR
        self._direction = SOAPY_SDR_RX
        self._channel = 0 if params.get("tuner", "A") == "A" else 1
        self._format = SOAPY_SDR_CF32
        self._transient_read_codes = {
            getattr(SoapySDR, "SOAPY_SDR_OVERFLOW", -4),
            getattr(SoapySDR, "SOAPY_SDR_TIMEOUT", -1),
        }
        self._samples_per_level = int(params.get("samples_per_level", 8192))
        self._dbm_offset = float(params.get("dbm_offset", -30.0))

        device_args = _parse_device_args(str(params.get("device_args", "driver=sdrplay")))
        try:
            self._sdr = SoapySDR.Device(device_args)
        except Exception as exc:
            raise RuntimeError(
                "SoapySDR could not find an SDRplay device matching "
                f"{_format_device_args(device_args)}. On Ubuntu, run "
                "`SoapySDRUtil --find` and use the reported driver/device args, "
                "or switch the SDR backend to simulator until the SDRplay API and "
                "SoapySDR SDRplay module are installed."
            ) from exc
        self._sdr.setSampleRate(self._direction, self._channel, float(params["sample_rate_hz"]))
        self._sdr.setFrequency(self._direction, self._channel, float(params["center_frequency_hz"]))
        self._set_if_supported("setBandwidth", float(params["bandwidth_hz"]))
        self._set_if_supported("setAntenna", str(params.get("antenna", "A")))
        self._set_gain(params)
        self._write_settings(params)
        self._stream = self._sdr.setupStream(self._direction, self._format, [self._channel])

    def read_level_dbm(self) -> float:
        if self._sdr is None or self._stream is None:
            raise RuntimeError("SDR is not configured")

        buff = self._np.empty(self._samples_per_level, self._np.complex64)
        last_error_code: int | None = None
        self._activate_stream()

        try:
            for _attempt in range(10):
                result = self._sdr.readStream(self._stream, [buff], len(buff), timeoutUs=250_000)
                if result.ret > 0:
                    samples = buff[: result.ret]
                    rms = self._np.sqrt(self._np.mean(self._np.abs(samples) ** 2))
                    dbfs = 20.0 * math.log10(max(float(rms), 1e-12))
                    self._last_level_dbm = dbfs + self._dbm_offset
                    return self._last_level_dbm
                last_error_code = int(result.ret)
                if last_error_code not in self._transient_read_codes:
                    raise RuntimeError(f"SDR read failed with code {last_error_code}")
        finally:
            self._deactivate_stream()

        if self._last_level_dbm is not None:
            return self._last_level_dbm
        raise RuntimeError(
            "SDR did not return samples before timeout. Try a lower sample rate, "
            "larger samples-per-level value, or confirm the SDRplay API service is stable."
        )

    def close(self) -> None:
        if self._sdr is not None and self._stream is not None:
            try:
                self._deactivate_stream()
                self._sdr.closeStream(self._stream)
            finally:
                self._stream = None
                self._sdr = None
                self._stream_active = False

    def _activate_stream(self) -> None:
        if not self._stream_active:
            self._sdr.activateStream(self._stream)
            self._stream_active = True

    def _deactivate_stream(self) -> None:
        if self._stream_active:
            self._sdr.deactivateStream(self._stream)
            self._stream_active = False

    def _set_if_supported(self, method_name: str, value: object) -> None:
        method = getattr(self._sdr, method_name, None)
        if method is not None:
            method(self._direction, self._channel, value)

    def _set_gain(self, params: dict[str, object]) -> None:
        if str(params.get("gain_mode", "manual")) == "agc":
            self._sdr.setGainMode(self._direction, self._channel, True)
            return

        self._sdr.setGainMode(self._direction, self._channel, False)
        for name, key in (("RFGR", "rf_gain_reduction_db"), ("IFGR", "if_gain_reduction_db")):
            try:
                self._sdr.setGain(self._direction, self._channel, name, float(params[key]))
            except Exception:
                pass

    def _write_settings(self, params: dict[str, object]) -> None:
        setting_map = {
            "lna_state": "lnaState",
            "bias_t": "biasT_ctrl",
            "rf_notch": "rfnotch_ctrl",
            "dab_notch": "dabnotch_ctrl",
            "ppm_correction": "corr",
        }
        for param_key, setting_key in setting_map.items():
            if param_key not in params:
                continue
            try:
                self._sdr.writeSetting(setting_key, str(params[param_key]))
            except Exception:
                pass


def create_level_meter(backend: str) -> LevelMeter:
    if backend == "soapy_sdrplay":
        return SoapySdrplayLevelMeter()
    return SimulatedLevelMeter()


def _parse_device_args(value: str) -> dict[str, str]:
    args: dict[str, str] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        key, separator, raw_value = item.partition("=")
        if separator:
            args[key.strip()] = raw_value.strip()
    return args or {"driver": "sdrplay"}


def _format_device_args(args: dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in args.items())
