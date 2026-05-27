from __future__ import annotations

import queue
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__
from .op25_runner import OP25Config, OP25Status, build_op25_command, parse_op25_status_line, write_trunk_tsv
from .settings import load_settings, save_settings


class P25ReceiverApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"OP25 Control Channel Receiver {__version__}")
        self.geometry("1160x760")
        self.minsize(1040, 700)

        self._settings = load_settings()
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._status = OP25Status()
        self._work_dir = Path.home() / ".config" / "radio_survey" / "op25"

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_output)

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        controls = ttk.LabelFrame(self, text="OP25 Receiver", padding=10)
        controls.grid(row=0, column=0, sticky="nsw", padx=10, pady=10)
        controls.columnconfigure(1, weight=1)

        self.op25_dir_var = tk.StringVar(value=str(self._settings.get("op25_apps_dir", "~/op25/op25/gr-op25_repeater/apps")))
        self.python_var = tk.StringVar(value=str(self._settings.get("op25_python", "python3")))
        self.device_args_var = tk.StringVar(value=str(self._settings.get("op25_device_args", "soapy=0,driver=sdrplay")))
        self.frequency_var = tk.StringVar(value=f"{float(self._settings.get('center_frequency_mhz', 100.0)):.6f}")
        self.sample_rate_var = tk.StringVar(value=str(int(self._settings.get("op25_sample_rate_hz", 1_000_000))))
        self.gains_var = tk.StringVar(value=str(self._settings.get("op25_gains", "")))
        self.fine_tune_var = tk.StringVar(value=str(int(self._settings.get("op25_fine_tune", 0))))
        self.nac_var = tk.StringVar(value=str(self._settings.get("op25_nac", "0x0")))
        self.modulation_var = tk.StringVar(value=str(self._settings.get("op25_modulation", "C4FM")))
        self.system_name_var = tk.StringVar(value=str(self._settings.get("op25_system_name", "P25")))
        self.terminal_url_var = tk.StringVar(value=str(self._settings.get("op25_terminal_url", "http:127.0.0.1:8080")))
        self.verbosity_var = tk.StringVar(value=str(int(self._settings.get("op25_verbosity", 5))))
        self.plots_var = tk.StringVar(value=str(self._settings.get("op25_plots", "symbol,constellation")))
        self.audio_var = tk.BooleanVar(value=bool(self._settings.get("op25_audio_enabled", False)))

        fields: tuple[tuple[str, tk.Variable, str, tuple[str, ...]], ...] = (
            ("OP25 apps dir", self.op25_dir_var, "text", ()),
            ("Python command", self.python_var, "text", ()),
            ("Device args", self.device_args_var, "text", ()),
            ("Control freq", self.frequency_var, "text", ()),
            ("Sample rate", self.sample_rate_var, "text", ()),
            ("Gains", self.gains_var, "text", ()),
            ("Fine tune", self.fine_tune_var, "text", ()),
            ("NAC", self.nac_var, "text", ()),
            ("Modulation", self.modulation_var, "choice", ("C4FM", "CQPSK")),
            ("System name", self.system_name_var, "text", ()),
            ("Terminal", self.terminal_url_var, "text", ()),
            ("Verbosity", self.verbosity_var, "text", ()),
            ("Plots", self.plots_var, "text", ()),
        )
        for row, (label, var, kind, choices) in enumerate(fields):
            ttk.Label(controls, text=label).grid(row=row, column=0, sticky="w", pady=2)
            if kind == "choice":
                widget = ttk.Combobox(controls, textvariable=var, values=choices, state="readonly", width=28)
            else:
                widget = ttk.Entry(controls, textvariable=var, width=30)
            widget.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        ttk.Label(controls, text="MHz").grid(row=3, column=2, sticky="w", padx=(4, 0))
        ttk.Label(controls, text="Hz").grid(row=4, column=2, sticky="w", padx=(4, 0))
        ttk.Checkbutton(controls, text="Enable audio", variable=self.audio_var, command=self._save_settings).grid(
            row=len(fields),
            column=0,
            columnspan=3,
            sticky="w",
            pady=(10, 0),
        )

        buttons = ttk.Frame(controls)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        buttons.columnconfigure((0, 1), weight=1)
        self.start_button = ttk.Button(buttons, text="Start OP25", command=self._start)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.stop_button = ttk.Button(buttons, text="Stop", command=self._stop, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        display = ttk.Frame(self, padding=(0, 10, 10, 10))
        display.grid(row=0, column=1, sticky="nsew")
        display.columnconfigure(0, weight=1)
        display.rowconfigure(1, weight=1)

        status = ttk.LabelFrame(display, text="Decoded Control Channel", padding=10)
        status.grid(row=0, column=0, sticky="ew")
        status.columnconfigure(1, weight=1)
        self.state_var = tk.StringVar(value="Stopped")
        self.command_var = tk.StringVar(value="-")
        self.wacn_var = tk.StringVar(value="-")
        self.system_var = tk.StringVar(value="-")
        self.nac_status_var = tk.StringVar(value="-")
        self.site_var = tk.StringVar(value="-")
        self.neighbours_var = tk.StringVar(value="-")
        for row, (label, var) in enumerate(
            (
                ("State", self.state_var),
                ("Command", self.command_var),
                ("WACN", self.wacn_var),
                ("System", self.system_var),
                ("NAC", self.nac_status_var),
                ("RFSS/Site", self.site_var),
                ("Neighbours", self.neighbours_var),
            )
        ):
            ttk.Label(status, text=label).grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(status, textvariable=var, wraplength=760, justify="left").grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)

        log_frame = ttk.LabelFrame(display, text="OP25 Output", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.output_text = tk.Text(log_frame, wrap="word", height=24, background="#101418", foreground="#d7e0ea", insertbackground="#d7e0ea")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.output_text.yview)
        self.output_text.configure(yscrollcommand=scrollbar.set)
        self.output_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _config(self) -> OP25Config:
        return OP25Config(
            op25_apps_dir=self.op25_dir_var.get(),
            python_command=self.python_var.get(),
            device_args=self.device_args_var.get(),
            frequency_mhz=float(self.frequency_var.get()),
            sample_rate_hz=int(float(self.sample_rate_var.get())),
            gains=self.gains_var.get(),
            fine_tune=int(float(self.fine_tune_var.get())),
            nac=self.nac_var.get(),
            modulation=self.modulation_var.get(),
            system_name=self.system_name_var.get() or "P25",
            terminal_url=self.terminal_url_var.get(),
            verbosity=int(float(self.verbosity_var.get())),
            audio_enabled=bool(self.audio_var.get()),
            plots=self.plots_var.get(),
        )

    def _start(self) -> None:
        if self._process is not None:
            return
        try:
            self._save_settings()
            config = self._config()
            trunk_path = write_trunk_tsv(config, self._work_dir)
            command = build_op25_command(config, trunk_path)
            self._append_output(f"$ {' '.join(command)}\n")
            self._append_output(f"# trunk.tsv: {trunk_path}\n")
            self._process = subprocess.Popen(
                command,
                cwd=str(Path(config.op25_apps_dir).expanduser()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._process = None
            messagebox.showerror("Unable to start OP25", str(exc))
            return
        self._status = OP25Status()
        self.state_var.set("OP25 running")
        self.command_var.set(" ".join(command))
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._reader_thread = threading.Thread(target=self._read_process_output, name="op25-output", daemon=True)
        self._reader_thread.start()

    def _stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2.0)
        self._process = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        if self.state_var.get() != "Stopped":
            self.state_var.set("Stopped")

    def _read_process_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self._output_queue.put(line)
        return_code = process.wait()
        self._output_queue.put(f"\n[OP25 exited with code {return_code}]\n")

    def _process_output(self) -> None:
        while True:
            try:
                line = self._output_queue.get_nowait()
            except queue.Empty:
                break
            self._append_output(line)
            self._status = parse_op25_status_line(line, self._status)
            self._update_status_fields()
        if self._process is not None and self._process.poll() is not None:
            self._process = None
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.state_var.set("OP25 stopped")
        self.after(100, self._process_output)

    def _append_output(self, text: str) -> None:
        self.output_text.insert("end", text)
        self.output_text.see("end")

    def _update_status_fields(self) -> None:
        self.wacn_var.set(self._status.wacn or "-")
        self.system_var.set(self._status.system_id or "-")
        self.nac_status_var.set(self._status.nac or "-")
        if self._status.rfss or self._status.site:
            self.site_var.set(f"{self._status.rfss or '-'}/{self._status.site or '-'}")
        else:
            self.site_var.set("-")
        self.neighbours_var.set("; ".join(self._status.neighbours) or "-")

    def _save_settings(self) -> None:
        self._settings.update(
            {
                "op25_apps_dir": self.op25_dir_var.get(),
                "op25_python": self.python_var.get(),
                "op25_device_args": self.device_args_var.get(),
                "center_frequency_mhz": float(self.frequency_var.get()),
                "op25_sample_rate_hz": int(float(self.sample_rate_var.get())),
                "op25_gains": self.gains_var.get(),
                "op25_fine_tune": int(float(self.fine_tune_var.get())),
                "op25_nac": self.nac_var.get(),
                "op25_modulation": self.modulation_var.get(),
                "op25_system_name": self.system_name_var.get(),
                "op25_terminal_url": self.terminal_url_var.get(),
                "op25_verbosity": int(float(self.verbosity_var.get())),
                "op25_plots": self.plots_var.get(),
                "op25_audio_enabled": bool(self.audio_var.get()),
            }
        )
        save_settings(self._settings)

    def _on_close(self) -> None:
        self._stop()
        self.destroy()


def main() -> None:
    app = P25ReceiverApp()
    app.mainloop()
