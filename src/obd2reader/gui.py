"""A simple cross-platform Tkinter GUI for the OBD-II reader.

Uses only the standard library (tkinter), so it runs on Windows and Linux
without extra dependencies. Serial/Bluetooth I/O runs on a background thread so
the window stays responsive.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk

from .elm327 import ELM327, SerialConnection
from .mock import MockConnection
from .reader import OBD2Reader


def _list_ports() -> List[str]:
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return [port.device for port in list_ports.comports()]


class ObdGui:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("OBD-II Reader")
        self.root.minsize(640, 420)

        self._reader: Optional[OBD2Reader] = None
        self._elm: Optional[ELM327] = None
        self._results: "queue.Queue" = queue.Queue()
        self._ble_devices: dict[str, str] = {}

        self._build_connection_bar()
        self._build_code_table()
        self._build_action_bar()
        self._build_status_bar()

        self._on_source_change()
        self._set_connected(False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI construction ---------------------------------------------------

    def _build_connection_bar(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        frame.pack(fill="x")

        ttk.Label(frame, text="Source:").pack(side="left")
        self.source_var = tk.StringVar(value="Serial port")
        self.source_combo = ttk.Combobox(
            frame,
            textvariable=self.source_var,
            width=15,
            state="readonly",
            values=["Serial port", "Bluetooth (BLE)", "Mock vehicle"],
        )
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_source_change())
        self.source_combo.pack(side="left", padx=(4, 8))

        ttk.Label(frame, text="Device:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var, width=28, state="readonly")
        self.port_combo.pack(side="left", padx=(4, 4))

        self.refresh_btn = ttk.Button(frame, text="Refresh", command=self._refresh_devices)
        self.refresh_btn.pack(side="left", padx=(0, 10))

        self.connect_btn = ttk.Button(frame, text="Connect", command=self._on_connect_toggle)
        self.connect_btn.pack(side="right")

    def _build_code_table(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 4))
        frame.pack(fill="both", expand=True)

        columns = ("code", "status", "description")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("code", text="Code")
        self.tree.heading("status", text="Status")
        self.tree.heading("description", text="Description")
        self.tree.column("code", width=80, anchor="w", stretch=False)
        self.tree.column("status", width=90, anchor="w", stretch=False)
        self.tree.column("description", width=420, anchor="w")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_action_bar(self) -> None:
        frame = ttk.Frame(self.root, padding=(10, 4))
        frame.pack(fill="x")

        self.read_btn = ttk.Button(frame, text="Read codes", command=self._on_read)
        self.read_btn.pack(side="left")

        self.clear_sel_btn = ttk.Button(frame, text="Clear selected", command=self._on_clear_selected)
        self.clear_sel_btn.pack(side="left", padx=(8, 0))

        self.clear_all_btn = ttk.Button(frame, text="Clear all", command=self._on_clear_all)
        self.clear_all_btn.pack(side="left", padx=(8, 0))

    def _build_status_bar(self) -> None:
        self.status_var = tk.StringVar(value="Not connected.")
        status = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 3))
        status.pack(fill="x", side="bottom")

    # -- Connection state --------------------------------------------------

    def _set_connected(self, connected: bool) -> None:
        self._connected = connected
        self.connect_btn.configure(text="Disconnect" if connected else "Connect")
        action_state = "normal" if connected else "disabled"
        for btn in (self.read_btn, self.clear_sel_btn, self.clear_all_btn):
            btn.configure(state=action_state)

        if connected:
            self.source_combo.configure(state="disabled")
            self.port_combo.configure(state="disabled")
            self.refresh_btn.configure(state="disabled")
        else:
            self.source_combo.configure(state="readonly")
            self._apply_source_widget_state()

    def _apply_source_widget_state(self) -> None:
        source = self.source_var.get()
        is_mock = source == "Mock vehicle"
        self.port_combo.configure(state="disabled" if is_mock else "readonly")
        self.refresh_btn.configure(state="disabled" if is_mock else "normal")
        self.refresh_btn.configure(text="Scan" if source == "Bluetooth (BLE)" else "Refresh")

    def _on_source_change(self) -> None:
        self._apply_source_widget_state()
        source = self.source_var.get()
        self.port_var.set("")
        self.port_combo["values"] = []
        self._ble_devices = {}
        if source == "Serial port":
            self._refresh_devices()
        elif source == "Bluetooth (BLE)":
            self.status_var.set("Click Scan to search for Bluetooth LE adapters.")
        else:
            self.status_var.set("Mock vehicle selected. Click Connect to read simulated codes.")

    def _refresh_devices(self) -> None:
        if self.source_var.get() == "Bluetooth (BLE)":
            self._scan_ble()
            return
        ports = _list_ports()
        self._ble_devices = {}
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
        if not ports:
            self.status_var.set("No serial ports found. Pair your adapter, scan for BLE, or use the mock vehicle.")

    def _scan_ble(self) -> None:
        def work() -> object:
            from .ble import scan_bluetooth

            return scan_bluetooth(8.0)

        def done(devices: object) -> None:
            self._ble_devices = {}
            labels = []
            for address, name in devices:  # type: ignore[union-attr]
                label = f"{name} [{address}]"
                self._ble_devices[label] = address
                labels.append(label)
            self.port_combo["values"] = labels
            if labels:
                self.port_var.set(labels[0])
                self.status_var.set(f"Found {len(labels)} Bluetooth LE device(s). Select one and Connect.")
            else:
                self.status_var.set("No Bluetooth LE devices found. Make sure the adapter is powered and nearby.")

        self._run_async(work, done, "Scanning for Bluetooth LE devices...")

    # -- Background work helper -------------------------------------------

    def _run_async(self, work: Callable[[], object], on_done: Callable[[object], None], busy_message: str) -> None:
        self.status_var.set(busy_message)
        self._set_busy(True)

        def worker() -> None:
            try:
                result = work()
                self._results.put(("ok", result, on_done))
            except Exception as exc:  # surfaced to the user in the UI thread
                self._results.put(("error", exc, on_done))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(50, self._poll_results)

    def _poll_results(self) -> None:
        try:
            kind, payload, on_done = self._results.get_nowait()
        except queue.Empty:
            self.root.after(50, self._poll_results)
            return

        self._set_busy(False)
        if kind == "error":
            messagebox.showerror("OBD-II error", str(payload))
            self.status_var.set(f"Error: {payload}")
            return
        on_done(payload)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for btn in (self.connect_btn, self.read_btn, self.clear_sel_btn, self.clear_all_btn):
            btn.configure(state=state)
        if not busy and not self._connected:
            self._set_connected(False)

    # -- Actions -----------------------------------------------------------

    def _on_connect_toggle(self) -> None:
        if self._connected:
            self._disconnect()
            return

        source = self.source_var.get()
        baud = 38400

        if source == "Mock vehicle":
            def make_connection():
                return MockConnection()

            where = "mock vehicle"
        elif source == "Bluetooth (BLE)":
            label = self.port_var.get()
            address = self._ble_devices.get(label, label)
            if not address:
                messagebox.showwarning("No device", "Scan and select a Bluetooth LE device first.")
                return

            def make_connection():
                from .ble import BleConnection

                return BleConnection(address)

            where = label or address
        else:
            port = self.port_var.get()
            if not port:
                messagebox.showwarning("No port", "Select a serial port, scan for BLE, or use the mock vehicle.")
                return

            def make_connection():
                return SerialConnection(port, baudrate=baud)

            where = port

        def work() -> None:
            elm = ELM327(make_connection())
            elm.connect()
            self._elm = elm
            self._reader = OBD2Reader(elm)

        def done(_: object) -> None:
            self._set_connected(True)
            self.status_var.set(f"Connected to {where}. Reading codes...")
            self._on_read()

        self._run_async(work, done, "Connecting...")

    def _disconnect(self) -> None:
        if self._elm is not None:
            try:
                self._elm.close()
            except Exception:
                pass
        self._elm = None
        self._reader = None
        self._set_connected(False)
        self.status_var.set("Disconnected.")

    def _on_read(self) -> None:
        if self._reader is None:
            return
        reader = self._reader
        self._run_async(reader.read_dtcs, self._populate_table, "Reading codes...")

    def _populate_table(self, dtcs: object) -> None:
        self.tree.delete(*self.tree.get_children())
        dtcs = list(dtcs)  # type: ignore[arg-type]
        for dtc in dtcs:
            self.tree.insert("", "end", values=(dtc.code, dtc.status, dtc.description))
        if dtcs:
            self.status_var.set(f"Found {len(dtcs)} trouble code(s).")
        else:
            self.status_var.set("No diagnostic trouble codes. The vehicle reports no faults.")

    def _on_clear_all(self) -> None:
        if self._reader is None:
            return
        if not messagebox.askyesno("Clear all codes", "Clear ALL diagnostic trouble codes?"):
            return
        reader = self._reader

        def done(ok: object) -> None:
            self.status_var.set("All codes cleared." if ok else "Clear request was not acknowledged.")
            self._on_read()

        self._run_async(reader.clear_all, done, "Clearing all codes...")

    def _on_clear_selected(self) -> None:
        if self._reader is None:
            return
        codes = [self.tree.item(item, "values")[0] for item in self.tree.selection()]
        if not codes:
            messagebox.showinfo("Clear selected", "Select one or more codes in the list first.")
            return
        if not messagebox.askyesno("Clear selected", "Clear selected code(s):\n" + ", ".join(codes)):
            return
        reader = self._reader

        def done(cleared: object) -> None:
            cleared = list(cleared)  # type: ignore[arg-type]
            self.status_var.set("Cleared: " + ", ".join(cleared) if cleared else "No codes were cleared.")
            self._on_read()

        self._run_async(lambda: reader.clear_dtcs(codes), done, "Clearing selected codes...")

    def _on_close(self) -> None:
        self._disconnect()
        self.root.destroy()


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
