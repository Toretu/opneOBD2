"""A professional cross-platform Tkinter GUI for OBD-II diagnostics.

Presents a VCDS/OBDeleven-style full-system view: a list of control modules,
each expandable to show its diagnostic trouble codes with status and
descriptions. Works against real adapters (serial / Bluetooth SPP / BLE) and
against a fully configurable **virtual car** — pick which components are faulty
(glow plugs, ABS sensors, airbag, ...) and preview exactly how a real scan looks.

Uses only the Python standard library (tkinter). All adapter I/O runs on a
background thread so the window stays responsive.
"""

from __future__ import annotations

import queue
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import uds  # noqa: F401  (kept for external callers/back-compat)
from .mockvehicle import (
    FAULT_CATALOG,
    MockFault,
    MockVehicle,
    MockVehicleConnection,
    catalog_module_name,
    default_mock_vehicle,
    vehicle_from_selection,
)
from .canbus import discover_can_adapters
from .scan import Tp20Scanner, UdsScanner

SOURCE_MOCK = "Virtual car (mock)"
SOURCE_SERIAL = "Serial port"
SOURCE_SPP = "Bluetooth Classic (SPP)"
SOURCE_BLE = "Bluetooth LE"
SOURCE_CAN = "Raw CAN (VW TP2.0)"
ERROR_LOG_PATH = Path.cwd() / "obd2reader-error.log"


def _append_error_log(error: BaseException) -> Optional[str]:
    try:
        with ERROR_LOG_PATH.open("a", encoding="utf-8") as log_file:
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            log_file.write(f"[{timestamp}]\n")
            log_file.write(f"GUI warning: {error}\n\n")
            log_file.writelines(traceback.format_exception(error))
            log_file.write("\n")
    except OSError:
        return None
    return str(ERROR_LOG_PATH)


def _code_lookup_url(module_name: str, code: str) -> str:
    query = f"Volkswagen Sharan 2012 {module_name} {code} fault code"
    return "https://www.google.com/search?" + urlencode({"q": query})

# Colour palette (theme-neutral, works on light backgrounds).
COL_BG = "#f4f6f8"
COL_HEADER = "#1f2a37"
COL_HEADER_TEXT = "#ffffff"
COL_ACCENT = "#2563eb"
COL_OK = "#15803d"
COL_FAULT = "#b91c1c"
COL_PASSIVE = "#6b7280"
COL_ACTIVE_BG = "#fdecec"


def _create_app_icon(root: tk.Tk) -> tk.PhotoImage:
    """Draw a small diagnostic connector icon using Tk's built-in image API."""

    image = tk.PhotoImage(master=root, width=64, height=64)
    image.put("#102b3a", to=(0, 0, 64, 64))

    def rounded_rect(x0: int, y0: int, x1: int, y1: int, radius: int, color: str) -> None:
        for y in range(y0, y1):
            offset = max(0, radius - min(y - y0, y1 - y - 1))
            image.put(color, to=(x0 + offset, y, x1 - offset, y + 1))

    def rect(x0: int, y0: int, x1: int, y1: int, color: str) -> None:
        image.put(color, to=(x0, y0, x1, y1))

    def circle(cx: int, cy: int, radius: int, color: str) -> None:
        for y in range(cy - radius, cy + radius + 1):
            half_width = int((radius * radius - (y - cy) ** 2) ** 0.5)
            rect(cx - half_width, y, cx + half_width + 1, y + 1, color)

    # Connector shell and face.
    rounded_rect(13, 13, 51, 43, 6, "#35d0b2")
    rounded_rect(18, 18, 46, 37, 3, "#e7fbf7")
    for x in (22, 29, 36, 42):
        circle(x, 25, 1, "#167a74")
        circle(x, 31, 1, "#167a74")
    circle(45, 17, 2, "#f5c451")

    # Cable stem and heartbeat trace.
    rect(29, 43, 35, 49, "#35d0b2")
    rect(31, 48, 34, 53, "#35d0b2")
    rect(18, 52, 25, 55, "#35d0b2")
    rect(24, 50, 27, 55, "#35d0b2")
    rect(26, 48, 29, 52, "#35d0b2")
    rect(29, 47, 32, 50, "#35d0b2")
    rect(33, 48, 36, 52, "#35d0b2")
    rect(36, 51, 39, 55, "#35d0b2")
    rect(39, 52, 46, 55, "#35d0b2")
    return image


