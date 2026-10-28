# OBD-II Reader (Bluetooth / serial ELM327)

A small, cross-platform (Windows + Linux) tool to read and clear diagnostic
trouble codes (DTCs) from **any OBD-II vehicle** using an ELM327 adapter over
Bluetooth or serial. It decodes standard SAE codes shared across all makes, with
extra detail for common petrol and diesel faults (misfires, catalyst, EGR,
**glow-plug**, DPF, **ABS/EBS brake** errors, and more). Codes it doesn't know by
name still get a meaningful subsystem-level description.

## How the Bluetooth connection works

There are two kinds of Bluetooth ELM327 adapter:

**Bluetooth Classic (SPP)** — the most common ELM327 type. It can appear as a
virtual serial port, or you can connect to it **directly** (no serial port
needed):

- **Windows:** pair the adapter in Bluetooth settings; it becomes a `COM` port
  (e.g. `COM5`). Use `--port COM5`.
- **Linux:** either bind it to an rfcomm device
  (`sudo rfcomm bind 0 <ADAPTER_MAC>` → `/dev/rfcomm0`, then `--port
  /dev/rfcomm0`), **or** connect directly over an RFCOMM socket with `--spp
  <ADDRESS>` — handy on modern distros where the `rfcomm` tool has been removed.
  Direct SPP needs no extra setup; pair the adapter first, then run `obd2
  spp-scan` to find it.

  > Listing paired adapters (`obd2 spp-scan`) and automatic RFCOMM-channel
  > detection use BlueZ over D-Bus, which ships with the `[ble]` extra. The
  > direct connection itself (`--spp <ADDRESS>`, optionally with `--channel N`)
  > works with the core install.

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
python -m pip install -e ".[can]"     # add raw-CAN (SocketCAN) + VW TP2.0 support
```

## Usage

```bash
# List available serial/Bluetooth ports
obd2 ports

# Scan for nearby Bluetooth LE adapters (needs the [ble] extra)
obd2 scan

# List paired Bluetooth Classic (SPP) adapters
obd2 spp-scan

# Read all stored, pending and permanent codes
obd2 read --port COM5             # Windows serial / Bluetooth Classic
obd2 read --port /dev/rfcomm0     # Linux serial / Bluetooth Classic
obd2 read --spp AA:BB:CC:DD:EE:FF          # direct Bluetooth Classic (SPP)
obd2 read --bluetooth AA:BB:CC:DD:EE:FF    # direct Bluetooth LE

# Read DTCs from ALL control modules (ABS, airbag, cluster, ...) via
# manufacturer UDS, not just the engine's emission codes
obd2 fullscan --spp AA:BB:CC:DD:EE:FF
obd2 fullscan --spp AA:BB:CC:DD:EE:FF --active-only   # hide passive history

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

It presents a **full-system, VCDS/OBDeleven-style view**: a list of control
modules (engine, ABS/ESP, airbag, instrument cluster, ...), each expandable to
show its trouble codes with status and descriptions. Active (confirmed /
warning-light) codes are highlighted; passive history is greyed out.

Workflow:

1. Pick a **Source** — Virtual car (mock), Serial port, Bluetooth Classic (SPP),
   Bluetooth LE, or **Raw CAN (VW TP2.0)** for a SocketCAN adapter. For SPP/BLE
  use **Refresh/Scan** to find the adapter; for Raw CAN click **Refresh** and
  select a detected CANable USB device or SocketCAN interface.
2. **Connect & scan** — every responding module is listed with its code count.
3. **Clear selected module** or **Clear all modules**, or **Export report…** to
   save the results as text.

All adapter I/O runs on a background thread so the window stays responsive.

#### Virtual car (no hardware needed)

The **Virtual car (mock)** source is a fully simulated vehicle you can connect to
and diagnose without any adapter. Click **Configure faults…** to open the
chooser and tick exactly which components should be faulty — individual
**glow plugs per cylinder**, **ABS wheel-speed sensor**, **airbag igniter**,
**steering angle sensor**, and more. Connect & scan to see precisely how a real
multi-module scan (and clearing codes) would look. The same simulated car powers
the CLI:

```bash
obd2 fullscan --mock      # full multi-module scan of the virtual car
```


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

