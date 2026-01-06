"""
CalibV4_AFSUAM (Updated) - Beam Steering + User-Named Reference Antenna + AFSUAM Test-Bed Protocol
==================================================================================================

Updates requested
1) MCU presence check:
   - If MCU is not connected (serial not open), the GUI warns and protocol run is blocked.
   - When connecting MCU, /dev/cu.usbmodem1201 (or /dev/cu/.usbmodem1201 if that is what your OS reports)
     is auto-prioritized if present.

2) Default reader power:
   - Default Tx power is set to 26.5 dBm.

3) Reference antenna naming:
   - "Fixed antenna type" is now a free text field; you name it.

4) Place naming:
   - Default station name is "AFSUAM Test-Bed".

5) Tag location integration + per-beam read mapping:
   - tag_config.json can include per-tag "location".
   - Exports include TAGSTEP_ROWS: for each repeat + beam state (LEFT/CENTER/RIGHT) and each tag (T1..T8),
     indicates whether it was seen by Ant1 (phased array) and Ant2 (reference), with RSSI/Count when available.
   - This directly answers "which location is read/not read" and "which phased-array state reads it".

CSV Output
- STEP_ROWS: step-level metrics (coverage counts, missed lists) per repeat and beam state.
- TAGSTEP_ROWS: per-tag, per-step, per-repeat detail (location + seen/not seen + RSSI + count), includes beam state.
- UNION_ROWS: union coverage over L∪C∪R per repeat.

Notes
- Port 1 = phased array beam steering (LUT-driven voltages).
- Port 2 = reference antenna (named by user), collected simultaneously via AntennaID=2.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import serial
import serial.tools.list_ports
import time
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
import csv
from datetime import datetime
import threading
import os

# Optional plotting
try:
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MPL_AVAILABLE = True
except Exception:
    MPL_AVAILABLE = False

# ML (optional)
try:
    import joblib
    from sklearn.preprocessing import StandardScaler
    ML_AVAILABLE = True
except ImportError:
    ML_AVAILABLE = False

# LLRP / SLLURP
try:
    from sllurp.llrp import (
        LLRPReaderConfig,
        LLRPReaderClient,
        LLRP_DEFAULT_PORT,
        LLRPReaderState
    )
    from twisted.internet import reactor
    SLLURP_AVAILABLE = True
except ImportError:
    SLLURP_AVAILABLE = False


# =============================================================================
# 1) CORRECTED LUT ENGINE
# =============================================================================
class CorrectedBeamLUT:
    """Handles corrected_lut_final.csv format with Port_Config support."""

    def __init__(self, csv_path="corrected_lut_final.csv"):
        self.loaded = False
        self.csv_path = csv_path
        self.df = None
        self.config_0 = pd.DataFrame()
        self.config_1 = pd.DataFrame()
        self.interp = {0: {}, 1: {}}

        try:
            if not os.path.exists(csv_path):
                print(f"ERROR: LUT file not found: {csv_path}")
                return

            self.df = pd.read_csv(csv_path)
            self.df.columns = [c.strip() for c in self.df.columns]

            self.config_0 = self.df[self.df["Port_Config"] == 0].copy()
            self.config_1 = self.df[self.df["Port_Config"] == 1].copy()

            for config_num, config_df in [(0, self.config_0), (1, self.config_1)]:
                if not config_df.empty:
                    angles = config_df["Angle_Cmd_Deg"].values
                    v_ch1 = config_df["V_CH1"].values
                    v_ch2 = config_df["V_CH2"].values

                    self.interp[config_num]["V_CH1"] = interp1d(
                        angles, v_ch1, kind="linear", fill_value="extrapolate"
                    )
                    self.interp[config_num]["V_CH2"] = interp1d(
                        angles, v_ch2, kind="linear", fill_value="extrapolate"
                    )

            self.loaded = True
            print(
                f"LUT Loaded: Config 0 has {len(self.config_0)} points, "
                f"Config 1 has {len(self.config_1)} points"
            )
        except Exception as e:
            print(f"Error loading LUT: {e}")
            import traceback
            traceback.print_exc()

    def get_voltages(self, port_config: int, target_angle: float) -> tuple:
        """Returns (V_CH1, V_CH2) for given port config and angle."""
        if not self.loaded:
            return 0.0, 0.0

        config = port_config if port_config in [0, 1] else 0
        if config not in self.interp or not self.interp[config]:
            return 0.0, 0.0

        try:
            v1 = float(self.interp[config]["V_CH1"](target_angle))
            v2 = float(self.interp[config]["V_CH2"](target_angle))

            # Clamp to valid range used by your phase-shifter control
            v1 = max(0.0, min(8.5, v1))
            v2 = max(0.0, min(8.5, v2))
            return v1, v2
        except Exception as e:
            print(f"Interpolation error: {e}")
            return 0.0, 0.0

    def get_available_angles(self, port_config: int) -> list:
        config_df = self.config_0 if port_config == 0 else self.config_1
        if config_df.empty:
            return []
        return sorted(config_df["Angle_Cmd_Deg"].unique().tolist())

    def get_beam_presets(self, port_config: int) -> dict:
        """Returns LEFT/CENTER/RIGHT angle presets from LUT coverage."""
        angles = self.get_available_angles(port_config)
        if not angles:
            return {"LEFT": 30.0, "CENTER": 0.0, "RIGHT": -30.0}
        return {
            "LEFT": max(angles),
            "CENTER": min(angles, key=abs),
            "RIGHT": min(angles),
        }


# =============================================================================
# 2) LLRP READER WRAPPER
# =============================================================================
class LLRPReader:
    def __init__(self):
        self.inventory = {}
        self.connected = False
        self.inventory_running = False
        self.reader_client = None
        self.reactor_thread = None
        self.last_disconnect_time = 0
        self.lock = threading.Lock()

    def connect(self, ip_address, power_dbm=26.5, antennas=None):
        """
        Connect to RFID reader.
        antennas: list of antenna ports to enable, e.g. [1], [2], [1,2]
        """
        if not SLLURP_AVAILABLE:
            print("SLLURP not available")
            return False

        if antennas is None:
            antennas = [1]

        time_since_disc = time.time() - self.last_disconnect_time
        if time_since_disc < 3.0:
            time.sleep(3.0 - time_since_disc)

        if self.connected:
            self.disconnect()
            time.sleep(1.0)

        try:
            power_idx = max(1, min(93, int((power_dbm - 10.0) / 0.25) + 1))

            factory_args = {
                "tx_power": power_idx,
                "mode_identifier": 1002,
                "report_every_n_tags": 10,
                "start_inventory": True,
                "tag_content_selector": {
                    "EnableROSpecID": True,
                    "EnableAntennaID": True,
                    "EnablePeakRSSI": True,
                    "EnableFirstSeenTimestamp": True,
                    "EnableLastSeenTimestamp": True,
                    "EnableTagSeenCount": True,
                    "EnableRFDopplerFrequency": True,
                },
                "impinj_extended_configuration": True,
                "impinj_reports": True,
                "impinj_tag_content_selector": {
                    "EnableRFPhaseAngle": True,
                    "EnablePeakRSSI": True,
                    "EnableRFDopplerFrequency": True,
                    "EnableOptimizerOne": False,
                },
                "impinj_search_mode": "2",
                "session": 0,
            }

            factory_args["antennas"] = antennas

            config = LLRPReaderConfig(factory_args)
            self.reader_client = LLRPReaderClient(ip_address, LLRP_DEFAULT_PORT, config)
            self.reader_client.add_tag_report_callback(self._on_tag_report)
            self.reader_client.add_state_callback(LLRPReaderState.STATE_CONNECTED, self._on_state_change)
            self.reader_client.add_state_callback(LLRPReaderState.STATE_DISCONNECTED, self._on_state_change)

            if self.reactor_thread is None:
                self.reactor_thread = threading.Thread(target=self._run_reactor, daemon=True)
                self.reactor_thread.start()

            self.reader_client.connect()
            return True

        except Exception as e:
            print(f"Connect error: {e}")
            return False

    def disconnect(self):
        if self.reader_client:
            try:
                self.reader_client.disconnect()
            except Exception:
                pass
        self.connected = False
        self.inventory_running = False
        self.last_disconnect_time = time.time()

    def _on_state_change(self, reader, state):
        if state == LLRPReaderState.STATE_CONNECTED:
            self.connected = True
            self.inventory_running = True
        elif state == LLRPReaderState.STATE_DISCONNECTED:
            self.connected = False
            self.inventory_running = False

    def _run_reactor(self):
        try:
            if not reactor.running:
                reactor.run(installSignalHandlers=False)
        except Exception as e:
            print(f"Reactor error: {e}")

    def _on_tag_report(self, reader, tag_reports):
        if not self.inventory_running:
            return

        def get_val_any(obj, keys, default=None):
            for k in keys:
                if k in obj:
                    v = obj[k]
                    if isinstance(v, dict):
                        return v.get("Value", default)
                    return v
            return default

        for tag in tag_reports:
            try:
                epc_raw = tag.get("EPC-96") or tag.get("EPCUnknown")
                if not epc_raw:
                    continue

                if isinstance(epc_raw, bytes):
                    try:
                        epc = epc_raw.decode("utf-8").upper()
                    except Exception:
                        epc = epc_raw.hex().upper()
                else:
                    epc = str(epc_raw).upper()

                rssi = float(tag.get("ImpinjPeakRSSI", tag.get("PeakRSSI", -90)))
                if rssi < -150:
                    rssi = rssi / 100.0

                doppler = float(tag.get("RFDopplerFrequency", tag.get("DopplerFrequency", 0.0)))

                p_val = get_val_any(tag, ["ImpinjRFPhaseAngle", "RFPhaseAngle", "PhaseAngle", "Phase"])
                if p_val is None and "Custom" in tag:
                    for item in tag["Custom"]:
                        if isinstance(item, dict):
                            p_val = get_val_any(item, ["ImpinjRFPhaseAngle", "RFPhaseAngle", "PhaseAngle"])
                            if p_val is not None:
                                break

                if p_val is not None:
                    try:
                        v_final = p_val.get("Value") if isinstance(p_val, dict) else p_val
                        phase = (float(v_final) / 4096.0 * 360.0) % 360.0
                    except Exception:
                        phase = 0.0
                else:
                    phase = 0.0

                ant_id = tag.get("AntennaID", 1)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                with self.lock:
                    prev = self.inventory.get(epc, {"count": 0})
                    count = prev["count"] + 1
                    self.inventory[epc] = {
                        "epc": epc,
                        "rssi": rssi,
                        "phase": phase,
                        "doppler": doppler,
                        "count": count,
                        "timestamp": timestamp,
                        "seen_time": time.time(),
                        "antenna": ant_id,
                    }
            except Exception:
                pass

    def get_all_data(self):
        with self.lock:
            return self.inventory.copy()

    def clear_data(self):
        with self.lock:
            self.inventory = {}


# =============================================================================
# 3) MAIN GUI + AFSUAM PROTOCOL RUNNER
# =============================================================================
class CalibV4GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CalibV4 AFSUAM - Phased Beam + User Named Ref + Protocol Runner")
        self.root.geometry("1600x980")

        self.lut = CorrectedBeamLUT()
        self.reader = LLRPReader() if SLLURP_AVAILABLE else None
        self.serial = None

        # State
        self.current_port_config = 0
        self.current_angle = 0.0
        self.current_mode = "CENTER"

        # Tags
        self.tag_config_file = "tag_config.json"
        self.tag_suffixes = ["7476", "7486", "7426", "7436", "7446", "7496", "72E6", "72F6"]
        self.tag_labels = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]
        self.tag_locations = [""] * len(self.tag_suffixes)  # aligns with suffix/label

        # Reader settings - MUST be defined BEFORE load_tag_config()
        self.port_2_enabled = tk.BooleanVar(value=True)  # kept for backward compat
        
        # Antenna Mode: "BOTH", "ANT1_ONLY", "ANT2_ONLY"
        self.antenna_mode = tk.StringVar(value="BOTH")
        self.current_antennas = [1, 2]  # tracks active antenna list
        
        # Load tag config (may modify port_2_enabled)
        self.load_tag_config()

        # Protocol storage
        self.afsuam_step_rows = []        # STEP_ROWS
        self.afsuam_tagstep_rows = []     # TAGSTEP_ROWS (per-tag, per-step detail)
        self.afsuam_union_rows = []       # UNION_ROWS

        self.update_timer = None

        self._setup_styles()
        self._setup_ui()
        self._start_update_loop()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ------------------------------
    # Config
    # ------------------------------
    def load_tag_config(self):
        if os.path.exists(self.tag_config_file):
            try:
                with open(self.tag_config_file, "r") as f:
                    data = json.load(f)

                tags = data.get("tags", [])
                if tags:
                    self.tag_suffixes = [t.get("suffix", "").strip().upper() for t in tags]
                    self.tag_labels = [t.get("label", "").strip() for t in tags]
                    self.tag_locations = [t.get("location", "").strip() for t in tags]

                    # backfill missing
                    while len(self.tag_locations) < len(self.tag_suffixes):
                        self.tag_locations.append("")

                ant_settings = data.get("antenna_settings", {})
                if "port_2_enabled" in ant_settings:
                    self.port_2_enabled.set(bool(ant_settings["port_2_enabled"]))

                print(f"Loaded {len(self.tag_suffixes)} tags from {self.tag_config_file}")
            except Exception as e:
                print(f"tag_config.json load error: {e}")

        # Safety: align lengths
        n = min(len(self.tag_suffixes), len(self.tag_labels))
        self.tag_suffixes = self.tag_suffixes[:n]
        self.tag_labels = self.tag_labels[:n]
        if len(self.tag_locations) != n:
            self.tag_locations = (self.tag_locations + [""] * n)[:n]

    # ------------------------------
    # Serial port preference
    # ------------------------------
    def _preferred_mcu_port(self, available_ports):
        """
        Priority:
        1) /dev/cu.usbmodem1201
        2) /dev/cu/.usbmodem1201 (as requested)
        3) any port containing 'usbmodem1201'
        4) first available
        """
        if not available_ports:
            return None

        exact_1 = "/dev/cu.usbmodem1201"
        exact_2 = "/dev/cu/.usbmodem1201"

        if exact_1 in available_ports:
            return exact_1
        if exact_2 in available_ports:
            return exact_2

        for p in available_ports:
            if "usbmodem1201" in p:
                return p

        return available_ports[0]

    # ------------------------------
    # UI setup
    # ------------------------------
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#FFFFFF", foreground="#0f172a", font=("Arial", 10))
        style.configure("TLabel", background="#FFFFFF", foreground="#0f172a")
        style.configure("TFrame", background="#FFFFFF")
        style.configure("TLabelframe", background="#FFFFFF")
        style.configure("TLabelframe.Label", foreground="#1e40af", font=("Arial", 11, "bold"))
        style.configure("TButton", padding=6)
        self.root.configure(bg="#FFFFFF")

    def _setup_ui(self):
        sidebar = ttk.Frame(self.root, width=420, padding=10)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)

        self.nb = ttk.Notebook(self.root, padding=10)
        self.nb.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # ---------------- Sidebar: Hardware ----------------
        hw_fr = ttk.LabelFrame(sidebar, text="Hardware", padding=10)
        hw_fr.pack(fill=tk.X, pady=5)

        ttk.Label(hw_fr, text="MCU Port:").pack(anchor=tk.W)

        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.cb_port = ttk.Combobox(hw_fr, values=ports)

        preferred = self._preferred_mcu_port(ports)
        if preferred:
            self.cb_port.set(preferred)
        elif ports:
            self.cb_port.current(0)

        self.cb_port.pack(fill=tk.X, pady=2)

        btn_mcu_row = ttk.Frame(hw_fr)
        btn_mcu_row.pack(fill=tk.X, pady=4)
        ttk.Button(btn_mcu_row, text="Connect MCU", command=self.connect_mcu).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_mcu_row, text="Refresh Ports", command=self.refresh_mcu_ports).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        ttk.Separator(hw_fr, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ttk.Label(hw_fr, text="Reader IP:").pack(anchor=tk.W)
        self.ent_ip = ttk.Entry(hw_fr)
        self.ent_ip.insert(0, "169.254.1.1")
        self.ent_ip.pack(fill=tk.X, pady=2)

        ttk.Label(hw_fr, text="Power (dBm):").pack(anchor=tk.W)
        self.ent_pwr = ttk.Entry(hw_fr)
        self.ent_pwr.insert(0, "26.5")
        self.ent_pwr.pack(fill=tk.X, pady=2)

        # Antenna Mode Selection
        ant_mode_fr = ttk.LabelFrame(hw_fr, text="📡 Anten Modu", padding=5)
        ant_mode_fr.pack(fill=tk.X, pady=4)
        
        ttk.Radiobutton(
            ant_mode_fr, text="İkisi Birden (Ant1 + Ant2)",
            variable=self.antenna_mode, value="BOTH"
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            ant_mode_fr, text="Sadece Anten 1 (Phased Array)",
            variable=self.antenna_mode, value="ANT1_ONLY"
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            ant_mode_fr, text="Sadece Anten 2 (Referans)",
            variable=self.antenna_mode, value="ANT2_ONLY"
        ).pack(anchor=tk.W)
        
        self.lbl_antenna_status = ttk.Label(ant_mode_fr, text="Aktif: Ant1+Ant2", foreground="#1e40af", font=("Arial", 9, "bold"))
        self.lbl_antenna_status.pack(anchor=tk.W, pady=(4, 0))
        
        self.btn_apply_antenna = ttk.Button(
            ant_mode_fr, text="Uygula & Yeniden Bağlan",
            command=self.apply_antenna_mode
        )
        self.btn_apply_antenna.pack(fill=tk.X, pady=4)

        btn_fr = ttk.Frame(hw_fr)
        btn_fr.pack(fill=tk.X, pady=4)
        self.btn_connect = ttk.Button(btn_fr, text="Connect Reader", command=self.connect_reader)
        self.btn_connect.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.btn_disconnect = ttk.Button(btn_fr, text="Disconnect", command=self.disconnect_reader, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        # ---------------- Sidebar: Beam control ----------------
        beam_fr = ttk.LabelFrame(sidebar, text="Beam Control (Port 1)", padding=10)
        beam_fr.pack(fill=tk.X, pady=8)

        self.var_port_config = tk.IntVar(value=0)
        ttk.Label(beam_fr, text="Port_Config:").pack(anchor=tk.W)
        ttk.Radiobutton(beam_fr, text="0 (P1-P4)", variable=self.var_port_config, value=0, command=self.on_port_config_change).pack(anchor=tk.W)
        ttk.Radiobutton(beam_fr, text="1 (P2-P3)", variable=self.var_port_config, value=1, command=self.on_port_config_change).pack(anchor=tk.W)

        ttk.Label(beam_fr, text="Angle (deg):").pack(anchor=tk.W, pady=(8, 0))
        self.scale_angle = tk.Scale(
            beam_fr, from_=-30, to=30, resolution=0.5, orient=tk.HORIZONTAL,
            length=360, command=self.on_angle_change
        )
        self.scale_angle.set(0)
        self.scale_angle.pack(fill=tk.X, pady=2)

        btn_modes = ttk.Frame(beam_fr)
        btn_modes.pack(fill=tk.X, pady=4)

        ttk.Button(btn_modes, text="LEFT", command=lambda: self.set_beam_mode("LEFT")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_modes, text="CENTER", command=lambda: self.set_beam_mode("CENTER")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_modes, text="RIGHT", command=lambda: self.set_beam_mode("RIGHT")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)

        volt_fr = ttk.Frame(beam_fr)
        volt_fr.pack(fill=tk.X, pady=4)
        ttk.Label(volt_fr, text="V_CH1:").pack(side=tk.LEFT)
        self.lbl_v1 = ttk.Label(volt_fr, text="0.000 V", font=("Arial", 11, "bold"), foreground="#1e40af")
        self.lbl_v1.pack(side=tk.LEFT, padx=(6, 16))
        ttk.Label(volt_fr, text="V_CH2:").pack(side=tk.LEFT)
        self.lbl_v2 = ttk.Label(volt_fr, text="0.000 V", font=("Arial", 11, "bold"), foreground="#16a34a")
        self.lbl_v2.pack(side=tk.LEFT, padx=(6, 0))

        self.lbl_mode = ttk.Label(beam_fr, text="Mode: CENTER", font=("Arial", 12, "bold"))
        self.lbl_mode.pack(pady=4)

        # ---------------- Sidebar: Status ----------------
        st_fr = ttk.LabelFrame(sidebar, text="Status", padding=10)
        st_fr.pack(fill=tk.X, pady=8)
        self.lbl_status = ttk.Label(st_fr, text="Ready.")
        self.lbl_status.pack(anchor=tk.W)

        # ===================== Tabs =====================
        self.tab_monitor = ttk.Frame(self.nb)
        self.tab_afsuam = ttk.Frame(self.nb)
        self.tab_export = ttk.Frame(self.nb)

        self.nb.add(self.tab_monitor, text="Live Monitor")
        self.nb.add(self.tab_afsuam, text="AFSUAM Protocol")
        self.nb.add(self.tab_export, text="Export / Logs")

        self._build_live_monitor_tab()
        self._build_afsuam_tab()
        self._build_export_tab()

    def _build_live_monitor_tab(self):
        fr = ttk.Frame(self.tab_monitor, padding=10)
        fr.pack(fill=tk.BOTH, expand=True)

        # ==================== ANTENNA 1 & 2 SIDE BY SIDE ====================
        antenna_container = ttk.Frame(fr)
        antenna_container.pack(fill=tk.BOTH, expand=True)
        
        # ----- Antenna 1 (Phased Array) -----
        ant1_fr = ttk.LabelFrame(antenna_container, text="📡 Anten 1 - Phased Array (Beam Steering)", padding=10)
        ant1_fr.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        cols_ant = ("Tag", "Location", "Suffix", "Reads", "RSSI", "Phase")
        self.tree_ant1 = ttk.Treeview(ant1_fr, columns=cols_ant, show="headings", height=10)
        for c in cols_ant:
            self.tree_ant1.heading(c, text=c)
            self.tree_ant1.column(c, width=80, anchor=tk.CENTER)
        self.tree_ant1.column("Tag", width=50)
        self.tree_ant1.column("Location", width=120, anchor=tk.W)
        self.tree_ant1.column("Suffix", width=60)
        self.tree_ant1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb_ant1 = ttk.Scrollbar(ant1_fr, orient="vertical", command=self.tree_ant1.yview)
        self.tree_ant1.configure(yscrollcommand=vsb_ant1.set)
        vsb_ant1.pack(side=tk.RIGHT, fill=tk.Y)

        # ----- Antenna 2 (Reference) -----
        ant2_fr = ttk.LabelFrame(antenna_container, text="📡 Anten 2 - Referans Anten", padding=10)
        ant2_fr.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))

        self.tree_ant2 = ttk.Treeview(ant2_fr, columns=cols_ant, show="headings", height=10)
        for c in cols_ant:
            self.tree_ant2.heading(c, text=c)
            self.tree_ant2.column(c, width=80, anchor=tk.CENTER)
        self.tree_ant2.column("Tag", width=50)
        self.tree_ant2.column("Location", width=120, anchor=tk.W)
        self.tree_ant2.column("Suffix", width=60)
        self.tree_ant2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb_ant2 = ttk.Scrollbar(ant2_fr, orient="vertical", command=self.tree_ant2.yview)
        self.tree_ant2.configure(yscrollcommand=vsb_ant2.set)
        vsb_ant2.pack(side=tk.RIGHT, fill=tk.Y)

        # ==================== STATISTICS PANEL ====================
        stats_container = ttk.Frame(fr)
        stats_container.pack(fill=tk.X, pady=5)
        
        # Ant1 Stats
        stats1_fr = ttk.LabelFrame(stats_container, text="📊 Anten 1 İstatistikleri", padding=5)
        stats1_fr.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.lbl_ant1_stats = ttk.Label(
            stats1_fr, 
            text="RSSI: Min=- | Max=- | Avg=-  |  Read: 0  |  Tags: 0/8",
            font=("Courier New", 10)
        )
        self.lbl_ant1_stats.pack(anchor=tk.W)
        
        # Ant2 Stats
        stats2_fr = ttk.LabelFrame(stats_container, text="📊 Anten 2 İstatistikleri", padding=5)
        stats2_fr.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        self.lbl_ant2_stats = ttk.Label(
            stats2_fr, 
            text="RSSI: Min=- | Max=- | Avg=-  |  Read: 0  |  Tags: 0/8",
            font=("Courier New", 10)
        )
        self.lbl_ant2_stats.pack(anchor=tk.W)

        # ==================== OLD TARGET TAGS (Combined view) ====================
        monitor_fr = ttk.LabelFrame(fr, text="Target Tags (T1-T8) - Combined View", padding=10)
        monitor_fr.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        cols = ("Tag", "Location", "Suffix", "Reads", "RSSI", "Phase", "Doppler", "Antenna")
        self.tree_targets = ttk.Treeview(monitor_fr, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree_targets.heading(c, text=c)
            self.tree_targets.column(c, width=115, anchor=tk.CENTER)
        self.tree_targets.column("Tag", width=70)
        self.tree_targets.column("Location", width=160, anchor=tk.W)
        self.tree_targets.column("Suffix", width=80)
        self.tree_targets.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(monitor_fr, orient="vertical", command=self.tree_targets.yview)
        self.tree_targets.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # ==================== ALL DISCOVERED TAGS ====================
        disc_fr = ttk.LabelFrame(fr, text="All Discovered Tags (last 5 s)", padding=10)
        disc_fr.pack(fill=tk.BOTH, expand=True, pady=10)

        cols2 = ("Suffix", "Type", "EPC", "RSSI", "Phase", "Count", "Antenna", "LastSeen")
        self.tree_all = ttk.Treeview(disc_fr, columns=cols2, show="headings", height=10)
        for c in cols2:
            self.tree_all.heading(c, text=c)
            self.tree_all.column(c, width=100, anchor=tk.CENTER)
        self.tree_all.column("Suffix", width=70)
        self.tree_all.column("Type", width=80)
        self.tree_all.column("EPC", width=350)
        self.tree_all.column("Count", width=60)
        self.tree_all.column("Antenna", width=60)
        
        # Tag colors for known/unknown
        self.tree_all.tag_configure("known", foreground="#16a34a")  # Green
        self.tree_all.tag_configure("unknown", foreground="#dc2626")  # Red
        
        self.tree_all.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb2 = ttk.Scrollbar(disc_fr, orient="vertical", command=self.tree_all.yview)
        self.tree_all.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_afsuam_tab(self):
        fr = ttk.Frame(self.tab_afsuam, padding=10)
        fr.pack(fill=tk.BOTH, expand=True)

        ctrl = ttk.LabelFrame(fr, text="Station Protocol Controls", padding=10)
        ctrl.pack(fill=tk.X)

        row1 = ttk.Frame(ctrl)
        row1.pack(fill=tk.X, pady=2)

        ttk.Label(row1, text="Place / Station Name:").pack(side=tk.LEFT)
        self.ent_station = ttk.Entry(row1, width=42)
        self.ent_station.insert(0, "AFSUAM Test-Bed")
        self.ent_station.pack(side=tk.LEFT, padx=6)

        ttk.Label(row1, text="Reference Antenna Name (Port 2):").pack(side=tk.LEFT, padx=(12, 0))
        self.ent_ref_name = ttk.Entry(row1, width=24)
        self.ent_ref_name.insert(0, "REF_ANT")
        self.ent_ref_name.pack(side=tk.LEFT, padx=6)

        row2 = ttk.Frame(ctrl)
        row2.pack(fill=tk.X, pady=2)

        ttk.Label(row2, text="Dwell (s):").pack(side=tk.LEFT)
        self.ent_dwell = ttk.Entry(row2, width=6)
        self.ent_dwell.insert(0, "3.0")
        self.ent_dwell.pack(side=tk.LEFT, padx=6)

        ttk.Label(row2, text="Repeats:").pack(side=tk.LEFT, padx=(12, 0))
        self.ent_repeats = ttk.Entry(row2, width=6)
        self.ent_repeats.insert(0, "3")
        self.ent_repeats.pack(side=tk.LEFT, padx=6)

        ttk.Label(row2, text="Port_Config:").pack(side=tk.LEFT, padx=(12, 0))
        self.cb_pc = ttk.Combobox(row2, values=["0", "1"], width=5, state="readonly")
        self.cb_pc.current(0)
        self.cb_pc.pack(side=tk.LEFT, padx=6)

        # Antenna Mode Info for Protocol
        row_ant = ttk.Frame(ctrl)
        row_ant.pack(fill=tk.X, pady=4)
        
        ttk.Label(row_ant, text="📡 Aktif Anten Modu:", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        self.lbl_protocol_ant_mode = ttk.Label(
            row_ant, text="Ant1 + Ant2", 
            font=("Arial", 10, "bold"), foreground="#7c3aed"
        )
        self.lbl_protocol_ant_mode.pack(side=tk.LEFT, padx=6)
        
        ttk.Label(
            row_ant, text="(Anten modunu değiştirmek için Hardware panelini kullanın)",
            foreground="#6b7280", font=("Arial", 9)
        ).pack(side=tk.LEFT, padx=6)

        row3 = ttk.Frame(ctrl)
        row3.pack(fill=tk.X, pady=6)

        self.btn_run_protocol = ttk.Button(
            row3,
            text="Run L-C-R Protocol (Port1 sweep + Port2 reference)",
            command=self.start_afsuam_protocol_thread
        )
        self.btn_run_protocol.pack(side=tk.LEFT, padx=2)

        self.btn_clear_protocol = ttk.Button(row3, text="Clear Protocol Results", command=self.clear_afsuam_results)
        self.btn_clear_protocol.pack(side=tk.LEFT, padx=8)

        self.btn_export_protocol = ttk.Button(row3, text="Export Protocol CSV", command=self.export_afsuam_csv)
        self.btn_export_protocol.pack(side=tk.LEFT, padx=2)

        res_fr = ttk.LabelFrame(fr, text="Latest Union Summary (per repeat)", padding=10)
        res_fr.pack(fill=tk.BOTH, expand=True, pady=10)

        cols = (
            "Station", "RefName", "Repeat", "Port_Config", "Dwell_s",
            "Ant1_TgtSeen", "Ant2_TgtSeen", "Ant1_UniqueEPC", "Ant2_UniqueEPC",
            "Ant1_Missed", "Ant2_Missed"
        )
        self.tree_union = ttk.Treeview(res_fr, columns=cols, show="headings", height=12)
        for c in cols:
            self.tree_union.heading(c, text=c)
            self.tree_union.column(c, width=130, anchor=tk.CENTER)
        self.tree_union.column("Station", width=200, anchor=tk.W)
        self.tree_union.column("RefName", width=150, anchor=tk.W)
        self.tree_union.column("Ant1_Missed", width=260, anchor=tk.W)
        self.tree_union.column("Ant2_Missed", width=260, anchor=tk.W)

        self.tree_union.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(res_fr, orient="vertical", command=self.tree_union.yview)
        self.tree_union.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_export_tab(self):
        fr = ttk.Frame(self.tab_export, padding=10)
        fr.pack(fill=tk.BOTH, expand=True)

        exp_fr = ttk.LabelFrame(fr, text="Quick Export", padding=10)
        exp_fr.pack(fill=tk.X)

        ttk.Button(exp_fr, text="Export Live Inventory Snapshot (CSV)", command=self.export_live_snapshot).pack(side=tk.LEFT, padx=4)
        ttk.Button(exp_fr, text="Export Protocol CSV (STEP + TAGSTEP + UNION)", command=self.export_afsuam_csv).pack(side=tk.LEFT, padx=4)

        log_fr = ttk.LabelFrame(fr, text="Log", padding=10)
        log_fr.pack(fill=tk.BOTH, expand=True, pady=10)

        self.txt_log = tk.Text(log_fr, height=22, font=("Courier New", 10))
        self.txt_log.pack(fill=tk.BOTH, expand=True)

    # =============================================================================
    # Hardware
    # =============================================================================
    def refresh_mcu_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.cb_port["values"] = ports
        preferred = self._preferred_mcu_port(ports)
        if preferred:
            self.cb_port.set(preferred)
        elif ports:
            self.cb_port.current(0)
        self._log(f"Ports refreshed. Found {len(ports)} ports.")

    def connect_mcu(self, port_override=None):
        try:
            ports = [p.device for p in serial.tools.list_ports.comports()]
            chosen = None

            if port_override:
                chosen = port_override
            else:
                # If user already picked something, respect it; else prefer our priority selection
                selected = self.cb_port.get().strip()
                if selected:
                    chosen = selected
                else:
                    chosen = self._preferred_mcu_port(ports)

            if not chosen:
                messagebox.showerror("MCU", "No serial port detected/selected.")
                return

            self.serial = serial.Serial(chosen, 115200, timeout=0.1)
            self.cb_port.set(chosen)
            self._log(f"MCU connected: {chosen}")
            messagebox.showinfo("MCU", f"Connected: {chosen}")
        except Exception as e:
            messagebox.showerror("MCU", f"Connection failed: {e}")

    def _ensure_mcu_connected_or_warn(self) -> bool:
        if self.serial and self.serial.is_open:
            return True
        messagebox.showwarning(
            "MCU Required",
            "MCU is not connected. Connect the MCU before running the protocol or applying LUT voltages."
        )
        return False

    def connect_reader(self):
        if not self.reader:
            messagebox.showerror("Reader", "SLLURP is not available.")
            return

        # Best-effort MCU autoconnect (but do not block reader connection)
        if not (self.serial and self.serial.is_open):
            ports = [p.device for p in serial.tools.list_ports.comports()]
            preferred = self._preferred_mcu_port(ports)
            if preferred:
                try:
                    self.connect_mcu(port_override=preferred)
                except Exception:
                    pass

        ip = self.ent_ip.get().strip()
        try:
            pwr = float(self.ent_pwr.get().strip())
        except Exception:
            pwr = 26.5

        # Determine antennas from antenna_mode
        mode = self.antenna_mode.get()
        if mode == "ANT1_ONLY":
            antennas = [1]
        elif mode == "ANT2_ONLY":
            antennas = [2]
        else:  # BOTH
            antennas = [1, 2]
        
        self.current_antennas = antennas

        ok = self.reader.connect(ip, pwr, antennas=antennas)
        if ok:
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.NORMAL)
            self._update_antenna_status_label()
            self._log(f"Reader connected: {ip} | Pwr={pwr:.1f} dBm | Antennas={antennas}")
            messagebox.showinfo("Reader", f"Connected: {ip}\nAntennas: {antennas}")
        else:
            messagebox.showerror("Reader", "Connection failed.")

    def disconnect_reader(self):
        if self.reader:
            self.reader.disconnect()
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_disconnect.config(state=tk.DISABLED)
        self._log("Reader disconnected.")

    def _update_antenna_status_label(self):
        """Update the antenna status label based on current_antennas."""
        if self.current_antennas == [1]:
            txt = "Aktif: Sadece Anten 1"
            short_txt = "Sadece Ant1"
            color = "#2563eb"
        elif self.current_antennas == [2]:
            txt = "Aktif: Sadece Anten 2"
            short_txt = "Sadece Ant2"
            color = "#16a34a"
        else:
            txt = "Aktif: Ant1 + Ant2"
            short_txt = "Ant1 + Ant2"
            color = "#7c3aed"
        self.lbl_antenna_status.config(text=txt, foreground=color)
        # Also update protocol tab label
        if hasattr(self, 'lbl_protocol_ant_mode'):
            self.lbl_protocol_ant_mode.config(text=short_txt, foreground=color)

    def apply_antenna_mode(self):
        """Disconnect, apply new antenna mode, and reconnect."""
        if not self.reader:
            messagebox.showerror("Reader", "SLLURP is not available.")
            return

        mode = self.antenna_mode.get()
        if mode == "ANT1_ONLY":
            new_antennas = [1]
        elif mode == "ANT2_ONLY":
            new_antennas = [2]
        else:
            new_antennas = [1, 2]

        # Check if already at desired mode
        if self.current_antennas == new_antennas and self.reader.connected:
            self._log(f"Antenna mode already set to {new_antennas}")
            messagebox.showinfo("Anten Modu", f"Zaten bu modda: {new_antennas}")
            return

        self._log(f"Applying antenna mode: {mode} -> antennas={new_antennas}")

        # Disconnect first
        if self.reader.connected:
            self._log("Disconnecting reader for antenna mode change...")
            self.reader.disconnect()
            time.sleep(2.0)  # Wait for clean disconnect

        # Update antennas
        self.current_antennas = new_antennas
        self._update_antenna_status_label()

        # Reconnect with new config
        ip = self.ent_ip.get().strip()
        try:
            pwr = float(self.ent_pwr.get().strip())
        except Exception:
            pwr = 26.5

        self._log(f"Reconnecting with antennas={new_antennas}...")
        ok = self.reader.connect(ip, pwr, antennas=new_antennas)
        
        if ok:
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.NORMAL)
            self._log(f"Reader reconnected: {ip} | Antennas={new_antennas}")
            messagebox.showinfo("Anten Modu", f"Anten modu değiştirildi!\nAktif: {new_antennas}")
        else:
            self.btn_connect.config(state=tk.NORMAL)
            self.btn_disconnect.config(state=tk.DISABLED)
            messagebox.showerror("Reader", "Yeniden bağlanma başarısız.")

    # =============================================================================
    # Beam control
    # =============================================================================
    def on_port_config_change(self):
        self.current_port_config = int(self.var_port_config.get())
        self.update_voltages()

    def on_angle_change(self, val):
        try:
            self.current_angle = float(val)
        except Exception:
            self.current_angle = 0.0
        self.current_mode = "MANUAL"
        self.lbl_mode.config(text=f"Mode: MANUAL ({self.current_angle:.1f} deg)")
        self.update_voltages()

    def set_beam_mode(self, mode: str):
        mode = mode.upper()
        self.current_mode = mode

        pc = int(self.current_port_config)
        presets = self.lut.get_beam_presets(pc)
        if mode in presets:
            self.current_angle = float(presets[mode])
            self.scale_angle.set(self.current_angle)

        self.lbl_mode.config(text=f"Mode: {mode}")
        self.update_voltages()

    def update_voltages(self):
        pc = int(self.current_port_config)
        v1, v2 = self.lut.get_voltages(pc, float(self.current_angle))
        self.lbl_v1.config(text=f"{v1:.3f} V")
        self.lbl_v2.config(text=f"{v2:.3f} V")
        self.set_volts(v1, v2)

    def set_volts(self, v1, v2):
        # Do not silently fail: if MCU missing, just log (live manual slider should not spam popups)
        if not (self.serial and self.serial.is_open):
            self._log("MCU not connected: voltages not applied.")
            return
        try:
            cmd = f"SET1:{v1:.3f}\nSET2:{v2:.3f}\n"
            self.serial.write(cmd.encode())
        except Exception as e:
            self._log(f"Serial error: {e}")

    # =============================================================================
    # Live monitor update loop
    # =============================================================================
    def _start_update_loop(self):
        self.update_live_monitor()

    def update_live_monitor(self):
        try:
            if self.reader and self.reader.connected:
                inv = self.reader.get_all_data()
                now = time.time()

                # Split inventory by antenna
                inv1, inv2 = self._split_inventory_by_antenna(inv)

                # ==================== ANTENNA 1 PANEL ====================
                self.tree_ant1.delete(*self.tree_ant1.get_children())
                for label, suffix, loc in zip(self.tag_labels, self.tag_suffixes, self.tag_locations):
                    info = None
                    for epc, d in inv1.items():
                        if epc.endswith(suffix):
                            info = d
                            break
                    if info is None:
                        self.tree_ant1.insert("", tk.END, values=(label, loc, suffix, 0, "-", "-"))
                    else:
                        self.tree_ant1.insert(
                            "", tk.END,
                            values=(
                                label, loc, suffix,
                                info.get("count", 0),
                                f"{info.get('rssi', -99.0):.1f}",
                                f"{info.get('phase', 0.0):.0f}",
                            )
                        )

                # ==================== ANTENNA 2 PANEL ====================
                self.tree_ant2.delete(*self.tree_ant2.get_children())
                for label, suffix, loc in zip(self.tag_labels, self.tag_suffixes, self.tag_locations):
                    info = None
                    for epc, d in inv2.items():
                        if epc.endswith(suffix):
                            info = d
                            break
                    if info is None:
                        self.tree_ant2.insert("", tk.END, values=(label, loc, suffix, 0, "-", "-"))
                    else:
                        self.tree_ant2.insert(
                            "", tk.END,
                            values=(
                                label, loc, suffix,
                                info.get("count", 0),
                                f"{info.get('rssi', -99.0):.1f}",
                                f"{info.get('phase', 0.0):.0f}",
                            )
                        )

                # ==================== CALCULATE STATISTICS ====================
                self._update_antenna_statistics(inv1, inv2)

                # ==================== COMBINED TARGETS ====================
                self.tree_targets.delete(*self.tree_targets.get_children())
                for label, suffix, loc in zip(self.tag_labels, self.tag_suffixes, self.tag_locations):
                    info = None
                    for epc, d in inv.items():
                        if epc.endswith(suffix):
                            info = d
                            break
                    if info is None:
                        self.tree_targets.insert("", tk.END, values=(label, loc, suffix, 0, "-99.0", "0", "0.0", "-"))
                    else:
                        self.tree_targets.insert(
                            "", tk.END,
                            values=(
                                label, loc, suffix,
                                info.get("count", 0),
                                f"{info.get('rssi', -99.0):.1f}",
                                f"{info.get('phase', 0.0):.0f}",
                                f"{info.get('doppler', 0.0):.1f}",
                                info.get("antenna", 1),
                            )
                        )

                # ==================== ALL TAGS (recent 5s) ====================
                self.tree_all.delete(*self.tree_all.get_children())
                items = sorted(inv.items(), key=lambda x: x[1].get("rssi", -99), reverse=True)
                for epc, d in items:
                    age = now - d.get("seen_time", now)
                    if age <= 5.0:
                        suffix = epc[-4:] if len(epc) >= 4 else epc
                        is_known = suffix in self.tag_suffixes
                        tag_type = "KNOWN" if is_known else "UNKNOWN"
                        tag_style = "known" if is_known else "unknown"
                        
                        self.tree_all.insert(
                            "", tk.END,
                            values=(
                                suffix,
                                tag_type,
                                epc,
                                f"{d.get('rssi', -99.0):.1f}",
                                f"{d.get('phase', 0.0):.0f}",
                                d.get("count", 0),
                                d.get("antenna", 1),
                                d.get("timestamp", ""),
                            ),
                            tags=(tag_style,)
                        )
        except Exception:
            pass

        self.update_timer = self.root.after(250, self.update_live_monitor)

    def _update_antenna_statistics(self, inv1: dict, inv2: dict):
        """Calculate and update statistics for both antennas with mode awareness."""
        # Helper to get stats for target tags in an inventory
        def calc_stats(inv: dict):
            rssi_vals = []
            total_reads = 0
            tags_seen = 0
            unknown_epcs = 0
            
            # Track known suffixes found
            known_suffixes_found = set()
            
            for epc, info in inv.items():
                suffix = epc[-4:] if len(epc) >= 4 else ""
                if suffix in self.tag_suffixes:
                    known_suffixes_found.add(suffix)
                    rssi = info.get("rssi", -99.0)
                    count = info.get("count", 0)
                    rssi_vals.append(rssi)
                    total_reads += count
                else:
                    unknown_epcs += 1
                    total_reads += info.get("count", 0)
            
            tags_seen = len(known_suffixes_found)
            
            if rssi_vals:
                r_min = min(rssi_vals)
                r_max = max(rssi_vals)
                r_avg = sum(rssi_vals) / len(rssi_vals)
                return {
                    "min": f"{r_min:.1f}",
                    "max": f"{r_max:.1f}",
                    "avg": f"{r_avg:.1f}",
                    "reads": total_reads,
                    "tags": tags_seen,
                    "unknown": unknown_epcs
                }
            return {"min": "-", "max": "-", "avg": "-", "reads": 0, "tags": 0, "unknown": unknown_epcs}
        
        stats1 = calc_stats(inv1)
        stats2 = calc_stats(inv2)
        
        total_tags = len(self.tag_suffixes)
        
        # Format text based on active antenna mode
        if 1 in self.current_antennas:
            txt1 = f"📊 Tags: {stats1['tags']}/{total_tags} | RSSI: {stats1['min']}/{stats1['max']}/{stats1['avg']} | Reads: {stats1['reads']} | Unknown: {stats1['unknown']}"
        else:
            txt1 = "⚫ DEVRE DIŞI (Sadece Ant2 aktif)"
        
        if 2 in self.current_antennas:
            txt2 = f"📊 Tags: {stats2['tags']}/{total_tags} | RSSI: {stats2['min']}/{stats2['max']}/{stats2['avg']} | Reads: {stats2['reads']} | Unknown: {stats2['unknown']}"
        else:
            txt2 = "⚫ DEVRE DIŞI (Sadece Ant1 aktif)"
        
        if hasattr(self, 'lbl_ant1_stats'):
            self.lbl_ant1_stats.config(text=txt1)
        if hasattr(self, 'lbl_ant2_stats'):
            self.lbl_ant2_stats.config(text=txt2)

    # =============================================================================
    # AFSUAM Protocol helpers
    # =============================================================================
    def _split_inventory_by_antenna(self, inventory: dict):
        inv1, inv2 = {}, {}
        for epc, info in inventory.items():
            try:
                ant = int(info.get("antenna", 1))
            except Exception:
                ant = 1
            if ant == 2:
                inv2[epc] = info
            else:
                inv1[epc] = info
        return inv1, inv2

    def _find_tag_info_by_suffix(self, inv: dict, suffix: str):
        """
        Returns dict with: seen(bool), epc(str|""), rssi(float|None), count(int|0)
        inv is per-antenna inventory (epc -> info).
        """
        for epc, info in inv.items():
            if epc.endswith(suffix):
                return {
                    "seen": True,
                    "epc": epc,
                    "rssi": float(info.get("rssi", -99.0)),
                    "count": int(info.get("count", 0)),
                }
        return {"seen": False, "epc": "", "rssi": None, "count": 0}

    def _collect_step(self, step_name: str, dwell_s: float, angle_deg: float, port_config: int,
                      station: str, ref_name: str, repeat_idx: int):
        # Apply beam state (Port1)
        self.current_port_config = int(port_config)
        self.var_port_config.set(self.current_port_config)

        self.scale_angle.set(angle_deg)
        self.on_angle_change(angle_deg)  # LUT->voltage->MCU (if connected)

        time.sleep(0.8)

        self.reader.clear_data()
        time.sleep(dwell_s)
        inv = self.reader.get_all_data()

        inv1, inv2 = self._split_inventory_by_antenna(inv)

        # step coverage sets
        ant1_epcs = set(inv1.keys())
        ant2_epcs = set(inv2.keys())

        ant1_targets = set()
        ant2_targets = set()
        ant1_target_data = []  # (suffix, rssi, count)
        ant2_target_data = []
        
        for epc, info in inv1.items():
            suf = epc[-4:] if len(epc) >= 4 else ""
            if suf in self.tag_suffixes:
                ant1_targets.add(suf)
                ant1_target_data.append((suf, info.get("rssi", -99.0), info.get("count", 0)))
        
        for epc, info in inv2.items():
            suf = epc[-4:] if len(epc) >= 4 else ""
            if suf in self.tag_suffixes:
                ant2_targets.add(suf)
                ant2_target_data.append((suf, info.get("rssi", -99.0), info.get("count", 0)))

        # Calculate per-antenna target stats
        def calc_target_stats(target_data):
            if not target_data:
                return {"total_reads": 0, "rssi_min": "", "rssi_max": "", "rssi_avg": ""}
            rssi_vals = [d[1] for d in target_data]
            total_reads = sum(d[2] for d in target_data)
            return {
                "total_reads": total_reads,
                "rssi_min": f"{min(rssi_vals):.1f}",
                "rssi_max": f"{max(rssi_vals):.1f}",
                "rssi_avg": f"{sum(rssi_vals)/len(rssi_vals):.1f}"
            }
        
        ant1_stats = calc_target_stats(ant1_target_data)
        ant2_stats = calc_target_stats(ant2_target_data)

        # Build missed lists - separate suffixes, labels, locations
        def get_label(suf):
            idx = self.tag_suffixes.index(suf) if suf in self.tag_suffixes else -1
            return self.tag_labels[idx] if 0 <= idx < len(self.tag_labels) else ""
        
        def get_location(suf):
            idx = self.tag_suffixes.index(suf) if suf in self.tag_suffixes else -1
            return self.tag_locations[idx] if 0 <= idx < len(self.tag_locations) else ""
        
        missed1_suffixes = sorted(list(set(self.tag_suffixes) - ant1_targets))
        missed2_suffixes = sorted(list(set(self.tag_suffixes) - ant2_targets))
        missed1_labels = [get_label(s) for s in missed1_suffixes]
        missed2_labels = [get_label(s) for s in missed2_suffixes]
        missed1_locations = [get_location(s) for s in missed1_suffixes]
        missed2_locations = [get_location(s) for s in missed2_suffixes]

        v1, v2 = self.lut.get_voltages(self.current_port_config, angle_deg)

        step_row = {
            "record_type": "STEP",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "station": station,
            "ref_antenna_name": ref_name,
            "repeat": repeat_idx,
            "beam_state": step_name,
            "port_config": int(self.current_port_config),
            "angle_deg": float(angle_deg),
            "v_ch1": float(v1),
            "v_ch2": float(v2),
            "dwell_s": float(dwell_s),
            "active_antennas": "|".join(map(str, self.current_antennas)),

            # Coverage counts
            "ant1_unique_epc_n": len(ant1_epcs),
            "ant2_unique_epc_n": len(ant2_epcs),
            "ant1_targets_seen_n": len(ant1_targets),
            "ant2_targets_seen_n": len(ant2_targets),
            "tags_total": len(self.tag_suffixes),

            # Per-antenna target stats
            "ant1_total_reads_targets": ant1_stats["total_reads"],
            "ant1_rssi_min_targets": ant1_stats["rssi_min"],
            "ant1_rssi_max_targets": ant1_stats["rssi_max"],
            "ant1_rssi_avg_targets": ant1_stats["rssi_avg"],
            
            "ant2_total_reads_targets": ant2_stats["total_reads"],
            "ant2_rssi_min_targets": ant2_stats["rssi_min"],
            "ant2_rssi_max_targets": ant2_stats["rssi_max"],
            "ant2_rssi_avg_targets": ant2_stats["rssi_avg"],

            # Missed - machine readable (separate columns)
            "ant1_missed_suffixes": "|".join(missed1_suffixes),
            "ant1_missed_labels": "|".join(missed1_labels),
            "ant1_missed_locations": "|".join(missed1_locations),
            "ant2_missed_suffixes": "|".join(missed2_suffixes),
            "ant2_missed_labels": "|".join(missed2_labels),
            "ant2_missed_locations": "|".join(missed2_locations),
        }

        # Per-tag per-step detail rows (TAGSTEP) - clean version with only ant1/ant2
        tagstep_rows = []
        for label, suffix, loc in zip(self.tag_labels, self.tag_suffixes, self.tag_locations):
            t1 = self._find_tag_info_by_suffix(inv1, suffix)
            t2 = self._find_tag_info_by_suffix(inv2, suffix)
            tagstep_rows.append({
                "record_type": "TAGSTEP",
                "timestamp": step_row["timestamp"],
                "station": station,
                "ref_antenna_name": ref_name,
                "repeat": repeat_idx,
                "beam_state": step_name,
                "port_config": int(self.current_port_config),
                "angle_deg": float(angle_deg),
                "dwell_s": float(dwell_s),
                "active_antennas": "|".join(map(str, self.current_antennas)),

                "tag_label": label,
                "tag_suffix": suffix,
                "tag_location": loc,

                "ant1_seen": int(bool(t1["seen"])),
                "ant1_rssi": "" if t1["rssi"] is None else f"{t1['rssi']:.1f}",
                "ant1_count": int(t1["count"]),
                "ant2_seen": int(bool(t2["seen"])),
                "ant2_rssi": "" if t2["rssi"] is None else f"{t2['rssi']:.1f}",
                "ant2_count": int(t2["count"]),
            })

        raw = {
            "ant1_epcs": ant1_epcs,
            "ant2_epcs": ant2_epcs,
            "ant1_targets": ant1_targets,
            "ant2_targets": ant2_targets,
            "ant1_target_data": ant1_target_data,
            "ant2_target_data": ant2_target_data,
        }
        return step_row, tagstep_rows, raw

    def run_afsuam_sweep_protocol(self, station_name: str, ref_name: str, dwell_s: float, repeats: int, port_config: int):
        if not self.reader or not self.reader.connected:
            raise RuntimeError("Reader is not connected.")
        
        # Updated check: use current_antennas instead of port_2_enabled
        # Protocol can run in any antenna mode now
        if not self.current_antennas:
            raise RuntimeError("No antennas are active.")
        
        if not self._ensure_mcu_connected_or_warn():
            raise RuntimeError("MCU not connected.")

        presets = self.lut.get_beam_presets(int(port_config))
        steps = [("LEFT", presets["LEFT"]), ("CENTER", presets["CENTER"]), ("RIGHT", presets["RIGHT"])]

        all_runs = []
        for r in range(1, int(repeats) + 1):
            union_ant1_epcs, union_ant2_epcs = set(), set()
            union_ant1_targets, union_ant2_targets = set(), set()
            
            # Track best beam per tag for this repeat
            # Structure: {suffix: {ant1: {beam, rssi, count}, ant2: {...}}}
            tag_best_beam = {s: {"ant1": {"beam": "MISS", "rssi": None, "count": 0}, 
                                  "ant2": {"beam": "MISS", "rssi": None, "count": 0}} 
                              for s in self.tag_suffixes}

            for beam_state, ang in steps:
                step_row, tagstep_rows, raw = self._collect_step(
                    step_name=beam_state,
                    dwell_s=float(dwell_s),
                    angle_deg=float(ang),
                    port_config=int(port_config),
                    station=station_name,
                    ref_name=ref_name,
                    repeat_idx=r
                )

                self.afsuam_step_rows.append(step_row)
                self.afsuam_tagstep_rows.extend(tagstep_rows)

                union_ant1_epcs |= raw["ant1_epcs"]
                union_ant2_epcs |= raw["ant2_epcs"]
                union_ant1_targets |= raw["ant1_targets"]
                union_ant2_targets |= raw["ant2_targets"]
                
                # Update best beam tracking from tagstep_rows
                for ts in tagstep_rows:
                    suf = ts["tag_suffix"]
                    # Ant1
                    if ts["ant1_seen"] == 1:
                        rssi = float(ts["ant1_rssi"]) if ts["ant1_rssi"] else -99.0
                        count = ts["ant1_count"]
                        curr = tag_best_beam[suf]["ant1"]
                        if curr["rssi"] is None or rssi > curr["rssi"] or (rssi == curr["rssi"] and count > curr["count"]):
                            tag_best_beam[suf]["ant1"] = {"beam": beam_state, "rssi": rssi, "count": count}
                    # Ant2
                    if ts["ant2_seen"] == 1:
                        rssi = float(ts["ant2_rssi"]) if ts["ant2_rssi"] else -99.0
                        count = ts["ant2_count"]
                        curr = tag_best_beam[suf]["ant2"]
                        if curr["rssi"] is None or rssi > curr["rssi"] or (rssi == curr["rssi"] and count > curr["count"]):
                            tag_best_beam[suf]["ant2"] = {"beam": beam_state, "rssi": rssi, "count": count}

            # Build best beam per tag strings
            ant1_best_beam = []
            ant1_best_rssi = []
            ant2_best_beam = []
            ant2_best_rssi = []
            for suf in self.tag_suffixes:
                bb1 = tag_best_beam[suf]["ant1"]
                bb2 = tag_best_beam[suf]["ant2"]
                ant1_best_beam.append(f"{suf}:{bb1['beam']}")
                ant1_best_rssi.append(f"{suf}:{bb1['rssi']:.1f}" if bb1["rssi"] else f"{suf}:MISS")
                ant2_best_beam.append(f"{suf}:{bb2['beam']}")
                ant2_best_rssi.append(f"{suf}:{bb2['rssi']:.1f}" if bb2["rssi"] else f"{suf}:MISS")

            # Build missed lists - machine readable
            def get_label(suf):
                idx = self.tag_suffixes.index(suf) if suf in self.tag_suffixes else -1
                return self.tag_labels[idx] if 0 <= idx < len(self.tag_labels) else ""
            
            def get_location(suf):
                idx = self.tag_suffixes.index(suf) if suf in self.tag_suffixes else -1
                return self.tag_locations[idx] if 0 <= idx < len(self.tag_locations) else ""
            
            union_missed1_suffixes = sorted(list(set(self.tag_suffixes) - union_ant1_targets))
            union_missed2_suffixes = sorted(list(set(self.tag_suffixes) - union_ant2_targets))
            union_missed1_labels = [get_label(s) for s in union_missed1_suffixes]
            union_missed2_labels = [get_label(s) for s in union_missed2_suffixes]
            union_missed1_locations = [get_location(s) for s in union_missed1_suffixes]
            union_missed2_locations = [get_location(s) for s in union_missed2_suffixes]

            # Ant2 health check
            ant2_health = "OK" if len(union_ant2_targets) > 0 else ("DISABLED" if 2 not in self.current_antennas else "NO_TAG_REPORTS")

            union_row = {
                "record_type": "UNION",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "station": station_name,
                "ref_antenna_name": ref_name,
                "repeat": r,
                "port_config": int(port_config),
                "dwell_s": float(dwell_s),
                "active_antennas": "|".join(map(str, self.current_antennas)),
                "tags_total": len(self.tag_suffixes),

                "union_ant1_unique_epc_n": len(union_ant1_epcs),
                "union_ant2_unique_epc_n": len(union_ant2_epcs),
                "union_ant1_targets_seen_n": len(union_ant1_targets),
                "union_ant2_targets_seen_n": len(union_ant2_targets),

                # Missed - machine readable
                "ant1_missed_suffixes": "|".join(union_missed1_suffixes),
                "ant1_missed_labels": "|".join(union_missed1_labels),
                "ant1_missed_locations": "|".join(union_missed1_locations),
                "ant2_missed_suffixes": "|".join(union_missed2_suffixes),
                "ant2_missed_labels": "|".join(union_missed2_labels),
                "ant2_missed_locations": "|".join(union_missed2_locations),

                # Best beam per tag
                "ant1_best_beam_per_tag": "|".join(ant1_best_beam),
                "ant1_best_rssi_per_tag": "|".join(ant1_best_rssi),
                "ant2_best_beam_per_tag": "|".join(ant2_best_beam),
                "ant2_best_rssi_per_tag": "|".join(ant2_best_rssi),
                
                # Health check
                "ant2_health": ant2_health,
            }
            self.afsuam_union_rows.append(union_row)
            all_runs.append(union_row)

        return all_runs

    def start_afsuam_protocol_thread(self):
        if not self.reader or not self.reader.connected:
            messagebox.showwarning("AFSUAM", "Connect reader first.")
            return

        station = self.ent_station.get().strip()
        ref_name = self.ent_ref_name.get().strip()
        if not ref_name:
            ref_name = "REF_ANT"

        try:
            dwell_s = float(self.ent_dwell.get().strip())
        except Exception:
            dwell_s = 3.0

        try:
            repeats = int(self.ent_repeats.get().strip())
        except Exception:
            repeats = 3

        try:
            port_config = int(self.cb_pc.get().strip())
        except Exception:
            port_config = int(self.current_port_config)

        # Determine protocol type based on antenna mode
        is_ant2_only = (self.current_antennas == [2])
        
        self.btn_run_protocol.config(state=tk.DISABLED)
        
        if is_ant2_only:
            # Simple inventory for Ant2-only (no beam steering needed)
            self.lbl_status.config(text=f"Running SIMPLE INVENTORY (Ant2 only): {station}")
            
            def worker():
                try:
                    self.run_simple_inventory_protocol(station, ref_name, dwell_s, repeats)
                    self._log(f"Simple inventory complete: station={station}, repeats={repeats}, dwell={dwell_s:.1f}s")
                    self.root.after(0, self.refresh_union_table)
                    self.root.after(0, lambda: self.lbl_status.config(text="Simple inventory done."))
                except Exception as e:
                    self._log(f"Protocol error: {e}")
                    self.root.after(0, lambda: messagebox.showerror("AFSUAM", str(e)))
                    self.root.after(0, lambda: self.lbl_status.config(text="Protocol failed."))
                finally:
                    self.root.after(0, lambda: self.btn_run_protocol.config(state=tk.NORMAL))
            
            threading.Thread(target=worker, daemon=True).start()
        else:
            # L-C-R sweep protocol for Ant1 or Ant1+Ant2 (beam steering active)
            if not self._ensure_mcu_connected_or_warn():
                self.btn_run_protocol.config(state=tk.NORMAL)
                return
            
            self.lbl_status.config(text=f"Running L-C-R Protocol: {station} | Ref={ref_name} | PC={port_config}")
            
            def worker():
                try:
                    self.run_afsuam_sweep_protocol(station, ref_name, dwell_s, repeats, port_config)
                    self._log(f"L-C-R Protocol complete: station={station}, repeats={repeats}, dwell={dwell_s:.1f}s, pc={port_config}")
                    self.root.after(0, self.refresh_union_table)

                    last = self.afsuam_union_rows[-1] if self.afsuam_union_rows else None
                    if last:
                        msg = (
                            f"Done. Union targets: Ant1={last['union_ant1_targets_seen_n']}/{len(self.tag_suffixes)} "
                            f"Ant2={last['union_ant2_targets_seen_n']}/{len(self.tag_suffixes)}"
                        )
                        self.root.after(0, lambda: self.lbl_status.config(text=msg))

                except Exception as e:
                    self._log(f"Protocol error: {e}")
                    self.root.after(0, lambda: messagebox.showerror("AFSUAM", str(e)))
                    self.root.after(0, lambda: self.lbl_status.config(text="Protocol failed."))
                finally:
                    self.root.after(0, lambda: self.btn_run_protocol.config(state=tk.NORMAL))

            threading.Thread(target=worker, daemon=True).start()

    def run_simple_inventory_protocol(self, station_name: str, ref_name: str, dwell_s: float, repeats: int):
        """
        Simple inventory protocol for Ant2-only mode (no beam steering).
        Collects per-tag statistics: RSSI min/max/avg, read count, location.
        """
        if not self.reader or not self.reader.connected:
            raise RuntimeError("Reader is not connected.")
        
        if not self.current_antennas:
            raise RuntimeError("No antennas are active.")

        for r in range(1, int(repeats) + 1):
            self._log(f"Simple Inventory repeat {r}/{repeats}: dwell={dwell_s:.1f}s")
            
            # Clear and collect
            self.reader.clear_data()
            time.sleep(dwell_s)
            inv = self.reader.get_all_data()

            # Split by antenna (even though we expect only Ant2 data)
            inv1, inv2 = self._split_inventory_by_antenna(inv)
            
            # Determine which inventory to use based on mode
            active_inv = inv2 if self.current_antennas == [2] else inv

            # Calculate per-tag statistics
            tag_stats = []
            all_rssi = []
            total_reads = 0
            tags_seen = 0
            
            for label, suffix, loc in zip(self.tag_labels, self.tag_suffixes, self.tag_locations):
                tag_info = None
                for epc, info in active_inv.items():
                    if epc.endswith(suffix):
                        tag_info = info
                        break
                
                if tag_info:
                    rssi = float(tag_info.get("rssi", -99.0))
                    count = int(tag_info.get("count", 0))
                    phase = float(tag_info.get("phase", 0.0))
                    
                    all_rssi.append(rssi)
                    total_reads += count
                    tags_seen += 1
                    
                    tag_stats.append({
                        "tag_label": label,
                        "tag_suffix": suffix,
                        "tag_location": loc,
                        "seen": 1,
                        "rssi": rssi,
                        "count": count,
                        "phase": phase,
                    })
                else:
                    tag_stats.append({
                        "tag_label": label,
                        "tag_suffix": suffix,
                        "tag_location": loc,
                        "seen": 0,
                        "rssi": None,
                        "count": 0,
                        "phase": None,
                    })

            # Calculate aggregate stats
            if all_rssi:
                rssi_min = min(all_rssi)
                rssi_max = max(all_rssi)
                rssi_avg = sum(all_rssi) / len(all_rssi)
            else:
                rssi_min = rssi_max = rssi_avg = None

            # Build summary row
            summary_row = {
                "record_type": "SIMPLE_INV",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "station": station_name,
                "ref_antenna_name": ref_name,
                "repeat": r,
                "dwell_s": float(dwell_s),
                "active_antennas": "|".join(map(str, self.current_antennas)),
                "tags_seen": tags_seen,
                "tags_total": len(self.tag_suffixes),
                "total_reads": total_reads,
                "rssi_min": f"{rssi_min:.1f}" if rssi_min else "",
                "rssi_max": f"{rssi_max:.1f}" if rssi_max else "",
                "rssi_avg": f"{rssi_avg:.1f}" if rssi_avg else "",
            }
            
            # Store for export
            self.afsuam_step_rows.append(summary_row)
            
            # Store per-tag detail rows
            for ts in tag_stats:
                tagstep_row = {
                    "record_type": "SIMPLE_TAG",
                    "timestamp": summary_row["timestamp"],
                    "station": station_name,
                    "ref_antenna_name": ref_name,
                    "repeat": r,
                    "dwell_s": float(dwell_s),
                    "active_antennas": "|".join(map(str, self.current_antennas)),
                    "tag_label": ts["tag_label"],
                    "tag_suffix": ts["tag_suffix"],
                    "tag_location": ts["tag_location"],
                    "seen": ts["seen"],
                    "rssi": f"{ts['rssi']:.1f}" if ts["rssi"] else "",
                    "count": ts["count"],
                    "phase": f"{ts['phase']:.1f}" if ts["phase"] else "",
                }
                self.afsuam_tagstep_rows.append(tagstep_row)
            
            # Build missed list with labels
            def suffix_with_label(suf):
                idx = self.tag_suffixes.index(suf) if suf in self.tag_suffixes else -1
                if idx >= 0 and idx < len(self.tag_labels):
                    return f"{suf}({self.tag_labels[idx]})"
                return suf
            
            seen_suffixes = {ts["tag_suffix"] for ts in tag_stats if ts["seen"]}
            missed_suffixes = sorted(list(set(self.tag_suffixes) - seen_suffixes))
            missed_labels = [suffix_with_label(s) for s in missed_suffixes]
            
            # Create a union-style row for display in union table
            union_row = {
                "record_type": "SIMPLE_UNION",
                "timestamp": summary_row["timestamp"],
                "station": station_name,
                "ref_antenna_name": ref_name,
                "repeat": r,
                "port_config": "-",
                "dwell_s": float(dwell_s),
                "active_antennas": "|".join(map(str, self.current_antennas)),
                "union_ant1_unique_epc_n": 0,
                "union_ant2_unique_epc_n": len(active_inv),
                "union_ant1_targets_seen_n": 0,
                "union_ant2_targets_seen_n": tags_seen,
                "union_ant1_missed_targets": "",
                "union_ant2_missed_targets": "|".join(missed_labels),
            }
            self.afsuam_union_rows.append(union_row)

    def refresh_union_table(self):
        self.tree_union.delete(*self.tree_union.get_children())
        for u in self.afsuam_union_rows[-250:]:
            self.tree_union.insert(
                "", tk.END,
                values=(
                    u.get("station", ""),
                    u.get("ref_antenna_name", ""),
                    u.get("repeat", ""),
                    u.get("port_config", ""),
                    u.get("dwell_s", ""),
                    u.get("union_ant1_targets_seen_n", ""),
                    u.get("union_ant2_targets_seen_n", ""),
                    u.get("union_ant1_unique_epc_n", ""),
                    u.get("union_ant2_unique_epc_n", ""),
                    u.get("union_ant1_missed_targets", ""),
                    u.get("union_ant2_missed_targets", ""),
                )
            )

    def clear_afsuam_results(self):
        self.afsuam_step_rows = []
        self.afsuam_tagstep_rows = []
        self.afsuam_union_rows = []
        self.tree_union.delete(*self.tree_union.get_children())
        self._log("Cleared AFSUAM protocol results.")
        self.lbl_status.config(text="AFSUAM results cleared.")

    # =============================================================================
    # Export
    # =============================================================================
    def export_live_snapshot(self):
        if not self.reader or not self.reader.connected:
            messagebox.showwarning("Export", "Connect reader first.")
            return

        inv = self.reader.get_all_data()
        if not inv:
            messagebox.showwarning("Export", "No inventory data available.")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"live_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not filename:
            return

        v1, v2 = self.lut.get_voltages(int(self.current_port_config), float(self.current_angle))
        try:
            with open(filename, "w", newline="") as f:
                wr = csv.writer(f)
                wr.writerow([
                    "timestamp", "epc", "suffix", "count", "rssi", "phase", "doppler", "antenna",
                    "port_config", "angle_deg", "v_ch1", "v_ch2"
                ])
                for epc, info in inv.items():
                    wr.writerow([
                        info.get("timestamp", ""),
                        epc,
                        epc[-4:],
                        info.get("count", 0),
                        info.get("rssi", -99.0),
                        info.get("phase", 0.0),
                        info.get("doppler", 0.0),
                        info.get("antenna", 1),
                        int(self.current_port_config),
                        float(self.current_angle),
                        f"{v1:.3f}",
                        f"{v2:.3f}",
                    ])
            self._log(f"Exported live snapshot: {filename} (N={len(inv)})")
            messagebox.showinfo("Export", f"Saved: {filename}")
        except Exception as e:
            messagebox.showerror("Export", str(e))

    def export_afsuam_csv(self):
        if not (self.afsuam_step_rows or self.afsuam_tagstep_rows or self.afsuam_union_rows):
            messagebox.showwarning("Export", "No protocol results to export.")
            return

        # Determine antenna mode string for filename
        if self.current_antennas == [1]:
            ant_mode_str = "ANT1_ONLY"
        elif self.current_antennas == [2]:
            ant_mode_str = "ANT2_ONLY"
        else:
            ant_mode_str = "ANT1_ANT2"
        
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        station = self.ent_station.get().strip().replace(" ", "_") or "STATION"
        
        default_filename = f"protocol_{station}_{ant_mode_str}_{timestamp_str}.csv"

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=default_filename
        )
        if not filename:
            return

        # Build run metadata
        try:
            reader_ip = self.ent_ip.get().strip()
            tx_power = self.ent_pwr.get().strip()
        except:
            reader_ip = "unknown"
            tx_power = "unknown"
        
        try:
            mcu_port = self.cb_port.get().strip() if self.serial and self.serial.is_open else "disconnected"
            mcu_connected = 1 if self.serial and self.serial.is_open else 0
        except:
            mcu_port = "unknown"
            mcu_connected = 0
        
        reader_connected = 1 if self.reader and self.reader.connected else 0
        
        # Get protocol params from last run if available
        try:
            dwell_s = float(self.ent_dwell.get().strip())
            repeats = int(self.ent_repeats.get().strip())
            port_config = int(self.cb_pc.get().strip())
        except:
            dwell_s = 3.0
            repeats = 3
            port_config = 0
        
        run_metadata = {
            "run_id": timestamp_str,
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "software_version": "CalibV4_AFSUAM_v2.2",
            "station": station,
            "reader_ip": reader_ip,
            "reader_connected": reader_connected,
            "tx_power_dbm": tx_power,
            "mcu_port": mcu_port,
            "mcu_connected": mcu_connected,
            "active_antennas": "|".join(map(str, self.current_antennas)),
            "antenna_mode": ant_mode_str,
            "port2_enabled": 1 if 2 in self.current_antennas else 0,
            "protocol_dwell_s": dwell_s,
            "protocol_repeats": repeats,
            "port_config": port_config,
            "beam_sequence": "LEFT|CENTER|RIGHT",
            "lut_file": self.lut.csv_path if self.lut.loaded else "not_loaded",
            "total_tags_configured": len(self.tag_suffixes),
            "tag_suffixes": "|".join(self.tag_suffixes),
        }

        # STEP headers - all fields including new RSSI stats
        step_headers = [
            "record_type", "timestamp", "station", "ref_antenna_name", "repeat",
            "beam_state", "port_config", "angle_deg", "v_ch1", "v_ch2", "dwell_s",
            "active_antennas", "tags_total",
            "ant1_unique_epc_n", "ant2_unique_epc_n", 
            "ant1_targets_seen_n", "ant2_targets_seen_n",
            "ant1_total_reads_targets", "ant1_rssi_min_targets", "ant1_rssi_max_targets", "ant1_rssi_avg_targets",
            "ant2_total_reads_targets", "ant2_rssi_min_targets", "ant2_rssi_max_targets", "ant2_rssi_avg_targets",
            "ant1_missed_suffixes", "ant1_missed_labels", "ant1_missed_locations",
            "ant2_missed_suffixes", "ant2_missed_labels", "ant2_missed_locations",
            # Simple inventory fields (for SIMPLE_INV records)
            "tags_seen", "total_reads", "rssi_min", "rssi_max", "rssi_avg"
        ]

        # TAGSTEP headers - clean version
        tagstep_headers = [
            "record_type", "timestamp", "station", "ref_antenna_name", "repeat",
            "beam_state", "port_config", "angle_deg", "dwell_s",
            "active_antennas",
            "tag_label", "tag_suffix", "tag_location",
            "ant1_seen", "ant1_rssi", "ant1_count",
            "ant2_seen", "ant2_rssi", "ant2_count",
            # Simple tag fields (for SIMPLE_TAG records)
            "seen", "rssi", "count", "phase"
        ]

        # UNION headers - with best beam per tag
        union_headers = [
            "record_type", "timestamp", "station", "ref_antenna_name", "repeat",
            "port_config", "dwell_s", "active_antennas", "tags_total",
            "union_ant1_unique_epc_n", "union_ant2_unique_epc_n",
            "union_ant1_targets_seen_n", "union_ant2_targets_seen_n",
            "ant1_missed_suffixes", "ant1_missed_labels", "ant1_missed_locations",
            "ant2_missed_suffixes", "ant2_missed_labels", "ant2_missed_locations",
            "ant1_best_beam_per_tag", "ant1_best_rssi_per_tag",
            "ant2_best_beam_per_tag", "ant2_best_rssi_per_tag",
            "ant2_health"
        ]

        try:
            with open(filename, "w", newline="") as f:
                wr = csv.writer(f)

                # Write run metadata header
                wr.writerow(["# RUN_METADATA"])
                for key, val in run_metadata.items():
                    wr.writerow([f"# {key}", val])
                wr.writerow([])

                wr.writerow(["# STEP_ROWS"])
                wr.writerow(step_headers)
                for r in self.afsuam_step_rows:
                    wr.writerow([r.get(h, "") for h in step_headers])

                wr.writerow([])
                wr.writerow(["# TAGSTEP_ROWS"])
                wr.writerow(tagstep_headers)
                for r in self.afsuam_tagstep_rows:
                    wr.writerow([r.get(h, "") for h in tagstep_headers])

                wr.writerow([])
                wr.writerow(["# UNION_ROWS"])
                wr.writerow(union_headers)
                for u in self.afsuam_union_rows:
                    wr.writerow([u.get(h, "") for h in union_headers])

            self._log(
                f"Exported: {filename} | mode={ant_mode_str} | "
                f"steps={len(self.afsuam_step_rows)} tagsteps={len(self.afsuam_tagstep_rows)} union={len(self.afsuam_union_rows)}"
            )
            messagebox.showinfo("Export", f"Saved: {filename}\n\nMode: {ant_mode_str}\nStation: {station}")
        except Exception as e:
            messagebox.showerror("Export", str(e))

    # =============================================================================
    # Logging
    # =============================================================================
    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        try:
            self.txt_log.insert(tk.END, line)
            self.txt_log.see(tk.END)
        except Exception:
            pass
        print(line, end="")

    # =============================================================================
    # Shutdown
    # =============================================================================
    def on_closing(self):
        if self.update_timer:
            try:
                self.root.after_cancel(self.update_timer)
            except Exception:
                pass

        try:
            if self.serial and self.serial.is_open:
                self.serial.write(b"SET1:0.000\nSET2:0.000\n")
                time.sleep(0.05)
                self.serial.close()
        except Exception:
            pass

        try:
            if self.reader:
                self.reader.disconnect()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass

        try:
            os._exit(0)
        except Exception:
            pass


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = CalibV4GUI(root)
    root.mainloop()