def _list_ports() -> List[str]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [port.device for port in list_ports.comports()]


class ObdGui:
    """Main diagnostics window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("OBD-II Diagnostics")
        self.root.minsize(860, 560)
        self.root.configure(bg=COL_BG)
        self._app_icon = _create_app_icon(root)
        self.root.iconphoto(True, self._app_icon)

        self._results: "queue.Queue" = queue.Queue()
        self._scanner = None
        self._connected = False
        self._reports: list = []
        self._ble_devices: Dict[str, str] = {}
        self._can_devices: Dict[str, tuple[str, str]] = {}
        self._mock_vehicle: MockVehicle = default_mock_vehicle()

        self._init_style()
        self._build_header()
        self._build_toolbar()
        self._build_tree()
        self._build_action_bar()
        self._build_status_bar()

        self._on_source_change()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- Styling -----------------------------------------------------------

    def _init_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=COL_BG)
        style.configure("TLabel", background=COL_BG, font=("TkDefaultFont", 10))
        style.configure("Title.TLabel", background=COL_HEADER, foreground=COL_HEADER_TEXT,
                        font=("TkDefaultFont", 15, "bold"))
        style.configure("Subtitle.TLabel", background=COL_HEADER, foreground="#cbd5e1",
                        font=("TkDefaultFont", 9))
        style.configure("Status.TLabel", background="#e5e7eb", foreground="#111827",
                        padding=(10, 4))
        style.configure("Accent.TButton", font=("TkDefaultFont", 10, "bold"))
        style.configure("Treeview", rowheight=24, font=("TkDefaultFont", 10))
        style.configure("Treeview.Heading", font=("TkDefaultFont", 10, "bold"))

    # -- Layout ------------------------------------------------------------

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=COL_HEADER)
        header.pack(fill="x", side="top")
        inner = tk.Frame(header, bg=COL_HEADER)
        inner.pack(fill="x", padx=16, pady=10)
        ttk.Label(inner, text="OBD-II Diagnostics", style="Title.TLabel").pack(anchor="w")
        ttk.Label(inner, text="Full-system scan • read & clear trouble codes",
                  style="Subtitle.TLabel").pack(anchor="w")

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        bar.pack(fill="x")

        ttk.Label(bar, text="Source:").pack(side="left")
        self.source_var = tk.StringVar(value=SOURCE_CAN)
        self.source_combo = ttk.Combobox(
            bar, textvariable=self.source_var, width=22, state="readonly",
            values=[SOURCE_MOCK, SOURCE_SERIAL, SOURCE_SPP, SOURCE_BLE, SOURCE_CAN],
        )
        self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_source_change())
        self.source_combo.pack(side="left", padx=(4, 10))

        self.device_label = ttk.Label(bar, text="Device:")
        self.device_label.pack(side="left")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(bar, textvariable=self.device_var, width=26, state="readonly")
        self.device_combo.pack(side="left", padx=(4, 6))

        self.refresh_btn = ttk.Button(bar, text="Refresh", command=self._refresh_devices)
        self.refresh_btn.pack(side="left")

        self.configure_btn = ttk.Button(bar, text="Configure faults…", command=self._open_mock_chooser)
        self.configure_btn.pack(side="left", padx=(6, 0))

        self.connect_btn = ttk.Button(bar, text="Connect & scan", style="Accent.TButton",
                                      command=self._on_connect_toggle)
        self.connect_btn.pack(side="right")

    def _build_tree(self) -> None:
        frame = ttk.Frame(self.root, padding=(12, 4))
        frame.pack(fill="both", expand=True)

        columns = ("status", "ftb", "description")
        self.tree = ttk.Treeview(frame, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Module / Code")
        self.tree.heading("status", text="Status")
        self.tree.heading("ftb", text="FTB")
        self.tree.heading("description", text="Description")
        self.tree.column("#0", width=260, anchor="w", stretch=False)
        self.tree.column("status", width=150, anchor="w", stretch=False)
        self.tree.column("ftb", width=50, anchor="center", stretch=False)
        self.tree.column("description", width=360, anchor="w")

        self.tree.tag_configure("module_ok", foreground=COL_OK)
        self.tree.tag_configure("module_fault", foreground=COL_FAULT, font=("TkDefaultFont", 10, "bold"))
        self.tree.tag_configure("active", background=COL_ACTIVE_BG, foreground=COL_FAULT)
        self.tree.tag_configure("passive", foreground=COL_PASSIVE)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_selection)

    def _build_action_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=(12, 4))
        bar.pack(fill="x")
        self.rescan_btn = ttk.Button(bar, text="Rescan", command=self._on_rescan)
        self.rescan_btn.pack(side="left")
        self.clear_mod_btn = ttk.Button(bar, text="Clear selected module", command=self._on_clear_selected)
        self.clear_mod_btn.pack(side="left", padx=(8, 0))
        self.clear_all_btn = ttk.Button(bar, text="Clear all modules", command=self._on_clear_all)
        self.clear_all_btn.pack(side="left", padx=(8, 0))
        self.lookup_btn = ttk.Button(
            bar, text="Search code online", command=self._on_lookup_code, state="disabled"
        )
        self.lookup_btn.pack(side="left", padx=(8, 0))
        self.export_btn = ttk.Button(bar, text="Export report…", command=self._on_export)
        self.export_btn.pack(side="right")
        self._set_actions_enabled(False)

    def _build_status_bar(self) -> None:
        wrap = tk.Frame(self.root, bg="#e5e7eb")
        wrap.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Select a source and click Connect & scan.")
        ttk.Label(wrap, textvariable=self.status_var, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(wrap, mode="indeterminate", length=140)

    # -- Source / device handling -----------------------------------------

    def _on_source_change(self) -> None:
        source = self.source_var.get()
        is_mock = source == SOURCE_MOCK
        is_can = source == SOURCE_CAN
        self.configure_btn.configure(state="normal" if is_mock else "disabled")
        # CAN uses a free-text channel name; others pick from a discovered list.
        self.device_combo.configure(state="disabled" if is_mock else ("normal" if is_can else "readonly"))
        self.refresh_btn.configure(
            state="disabled" if is_mock else "normal",
            text="Scan" if source == SOURCE_BLE else "Refresh",
        )
        self.device_var.set("")
        self.device_combo["values"] = []
        self._ble_devices = {}
        if source == SOURCE_SERIAL:
            self._refresh_devices()
            self.status_var.set("Select a serial port, then Connect & scan.")
        elif source == SOURCE_SPP:
            self._refresh_devices()
            self.status_var.set("Select a paired adapter, then Connect & scan.")
        elif source == SOURCE_BLE:
            self.status_var.set("Click Scan to find Bluetooth LE adapters.")
        elif source == SOURCE_CAN:
            self._refresh_can()
        else:
            n = self._mock_vehicle.total_faults()
            self.status_var.set(f"Virtual car ready with {n} fault(s). Configure faults or Connect & scan.")

    def _refresh_devices(self) -> None:
        source = self.source_var.get()
        if source == SOURCE_BLE:
            self._scan_ble()
            return
        if source == SOURCE_SPP:
            self._refresh_spp()
            return
        if source == SOURCE_CAN:
            self._refresh_can()
            return
        ports = _list_ports()
        self.device_combo["values"] = ports
        if ports and not self.device_var.get():
            self.device_var.set(ports[0])
        if not ports:
            self.status_var.set("No serial ports found. Pair your adapter or try the virtual car.")

    def _refresh_can(self) -> None:
        adapters = discover_can_adapters()
        self._can_devices = {adapter.label: (adapter.kind, adapter.channel) for adapter in adapters}
        labels = [adapter.label for adapter in adapters]
        self.device_combo["values"] = labels

        canable = next((a for a in adapters if a.kind == "slcan"), None)
        socketcan = next((a for a in adapters if a.kind == "socketcan"), None)
        if canable is not None:
            self.device_var.set(canable.label)
            self.status_var.set(
                f"CANable detected at {canable.channel}. Connect the OBD cable before scanning the car."
            )
        elif socketcan is not None:
            self.device_var.set(socketcan.label)
            self.status_var.set(f"SocketCAN interface {socketcan.channel} detected.")
        else:
            self.device_var.set("can0")
            self.status_var.set(
                "No CANable or SocketCAN interface detected. Refresh after plugging in the adapter."
            )

    def _refresh_spp(self) -> None:
        def work():
            from .spp import list_paired_spp_devices
            return list_paired_spp_devices()

        def done(devices):
            self._ble_devices = {}
            labels = []
            for address, name in devices:
                label = f"{name} [{address}]"
                self._ble_devices[label] = address
                labels.append(label)
            self.device_combo["values"] = labels
            if labels:
                self.device_var.set(labels[0])
                self.status_var.set(f"Found {len(labels)} paired adapter(s).")
            else:
                self.status_var.set("No paired Bluetooth Classic adapters found.")

        self._run_async(work, done, "Looking for paired adapters…")

    def _scan_ble(self) -> None:
        def work():
            from .ble import scan_bluetooth
            return scan_bluetooth(8.0)

        def done(devices):
            self._ble_devices = {}
            labels = []
            for address, name in devices:
                label = f"{name} [{address}]"
                self._ble_devices[label] = address
                labels.append(label)
            self.device_combo["values"] = labels
            if labels:
                self.device_var.set(labels[0])
                self.status_var.set(f"Found {len(labels)} Bluetooth LE device(s).")
            else:
                self.status_var.set("No Bluetooth LE devices found.")

        self._run_async(work, done, "Scanning for Bluetooth LE devices…")

    # -- Mock fault chooser ------------------------------------------------

    def _open_mock_chooser(self) -> None:
        chooser = MockFaultChooser(self.root, self._mock_vehicle)
        self.root.wait_window(chooser.top)
        if chooser.result is not None:
            self._mock_vehicle = chooser.result
            n = self._mock_vehicle.total_faults()
            self.status_var.set(f"Virtual car updated: {n} fault(s) selected. Connect & scan to view.")

    # -- Connection / scan -------------------------------------------------

    def _build_scanner(self):
        source = self.source_var.get()
        if source == SOURCE_MOCK:
            return UdsScanner(MockVehicleConnection(self._mock_vehicle))
        if source == SOURCE_SERIAL:
            port = self.device_var.get()
            if not port:
                raise RuntimeError("Select a serial port first.")
            from .elm327 import SerialConnection
            return UdsScanner(SerialConnection(port))
        if source == SOURCE_SPP:
            address = self._ble_devices.get(self.device_var.get(), self.device_var.get())
            if not address:
                raise RuntimeError("Select a paired adapter first.")
            from .spp import RfcommConnection
            return UdsScanner(RfcommConnection(address))
        if source == SOURCE_BLE:
            address = self._ble_devices.get(self.device_var.get(), self.device_var.get())
            if not address:
                raise RuntimeError("Scan and select a Bluetooth LE device first.")
            from .ble import BleConnection
            return UdsScanner(BleConnection(address))
        if source == SOURCE_CAN:
            selection = self.device_var.get().strip() or "can0"
            detected = self._can_devices.get(selection)
            from .canbus import SlcanCanBus, SocketCanBus
            if detected is not None and detected[0] == "slcan":
                return Tp20Scanner(SlcanCanBus(channel=detected[1]))
            channel = detected[1] if detected is not None else selection
            return Tp20Scanner(SocketCanBus(channel=channel))
        raise RuntimeError("Unknown source")

    def _on_connect_toggle(self) -> None:
        if self._connected:
            self._disconnect()
            return

        def work():
            scanner = self._build_scanner()
            reports = scanner.scan()
            return scanner, reports

        def done(payload):
            self._scanner, reports = payload
            self._connected = True
            self.connect_btn.configure(text="Disconnect")
            self._set_source_locked(True)
            self._populate(reports)

        self._run_async(work, done, "Connecting and scanning modules…")

    def _disconnect(self) -> None:
        if self._scanner is not None:
            try:
                self._scanner.close()
            except Exception:
                pass
        self._scanner = None
        self._connected = False
        self._reports = []
        self.connect_btn.configure(text="Connect & scan")
        self._set_source_locked(False)
        self.tree.delete(*self.tree.get_children())
        self._set_actions_enabled(False)
        self.status_var.set("Disconnected.")

    def _on_rescan(self) -> None:
        if not self._connected or self._scanner is None:
            return
        scanner = self._scanner
        self._run_async(scanner.scan,
                        lambda reports: self._populate(reports), "Rescanning modules…")

    def _populate(self, reports) -> None:
        self._reports = list(reports)
        self.lookup_btn.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        total = 0
        for report in self._reports:
            active = sum(1 for d in report.dtcs if d.active)
            count = len(report.dtcs)
            total += count
            if count == 0:
                summary, tag = "OK — no faults", "module_ok"
            else:
                summary = f"{count} code(s)" + (f" · {active} active" if active else "")
                tag = "module_fault"
            parent = self.tree.insert(
                "", "end", text=f"{report.name}  [{report.request_id:03X}]",
                values=(summary, "", ""), tags=(tag,), open=count > 0,
            )
            for dtc in report.dtcs:
                status = "● active" if dtc.active else "stored"
                self.tree.insert(
                    parent, "end", text=f"   {dtc.code}",
                    values=(status, f"{dtc.failure_type:02X}", dtc.description),
                    tags=("active" if dtc.active else "passive",),
                )

        self._set_actions_enabled(True)
        modules = len(self._reports)
        if total == 0:
            self.status_var.set(f"Scan complete — {modules} module(s) responded, no fault codes.")
        else:
            self.status_var.set(f"Scan complete — {total} code(s) across {modules} module(s).")

    # -- Clearing ----------------------------------------------------------

    def _selected_module_report(self):
        sel = self.tree.selection()
        if not sel:
            return None
        node = sel[0]
        parent = self.tree.parent(node)
        module_node = parent if parent else node
        index = self.tree.index(module_node)
        return self._reports[index] if index < len(self._reports) else None

    def _selected_dtc(self):
        selection = self.tree.selection()
        if not selection:
            return None
        node = selection[0]
        parent = self.tree.parent(node)
        if not parent:
            return None
        report_index = self.tree.index(parent)
        dtc_index = self.tree.index(node)
        if report_index >= len(self._reports):
            return None
        report = self._reports[report_index]
        if dtc_index >= len(report.dtcs):
            return None
        return report, report.dtcs[dtc_index]

    def _on_tree_selection(self, _event=None) -> None:
        if not hasattr(self, "lookup_btn"):
            return
        self.lookup_btn.configure(
            state="normal" if self._selected_dtc() is not None else "disabled"
        )

    def _on_lookup_code(self) -> None:
        selected = self._selected_dtc()
        if selected is None:
            return
        report, dtc = selected
        url = _code_lookup_url(report.name, dtc.code)
        try:
            opened = webbrowser.open_new_tab(url)
        except webbrowser.Error as exc:
            messagebox.showerror("Online lookup failed", str(exc))
            return
        if not opened:
            messagebox.showerror("Online lookup failed", "Could not open the web browser.")
            return
        self.status_var.set(f"Opened online lookup for {dtc.code}.")

    def _on_clear_selected(self) -> None:
        report = self._selected_module_report()
        if report is None:
            messagebox.showinfo("Clear module", "Select a module in the list first.")
            return
        if not messagebox.askyesno("Clear module", f"Clear all codes in {report.name}?"):
            return
        scanner = self._scanner
        request_id = report.request_id

        def work():
            scanner.clear(request_id)
            return scanner.scan()

        self._run_async(work, lambda reports: self._populate(reports),
                        f"Clearing {report.name}…")

    def _on_clear_all(self) -> None:
        if not self._reports:
            return
        if not messagebox.askyesno("Clear all", "Clear codes in ALL responding modules?"):
            return
        scanner = self._scanner
        request_ids = [r.request_id for r in self._reports]

        def work():
            for rid in request_ids:
                scanner.clear(rid)
            return scanner.scan()

        self._run_async(work, lambda reports: self._populate(reports), "Clearing all modules…")

    def _on_export(self) -> None:
        if not self._reports:
            messagebox.showinfo("Export", "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save diagnostic report", defaultextension=".txt",
            filetypes=[("Text report", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._report_text())
            self.status_var.set(f"Report saved to {path}")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))

    def _report_text(self) -> str:
        lines = ["OBD-II Diagnostic Report", "=" * 40, ""]
        total = 0
        for report in self._reports:
            lines.append(f"{report.name}  [{report.request_id:03X}]")
            if not report.dtcs:
                lines.append("    No stored codes.")
            for dtc in report.dtcs:
                total += 1
                mark = "*" if dtc.active else " "
                lines.append(f"  {mark} {dtc.code}-{dtc.failure_type:02X}  "
                             f"[{dtc.status:02X}] {dtc.description}")
            lines.append("")
        lines.append(f"Total: {total} code(s) across {len(self._reports)} module(s).")
        lines.append("'*' marks confirmed / warning-light codes.")
        return "\n".join(lines)

    # -- Async helper ------------------------------------------------------

    def _run_async(self, work: Callable[[], object], on_done: Callable[[object], None], busy: str) -> None:
        self.status_var.set(busy)
        self._set_busy(True)

        def worker():
            try:
                self._results.put(("ok", work(), on_done))
            except Exception as exc:  # surfaced in the UI thread
                log_path = _append_error_log(exc)
                self._results.put(("error", (exc, log_path), on_done))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(50, self._poll)

    def _poll(self) -> None:
        try:
            kind, payload, on_done = self._results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._poll)
            return
        self._set_busy(False)
        if kind == "error":
            error, log_path = payload
            if log_path:
                message = f"{error}\n\nFull details saved to:\n{log_path}"
                self.status_var.set(f"Error details saved to {log_path}")
            else:
                message = f"{error}\n\nCould not write the error log."
                self.status_var.set(f"Error: {error}")
            messagebox.showerror("Error", message)
            return
        on_done(payload)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self.progress.pack(side="right", padx=8, pady=2)
            self.progress.start(12)
            self.connect_btn.configure(state="disabled")
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.connect_btn.configure(state="normal")

    def _set_source_locked(self, locked: bool) -> None:
        state = "disabled" if locked else "readonly"
        self.source_combo.configure(state=state)
        device_state = "disabled" if locked else ("normal" if self.source_var.get() == SOURCE_CAN else "readonly")
        self.device_combo.configure(state=device_state)
        self.refresh_btn.configure(state="disabled" if locked else "normal")
        is_mock = self.source_var.get() == SOURCE_MOCK
        self.configure_btn.configure(state="disabled" if (locked or not is_mock) else "normal")

    def _set_actions_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for btn in (self.rescan_btn, self.clear_mod_btn, self.clear_all_btn, self.export_btn):
            btn.configure(state=state)

    def _on_close(self) -> None:
        self._disconnect()
        self.root.destroy()


class MockFaultChooser:
    """Modal dialog to pick which components of the virtual car are faulty."""

    def __init__(self, parent: tk.Misc, current: MockVehicle) -> None:
        self.result: Optional[MockVehicle] = None
        self._vars: Dict[int, List[tuple]] = {}

        self.top = tk.Toplevel(parent)
        self.top.title("Configure virtual car faults")
        self.top.transient(parent)
        self.top.grab_set()
        self.top.configure(bg=COL_BG)
        self.top.minsize(440, 420)

        selected = self._current_codes(current)

        ttk.Label(self.top, text="Tick the components you want to simulate as faulty:",
                  padding=(12, 10)).pack(anchor="w")

        body = ttk.Frame(self.top, padding=(12, 0))
        body.pack(fill="both", expand=True)

        canvas = tk.Canvas(body, bg=COL_BG, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        for request_id, faults in FAULT_CATALOG.items():
            group = ttk.LabelFrame(inner, text=catalog_module_name(request_id), padding=(10, 6))
            group.pack(fill="x", expand=True, pady=6, padx=2)
            row = []
            for fault in faults:
                var = tk.BooleanVar(value=fault.code in selected.get(request_id, set()))
                text = f"{fault.title}   ({fault.code})"
                ttk.Checkbutton(group, text=text, variable=var).pack(anchor="w")
                row.append((var, fault))
            self._vars[request_id] = row

        btns = ttk.Frame(self.top, padding=(12, 10))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text="Clear all", command=self._clear_all).pack(side="left")
        ttk.Button(btns, text="Cancel", command=self.top.destroy).pack(side="right")
        ttk.Button(btns, text="Apply", style="Accent.TButton", command=self._apply).pack(side="right", padx=(0, 8))

    @staticmethod
    def _current_codes(vehicle: MockVehicle) -> Dict[int, set]:
        result: Dict[int, set] = {}
        for module in vehicle.modules:
            result[module.request_id] = {f.code for f in module.faults}
        return result

    def _clear_all(self) -> None:
        for row in self._vars.values():
            for var, _fault in row:
                var.set(False)

    def _apply(self) -> None:
        selection: Dict[int, List[MockFault]] = {}
        for request_id, row in self._vars.items():
            chosen = [fault for var, fault in row if var.get()]
            if chosen:
                selection[request_id] = chosen
        self.result = vehicle_from_selection(selection)
        self.top.destroy()


def main() -> int:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Unable to start GUI (no display available?): {exc}")
        return 1
    ObdGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
