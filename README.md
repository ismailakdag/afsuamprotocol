# AFSUAM Measurement System

A modular Python application for phased array RFID measurement and beam steering.

## Features

- **LLRP Reader Integration**: Connect to Impinj RFID readers with advanced settings (mode, session, search mode)
- **Beam Steering Control**: LUT-based angle-to-voltage conversion for phase shifters
- **Dual Antenna Support**: Monitor both phased array and reference antennas
- **L-C-R Protocol**: Automated LEFT-CENTER-RIGHT beam sweep measurements
- **Real-time Monitoring**: Live tag data with RSSI, phase, and doppler tracking
- **CSV Export**: Comprehensive data export for analysis

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python run.py
```

## Project Structure

```
afsuam_measurement/
├── run.py                 # Entry point
├── requirements.txt       # Dependencies
├── setup.py              # Package setup
├── tag_config.json       # Tag configuration
├── corrected_lut_final.csv  # Beam steering LUT
│
├── config/               # Configuration management
│   ├── __init__.py
│   └── settings.py       # Settings dataclasses
│
├── core/                 # Hardware abstraction
│   ├── __init__.py
│   ├── rfid_reader.py    # LLRP reader interface
│   ├── mcu_controller.py # Serial MCU control
│   ├── beam_lut.py       # LUT engines
│   └── tag_manager.py    # Tag config management
│
├── protocols/            # Measurement protocols
│   ├── __init__.py
│   ├── base.py           # Base protocol class
│   ├── afsuam.py         # L-C-R sweep protocol
│   └── inventory.py      # Simple inventory
│
├── gui/                  # Tkinter GUI
│   ├── __init__.py
│   ├── app.py            # Main application
│   ├── styles.py         # UI styles
│   ├── widgets/          # Reusable widgets
│   │   ├── hardware_panel.py
│   │   ├── beam_control.py
│   │   └── status_bar.py
│   └── tabs/             # Tab components
│       ├── live_monitor.py
│       ├── protocol_runner.py
│       └── export.py
│
└── utils/                # Utilities
    ├── __init__.py
    ├── csv_exporter.py   # CSV export
    └── logging.py        # Logging
```

## Configuration

### Tag Configuration (`tag_config.json`)

```json
{
    "tags": [
        {"suffix": "0001", "label": "TAG1", "location": "Slot A"},
        {"suffix": "0002", "label": "TAG2", "location": "Slot B"}
    ],
    "antenna_settings": {
        "port_2_enabled": true
    }
}
```

### Reader Settings

Advanced reader settings can be configured in the Hardware Panel:

| Setting | Options | Description |
|---------|---------|-------------|
| Mode | 1002 (DenseRdr), 1000 (AutoSet), etc. | Reader operating mode |
| Session | 0 (Fast), 2 (Extended) | RFID session |
| Search Mode | 2 (Dual Target), 1 (Single) | Tag search strategy |

## Usage

### 1. Connect Hardware

1. Select MCU serial port and click "Connect MCU"
2. Enter reader IP address and power level
3. Select antenna mode (Both, Ant1 Only, Ant2 Only)
4. Click "Connect Reader"

### 2. Beam Control

- Use the angle slider for manual control
- Click L/C/R buttons for preset positions
- Select Port Config (0 or 1) for different antenna configurations

### 3. Run Protocol

1. Go to "AFSUAM Protocol" tab
2. Set station name, dwell time, and repeats
3. Click "Run L-C-R Protocol"
4. Export results via "Export CSV"

## Development

### Install in Development Mode

```bash
pip install -e .
```

### Run Tests

```bash
python -m pytest tests/
```

## License

MIT License

## Repository

https://github.com/ismailakdag/afsuamprotocol