## Full-system scan (`fullscan`)

`obd2 read` uses standard OBD-II **mode 03**, which only returns
*emission-related powertrain* codes from the **engine ECU**. Many dashboard
warning lights (ABS, airbag/SRS, instrument cluster, ...) are stored in
*separate* control modules that do not answer generic OBD-II queries.

`obd2 fullscan` reaches those modules directly using the manufacturer **UDS
service 0x19** (ReadDTCInformation), addressing each module on the CAN bus and
decoding its stored codes:

```bash
obd2 fullscan --spp AA:BB:CC:DD:EE:FF
obd2 fullscan --spp AA:BB:CC:DD:EE:FF --active-only   # only confirmed / warning-light codes
obd2 fullscan --spp AA:BB:CC:DD:EE:FF --discover      # probe a wider address range
obd2 fullscan --mock                                  # simulated multi-module vehicle
obd2 fullscan --brand kia --spp AA:BB:CC:DD:EE:FF     # Kia/Hyundai EV (e.g. Soul EV)
obd2 fullscan --brand kia --discover --spp AA:BB:CC:DD:EE:FF
```

Each code is shown with its UDS failure-type byte and status flags; a `*` marks
codes that are **confirmed** or currently requesting a warning light (as opposed
to passive/historic entries).

The default module map targets **VW / Audi / SEAT / Skoda (VAG)** 11-bit CAN
addresses. `--brand kia` switches to the Kia/Hyundai EV map (VMCU, BMS, charger,
DC-DC, cluster, TPMS; reply ID = request + 8), taken from the
[OBD-PIDs-for-HKMC-EVs](https://github.com/JejuSoul/OBD-PIDs-for-HKMC-EVs)
project. ABS, airbag and parking-sensor addresses are not documented there; use
`--discover` to probe 0x780-0x7E7. The Kia mode is untested on a real car.
On other makes, `fullscan` simply reports whichever modules respond.

**Limitation:** on some vehicles certain chassis modules (ABS/ESP, steering,
gateway) sit on a *separate bus behind a gateway* and are **not reachable with a
generic ELM327** \u2014 they require manufacturer-specific routing (e.g. VCDS /
OBDeleven for VAG). `fullscan` reads every module the adapter *can* reach, but
cannot read modules the gateway does not expose to generic diagnostics.

## Reaching gateway-protected modules (VW TP2.0 over raw CAN)

Some VAG chassis modules (ABS/ESP, steering, gateway) sit on a bus behind the
gateway and speak **VW TP2.0 + KWP2000**, not ISO-TP/UDS — a generic ELM327
cannot reach them at all. A **raw-CAN adapter** (e.g. a CANable on Linux
SocketCAN) can. Install the extra and bring the interface up at the car's
bitrate (500 kbps for most 11-bit buses):

```bash
python -m pip install -e ".[can]"
sudo ip link set can0 up type can bitrate 500000
```

The GUI also detects CANable 2.0 devices that appear as a USB serial port
(for example `/dev/ttyACM0`) and opens them through python-can's SLCAN backend;
select **CANable 2.0** from **Raw CAN (VW TP2.0)**. Connect the OBD cable before
scanning. Devices already exposed as `can0` can be selected directly instead.

Then read or clear a specific module by its VAG address (hex):

```bash
obd2 tp20-read  --channel can0 --module 03   # ABS / ESP brakes
obd2 tp20-read  --channel can0 --module 15   # airbag / SRS
obd2 tp20-clear --channel can0 --module 03   # clear ABS codes
```

The TP2.0 channel setup, framing and KWP2000 DTC read/clear are implemented in
[`tp20.py`](src/obd2reader/tp20.py) and [`kwp.py`](src/obd2reader/kwp.py), driven
through the CAN abstraction in [`canbus.py`](src/obd2reader/canbus.py). Common VAG
codes are given text descriptions ([`vagdtc.py`](src/obd2reader/vagdtc.py)); others
show their raw 5-digit number, since the full mapping is VW proprietary.

The same modules also appear in the **GUI** under the **Raw CAN (VW TP2.0)**
source, listed side-by-side with UDS modules in the unified module view.

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
