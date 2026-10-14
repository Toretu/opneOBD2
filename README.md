# OBD-II Reader for VW Sharan (Bluetooth ELM327)

A small, cross-platform (Windows + Linux) command-line tool to read and clear
diagnostic trouble codes (DTCs) from a VW Sharan using a **Bluetooth ELM327**
adapter. It decodes generic OBD-II codes plus common VW TDI faults such as
**glow-plug** and **ABS/EBS brake** errors.

## How the Bluetooth connection works

There are two kinds of Bluetooth ELM327 adapter:

**Bluetooth Classic (SPP)** — appears to the OS as a virtual serial port:

- **Windows:** pair the adapter in Bluetooth settings; it becomes a `COM` port
  (e.g. `COM5`).
- **Linux:** bind it to an rfcomm device:
  ```bash
  sudo rfcomm bind 0 <ADAPTER_MAC>   # creates /dev/rfcomm0
  ```

**Bluetooth Low Energy (BLE)** — does *not* create a serial port. These connect
directly over GATT using the optional `bleak` backend (`--bluetooth <ADDRESS>`
or the **Bluetooth (BLE)** source in the GUI). Install the extra:

```bash
python -m pip install -e ".[ble]"
```

## Install

```bash
python -m pip install -e .            # core (serial / Bluetooth Classic)
python -m pip install -e ".[ble]"     # add direct Bluetooth LE support
```

## Usage

```bash
# List available serial/Bluetooth ports
obd2 ports

# Scan for nearby Bluetooth LE adapters (needs the [ble] extra)
obd2 scan

# Read all stored, pending and permanent codes
obd2 read --port COM5             # Windows serial / Bluetooth Classic
obd2 read --port /dev/rfcomm0     # Linux serial / Bluetooth Classic
obd2 read --bluetooth AA:BB:CC:DD:EE:FF   # direct Bluetooth LE

# Clear all codes
obd2 clear --port COM5

# Clear specific codes only (best-effort, manufacturer UDS request)
obd2 clear --port COM5 --codes P0380 C1011

# Try it without any hardware using the built-in simulated vehicle
obd2 read --mock
obd2 clear --mock --codes P0380
```

### Graphical interface

A cross-platform GUI (built with Tkinter, no extra dependencies) is available:

```bash
obd2-gui                 # launch the window
python -m obd2reader.gui # equivalent
```

Choose a **Source** (Serial port, Bluetooth BLE, or Mock vehicle). For BLE click
**Scan** to discover adapters, pick one, then **Connect** to read codes. Use
**Clear selected** or **Clear all** to clear. All I/O runs on a background thread
so the window stays responsive.

### Example output (`obd2 read --mock`)

```
Found 6 diagnostic trouble code(s):

  [   stored] P0380  Glow plug / heater circuit A malfunction
  [   stored] P0671  Cylinder 1 glow plug circuit malfunction
  [   stored] P2002  Diesel particulate filter (DPF) efficiency below threshold
  [   stored] U0100  Lost communication with engine control module (ECM)
  [   stored] C1011  ABS/EBS control unit fault
  [  pending] P0401  Exhaust gas recirculation (EGR) flow insufficient
```

## Notes on clearing codes

- `clear` (no `--codes`) uses standard OBD-II **mode 04**, which clears *all*
  emission-related codes at once.
- `clear --codes ...` uses the manufacturer **UDS service 0x14**
  (ClearDiagnosticInformation). Standard OBD-II has no way to clear a single
  code, so this is best-effort and depends on the ECU accepting the request.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
```
