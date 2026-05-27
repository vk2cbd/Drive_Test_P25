from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from . import __version__
from .p25 import P25Constellation, P25ControlChannelDecoder, P25ControlStatus, make_constellation
from .sdr import IqSnapshot, LevelMeter, create_level_meter
from .settings import load_settings, save_settings


@dataclass
class P25RuntimeCounters:
    sdr_blocks: int = 0
    worker_blocks: int = 0
    queue_drops: int = 0
    worker_samples: int = 0
    gui_refreshes: int = 0
    last_sdr_blocks: int = 0
    last_worker_blocks: int = 0
    last_queue_drops: int = 0
    last_worker_samples: int = 0
    last_gui_refreshes: int = 0
    last_report_s: float = 0.0


class P25ReceiverApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"P25 Control Channel Receiver {__version__}")
        self.geometry("1120x760")
        self.minsize(980, 680)

        self._settings = load_settings()
        self._level_meter: LevelMeter | None = None
        self._decoder = P25ControlChannelDecoder()
        self._iq_queue: queue.Queue[IqSnapshot] = queue.Queue(maxsize=24)
        self._stop_worker = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = P25ControlStatus(message="Stopped")
        self._constellation = P25Constellation(message="No IQ samples")
        self._afc_enabled = bool(self._settings.get("p25_afc_enabled", True))
        self._counters = P25RuntimeCounters(last_report_s=time.monotonic())
        self._rates_text = "SDR 0.0 blk/s | worker 0.0 blk/s | drops 0.0/s | samples 0.000 Msps | GUI 0.0 Hz"

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self._refresh_ui)

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        controls = ttk.LabelFrame(self, text="SDR", padding=10)
        controls.grid(row=0, column=0, sticky="nsw", padx=10, pady=10)
        controls.columnconfigure(1, weight=1)

        self.backend_var = tk.StringVar(value=str(self._settings.get("backend", "soapy_sdrplay")))
        self.device_args_var = tk.StringVar(value=str(self._settings.get("device_args", "driver=sdrplay")))
        self.frequency_var = tk.StringVar(value=f"{float(self._settings.get('center_frequency_mhz', 100.0)):.6f}")
        self.sample_rate_var = tk.StringVar(value=str(float(self._settings.get("sample_rate_msps", 0.192))))
        self.bandwidth_var = tk.StringVar(value=str(float(self._settings.get("bandwidth_mhz", 0.2))))
        self.tuner_var = tk.StringVar(value=str(self._settings.get("tuner", "A")))
        self.antenna_var = tk.StringVar(value=str(self._settings.get("antenna", "A")))
        self.gain_mode_var = tk.StringVar(value=str(self._settings.get("gain_mode", "manual")))
        self.rf_gain_var = tk.StringVar(value=str(float(self._settings.get("rf_gain_reduction_db", 20.0))))
        self.if_gain_var = tk.StringVar(value=str(float(self._settings.get("if_gain_reduction_db", 30.0))))
        self.lna_var = tk.StringVar(value=str(int(self._settings.get("lna_state", 0))))
        self.afc_var = tk.BooleanVar(value=self._afc_enabled)

        fields: tuple[tuple[str, tk.Variable, str, tuple[str, ...]], ...] = (
            ("Backend", self.backend_var, "choice", ("soapy_sdrplay", "simulator")),
            ("Device args", self.device_args_var, "text", ()),
            ("Frequency", self.frequency_var, "text", ()),
            ("Sample rate", self.sample_rate_var, "choice", ("0.096", "0.192", "0.25", "0.384", "0.5", "0.768", "1.0")),
            ("IF bandwidth", self.bandwidth_var, "choice", ("0.2", "0.3", "0.6", "1.536")),
            ("Tuner", self.tuner_var, "choice", ("A", "B")),
            ("Antenna", self.antenna_var, "choice", ("A", "B", "C")),
            ("Gain mode", self.gain_mode_var, "choice", ("manual", "agc")),
            ("RF gain reduction", self.rf_gain_var, "text", ()),
            ("IF gain reduction", self.if_gain_var, "text", ()),
            ("LNA state", self.lna_var, "text", ()),
        )
        for row, (label, var, kind, choices) in enumerate(fields):
            ttk.Label(controls, text=label).grid(row=row, column=0, sticky="w", pady=2)
            if kind == "choice":
                widget = ttk.Combobox(controls, textvariable=var, values=choices, state="readonly", width=18)
            else:
                widget = ttk.Entry(controls, textvariable=var, width=22)
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)

        ttk.Label(controls, text="MHz").grid(row=2, column=2, sticky="w", padx=(4, 0))
        ttk.Label(controls, text="Msps").grid(row=3, column=2, sticky="w", padx=(4, 0))
        ttk.Label(controls, text="MHz").grid(row=4, column=2, sticky="w", padx=(4, 0))
        ttk.Checkbutton(controls, text="Auto frequency correction", variable=self.afc_var, command=self._save_settings).grid(
            row=len(fields),
            column=0,
            columnspan=3,
            sticky="w",
            pady=(10, 0),
        )
        buttons = ttk.Frame(controls)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        buttons.columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(buttons, text="Start", command=self._start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(buttons, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        display = ttk.Frame(self, padding=(0, 10, 10, 10))
        display.grid(row=0, column=1, sticky="nsew")
        display.columnconfigure(0, weight=1)
        display.rowconfigure(1, weight=1)

        status = ttk.LabelFrame(display, text="P25 Control Channel", padding=10)
        status.grid(row=0, column=0, sticky="ew")
        status.columnconfigure(1, weight=1)
        self.status_var = tk.StringVar(value="Stopped")
        self.offset_var = tk.StringVar(value="-")
        self.wacn_var = tk.StringVar(value="-")
        self.system_var = tk.StringVar(value="-")
        self.site_var = tk.StringVar(value="-")
        self.neighbours_var = tk.StringVar(value="-")
        self.sync_var = tk.StringVar(value="-")
        self.rates_var = tk.StringVar(value=self._rates_text)
        for row, (label, var) in enumerate(
            (
                ("Status", self.status_var),
                ("AFC offset", self.offset_var),
                ("Sync quality", self.sync_var),
                ("Stream rates", self.rates_var),
                ("WACN", self.wacn_var),
                ("System", self.system_var),
                ("RFSS/Site", self.site_var),
                ("Neighbours", self.neighbours_var),
            )
        ):
            ttk.Label(status, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Label(status, textvariable=var, font=("TkDefaultFont", 11, "bold"), wraplength=680).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)

        self.constellation_canvas = tk.Canvas(display, background="#101418", highlightthickness=0)
        self.constellation_canvas.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.constellation_canvas.bind("<Configure>", lambda _event: self._draw_constellation())

    def _params(self) -> dict[str, object]:
        frequency_mhz = float(self.frequency_var.get())
        sample_rate_msps = float(self.sample_rate_var.get())
        bandwidth_mhz = float(self.bandwidth_var.get())
        params = {
            "backend": self.backend_var.get(),
            "device_args": self.device_args_var.get(),
            "center_frequency_mhz": frequency_mhz,
            "center_frequency_hz": frequency_mhz * 1_000_000.0,
            "sample_rate_msps": sample_rate_msps,
            "sample_rate_hz": sample_rate_msps * 1_000_000.0,
            "bandwidth_mhz": bandwidth_mhz,
            "bandwidth_hz": bandwidth_mhz * 1_000_000.0,
            "tuner": self.tuner_var.get(),
            "antenna": self.antenna_var.get(),
            "gain_mode": self.gain_mode_var.get(),
            "rf_gain_reduction_db": max(0.0, min(66.0, float(self.rf_gain_var.get()))),
            "if_gain_reduction_db": max(20.0, min(59.0, float(self.if_gain_var.get()))),
            "lna_state": max(0, min(9, int(float(self.lna_var.get())))),
            "samples_per_level": 8192,
            "measurement_bandwidth_khz": 12.5,
            "dbm_offset": -30.0,
            "dc_offset_correction": True,
            "iq_balance_correction": True,
            "ppm_correction": 0.0,
            "decimation": 1,
            "if_mode": "Zero IF",
            "lo_mode": "Auto",
            "hdr_mode": False,
            "bias_t": False,
            "dab_notch": False,
            "fm_notch": False,
            "mw_notch": False,
        }
        return params

    def _start(self) -> None:
        try:
            self._save_settings()
            params = self._params()
            self._level_meter = create_level_meter(str(params["backend"]))
            self._level_meter.configure(params)
            self._decoder = P25ControlChannelDecoder()
            self._iq_queue = queue.Queue(maxsize=24)
            self._stop_worker = threading.Event()
            with self._lock:
                self._counters = P25RuntimeCounters(last_report_s=time.monotonic())
                self._rates_text = "SDR 0.0 blk/s | worker 0.0 blk/s | drops 0.0/s | samples 0.000 Msps | GUI 0.0 Hz"
            self._worker = threading.Thread(target=self._worker_loop, name="p25-only-decoder", daemon=True)
            self._worker.start()
            self._level_meter.set_iq_callback(self._queue_iq)
        except Exception as exc:
            self._stop()
            messagebox.showerror("Unable to start P25 receiver", str(exc))
            return
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        with self._lock:
            self._status = P25ControlStatus(message="Running")
            self._constellation = P25Constellation(message="Waiting for IQ samples")

    def _stop(self) -> None:
        self._stop_worker.set()
        if self._level_meter is not None:
            self._level_meter.set_iq_callback(None)
            self._level_meter.close()
            self._level_meter = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None
        while True:
            try:
                self._iq_queue.get_nowait()
            except queue.Empty:
                break
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        with self._lock:
            self._status = P25ControlStatus(message="Stopped")
            self._constellation = P25Constellation(message="No IQ samples")

    def _queue_iq(self, snapshot: IqSnapshot) -> None:
        with self._lock:
            self._counters.sdr_blocks += 1
        try:
            self._iq_queue.put_nowait(snapshot)
        except queue.Full:
            with self._lock:
                self._counters.queue_drops += 1
            try:
                self._iq_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._iq_queue.put_nowait(snapshot)
            except queue.Full:
                pass

    def _worker_loop(self) -> None:
        count = 0
        while not self._stop_worker.is_set():
            try:
                snapshot = self._iq_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                status = self._decoder.update(snapshot.samples, snapshot.sample_rate_hz, self._afc_enabled)
                count += 1
                with self._lock:
                    self._counters.worker_blocks += 1
                    self._counters.worker_samples += len(snapshot.samples)
                constellation = None
                if count % 3 == 0:
                    constellation = make_constellation(snapshot.samples, snapshot.sample_rate_hz, frequency_offset_hz=status.frequency_offset_hz)
                with self._lock:
                    self._status = status
                    if constellation is not None:
                        self._constellation = constellation
            except Exception as exc:
                with self._lock:
                    self._status = P25ControlStatus(message=f"P25 error: {exc}")

    def _refresh_ui(self) -> None:
        with self._lock:
            status = self._status
            constellation = self._constellation
            self._counters.gui_refreshes += 1
            self._update_rates_locked()
            rates_text = self._rates_text
        detail = status.message
        if status.frame_syncs:
            detail = f"{detail}; syncs {status.frame_syncs}; TSBK {status.tsbks}"
        self.status_var.set(detail)
        if status.channel_sample_rate_hz:
            self.offset_var.set(f"{status.frequency_offset_hz:+.0f} Hz, channel {status.channel_sample_rate_hz / 1000.0:.1f} ksps")
        else:
            self.offset_var.set("-")
        if status.best_sync_distance is None:
            self.sync_var.set(f"buffer {status.bit_buffer_length} bits, no full sync window yet")
        else:
            self.sync_var.set(
                f"best {status.best_sync_distance} bit errors / 48, "
                f"near {status.near_syncs}, buffer {status.bit_buffer_length} bits"
            )
        self.rates_var.set(rates_text)
        self.wacn_var.set(status.wacn or "-")
        self.system_var.set(status.system_id or "-")
        self.site_var.set(f"{status.rfss_id}/{status.site_id}" if status.rfss_id is not None and status.site_id is not None else "-")
        self.neighbours_var.set("; ".join(neighbour.display() for neighbour in status.neighbours) or "-")
        self._latest_constellation = constellation
        self._draw_constellation()
        self.after(200, self._refresh_ui)

    def _update_rates_locked(self) -> None:
        now_s = time.monotonic()
        elapsed_s = now_s - self._counters.last_report_s
        if elapsed_s < 0.75:
            return
        sdr_delta = self._counters.sdr_blocks - self._counters.last_sdr_blocks
        worker_delta = self._counters.worker_blocks - self._counters.last_worker_blocks
        drop_delta = self._counters.queue_drops - self._counters.last_queue_drops
        sample_delta = self._counters.worker_samples - self._counters.last_worker_samples
        gui_delta = self._counters.gui_refreshes - self._counters.last_gui_refreshes
        self._rates_text = (
            f"SDR {sdr_delta / elapsed_s:.1f} blk/s | "
            f"worker {worker_delta / elapsed_s:.1f} blk/s | "
            f"drops {drop_delta / elapsed_s:.1f}/s | "
            f"samples {sample_delta / elapsed_s / 1_000_000.0:.3f} Msps | "
            f"GUI {gui_delta / elapsed_s:.1f} Hz"
        )
        self._counters.last_sdr_blocks = self._counters.sdr_blocks
        self._counters.last_worker_blocks = self._counters.worker_blocks
        self._counters.last_queue_drops = self._counters.queue_drops
        self._counters.last_worker_samples = self._counters.worker_samples
        self._counters.last_gui_refreshes = self._counters.gui_refreshes
        self._counters.last_report_s = now_s

    def _draw_constellation(self) -> None:
        canvas = self.constellation_canvas
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
        constellation = getattr(self, "_latest_constellation", P25Constellation())
        gap = 24
        left_w = int((width - gap) * 0.45)
        right_x = left_w + gap
        margin = 36
        canvas.create_text(12, 12, anchor="nw", text="Channelized IQ", fill="#d7e0ea")
        canvas.create_text(right_x, 12, anchor="nw", text="C4FM discriminator symbols", fill="#d7e0ea")

        iq_cx = left_w / 2
        iq_cy = height / 2 + 10
        iq_radius = max(20, min(left_w - margin * 2, height - margin * 2) / 2)
        canvas.create_oval(iq_cx - iq_radius, iq_cy - iq_radius, iq_cx + iq_radius, iq_cy + iq_radius, outline="#39424d")
        canvas.create_line(iq_cx - iq_radius, iq_cy, iq_cx + iq_radius, iq_cy, fill="#8fa2b3")
        canvas.create_line(iq_cx, iq_cy - iq_radius, iq_cx, iq_cy + iq_radius, fill="#8fa2b3")
        for real, imag in constellation.iq_points:
            x = iq_cx + max(-1.4, min(1.4, real)) * iq_radius / 1.4
            y = iq_cy - max(-1.4, min(1.4, imag)) * iq_radius / 1.4
            canvas.create_oval(x - 1, y - 1, x + 1, y + 1, fill="#73d2de", outline="")

        symbol_left = right_x + margin
        symbol_right = width - margin
        symbol_top = margin
        symbol_bottom = height - margin
        canvas.create_rectangle(symbol_left, symbol_top, symbol_right, symbol_bottom, outline="#39424d")
        for level in (-1.0, -0.33, 0.33, 1.0):
            y = symbol_top + (1.5 - level) / 3.0 * (symbol_bottom - symbol_top)
            canvas.create_line(symbol_left, y, symbol_right, y, fill="#8fa2b3")
            canvas.create_text(symbol_left - 5, y, anchor="e", text=f"{level:g}", fill="#d7e0ea", font=("TkDefaultFont", 8))
        for fraction, level in constellation.symbol_points:
            x = symbol_left + fraction * (symbol_right - symbol_left)
            y = symbol_top + (1.5 - level) / 3.0 * (symbol_bottom - symbol_top)
            canvas.create_oval(x - 1, y - 1, x + 1, y + 1, fill="#ffd166", outline="")
        if not constellation.iq_points and not constellation.symbol_points:
            canvas.create_text(width / 2, height / 2, text=constellation.message, fill="#d7e0ea")

    def _save_settings(self) -> None:
        self._afc_enabled = bool(self.afc_var.get())
        self._settings.update(
            {
                "backend": self.backend_var.get(),
                "device_args": self.device_args_var.get(),
                "center_frequency_mhz": float(self.frequency_var.get()),
                "sample_rate_msps": float(self.sample_rate_var.get()),
                "bandwidth_mhz": float(self.bandwidth_var.get()),
                "tuner": self.tuner_var.get(),
                "antenna": self.antenna_var.get(),
                "gain_mode": self.gain_mode_var.get(),
                "rf_gain_reduction_db": float(self.rf_gain_var.get()),
                "if_gain_reduction_db": float(self.if_gain_var.get()),
                "lna_state": int(float(self.lna_var.get())),
                "p25_afc_enabled": self._afc_enabled,
            }
        )
        save_settings(self._settings)

    def _on_close(self) -> None:
        self._stop()
        self.destroy()


def main() -> None:
    app = P25ReceiverApp()
    app.mainloop()
