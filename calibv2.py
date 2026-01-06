import tkinter as tk
import os
from tkinter import ttk, messagebox, filedialog
import serial
import serial.tools.list_ports
import time
import numpy as np
from scipy.interpolate import interp1d
import io
import pandas as pd
import csv
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import threading
import logging

# LLRP / SLLURP IMPORTS
try:
    from sllurp.llrp import LLRPReaderConfig, LLRPReaderClient, LLRP_DEFAULT_PORT, LLRPReaderState
    from twisted.internet import reactor
    SLLURP_AVAILABLE = True
except ImportError:
    SLLURP_AVAILABLE = False
    print("CRITICAL: 'sllurp' or 'twisted' library not found.")

class BeamSteerLUT:
    def __init__(self, csv_path):
        self.loaded = False
        try:
            # 1. Dosya var mı kontrol et
            if not os.path.exists(csv_path):
                print(f"CRITICAL ERROR: LUT file not found at {csv_path}")
                return

            # 2. Akıllı Okuma (Virgül veya Noktalı Virgül ayrımını otomatik yap)
            try:
                self.df = pd.read_csv(csv_path, sep=None, engine='python')
            except:
                # Fallback: Zorla virgül dene
                self.df = pd.read_csv(csv_path, sep=',')

            # 3. Sütun isimlerini temizle (Başlardaki/sonlardaki boşlukları sil)
            self.df.columns = [c.strip() for c in self.df.columns]
            print(f"LUT Columns Found: {list(self.df.columns)}")

            # 4. Scan_Mode sütununu temizle (Satırlardaki boşlukları sil)
            if 'Scan_Mode' in self.df.columns:
                self.df['Scan_Mode'] = self.df['Scan_Mode'].astype(str).str.strip()
            else:
                print("ERROR: 'Scan_Mode' column missing in CSV!")
                return

            # 5. Verileri Ayır
            self.h_plane = self.df[self.df['Scan_Mode'] == 'H-Plane']
            self.e_plane = self.df[self.df['Scan_Mode'] == 'E-Plane']
            
            # Veri var mı kontrol et
            if self.h_plane.empty and self.e_plane.empty:
                print("ERROR: CSV loaded but no 'H-Plane' or 'E-Plane' rows found. Check CSV content.")
                return

            self.interp_h = {}
            self.interp_e = {}
            
            # Beklenen Sütunlar (Yeni format: V_CH1, V_CH2)
            self.cols = ['V_CH1', 'V_CH2', 'Est_Gain_dBi', 'Est_SLL_dB']
            
            # İnterpolasyon Fonksiyonlarını Oluştur
            for col in self.cols:
                # Eğer sütun CSV'de yoksa, hata verme, 0.0 ile doldur
                if col not in self.df.columns:
                    print(f"Warning: Column '{col}' missing in CSV. Using 0.0 defaults.")
                    continue

                if not self.h_plane.empty:
                    self.interp_h[col] = interp1d(self.h_plane['Target_Angle'], self.h_plane[col], kind='linear', fill_value="extrapolate")
                
                if not self.e_plane.empty:
                    self.interp_e[col] = interp1d(self.e_plane['Target_Angle'], self.e_plane[col], kind='linear', fill_value="extrapolate")
            
            self.loaded = True
            print(f"LUT Loaded Successfully. H-Plane Points: {len(self.h_plane)}, E-Plane Points: {len(self.e_plane)}")

        except Exception as e:
            print(f"Error loading Steer LUT: {e}")
            import traceback
            traceback.print_exc()
            self.loaded = False

    def get_data(self, mode, angle):
        if not self.loaded: return None
        interp = self.interp_h if mode == 'H-Plane' else self.interp_e
        
        if not interp: return None # Veri seti boşsa

        ret = {}
        for col in self.cols:
            if col in interp:
                try:
                    val = float(interp[col](angle))
                    # Voltajları sınırla (0 - 8.5V)
                    if col.startswith('V_'):
                        val = max(0.0, min(8.5, val))
                    ret[col] = val
                except:
                    ret[col] = 0.0
            else:
                ret[col] = 0.0 # Sütun yoksa 0 dön
        return ret

    def get_active_voltages(self, mode, angle):
        """Returns (v_ch1, v_ch2) directly based on CSV data"""
        data = self.get_data(mode, angle)
        if not data: return 0.0, 0.0
        return data.get('V_CH1', 0.0), data.get('V_CH2', 0.0)

# -----------------------------------------------------------------------------
# 1. LUT ENGINE
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# 1. LUT ENGINE
# -----------------------------------------------------------------------------
class PhaseLUT:
    def __init__(self):
        try:
            # Try loading local CSV first which has high resolution (0.025V)
            df = pd.read_csv("phase_lut.csv")
            print("Loaded phase_lut.csv successfully.")
        except Exception as e:
            print(f"Warning: Could not load phase_lut.csv ({e}). Using fallback data.")
            # Fallback data (Low Res) - simplified for fallback
            fallback_data = """Control Voltage (V),Olcum1_Shift,Olcum2_Shift
0.0,0.0,0.0
8.5,363.42,363.43"""
            df = pd.read_csv(io.StringIO(fallback_data))

        self.voltage = df['Control Voltage (V)'].values
        
        # Extract columns for P1 and P4 (Olcum1 -> P1, Olcum2 -> P4)
        if 'Olcum1_Shift' in df.columns and 'Olcum2_Shift' in df.columns:
            self.phase1 = df['Olcum1_Shift'].values
            self.phase4 = df['Olcum2_Shift'].values
        else:
            # Fallback if columns missing, use col 1 for both
            self.phase1 = df.iloc[:, 1].values
            self.phase4 = df.iloc[:, 1].values

        # Interpolators for symmetric mapping
        self.func_p1_to_v = interp1d(self.phase1, self.voltage, kind='linear', fill_value="extrapolate")
        self.func_p4_to_v = interp1d(self.phase4, self.voltage, kind='linear', fill_value="extrapolate")
        
        # Interpolators for Voltage -> Phase
        self.func_v_to_p1 = interp1d(self.voltage, self.phase1, kind='linear', fill_value="extrapolate")
        self.func_v_to_p4 = interp1d(self.voltage, self.phase4, kind='linear', fill_value="extrapolate")

    def get_voltage(self, target_phase, channel=1):
        # Remove modulo 360 to prevent jump from 8.5V back to 0V during sweep
        if channel == 4:
            v = float(self.func_p4_to_v(target_phase))
        else:
            v = float(self.func_p1_to_v(target_phase))
        return max(0.0, min(8.5, v))

    def get_phase(self, voltage, channel=1):
        v = max(0.0, min(8.5, float(voltage)))
        if channel == 4:
            return float(self.func_v_to_p4(v)) % 360.0
        return float(self.func_v_to_p1(v)) % 360.0

# ... (Reader class remains same) ...

    # IN CALC_PHASES:
    def calc_phases(self):
        try:
            az = float(self.ent_az.get())
            el = float(self.ent_el.get())
            v_offset = float(self.ent_offset.get())
            
            # Additional manual correction
            try: off_p1 = float(self.ent_off_p1.get())
            except: off_p1 = 0.0
            try: off_p4 = float(self.ent_off_p4.get())
            except: off_p4 = 0.0

            # Visualizer Update
            vis_az = "CENTER"
            if az > 5: vis_az = "RIGHT (>>>)"
            elif az < -5: vis_az = "LEFT (<<<)"
            
            vis_el = "LEVEL"
            if el > 5: vis_el = "UP (^^^)"
            elif el < -5: vis_el = "DOWN (vvv)"
            
            self.lbl_vis_dir.config(text=f"{vis_az}\n{vis_el}")

            # Initial Phase from Boresight Voltage (Using P1 curve as reference for "System Phase")
            # This is an approximation. Ideally we calibrate P1 and P4 to yield 0 phase Diff at Boresight.
            # Here we assume v_offset gives the base phase on P1.
            phi_calib = float(self.lut.func_v_to_p1(v_offset))
            
            rad_az = np.radians(az)
            rad_el = np.radians(el)
            
            # Formula: 180 * sin(theta) fits lambda/2 spacing
            phi_comm = 180.0 * np.sin(rad_az)
            phi_diff = 180.0 * np.sin(rad_el)
            
            # Derived Phases
            phi_p4 = phi_calib + phi_comm + (phi_diff / 2.0)
            phi_p1 = phi_calib + phi_comm - (phi_diff / 2.0)
            
            phi_p4 %= 360.0
            phi_p1 %= 360.0
            
            # Convert back to Voltage using SPECIFIC CHANNEL LUTs
            v_p4 = self.lut.get_voltage(phi_p4, channel=4) + off_p4
            v_p1 = self.lut.get_voltage(phi_p1, channel=1) + off_p1
            
            # Cap voltages
            v_p4 = max(0.0, min(8.5, v_p4))
            v_p1 = max(0.0, min(8.5, v_p1))
            
            self.calc_res = (v_p1, v_p4)
            
            self.lbl_res_p1.config(text=f"P1: {v_p1:.2f}V ({phi_p1:.0f}°)")
            self.lbl_res_p4.config(text=f"P4: {v_p4:.2f}V ({phi_p4:.0f}°)")
            return v_p1, v_p4
            
        except Exception as e:
            print(f"Calc error: {e}")
            messagebox.showerror("Calc Error", str(e))
            return None, None

# ...
# -----------------------------------------------------------------------------
# 2. READER WRAPPER (FIXED: Protocol Handling)
# -----------------------------------------------------------------------------
class RealLLRPReader:
    def __init__(self):
        self.inventory = {} 
        self.connected = False
        self.inventory_running = False
        self.target_epc = None
        
        # Sllurp objects
        self.reader_client = None
        self.active_protocol = None # The actual connection handle
        self.reactor_thread = None
        self.last_disconnect_time = 0
        self.lock = threading.Lock()


    def _calculate_power_index(self, dbm):
        # User provided table: 10.00dBm -> Index 1, 33.00dBm -> Index 93. Step 0.25dBm.
        # Formula: Index = (dBm - 10.0) / 0.25 + 1
        # Clamp dBm first
        if dbm < 10.0: dbm = 10.0
        if dbm > 33.0: dbm = 33.0
        
        index = int((dbm - 10.0) / 0.25) + 1
        return max(1, min(93, index))

    def _create_config(self, power=20.0, mode=1002, session=0, search_mode='2'):
        power_idx = self._calculate_power_index(power)
        print(f"Power: {power} dBm -> Index: {power_idx}")
        print(f"Mode: {mode}, Session: {session}, Search Mode: {search_mode}")
        
        # Cleaned up factory_args to match R420GUI/reader.py structure more closely
        factory_args = {
            'tx_power': power_idx,
            'mode_identifier': mode,    # Reader mode from UI
            'report_every_n_tags': 10,
            'start_inventory': True,
            'tag_content_selector': {
                'EnableROSpecID': True, 'EnableSpecIndex': True, 'EnableInventoryParameterSpecID': True,
                'EnableAntennaID': True, 'EnableChannelIndex': True, 'EnablePeakRSSI': True,
                'EnableFirstSeenTimestamp': True, 'EnableLastSeenTimestamp': True,
                'EnableTagSeenCount': True, 'EnableAccessSpecID': True,
                'C1G2EPCMemorySelector': {'EnableCRC': True, 'EnablePCBits': True}
            },
            'impinj_extended_configuration': True, 
            'impinj_reports': True,
            'impinj_search_mode': str(search_mode),  # Search mode from UI
            'session': session,              # Session from UI
            'receive_sensitivity_table_entry_index': 0, # MAX SENSITIVITY
            'impinj_tag_content_selector': {
                'EnableRFPhaseAngle': True, 
                'EnablePeakRSSI': True, 
                'EnableRFDopplerFrequency': True,
                'EnableOptimizerOne': False
            }
        }
        return LLRPReaderConfig(factory_args)

    def connect(self, ip_address, power_dbm, mode=1002, session=0, search_mode='2'):
        if not SLLURP_AVAILABLE: return False
        
        # Connection Throttling: Ensure we don't reconnect too fast
        time_since_disc = time.time() - self.last_disconnect_time
        if time_since_disc < 3.0:
            wait_time = 3.0 - time_since_disc
            print(f"Throttling connection... waiting {wait_time:.2f}s")
            time.sleep(wait_time)

        self.last_ip = ip_address
        self.last_power = power_dbm
        
        if self.connected:
            print("Already connected. Disconnecting first...")
            self.disconnect_reader()
            time.sleep(1.0) # Grace time for cleanup

        try:
            print(f"Connecting to {ip_address}...")
            config = self._create_config(power=power_dbm, mode=mode, session=session, search_mode=search_mode)
            self.reader_client = LLRPReaderClient(ip_address, LLRP_DEFAULT_PORT, config)
            self.reader_client.add_tag_report_callback(self._on_tag_report)
            self.reader_client.add_state_callback(LLRPReaderState.STATE_CONNECTED, self._on_state_change)
            self.reader_client.add_state_callback(LLRPReaderState.STATE_DISCONNECTED, self._on_state_change)
            
            # Reactor Thread (Background)
            if self.reactor_thread is None:
                self.reactor_thread = threading.Thread(target=self._run_reactor, daemon=True)
                self.reactor_thread.start()
            
            # Connect
            self.reader_client.connect()
            return True
        except Exception as e:
            print(f"Init Error: {e}")
            return False

    def disconnect_reader(self):
        """Robust disconnect helper"""
        if self.reader_client:
            print("Disconnecting reader...")
            try:
                self.reader_client.disconnect()
            except Exception as e:
                print(f"Disconnect error: {e}")
        self.connected = False
        self.inventory_running = False
        self.active_protocol = None
        self.last_disconnect_time = time.time()

    def _on_state_change(self, reader, state):
        print(f"Reader State: {state}")
        if state == LLRPReaderState.STATE_CONNECTED:
            self._on_protocol_connected(reader)
        elif state == LLRPReaderState.STATE_DISCONNECTED:
            self.connected = False

    def _on_protocol_connected(self, protocol):
        """Baglanti kuruldugunda calisir. Protokol nesnesini kaydeder."""
        print("LLRP Connected! Protocol Captured.")
        self.active_protocol = protocol
        self.connected = True
        self.inventory_running = True

    def _on_protocol_error(self, failure):
        print(f"Connection Failed: {failure}")
        self.connected = False

    def start_inventory(self):
        # Soft Resume
        self.inventory_running = True
        
        # Use update_config to ensure reader is active
        if self.reader_client:
            try:
                print("Updating config to START inventory...")
                self.reader_client.update_config({'start_inventory': True})
                print("Inventory STARTED (Processing Enabled).")
            except Exception as e:
                print(f"Start Error: {e}")
                
        # If completely disconnected matches logic in connect
        if not self.connected and hasattr(self, 'last_ip'):
             pass # Logic handled by button mapping usually

    def stop_inventory(self):
        # Soft Pause
        self.inventory_running = False
        
        # Attempt to stop reader level too
        if self.reader_client:
            try:
                print("Updating config to STOP inventory...")
                self.reader_client.update_config({'start_inventory': False})
                print("Inventory PAUSED (Processing Disabled).")
            except Exception as e:
                print(f"Stop Error: {e}")

    def clear_data(self):
        self.inventory = {}
        print("Data Cleared.")

    def _run_reactor(self):
        if not reactor.running:
            reactor.run(installSignalHandlers=False)

    def _on_tag_report(self, reader, tag_reports):
        # Soft Pause Check
        if not self.inventory_running:
            return

        # Eger protocol henuz yakalanmadiysa buradan yakala (Fallback)
        if self.active_protocol is None:
            self.active_protocol = reader

        for tag in tag_reports:
            try:
                # DEBUG: Inspect keys to find where Phase is hiding
                # print(f"Tag Keys: {list(tag.keys())}") 
                
                # Check if Impinj data is in a 'Custom' field
                # if 'Custom' in tag:
                #    print(f"Custom Field: {tag['Custom']}")

                # Improved Extraction Logic
                def get_val_any(keys, default=None):
                    for k in keys:
                        if k in tag:
                            v = tag[k]
                            if isinstance(v, dict):
                                return v.get('Value', default)
                            return v
                    return default

                epc_raw = tag.get('EPC-96') or tag.get('EPCUnknown') or get_val_any(['EPC'])
                
                # Robust EPC decode
                if epc_raw:
                     try:
                        if isinstance(epc_raw, bytes):
                           if len(epc_raw) == 24: epc = epc_raw.decode('utf-8').upper()
                           else: epc = epc_raw.hex().upper()
                        elif isinstance(epc_raw, int):
                            epc = hex(epc_raw)[2:].upper()
                        else: epc = str(epc_raw)
                     except: epc = "UNKNOWN_EPC"
                else: epc = "UNKNOWN_EPC"
                
                # Extract Phase (Added ImpinjRFPhaseAngle based on debug output)
                p_val = get_val_any(['ImpinjRFPhaseAngle', 'RFPhaseAngle', 'PhaseAngle', 'Phase'], None)
                
                # Fallback: Look in 'Custom' list if present
                if p_val is None and 'Custom' in tag:
                    for item in tag['Custom']:
                        if isinstance(item, dict):
                             if 'ImpinjRFPhaseAngle' in item: p_val = item['ImpinjRFPhaseAngle']
                             if 'RFPhaseAngle' in item: p_val = item['RFPhaseAngle']
                             if 'PhaseAngle' in item: p_val = item['PhaseAngle']

                if p_val is not None:
                    try:
                        p_float = float(p_val)
                        # Impinj scale: 0-4096 -> 0-360
                        phase_deg = (p_float / 4096.0) * 360.0
                        phase_deg = phase_deg % 360.0
                    except: phase_deg = 0.0
                else:
                    # DEBUG: Print only if missing
                    # print(f"DEBUG: Missing Phase! Keys: {list(tag.keys())}")
                    phase_deg = 0.0

                # Extract other fields (Added Impinj prefixes)
                rssi = float(get_val_any(['ImpinjPeakRSSI', 'PeakRSSI', 'RSSI'], -90))
                
                # Correction for Impinj High-Res RSSI (often x100, e.g. -5000 -> -50.00 dBm)
                if rssi < -150.0:  # Heuristic: RSSI is rarely below -100 dBm in normal operation
                    rssi = rssi / 100.0

                antenna = get_val_any(['AntennaID', 'Antenna'], 1)
                doppler = get_val_any(['ImpinjRFDopplerFrequency', 'RFDopplerFrequency', 'DopplerFrequency'], 0)
                
                print(f"Tag: {epc} | RSSI: {rssi:.2f} | Phase: {phase_deg:.1f}") 

                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                
                with self.lock:
                    count = self.inventory[epc]['count'] + 1 if epc in self.inventory else 1
                    
                    self.inventory[epc] = {
                        'antenna': antenna, 'epc': epc, 'timestamp': timestamp,
                        'count': count, 'rssi': rssi, 'phase_deg': phase_deg,
                        'doppler': doppler, 'seen_time': time.time()
                    }
            except Exception as e: 
                print(f"Tag processing error: {e}")
                pass

    def get_target_data(self):
        with self.lock:
            if self.target_epc and self.target_epc in self.inventory:
                d = self.inventory[self.target_epc]
                # Timeout (2 sn) - Return extra status bool
                is_visible = (time.time() - d['seen_time']) < 2.0
                return d['rssi'], d['phase_deg'], is_visible
        return -90.0, 0.0, False

    def get_all_data(self):
        with self.lock:
            return self.inventory.copy()

# -----------------------------------------------------------------------------
# 3. MAIN GUI
# -----------------------------------------------------------------------------
class MasterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Phased Array & RFID Controller v2.4 (Light Mode)")
        self.root.geometry("1400x900")
        
        self.setup_styles() # Apply Light Mode Styles
        
        # Kapatma Protokolü
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.lut = PhaseLUT()
        self.reader = RealLLRPReader()
        self.serial = None
        self.boresight_p = 0.0
        self.calibrated = False
        self.logging_active = False
        self.csv_file = None
        self.csv_writer = None
        self.steer_lut = BeamSteerLUT("calibration_lut.csv")
        self.beam_tag_positions = {}  # For tracking beam check tag positions
        
        # Auto-load beam tag configuration if exists
        try:
            import json
            with open('beam_tags_config.json', 'r') as f:
                self.beam_tag_positions = json.load(f)
            print(f"Auto-loaded beam tag config: {self.beam_tag_positions}")
        except FileNotFoundError:
            print("No saved beam tag config found, using defaults")
        except Exception as e:
            print(f"Error auto-loading config: {e}")
        
        self.loop_timer = None
        self.table_timer = None
        
        self.setup_ui()
        
        # Auto-fill entry fields from loaded config
        if self.beam_tag_positions:
            self.auto_fill_tag_entries()
        
        self.update_loops()
        self.update_table()

    def auto_fill_tag_entries(self):
        """Auto-fill tag entry fields from loaded config"""
        try:
            if hasattr(self, 'ent_tag_1') and 'T1' in self.beam_tag_positions:
                self.ent_tag_1.delete(0, tk.END); self.ent_tag_1.insert(0, self.beam_tag_positions.get('T1', ''))
            if hasattr(self, 'ent_tag_2') and 'T2' in self.beam_tag_positions:
                self.ent_tag_2.delete(0, tk.END); self.ent_tag_2.insert(0, self.beam_tag_positions.get('T2', ''))
            if hasattr(self, 'ent_tag_3') and 'T3' in self.beam_tag_positions:
                self.ent_tag_3.delete(0, tk.END); self.ent_tag_3.insert(0, self.beam_tag_positions.get('T3', ''))
            if hasattr(self, 'ent_tag_4') and 'T4' in self.beam_tag_positions:
                self.ent_tag_4.delete(0, tk.END); self.ent_tag_4.insert(0, self.beam_tag_positions.get('T4', ''))
            if hasattr(self, 'ent_tag_5') and 'T5' in self.beam_tag_positions:
                self.ent_tag_5.delete(0, tk.END); self.ent_tag_5.insert(0, self.beam_tag_positions.get('T5', ''))
            if hasattr(self, 'ent_tag_6') and 'T6' in self.beam_tag_positions:
                self.ent_tag_6.delete(0, tk.END); self.ent_tag_6.insert(0, self.beam_tag_positions.get('T6', ''))
            if hasattr(self, 'ent_tag_7') and 'T7' in self.beam_tag_positions:
                self.ent_tag_7.delete(0, tk.END); self.ent_tag_7.insert(0, self.beam_tag_positions.get('T7', ''))
            if hasattr(self, 'ent_tag_8') and 'T8' in self.beam_tag_positions:
                self.ent_tag_8.delete(0, tk.END); self.ent_tag_8.insert(0, self.beam_tag_positions.get('T8', ''))
            print("Auto-filled tag entries from config")
        except Exception as e:
            print(f"Error auto-filling entries: {e}")

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam') # Clean light theme
        
        # Configure standard colors for Light Mode
        style.configure(".", background="#FFFFFF", foreground="#000000", font=("Arial", 10))
        style.configure("TLabel", background="#FFFFFF", foreground="#000000")
        style.configure("TFrame", background="#FFFFFF")
        style.configure("TLabelframe", background="#FFFFFF", foreground="#000000")
        style.configure("TLabelframe.Label", background="#FFFFFF", foreground="#003366", font=("Arial", 11, "bold"))
        style.configure("TButton", padding=6, background="#E0E0E0")
        style.configure("Treeview", background="#FFFFFF", foreground="#000000", fieldbackground="#FFFFFF", font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"), background="#DDDDDD")
        
        # Custom styles
        style.configure("Highlight.TLabel", font=("Arial", 12, "bold"), foreground="#003366")
        
        self.root.configure(bg="#FFFFFF")

    def setup_ui(self):
        # --- SIDEBAR ---
        sidebar = ttk.Frame(self.root, width=320, padding=10)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        
        # HARDWARE
        hw_fr = ttk.LabelFrame(sidebar, text="Hardware Setup")
        hw_fr.pack(fill=tk.X, pady=5)
        
        ttk.Label(hw_fr, text="Microcontroller:").pack(anchor=tk.W)
        self.cb_port = ttk.Combobox(hw_fr, values=[p.device for p in serial.tools.list_ports.comports()])
        if self.cb_port['values']: self.cb_port.current(0)
        self.cb_port.pack(fill=tk.X, pady=2)
        ttk.Button(hw_fr, text="Connect MCU", command=self.connect_mcu).pack(fill=tk.X, pady=2)
        
        ttk.Separator(hw_fr, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        ttk.Label(hw_fr, text="Reader IP:").pack(anchor=tk.W)
        self.ent_ip = ttk.Entry(hw_fr); self.ent_ip.insert(0, "169.254.1.1")
        self.ent_ip.pack(fill=tk.X, pady=2)
        
        ttk.Label(hw_fr, text="Power (dBm):").pack(anchor=tk.W)
        self.ent_pwr = ttk.Entry(hw_fr); self.ent_pwr.insert(0, "25.5")
        self.ent_pwr.pack(fill=tk.X, pady=2)
        
        # --- READER SETTINGS (Advanced) ---
        settings_exp = ttk.Frame(hw_fr)
        settings_exp.pack(fill=tk.X, pady=2)
        
        self.var_show_reader_settings = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings_exp, text="⚙️ Gelişmiş Reader Ayarları", 
                        variable=self.var_show_reader_settings, 
                        command=self.toggle_reader_settings).pack(anchor=tk.W)
        
        self.reader_settings_fr = ttk.Frame(hw_fr)
        # Hidden by default, shown when checkbox is checked
        
        # Reader Mode
        ttk.Label(self.reader_settings_fr, text="Mode:", font=("Arial", 8)).grid(row=0, column=0, sticky=tk.W)
        self.cmb_reader_mode = ttk.Combobox(self.reader_settings_fr, width=18, state="readonly")
        self.cmb_reader_mode['values'] = [
            "1002 - AutoSet DenseRdr",  # Default - Deep Scan
            "1000 - AutoSet",
            "1003 - AutoSet Static Fast",
            "1004 - AutoSet Static Dense",
            "0 - Max Throughput",
            "1 - Hybrid",
            "2 - Dense Reader M4",
            "3 - Dense Reader M8",
            "4 - Max Miller"
        ]
        self.cmb_reader_mode.current(0)  # Default: 1002
        self.cmb_reader_mode.grid(row=0, column=1, padx=2, pady=1)
        
        # Session
        ttk.Label(self.reader_settings_fr, text="Session:", font=("Arial", 8)).grid(row=1, column=0, sticky=tk.W)
        self.cmb_session = ttk.Combobox(self.reader_settings_fr, width=18, state="readonly")
        self.cmb_session['values'] = [
            "0 - Fast cycle",
            "1 - Auto reset",
            "2 - Extended persist",
            "3 - Extended persist"
        ]
        self.cmb_session.current(0)  # Default: Session 0
        self.cmb_session.grid(row=1, column=1, padx=2, pady=1)
        
        # Search Mode
        ttk.Label(self.reader_settings_fr, text="Search:", font=("Arial", 8)).grid(row=2, column=0, sticky=tk.W)
        self.cmb_search_mode = ttk.Combobox(self.reader_settings_fr, width=18, state="readonly")
        self.cmb_search_mode['values'] = [
            "2 - Dual Target (Cont.)",  # Default for continuous reading
            "1 - Single Target",
            "3 - TagFocus",
            "0 - Reader Selected"
        ]
        self.cmb_search_mode.current(0)  # Default: Dual Target
        self.cmb_search_mode.grid(row=2, column=1, padx=2, pady=1)
        
        # Presets
        ttk.Label(self.reader_settings_fr, text="Preset:", font=("Arial", 8)).grid(row=3, column=0, sticky=tk.W)
        self.cmb_reader_preset = ttk.Combobox(self.reader_settings_fr, width=18, state="readonly")
        self.cmb_reader_preset['values'] = [
            "Custom",
            "📊 Beam Analysis (Default)",
            "📦 Stationary Tags",
            "🚪 Portal (Moving)",
            "🔍 Dense Environment"
        ]
        self.cmb_reader_preset.current(1)  # Default
        self.cmb_reader_preset.bind("<<ComboboxSelected>>", self.apply_reader_preset)
        self.cmb_reader_preset.grid(row=3, column=1, padx=2, pady=1)
        
        # READERS BUTTONS
        btn_fr = ttk.Frame(hw_fr)
        btn_fr.pack(fill=tk.X, pady=5)
        
        self.btn_connect_reader = ttk.Button(btn_fr, text="Connect", command=self.connect_reader)
        self.btn_connect_reader.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        
        self.btn_disconnect_reader = ttk.Button(btn_fr, text="Disconnect", command=self.disconnect_and_reset, state=tk.DISABLED)
        self.btn_disconnect_reader.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))
        
        # INVENTORY CONTROL
        ctrl_fr = ttk.LabelFrame(sidebar, text="Inventory Control")
        ctrl_fr.pack(fill=tk.X, pady=10)
        
        # Baslangicta Pasif, Baglaninca Aktif Olacak
        self.btn_start = ttk.Button(ctrl_fr, text="RESUME Inventory", command=self.start_inv, state=tk.DISABLED)
        self.btn_start.pack(fill=tk.X, pady=2)
        
        self.btn_stop = ttk.Button(ctrl_fr, text="PAUSE Inventory", command=self.stop_inv, state=tk.DISABLED)
        self.btn_stop.pack(fill=tk.X, pady=2)
        
        ttk.Button(ctrl_fr, text="CLEAR Data", command=self.clear_data).pack(fill=tk.X, pady=5)

        # TARGET
        tgt_fr = ttk.LabelFrame(sidebar, text="Target Selection")
        tgt_fr.pack(fill=tk.X, pady=10)
        self.lb_epcs = tk.Listbox(tgt_fr, height=6)
        self.lb_epcs.pack(fill=tk.X)
        ttk.Button(tgt_fr, text="Select & LOCK Target", command=self.lock_target).pack(fill=tk.X, pady=5)
        self.lbl_target = ttk.Label(tgt_fr, text="Target: NONE", foreground="red", font=("Arial", 10, "bold"))
        self.lbl_target.pack()
        
        # New Feature: Filter View
        self.var_filter_target = tk.BooleanVar()
        ttk.Checkbutton(tgt_fr, text="Focus Target Only (Filter Table)", variable=self.var_filter_target).pack(anchor=tk.W, pady=2)

        # LOGGER
        log_fr = ttk.LabelFrame(sidebar, text="Data Logging")
        log_fr.pack(fill=tk.X, pady=10)
        self.lbl_log = ttk.Label(log_fr, text="Status: Stopped", foreground="gray")
        self.lbl_log.pack()
        self.btn_log = ttk.Button(log_fr, text="START RECORDING", command=self.toggle_log)
        self.btn_log.pack(fill=tk.X, pady=5)

        # MAIN AREA
        main = ttk.Frame(self.root, padding=10)
        main.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        mon_fr = ttk.LabelFrame(main, text="Target Monitor")
        mon_fr.pack(fill=tk.X)
        grid_fr = ttk.Frame(mon_fr)
        grid_fr.pack(padx=10, pady=10)
        
        # Enhanced Monitor UI
        ttk.Label(grid_fr, text="Status:").grid(row=0, column=0, padx=10)
        self.val_status = ttk.Label(grid_fr, text="UNKNOWN", font=("Arial", 14, "bold"), foreground="gray")
        self.val_status.grid(row=0, column=1, padx=10)
        
        ttk.Label(grid_fr, text="RSSI:").grid(row=0, column=2, padx=10)
        self.val_rssi = ttk.Label(grid_fr, text="-- dBm", font=("Arial", 16, "bold"), foreground="blue")
        self.val_rssi.grid(row=0, column=3, padx=10)
        
        ttk.Label(grid_fr, text="Phase:").grid(row=0, column=4, padx=10)
        self.val_phase = ttk.Label(grid_fr, text="-- °", font=("Arial", 16, "bold"), foreground="green")
        self.val_phase.grid(row=0, column=5, padx=10)
        
        ttk.Label(grid_fr, text="Angle:").grid(row=0, column=6, padx=10)
        self.val_angle = ttk.Label(grid_fr, text="0°", font=("Arial", 16, "bold"), foreground="purple")
        self.val_angle.grid(row=0, column=7, padx=10)

        nb = ttk.Notebook(main)
        nb.pack(fill=tk.BOTH, expand=True, pady=10)
        self.tab_calib = ttk.Frame(nb); nb.add(self.tab_calib, text="Calibrate")
        self.tab_azimuth = ttk.Frame(nb); nb.add(self.tab_azimuth, text="Steering")
        self.tab_experiments = ttk.Frame(nb); nb.add(self.tab_experiments, text="Experiments")
        self.tab_beam = ttk.Frame(nb); nb.add(self.tab_beam, text="Beam Check")
        self.tab_inv = ttk.Frame(nb); nb.add(self.tab_inv, text="TAG DATA (Live)")
        self.tab_plot = ttk.Frame(nb); nb.add(self.tab_plot, text="Spatial Plot")
        self.tab_steer_lut = ttk.Frame(nb); nb.add(self.tab_steer_lut, text="LUT Steer")
        self.tab_calib_sweep = ttk.Frame(nb); nb.add(self.tab_calib_sweep, text="🔧 Calibration Sweep")
        self.tab_auto_mapper = ttk.Frame(nb); nb.add(self.tab_auto_mapper, text="🗺️ Auto Tag Mapper")
        self.tab_ml = ttk.Frame(nb); nb.add(self.tab_ml, text="🤖 ML Data")
        
        self.setup_calib(); self.setup_azimuth(); self.setup_experiments(); self.setup_beam_check(); self.setup_inv_tab(); self.setup_plot(); self.setup_steer_lut(); self.setup_calib_sweep(); self.setup_auto_mapper(); self.setup_ml_tab()

    # --- TAB SETUPS ---
    def setup_calib(self):
        f = ttk.Frame(self.tab_calib, padding=20); f.pack(fill=tk.BOTH)
        ttk.Label(f, text="Calibration: Finds Peak RSSI at 0 deg").pack(pady=10)
        self.btn_calib = ttk.Button(f, text="START CALIBRATION SWEEP", command=self.run_calib)
        self.btn_calib.pack(ipadx=20, ipady=10)
        self.lbl_calib_res = ttk.Label(f, text="Not Calibrated", foreground="red"); self.lbl_calib_res.pack(pady=10)
        self.progress = ttk.Progressbar(f, length=400, mode='determinate'); self.progress.pack(pady=10)

    def setup_azimuth(self):
        f = ttk.Frame(self.tab_azimuth, padding=20); f.pack(fill=tk.BOTH)
        self.lbl_dir = ttk.Label(f, text="BEAM STEERING & LIVE DATA", font=("Arial", 18, "bold"), foreground="#003366"); self.lbl_dir.pack(pady=5)
        
        # --- LIVE DATA PANEL ---
        fb_fr = ttk.LabelFrame(f, text="Target Live Data", padding=10); fb_fr.pack(fill=tk.X, pady=10)
        
        self.lbl_st_rssi = ttk.Label(fb_fr, text="RSSI: -- dBm", font=("Arial", 16, "bold"))
        self.lbl_st_rssi.pack(side=tk.LEFT, padx=30)
        
        self.lbl_st_phase = ttk.Label(fb_fr, text="RF Phase: -- °", font=("Arial", 16, "bold"), foreground="darkblue")
        self.lbl_st_phase.pack(side=tk.LEFT, padx=30)
        
        # --- CONTROL PANEL ---
        ctrl_fr = ttk.LabelFrame(f, text="Beam Control", padding=10); ctrl_fr.pack(fill=tk.X, pady=10)
        
        # Mode Toggle: Sync vs Independent
        self.var_st_sync = tk.BooleanVar(value=True)
        self.chk_st_sync = ttk.Checkbutton(ctrl_fr, text="Phase-Sync Mode (Use LUT)", variable=self.var_st_sync)
        self.chk_st_sync.pack(anchor=tk.W, padx=10, pady=5)
        
        # Voltage Slider
        s_fr = ttk.Frame(ctrl_fr); s_fr.pack(fill=tk.X, pady=10)
        self.scale_az = tk.Scale(s_fr, from_=0, to=8.5, resolution=0.01, orient=tk.HORIZONTAL, length=500, label="Control Voltage (V)", font=("Arial", 10), command=self.update_voltage)
        self.scale_az.pack(side=tk.LEFT, padx=10)
        
        # Manual Entry + Phase Result
        e_fr = ttk.Frame(s_fr); e_fr.pack(side=tk.LEFT, padx=20)
        ttk.Label(e_fr, text="Manual V:", font=("Arial", 10, "bold")).grid(row=0, column=0)
        self.ent_manual_v = ttk.Entry(e_fr, width=10, font=("Arial", 11)); self.ent_manual_v.grid(row=0, column=1, padx=5)
        self.ent_manual_v.bind("<Return>", lambda e: self.scale_az.set(float(self.ent_manual_v.get() if self.ent_manual_v.get() else 0)))
        
        self.lbl_manual_phi = ttk.Label(e_fr, text="LUT Phase: 0.0°", font=("Arial", 11, "bold"), foreground="blue")
        self.lbl_manual_phi.grid(row=1, column=0, columnspan=2, pady=10)
        
        self.lbl_volt = ttk.Label(ctrl_fr, text="Applied: P1=0.00V, P4=0.00V", font=("Arial", 10, "italic"))
        self.lbl_volt.pack(pady=5)
        
        self.val_angle = ttk.Label(ctrl_fr, text="V: 0.00", font=("Arial", 14, "bold"), foreground="#333333")
        self.val_angle.pack()

    def setup_experiments(self):
        f = ttk.Frame(self.tab_experiments, padding=20); f.pack(fill=tk.BOTH)
        
        # --- THEORY ---
        theory_txt = (
            "THEORY & CALCULATOR:\n"
            "Control P1 (Bot-Left) & P4 (Top-Left). P2/P3 Fixed.\n"
            "Calculations assume d=lambda/2 spacing (180 deg shift max).\n\n"
            "Formulas relative to Boresight (V_calib):\n"
            "  Phi_Common = 180 * sin(Azimuth)\n"
            "  Phi_Diff   = 180 * sin(Elevation)\n\n"
            "  Phi_P4 = Phi_Calib + Phi_Common + (Phi_Diff / 2)\n"
            "  Phi_P1 = Phi_Calib + Phi_Common - (Phi_Diff / 2)\n"
        )
        ttk.Label(f, text=theory_txt, justify=tk.LEFT, background="#f0f0f0", relief="solid", padding=10).pack(fill=tk.X, pady=5)
        
        # --- CALCULATOR ---
        calc_fr = ttk.LabelFrame(f, text="Beam Steering Calculator"); calc_fr.pack(fill=tk.X, pady=10)
        
        # Grid input
        gf = ttk.Frame(calc_fr)
        gf.pack(padx=5, pady=5)
        
        ttk.Label(gf, text="Target Azimuth (°):").grid(row=0, column=0, padx=5, sticky=tk.E)
        self.ent_az = ttk.Entry(gf, width=10); self.ent_az.insert(0, "0")
        self.ent_az.grid(row=0, column=1, padx=5)
        
        ttk.Label(gf, text="Target Elevation (°):").grid(row=0, column=2, padx=5, sticky=tk.E)
        self.ent_el = ttk.Entry(gf, width=10); self.ent_el.insert(0, "0")
        self.ent_el.grid(row=0, column=3, padx=5)
        
        ttk.Label(gf, text="Boresight V (Offset):").grid(row=1, column=0, padx=5, sticky=tk.E)
        self.ent_offset = ttk.Entry(gf, width=10); self.ent_offset.insert(0, "0.0")
        self.ent_offset.grid(row=1, column=1, padx=5)
        ttk.Button(gf, text="Get from Calib", command=self.fetch_calib_v).grid(row=1, column=2, padx=5, sticky=tk.W)

        # Manual Offsets for Fine Tuning
        ttk.Label(gf, text="P1 Correct (V):").grid(row=1, column=3, padx=5, sticky=tk.E)
        self.ent_off_p1 = ttk.Entry(gf, width=6); self.ent_off_p1.insert(0, "0.0")
        self.ent_off_p1.grid(row=1, column=4, padx=5)
        
        ttk.Label(gf, text="P4 Correct (V):").grid(row=1, column=5, padx=5, sticky=tk.E)
        self.ent_off_p4 = ttk.Entry(gf, width=6); self.ent_off_p4.insert(0, "0.0")
        self.ent_off_p4.grid(row=1, column=6, padx=5)

        # Result Labels
        self.lbl_res_p1 = ttk.Label(gf, text="P1: --- V (---°)", foreground="blue")
        self.lbl_res_p1.grid(row=2, column=0, columnspan=2, pady=5)
        self.lbl_res_p4 = ttk.Label(gf, text="P4: --- V (---°)", foreground="blue")
        self.lbl_res_p4.grid(row=2, column=2, columnspan=2, pady=5)
        
        # Visual Direction
        self.lbl_vis_dir = ttk.Label(gf, text="<-- BEAM -->", font=("Courier", 12, "bold"), foreground="purple")
        self.lbl_vis_dir.grid(row=2, column=4, columnspan=4, pady=5)
        
        # Action Buttons
        btn_fr = ttk.Frame(calc_fr); btn_fr.pack(fill=tk.X, pady=5)
        ttk.Button(btn_fr, text="CALCULATE", command=self.calc_phases).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        ttk.Button(btn_fr, text="APPLY TO ARRAY", command=self.apply_calc).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        # --- MANUAL SLIDERS ---
        ctrl_fr = ttk.LabelFrame(f, text="Manual Independent Control (Syncs with Calc)")
        ctrl_fr.pack(fill=tk.X, pady=10)
        
        # Row 2: Parameters (Same as Beam Check for sync)
        self.var_sync_manual = tk.BooleanVar(value=True)
        self.chk_sync_man = ttk.Checkbutton(ctrl_fr, text="Phase-Sync Sliders (via LUT)", variable=self.var_sync_manual)
        self.chk_sync_man.pack(pady=5)
        
        p4_fr = ttk.Frame(ctrl_fr); p4_fr.pack(fill=tk.X, pady=5)
        ttk.Label(p4_fr, text="P4 (Top-Left):", width=15).pack(side=tk.LEFT)
        self.scale_p4 = tk.Scale(p4_fr, from_=0, to=8.5, resolution=0.05, orient=tk.HORIZONTAL, length=300, command=lambda v: self.update_experiments(v, "p4"))
        self.scale_p4.pack(side=tk.LEFT, padx=10)
        self.lbl_p4_val = ttk.Label(p4_fr, text="0.00 V"); self.lbl_p4_val.pack(side=tk.LEFT)
        
        p1_fr = ttk.Frame(ctrl_fr); p1_fr.pack(fill=tk.X, pady=5)
        ttk.Label(p1_fr, text="P1 (Bot-Left):", width=15).pack(side=tk.LEFT)
        self.scale_p1 = tk.Scale(p1_fr, from_=0, to=8.5, resolution=0.05, orient=tk.HORIZONTAL, length=300, command=lambda v: self.update_experiments(v, "p1"))
        self.scale_p1.pack(side=tk.LEFT, padx=10)
        self.lbl_p1_val = ttk.Label(p1_fr, text="0.00 V"); self.lbl_p1_val.pack(side=tk.LEFT)

    def fetch_calib_v(self):
        self.ent_offset.delete(0, tk.END)
        self.ent_offset.insert(0, f"{self.boresight_p:.2f}")

    def calc_phases(self):
        try:
            az = float(self.ent_az.get())
            el = float(self.ent_el.get())
            v_offset = float(self.ent_offset.get())
            
            # Additional manual correction
            try: off_p1 = float(self.ent_off_p1.get())
            except: off_p1 = 0.0
            try: off_p4 = float(self.ent_off_p4.get())
            except: off_p4 = 0.0

            # Visualizer Update
            vis_az = "CENTER"
            if az > 5: vis_az = "RIGHT (>>>)"
            elif az < -5: vis_az = "LEFT (<<<)"
            
            vis_el = "LEVEL"
            if el > 5: vis_el = "UP (^^^)"
            elif el < -5: vis_el = "DOWN (vvv)"
            
            self.lbl_vis_dir.config(text=f"{vis_az}\n{vis_el}")

            # Initial Phase from Boresight Voltage (Using P1 curve as reference)
            phi_calib = float(self.lut.func_v_to_p1(v_offset))
            
            rad_az = np.radians(az)
            rad_el = np.radians(el)
            
            # Formula: 180 * sin(theta) fits lambda/2 spacing
            phi_comm = 180.0 * np.sin(rad_az)
            phi_diff = 180.0 * np.sin(rad_el)
            
            # Derived Phases
            phi_p4 = phi_calib + phi_comm + (phi_diff / 2.0)
            phi_p1 = phi_calib + phi_comm - (phi_diff / 2.0)
            
            phi_p4 %= 360.0
            phi_p1 %= 360.0
            
            # Convert back to Voltage using SPECIFIC CHANNEL LUTs
            v_p4 = self.lut.get_voltage(phi_p4, channel=4) + off_p4
            v_p1 = self.lut.get_voltage(phi_p1, channel=1) + off_p1
            
            # Cap voltages
            v_p4 = max(0.0, min(8.5, v_p4))
            v_p1 = max(0.0, min(8.5, v_p1))
            
            self.calc_res = (v_p1, v_p4)
            
            self.lbl_res_p1.config(text=f"P1: {v_p1:.2f}V ({phi_p1:.0f}°)")
            self.lbl_res_p4.config(text=f"P4: {v_p4:.2f}V ({phi_p4:.0f}°)")
            return v_p1, v_p4
            
        except Exception as e:
            print(f"Calc error: {e}")
            messagebox.showerror("Calc Error", str(e))
            return None, None

    def apply_calc(self):
        v1, v2 = self.calc_phases()
        if v1 is not None and v2 is not None:
            # Sync Manual Sliders
            self.scale_p1.set(v1)
            self.scale_p4.set(v2)
            # Apply
            self.set_volts(v1, v2)

    def setup_inv_tab(self):
        f = ttk.Frame(self.tab_inv, padding=5); f.pack(fill=tk.BOTH, expand=True)
        
        # --- TOP CONTROL BAR (Duplicate for convenience) ---
        ctrl_inv = ttk.LabelFrame(f, text="Live Sweep/Steer Control", padding=5); ctrl_inv.pack(fill=tk.X, pady=5)
        
        c_fr = ttk.Frame(ctrl_inv); c_fr.pack(fill=tk.X)
        self.scale_az_inv = tk.Scale(c_fr, from_=0, to=8.5, resolution=0.01, orient=tk.HORIZONTAL, length=350, label="Voltage (V)", command=self.update_voltage)
        self.scale_az_inv.pack(side=tk.LEFT, padx=10)
        
        e_fr = ttk.Frame(c_fr); e_fr.pack(side=tk.LEFT, padx=10)
        ttk.Label(e_fr, text="Manual V:").grid(row=0, column=0)
        self.ent_manual_v_inv = ttk.Entry(e_fr, width=8); self.ent_manual_v_inv.grid(row=0, column=1)
        self.ent_manual_v_inv.bind("<Return>", lambda e: self.scale_az_inv.set(float(self.ent_manual_v_inv.get() if self.ent_manual_v_inv.get() else 0)))
        
        self.lbl_phi_inv = ttk.Label(e_fr, text="Phase: 0.0°", font=("Arial", 10, "bold"), foreground="blue")
        self.lbl_phi_inv.grid(row=1, column=0, columnspan=2)

        # Mode Selector for Colors
        m_fr = ttk.Frame(c_fr); m_fr.pack(side=tk.RIGHT, padx=20)
        ttk.Label(m_fr, text="Coloring Mode:").pack()
        self.var_color_mode = tk.StringVar(value="Absolute")
        ttk.Radiobutton(m_fr, text="Absolute", variable=self.var_color_mode, value="Absolute").pack(side=tk.LEFT)
        ttk.Radiobutton(m_fr, text="Connected (Rel)", variable=self.var_color_mode, value="Relative").pack(side=tk.LEFT)
        
        # Filter for beam check tags
        self.var_filter_beam_tags = tk.BooleanVar(value=False)
        ttk.Checkbutton(m_fr, text="Beam Tags Only", variable=self.var_filter_beam_tags).pack(side=tk.LEFT, padx=(20, 0))

        # --- TABLE ---
        cols = ("#", "Ant", "EPC", "Time", "Count", "RSSI", "Phase", "Doppler", "Position")
        self.tree = ttk.Treeview(f, columns=cols, show='headings', height=18)
        for c, w in zip(cols, [30,40,200,100,50,60,60,60,120]):
            self.tree.heading(c, text=c); self.tree.column(c, width=w)
        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); vsb.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Color Tags for RSSI
        self.tree.tag_configure('best', background='#c6efce', foreground='#006100') # Green
        self.tree.tag_configure('mid', background='#ffeb9c', foreground='#9c6500')  # Orange
        self.tree.tag_configure('poor', background='#ffc7ce', foreground='#9c0006') # Red
        self.tree.tag_configure('timeout', background='#d0d0d0', foreground='#000000') # Gray/Black
        self.tree.tag_configure('target', font=('Arial', 10, 'bold'))

    # --- BEAM CHECK TAB ---
    def setup_beam_check(self):
        f = ttk.Frame(self.tab_beam, padding=10); f.pack(fill=tk.BOTH, expand=True)
        
        # Controls
        ctrl = ttk.LabelFrame(f, text="8-Tag Setup (T1 - T2 - T3 - T4 - T5 - T6 - T7 - T8)")
        ctrl.pack(fill=tk.X, pady=5)
        
        # Tag Inputs - 8 tags from far left to far right
        fr_tags = ttk.Frame(ctrl); fr_tags.pack(padx=5, pady=5)
        
        # Row 0: Tags 1-4
        ttk.Label(fr_tags, text="T1 (FarL):").grid(row=0, column=0, sticky=tk.E)
        self.ent_tag_1 = ttk.Entry(fr_tags, width=10); self.ent_tag_1.insert(0, "72B6")
        self.ent_tag_1.grid(row=0, column=1, padx=2)

        ttk.Label(fr_tags, text="T2:").grid(row=0, column=2, sticky=tk.E)
        self.ent_tag_2 = ttk.Entry(fr_tags, width=10); self.ent_tag_2.insert(0, "7226")
        self.ent_tag_2.grid(row=0, column=3, padx=2)
        
        ttk.Label(fr_tags, text="T3:").grid(row=0, column=4, sticky=tk.E)
        self.ent_tag_3 = ttk.Entry(fr_tags, width=10); self.ent_tag_3.insert(0, "7236")
        self.ent_tag_3.grid(row=0, column=5, padx=2)

        ttk.Label(fr_tags, text="T4:").grid(row=0, column=6, sticky=tk.E)
        self.ent_tag_4 = ttk.Entry(fr_tags, width=10); self.ent_tag_4.insert(0, "7246")
        self.ent_tag_4.grid(row=0, column=7, padx=2)

        # Row 1: Tags 5-8
        ttk.Label(fr_tags, text="T5:").grid(row=1, column=0, sticky=tk.E)
        self.ent_tag_5 = ttk.Entry(fr_tags, width=10); self.ent_tag_5.insert(0, "72C6")
        self.ent_tag_5.grid(row=1, column=1, padx=2)

        ttk.Label(fr_tags, text="T6:").grid(row=1, column=2, sticky=tk.E)
        self.ent_tag_6 = ttk.Entry(fr_tags, width=10); self.ent_tag_6.insert(0, "7256")
        self.ent_tag_6.grid(row=1, column=3, padx=2)
        
        ttk.Label(fr_tags, text="T7:").grid(row=1, column=4, sticky=tk.E)
        self.ent_tag_7 = ttk.Entry(fr_tags, width=10); self.ent_tag_7.insert(0, "7296")
        self.ent_tag_7.grid(row=1, column=5, padx=2)

        ttk.Label(fr_tags, text="T8 (FarR):").grid(row=1, column=6, sticky=tk.E)
        self.ent_tag_8 = ttk.Entry(fr_tags, width=10); self.ent_tag_8.insert(0, "4096")
        self.ent_tag_8.grid(row=1, column=7, padx=2)
        
        # Save/Load buttons
        btn_save_fr = ttk.Frame(fr_tags)
        btn_save_fr.grid(row=2, column=0, columnspan=8, pady=5)
        ttk.Button(btn_save_fr, text="Save Config", command=self.save_beam_tags).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_save_fr, text="Load Config", command=self.load_beam_tags).pack(side=tk.LEFT, padx=2)

        # Row 2: Parameters
        fr_params = ttk.Frame(ctrl); fr_params.pack(padx=5, pady=2)
        ttk.Label(fr_params, text="Settling Delay (s):").grid(row=0, column=0)
        self.ent_beam_delay = ttk.Entry(fr_params, width=8); self.ent_beam_delay.insert(0, "0.5")
        self.ent_beam_delay.grid(row=0, column=1, padx=5)

        ttk.Label(fr_params, text="Voltage Step (V):").grid(row=0, column=2)
        self.ent_beam_step = ttk.Entry(fr_params, width=8); self.ent_beam_step.insert(0, "0.1")
        self.ent_beam_step.grid(row=0, column=3, padx=5)

        ttk.Label(fr_params, text="Min Reads:").grid(row=0, column=4)
        self.ent_beam_min_reads = ttk.Entry(fr_params, width=6); self.ent_beam_min_reads.insert(0, "1")
        self.ent_beam_min_reads.grid(row=0, column=5, padx=5)

        ttk.Label(fr_params, text="Sample Time (ms):").grid(row=0, column=6)
        self.ent_beam_sample_ms = ttk.Entry(fr_params, width=6); self.ent_beam_sample_ms.insert(0, "800")
        self.ent_beam_sample_ms.grid(row=0, column=7, padx=5)

        self.var_sync_phase = tk.BooleanVar(value=True)
        self.chk_sync_beam = ttk.Checkbutton(fr_params, text="Phase-Sync (LUT)", variable=self.var_sync_phase)
        self.chk_sync_beam.grid(row=0, column=8, padx=10)

        self.btn_beam_Sweep = ttk.Button(ctrl, text="START SWEEP", command=self.run_beam_sweep)
        self.btn_beam_Sweep.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, pady=5)
        
        self.btn_beam_pause = ttk.Button(ctrl, text="PAUSE", command=self.pause_beam_sweep, state=tk.DISABLED)
        self.btn_beam_pause.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, pady=5)
        
        self.btn_beam_stop = ttk.Button(ctrl, text="STOP", command=self.stop_beam_sweep, state=tk.DISABLED)
        self.btn_beam_stop.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5, pady=5)
        
        # Flags for control
        self.sweep_stop_flag = False
        self.sweep_pause_flag = False
        
        # Status Label
        # Status Label
        self.lbl_beam_status = ttk.Label(f, text="Status: Ready", font=("Arial", 12, "bold"))
        self.lbl_beam_status.pack(pady=5)

        # Plot Frame
        self.fig_beam, self.ax_beam = plt.subplots(figsize=(8, 4))
        self.canvas_beam = FigureCanvasTkAgg(self.fig_beam, master=f)
        self.canvas_beam.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def find_epc_by_suffix(self, suffix):
        # Search current inventory
        for epc in self.reader.inventory.keys():
            if epc.endswith(suffix): return epc
        return suffix

    def run_beam_sweep(self):
        if not self.serial or not self.serial.is_open:
            messagebox.showerror("Error", "MCU Not Connected!")
            return

        # 1. RESET FLAGS
        self.sweep_stop_flag = False
        self.sweep_pause_flag = False
        self.btn_beam_Sweep.config(state=tk.DISABLED)
        self.btn_beam_pause.config(state=tk.NORMAL, text="PAUSE")
        self.btn_beam_stop.config(state=tk.NORMAL)

        # 1. READ PARAMETERS
        try:
            settle_val = float(self.ent_beam_delay.get())
            step_val = float(self.ent_beam_step.get())
            min_reads = int(self.ent_beam_min_reads.get())
            sample_time = float(self.ent_beam_sample_ms.get()) / 1000.0
        except:
            settle_val, step_val, min_reads, sample_time = 0.3, 0.1, 1, 0.4
        do_sync = self.var_sync_phase.get()

        # Resolve EPCs for 8 tags
        epc_1 = self.find_epc_by_suffix(self.ent_tag_1.get().strip())
        epc_2 = self.find_epc_by_suffix(self.ent_tag_2.get().strip())
        epc_3 = self.find_epc_by_suffix(self.ent_tag_3.get().strip())
        epc_4 = self.find_epc_by_suffix(self.ent_tag_4.get().strip())
        epc_5 = self.find_epc_by_suffix(self.ent_tag_5.get().strip())
        epc_6 = self.find_epc_by_suffix(self.ent_tag_6.get().strip())
        epc_7 = self.find_epc_by_suffix(self.ent_tag_7.get().strip())
        epc_8 = self.find_epc_by_suffix(self.ent_tag_8.get().strip())
        
        targets = {'T1': epc_1, 'T2': epc_2, 'T3': epc_3, 'T4': epc_4, 'T5': epc_5, 'T6': epc_6, 'T7': epc_7, 'T8': epc_8}
        ordered_names = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']
        
        voltages_scanned = []
        results = {n: [] for n in ordered_names}
        phases_out = {n: [] for n in ordered_names}
        
        # 2. PREPARE PLOT
        self.ax_beam.clear()
        self.ax_beam.set_title("8-Tag Beam Steering Check (Real-Time)")
        self.ax_beam.set_xlabel("Control Voltage (V)")
        self.ax_beam.set_ylabel("RSSI (dBm)")
        self.ax_beam.grid(True)
        
        lines = {}
        colors = {'T1': 'darkviolet', 'T2': 'blue', 'T3': 'green', 'T4': 'cyan', 'T5': 'orange', 'T6': 'red', 'T7': 'brown', 'T8': 'magenta'}
        for name in ordered_names:
            line, = self.ax_beam.plot([], [], marker='o', label=f"{name} ({targets[name][-4:]})", color=colors[name], markersize=4)
            lines[name] = line
        self.ax_beam.legend(fontsize='small')
        self.canvas_beam.draw()
        
        # 3. CSV RECORDING (Timestamped Filename)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"beam_sweep_{ts}.csv"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            f_csv = open(csv_filename, 'w', newline='')
            writer = csv.writer(f_csv)
            writer.writerow([f"# Timestamp: {now_str}"])
            writer.writerow([f"# Reader Power: {self.reader.last_power} dBm"])
            writer.writerow([f"# Setup: 1m dist, 15cm spacing"])
            writer.writerow([f"# Params: Delay={settle_val}s, Step={step_val}V, MinReads={min_reads}, SyncPhase={do_sync}"])
            
            header = ["Voltage_P1", "Voltage_P4"]
            for n in ordered_names: header.extend([f"{n}_RSSI", f"{n}_Phase"])
            writer.writerow(header)
        except: 
            f_csv = None
            print("CSV Open Failed")
        
        sweep_range = np.arange(0, 8.51, step_val)
        self.btn_beam_Sweep.config(state=tk.DISABLED)
        if not self.reader.inventory_running: self.start_inv()
        
        print("Starting 5-tag sweep...")
        votes_l, votes_r = 0, 0
        
        # 4. LOOP (Integration Window Approach)
        for v_step in sweep_range:
            # CHECK PAUSE/STOP
            while self.sweep_pause_flag and not self.sweep_stop_flag:
                self.lbl_beam_status.config(text="|| SWEEP PAUSED", foreground="blue")
                self.root.update(); time.sleep(0.1)
                
            if self.sweep_stop_flag:
                self.lbl_beam_status.config(text="X SWEEP STOPPED", foreground="red")
                break

            v1 = v_step
            if do_sync:
                target_phi = float(self.lut.func_v_to_p1(v1))
                v4 = self.lut.get_voltage(target_phi, channel=4)
            else:
                v4 = v1

            # A. Update Voltage and Wait for Hardware to Settle
            self.update_voltage(v1, v4) 
            self.root.update()
            time.sleep(settle_val) 
            
            # B. Collect Data with Retry (Robust Reading)
            temp_rssi = {n: [] for n in ordered_names}
            temp_phase = {n: [] for n in ordered_names}
            
            # We will use a maximum of 3 cycles if we miss tags
            max_read_cycles = 2 if min_reads > 0 else 1
            for cycle in range(max_read_cycles):
                self.reader.clear_data()
                time.sleep(sample_time)
                data = self.reader.get_all_data()
                
                tags_found_this_cycle = 0
                for name, epc in targets.items():
                    if epc in data:
                        d = data[epc]
                        temp_rssi[name].append(d['rssi'])
                        temp_phase[name].append(d['phase_deg'])
                        tags_found_this_cycle += 1
                
                # If we found at least one target tag, we can stop retrying for this step
                if tags_found_this_cycle > 0:
                    break
                # Only retry if min_reads > 0 and we found nothing
                if min_reads > 0:
                    print(f"No tags found at V={v1:.2f}, retrying cycle {cycle+1}...")
            
            # C. Collect Data Summary
            tags_found_count = sum(1 for n in ordered_names if len(temp_rssi[n]) > 0)
            
            # D. Process Step Results
            voltages_scanned.append(v1)
            row_data = [v1, v4]
            avg_rssi_step = {}
            for name in ordered_names:
                rs, ph = temp_rssi[name], temp_phase[name]
                if len(rs) >= min_reads:
                    val_r = sum(rs)/len(rs)
                    val_p = (sum(ph)/len(ph)) % 360.0
                else:
                    val_r = -100.0
                    val_p = 0.0
                avg_rssi_step[name] = val_r
                results[name].append(val_r)
                phases_out[name].append(val_p)
                row_data.extend([val_r, val_p])
                lines[name].set_data(voltages_scanned, results[name])
            
            # Status Reporting
            pow_l = max(avg_rssi_step.get('T1', -100), avg_rssi_step.get('T2', -100), avg_rssi_step.get('T3', -100), avg_rssi_step.get('T4', -100))
            pow_r = max(avg_rssi_step.get('T5', -100), avg_rssi_step.get('T6', -100), avg_rssi_step.get('T7', -100), avg_rssi_step.get('T8', -100))
            status = f"V: {v1:.1f}/{v4:.1f} | Found: {tags_found_count}/8 | L: {pow_l:.1f} vs R: {pow_r:.1f}"
            self.lbl_beam_status.config(text=status)
            
            self.ax_beam.relim(); self.ax_beam.autoscale_view(); self.canvas_beam.draw()
            self.root.update() 
            
        # FINISH
        self.btn_beam_Sweep.config(state=tk.NORMAL)
        self.btn_beam_pause.config(state=tk.DISABLED)
        self.btn_beam_stop.config(state=tk.DISABLED)
        if self.sweep_stop_flag: return
            
        if f_csv: f_csv.close()
        self.btn_beam_Sweep.config(state=tk.NORMAL)
        
        # 5. ADVANCED LLM & DIAGNOSTIC ANALYSIS
        tag_angles = {'T1': -24, 'T2': -17, 'T3': -10, 'T4': -3, 'T5': 3, 'T6': 10, 'T7': 17, 'T8': 24}
        peak_tags_sequence = []
        
        # Determine Initial State (at 0V)
        start_rssis = {n: results[n][0] for n in ordered_names}
        initial_peak_tag = max(start_rssis, key=start_rssis.get)
        
        # Find Practical Boresight (Peak of center tags T4 or T5)
        m_rssi_arr = np.array([(results['T4'][i] + results['T5'][i])/2 for i in range(len(voltages_scanned))])
        m_peak_idx = np.argmax(m_rssi_arr)
        boresight_v_actual = voltages_scanned[m_peak_idx]
        boresight_rssi_actual = m_rssi_arr[m_peak_idx]
        
        # Track peak movement
        for i, v in enumerate(voltages_scanned):
            step_rssis = {n: results[n][i] for n in ordered_names}
            peak_t = max(step_rssis, key=step_rssis.get)
            if not peak_tags_sequence or peak_tags_sequence[-1]['tag'] != peak_t:
                peak_tags_sequence.append({'tag': peak_t, 'v': v})

        # Determine Steering Trend
        # (Compare start of sweep vs end of sweep predominant tags)
        end_rssis = {n: results[n][-1] for n in ordered_names}
        final_peak_tag = max(end_rssis, key=end_rssis.get)
        
        steering_trend = "Unknown"
        if tag_angles[final_peak_tag] > tag_angles[initial_peak_tag]:
            steering_trend = "LEFT-TO-RIGHT (Standard)"
        elif tag_angles[final_peak_tag] < tag_angles[initial_peak_tag]:
            steering_trend = "RIGHT-TO-LEFT (Reversed)"
        else:
            steering_trend = "STATIONARY / NO CLEAR MOVEMENT"

        # 5.1 LLM PROMPT EXPORT (Enriched)
        prompt_file = "llm_analysis_prompt.txt"
        with open(prompt_file, 'w') as f_p:
            f_p.write(f"--- BEAM STEERING DIAGNOSTIC REPORT ({now_str}) ---\n")
            f_p.write(f"Parameters: Power={self.reader.last_power}dBm, Delay={settle_val}s, Step={step_val}V, Sync={do_sync}\n")
            f_p.write(f"Initial State (0V): Strongest Tag is {initial_peak_tag} (Initial Skew: {tag_angles[initial_peak_tag]}°)\n")
            f_p.write(f"Detected Boresight: Occurs at {boresight_v_actual:.2f}V (Middle Tag @ {boresight_rssi_actual:.1f}dBm)\n")
            f_p.write(f"Steering Trend: {steering_trend}\n")
            f_p.write("-" * 50 + "\n\n")
            f_p.write("RAW DATA SEQUENCE (Peak Tag Movement):\n")
            for entry in peak_tags_sequence:
                f_p.write(f"At {entry['v']:.2f}V -> Peak is {entry['tag']} ({tag_angles[entry['tag']]}°)\n")
            
            f_p.write("\nFULL DATA (CSV format for plotting/analysis):\n")
            f_p.write("V_P1, FL_RSSI, L_RSSI, M_RSSI, R_RSSI, FR_RSSI, FL_Ph, L_Ph, M_Ph, R_Ph, FR_Ph\n")
            for i in range(len(voltages_scanned)):
                rssis = ",".join([f"{results[n][i]:.1f}" for n in ordered_names])
                phases = ",".join([f"{phases_out[n][i]:.1f}" for n in ordered_names])
                f_p.write(f"{voltages_scanned[i]:.2f}, {rssis}, {phases}\n")

        # 6. FINAL UI MESSAGE
        msg = "--- SWEEP DIAGNOSTICS ---\n\n"
        msg += f"1. Initial Skew (0V): {initial_peak_tag} ({tag_angles[initial_peak_tag]}°)\n"
        msg += f"   (Note: If not Middle, check cable phase offsets)\n\n"
        msg += f"2. Practical Boresight: {boresight_v_actual:.2f} V\n"
        msg += f"   (Voltage where Middle tag is strongest)\n\n"
        msg += f"3. Steering Direction: {steering_trend}\n"
        msg += f"   (As voltage increases from 0 to 8.5V)\n\n"
        msg += f"Detailed logs saved to {prompt_file}"
        messagebox.showinfo("Diagnostic Result", msg)

    # --- SWEEP CONTROL HELPERS ---
    def pause_beam_sweep(self):
        if not hasattr(self, 'sweep_pause_flag'): return
        self.sweep_pause_flag = not self.sweep_pause_flag
        self.btn_beam_pause.config(text="RESUME" if self.sweep_pause_flag else "PAUSE")

    def stop_beam_sweep(self):
        self.sweep_stop_flag = True
        self.sweep_pause_flag = False

    def update_table(self):
        if self.reader.inventory_running:
            data = self.reader.get_all_data()
            curr_list = self.lb_epcs.get(0, tk.END)
            
            # Timeout detection: 2 seconds
            current_time = time.time()
            timeout_threshold = 2.0
            
            # Find strongest RSSI for 'Connected' mode and position detection
            max_r = -100
            best_epc = None
            if data:
                for epc, d in data.items():
                    if (current_time - d['seen_time']) < timeout_threshold:
                        if d['rssi'] > max_r:
                            max_r = d['rssi']
                            best_epc = epc
            
            # Sort by RSSI (best to worst)
            sorted_data = sorted(data.items(), key=lambda x: x[1]['rssi'], reverse=True)
            
            # Apply beam tag filter if enabled
            if self.var_filter_beam_tags.get() and hasattr(self, 'beam_tag_positions'):
                beam_suffixes = [v for v in self.beam_tag_positions.values() if v]
                sorted_data = [(epc, d) for epc, d in sorted_data 
                              if any(epc.endswith(suffix) or suffix.endswith(epc[-4:]) for suffix in beam_suffixes)]
            
            # Clear and rebuild tree for sorting
            for item in self.tree.get_children():
                self.tree.delete(item)
            
            for idx, (epc, d) in enumerate(sorted_data, 1):
                if epc not in curr_list:
                    self.lb_epcs.insert(tk.END, epc)
                
                # Check timeout
                is_timeout = (current_time - d['seen_time']) > timeout_threshold
                
                # Position indicator
                position = ""
                if hasattr(self, 'beam_tag_positions'):
                    for pos_name, pos_epc in self.beam_tag_positions.items():
                        if epc.endswith(pos_epc) or pos_epc.endswith(epc[-4:]):
                            position = pos_name
                            break
                
                # Update Treeview with Color Coding
                if is_timeout:
                    status = "Not Read"
                    rssi_str = "--"
                    phase_str = "--"
                else:
                    status = position if position else "Active"
                    rssi_str = f"{d['rssi']:.1f}"
                    phase_str = f"{d['phase_deg']:.1f}"
                
                # Show beam direction indicator for best tag
                direction = ""
                if epc == best_epc and position:
                    if "Left" in position:
                        direction = " ← LEFT"
                    elif "Right" in position:
                        direction = " RIGHT →"
                    elif "Middle" in position:
                        direction = " ● CENTER"
                
                values = (
                    idx, 
                    d.get('antenna',1),
                    epc,
                    datetime.fromtimestamp(d['seen_time']).strftime('%H:%M:%S'),
                    d['count'],
                    rssi_str,
                    phase_str,
                    f"{d['doppler']:.1f}" if not is_timeout else "--",
                    status + direction
                )
                
                # Thresholds for colors
                if is_timeout:
                    tag = 'timeout'
                else:
                    r = d['rssi']
                    tag = 'poor'
                    if self.var_color_mode.get() == "Relative":
                        if r >= max_r - 1.0: tag = 'best'
                        elif r >= max_r - 5.0: tag = 'mid'
                    else:
                        if r > -55: tag = 'best'
                        elif r > -72: tag = 'mid'
                
                if epc == self.reader.target_epc:
                    tag = 'best'
                
                self.tree.insert('', tk.END, values=values, tags=(tag,))
        
        self.table_timer = self.root.after(500, self.update_table)

    def setup_plot(self):
        f = ttk.Frame(self.tab_plot, padding=10); f.pack(fill=tk.BOTH, expand=True)
        ttk.Button(f, text="RUN SPATIAL PLOT", command=self.run_plot).pack(pady=5)
        self.fig, self.ax = plt.subplots(figsize=(8, 4))
        self.canvas = FigureCanvasTkAgg(self.fig, master=f); self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def save_beam_tags(self):
        """Save beam check tag configuration to file"""
        config = {
            'T1': self.ent_tag_1.get(),
            'T2': self.ent_tag_2.get(),
            'T3': self.ent_tag_3.get(),
            'T4': self.ent_tag_4.get(),
            'T5': self.ent_tag_5.get(),
            'T6': self.ent_tag_6.get(),
            'T7': self.ent_tag_7.get(),
            'T8': self.ent_tag_8.get()
        }
        try:
            import json
            with open('beam_tags_config.json', 'w') as f:
                json.dump(config, f, indent=2)
            messagebox.showinfo("Saved", "Tag configuration saved to beam_tags_config.json")
            self.beam_tag_positions = config
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save: {e}")
    
    def load_beam_tags(self):
        """Load beam check tag configuration from file"""
        try:
            import json
            with open('beam_tags_config.json', 'r') as f:
                config = json.load(f)
            self.ent_tag_1.delete(0, tk.END); self.ent_tag_1.insert(0, config.get('T1', ''))
            self.ent_tag_2.delete(0, tk.END); self.ent_tag_2.insert(0, config.get('T2', ''))
            self.ent_tag_3.delete(0, tk.END); self.ent_tag_3.insert(0, config.get('T3', ''))
            self.ent_tag_4.delete(0, tk.END); self.ent_tag_4.insert(0, config.get('T4', ''))
            self.ent_tag_5.delete(0, tk.END); self.ent_tag_5.insert(0, config.get('T5', ''))
            self.ent_tag_6.delete(0, tk.END); self.ent_tag_6.insert(0, config.get('T6', ''))
            self.ent_tag_7.delete(0, tk.END); self.ent_tag_7.insert(0, config.get('T7', ''))
            self.ent_tag_8.delete(0, tk.END); self.ent_tag_8.insert(0, config.get('T8', ''))
            messagebox.showinfo("Loaded", "Tag configuration loaded")
            self.beam_tag_positions = config
        except FileNotFoundError:
            messagebox.showwarning("Not Found", "No saved configuration found")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}")
    
    def update_beam_monitor(self):
        """Update live beam monitor display"""
        if not hasattr(self, 'beam_monitor_labels'):
            return
        
        # Get current tag data
        data = self.reader.get_all_data() if hasattr(self, 'reader') else {}
        current_time = time.time()
        timeout = 2.0
        
        # Find best RSSI for comparison
        max_rssi = -100
        best_tag = None
        
        for tag_name, tag_suffix in self.beam_tag_positions.items():
            if not tag_suffix:
                continue
            
            # Find matching EPC
            tag_data = None
            for epc, d in data.items():
                if epc.endswith(tag_suffix) or tag_suffix.endswith(epc[-4:]):
                    tag_data = d
                    break
            
            if tag_data and (current_time - tag_data['seen_time']) < timeout:
                rssi = tag_data['rssi']
                if rssi > max_rssi:
                    max_rssi = rssi
                    best_tag = tag_name
        
        # Update each tag box
        for tag_name, widgets in self.beam_monitor_labels.items():
            tag_suffix = self.beam_tag_positions.get(tag_name, '')
            if not tag_suffix:
                widgets['rssi'].config(text="N/A", bg="#d0d0d0", fg="black")
                widgets['phase'].config(text="∠ --°")
                continue
            
            # Find matching EPC
            tag_data = None
            for epc, d in data.items():
                if epc.endswith(tag_suffix) or tag_suffix.endswith(epc[-4:]):
                    tag_data = d
                    break
            
            if tag_data and (current_time - tag_data['seen_time']) < timeout:
                rssi = tag_data['rssi']
                phase = tag_data['phase_deg']
                
                # Color coding based on RSSI
                if rssi > -55:
                    bg_color = "#c6efce"  # Green
                    fg_color = "#006100"
                elif rssi > -65:
                    bg_color = "#ffeb9c"  # Yellow
                    fg_color = "#9c6500"
                else:
                    bg_color = "#ffc7ce"  # Red
                    fg_color = "#9c0006"
                
                # Highlight best tag
                if tag_name == best_tag:
                    widgets['rssi'].config(text=f"{rssi:.1f} ★", bg=bg_color, fg=fg_color, font=("Arial", 14, "bold"))
                else:
                    widgets['rssi'].config(text=f"{rssi:.1f}", bg=bg_color, fg=fg_color, font=("Arial", 14, "bold"))
                
                widgets['phase'].config(text=f"∠ {phase:.0f}°")
            else:
                # Timeout or not found
                widgets['rssi'].config(text="--", bg="#d0d0d0", fg="black")
                widgets['phase'].config(text="∠ --°")
        
        # Schedule next update
        self.root.after(300, self.update_beam_monitor)
    
    def save_beam_snapshot(self):
        """Save current beam state to CSV with timestamp and create visual report"""
        if not hasattr(self, 'beam_monitor_labels'):
            return
        
        data = self.reader.get_all_data() if hasattr(self, 'reader') else {}
        current_time = time.time()
        timeout = 2.0
        
        # Collect data for all 5 tags
        snapshot_data = {}
        tag_order = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']
        
        for tag_name in tag_order:
            tag_suffix = self.beam_tag_positions.get(tag_name, '')
            if not tag_suffix:
                continue
            
            # Find matching EPC
            for epc, d in data.items():
                if epc.endswith(tag_suffix) or tag_suffix.endswith(epc[-4:]):
                    if (current_time - d['seen_time']) < timeout:
                        snapshot_data[tag_name] = {
                            'rssi': d['rssi'],
                            'phase': d['phase_deg'],
                            'epc': epc
                        }
                    break
        
        if not snapshot_data:
            messagebox.showwarning("No Data", "No beam tags are currently visible")
            return
        
        # Get current voltages and mode
        v1_str = self.lbl_ch1_stat.cget('text').replace(' V', '') if hasattr(self, 'lbl_ch1_stat') else "0.00"
        v2_str = self.lbl_ch2_stat.cget('text').replace(' V', '') if hasattr(self, 'lbl_ch2_stat') else "0.00"
        mode = self.var_steer_mode.get() if hasattr(self, 'var_steer_mode') else "Unknown"
        angle = self.scale_steer_angle.get() if hasattr(self, 'scale_steer_angle') else 0
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. Save to CSV
        filename = "beam_snapshots.csv"
        import os
        file_exists = os.path.exists(filename)
        
        try:
            with open(filename, 'a', newline='') as f:
                writer = csv.writer(f)
                
                if not file_exists:
                    header = ['Timestamp', 'Mode', 'Angle', 'CH1_V', 'CH2_V']
                    for tag in tag_order:
                        header.extend([f'{tag}_RSSI', f'{tag}_Phase'])
                    writer.writerow(header)
                
                row = [timestamp, mode, f"{angle:.1f}", v1_str, v2_str]
                for tag in tag_order:
                    if tag in snapshot_data:
                        row.extend([f"{snapshot_data[tag]['rssi']:.1f}", f"{snapshot_data[tag]['phase']:.0f}"])
                    else:
                        row.extend(['--', '--'])
                
                writer.writerow(row)
            
            # 2. Create visual bar chart and LLM analysis
            self.create_snapshot_visual(timestamp, mode, angle, v1_str, v2_str, snapshot_data, tag_order)
            
            messagebox.showinfo("Saved", f"Snapshot saved to {filename}\nVisual report: beam_snapshot_visual.txt")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save snapshot: {e}")
    
    def create_snapshot_visual(self, timestamp, mode, angle, v1, v2, data, tag_order):
        """Create visual bar chart and LLM-ready analysis"""
        # Find max RSSI for relative scaling
        rssi_values = [data[tag]['rssi'] for tag in tag_order if tag in data]
        if not rssi_values:
            return
        
        max_rssi = max(rssi_values)
        min_rssi = min(rssi_values)
        rssi_range = max_rssi - min_rssi if max_rssi != min_rssi else 1
        
        # Determine beam direction
        best_tag = max(data.keys(), key=lambda t: data[t]['rssi'])
        if 'Left' in best_tag:
            direction = "LEFT"
        elif 'Right' in best_tag:
            direction = "RIGHT"
        elif 'Middle' in best_tag:
            direction = "CENTER"
        else:
            direction = "UNKNOWN"
        
        # Create visual report
        report = []
        report.append("="*70)
        report.append(f"BEAM SNAPSHOT - {timestamp}")
        report.append("="*70)
        report.append(f"Mode: {mode} | Target Angle: {angle}° | Voltages: CH1={v1}V, CH2={v2}V")
        report.append(f"Beam Direction: {direction} (Strongest: {best_tag})")
        report.append("")
        report.append("RSSI Distribution (Relative):")
        report.append("")
        
        # Create bar chart
        for tag in tag_order:
            if tag in data:
                rssi = data[tag]['rssi']
                phase = data[tag]['phase']
                
                # Relative strength (0-100%)
                rel_strength = ((rssi - min_rssi) / rssi_range) * 100 if rssi_range > 0 else 50
                bar_length = int(rel_strength / 2)  # Scale to 50 chars max
                
                # Color indicator
                if rssi == max_rssi:
                    indicator = "★"
                elif rssi >= max_rssi - 3:
                    indicator = "●"
                else:
                    indicator = "○"
                
                bar = "█" * bar_length
                report.append(f"{tag:10s} {indicator} [{bar:<50s}] {rssi:6.1f} dBm (∠{phase:3.0f}°)")
            else:
                report.append(f"{tag:10s}   [{'':50s}]   -- dBm")
        
        report.append("")
        report.append("-"*70)
        report.append("LLM ANALYSIS PROMPT:")
        report.append("-"*70)
        report.append(f"""Analyze this phased array beam steering measurement:

Configuration:
- Scan Mode: {mode}
- Target Angle: {angle}°
- Applied Voltages: CH1={v1}V, CH2={v2}V

Measured RSSI by Position (Left to Right):
""")
        
        for tag in tag_order:
            if tag in data:
                report.append(f"  {tag}: {data[tag]['rssi']:.1f} dBm (Phase: {data[tag]['phase']:.0f}°)")
            else:
                report.append(f"  {tag}: Not detected")
        
        report.append(f"\nObserved Beam Direction: {direction}")
        report.append(f"Strongest Signal: {best_tag} ({max_rssi:.1f} dBm)")
        report.append(f"Signal Range: {rssi_range:.1f} dB")
        report.append("\nQuestion: Based on this data, is the beam steering working correctly?")
        report.append("Does the beam direction match the expected angle?")
        report.append("="*70)
        
        # Save to file
        with open('beam_snapshot_visual.txt', 'w') as f:
            f.write('\n'.join(report))
    
    def apply_lut_voltages(self):
        """Manually apply the calculated LUT voltages to hardware"""
        if not hasattr(self, 'lbl_ch1_stat') or not hasattr(self, 'lbl_ch2_stat'):
            return
        
        try:
            v1_str = self.lbl_ch1_stat.cget('text').replace(' V', '')
            v2_str = self.lbl_ch2_stat.cget('text').replace(' V', '')
            v1 = float(v1_str)
            v2 = float(v2_str)
            
            self.set_volts(v1, v2)
            messagebox.showinfo("Applied", f"Voltages applied: CH1={v1:.3f}V, CH2={v2:.3f}V")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply: {e}")

    # --- FUNCTIONS ---
    def connect_mcu(self):
        try:
            self.serial = serial.Serial(self.cb_port.get(), 115200, timeout=0.1)
            messagebox.showinfo("OK", "MCU Connected")
        except: messagebox.showerror("Error", "MCU Fail")

    def connect_reader(self):
        ip = self.ent_ip.get()
        try: pwr = float(self.ent_pwr.get())
        except: pwr = 20.0
        
        # Get advanced reader settings
        settings = self.get_reader_settings()
        mode = settings['mode_identifier']
        session = settings['session']
        search_mode = settings['impinj_search_mode']
        
        if self.reader.connect(ip, pwr, mode=mode, session=session, search_mode=search_mode):
            # Otomatik baslattik, o yuzden Stop aktif olsun
            self.btn_connect_reader.config(state=tk.DISABLED)
            self.btn_disconnect_reader.config(state=tk.NORMAL)
            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
            messagebox.showinfo("Success", f"Reader Connected.\nPower: {pwr} dBm\nMode: {mode}\nSession: {session}\nSearch: {search_mode}\nInventory Running.")
        else:
            messagebox.showerror("Fail", "Check connection.")

    def toggle_reader_settings(self):
        """Show/hide advanced reader settings"""
        if self.var_show_reader_settings.get():
            self.reader_settings_fr.pack(fill=tk.X, pady=2)
        else:
            self.reader_settings_fr.pack_forget()

    def apply_reader_preset(self, event=None):
        """Apply reader preset configuration"""
        preset = self.cmb_reader_preset.get()
        
        if "Beam Analysis" in preset:
            # Default: High duty cycle for continuous beam measurement
            self.cmb_reader_mode.set("1002 - AutoSet DenseRdr")
            self.cmb_session.set("0 - Fast cycle")
            self.cmb_search_mode.set("2 - Dual Target (Cont.)")
        
        elif "Stationary Tags" in preset:
            # For inventorying stationary items (cabinets, shelves)
            self.cmb_reader_mode.set("1002 - AutoSet DenseRdr")
            self.cmb_session.set("2 - Extended persist")
            self.cmb_search_mode.set("1 - Single Target")
        
        elif "Portal" in preset:
            # For items moving through a portal/door
            self.cmb_reader_mode.set("4 - Max Miller")
            self.cmb_session.set("2 - Extended persist")
            self.cmb_search_mode.set("1 - Single Target")
        
        elif "Dense Environment" in preset:
            # For multi-reader or RF-dense environments
            self.cmb_reader_mode.set("1004 - AutoSet Static Dense")
            self.cmb_session.set("2 - Extended persist")
            self.cmb_search_mode.set("2 - Dual Target (Cont.)")
        
        # Mark as custom if user changes individual settings
        if "Custom" not in preset:
            print(f"Reader preset applied: {preset}")

    def get_reader_settings(self):
        """Get current reader settings from UI"""
        # Extract mode number
        mode_str = self.cmb_reader_mode.get()
        mode_num = int(mode_str.split(" - ")[0])
        
        # Extract session number
        session_str = self.cmb_session.get()
        session_num = int(session_str.split(" - ")[0])
        
        # Extract search mode number
        search_str = self.cmb_search_mode.get()
        search_num = search_str.split(" - ")[0]
        
        return {
            'mode_identifier': mode_num,
            'session': session_num,
            'impinj_search_mode': search_num
        }

    def disconnect_and_reset(self):
        self.reader.disconnect_reader()
        self.btn_connect_reader.config(state=tk.NORMAL)
        self.btn_disconnect_reader.config(state=tk.DISABLED)
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)
        messagebox.showinfo("Disconnected", "Reader disconnected.")

    def start_inv(self): 
        self.reader.start_inventory()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)

    def stop_inv(self): 
        self.reader.stop_inventory()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def clear_data(self): 
        self.reader.clear_data()
        self.tree.delete(*self.tree.get_children())
        self.lb_epcs.delete(0, tk.END)

    def lock_target(self):
        try: 
            self.reader.target_epc = self.lb_epcs.get(self.lb_epcs.curselection())
            self.lbl_target.config(text=f"Target: {self.reader.target_epc}", foreground="green")
        except: pass

    # --- LOOPS & CLEANUP ---

    def update_loops(self):
        r, p, vis = self.reader.get_target_data()
        
        if self.reader.target_epc:
            if vis:
                self.val_status.config(text="VISIBLE", foreground="green")
            else:
                self.val_status.config(text="LOST", foreground="red")
        else:
             self.val_status.config(text="NO TARGET", foreground="gray")

        self.val_rssi.config(text=f"{r:.1f} dBm")
        self.val_phase.config(text=f"{p:.0f}°")
        self.loop_timer = self.root.after(200, self.update_loops)

    def on_closing(self):
        # Timer iptal
        if self.loop_timer: self.root.after_cancel(self.loop_timer)
        if self.table_timer: self.root.after_cancel(self.table_timer)
        
        # SAFE SHUTDOWN: Reset voltages to 0V
        if self.serial and self.serial.is_open:
            print("Resetting voltages to 0V before shutdown...")
            try:
                self.serial.write(b"SET1:0.00\nSET2:0.00\n")
                self.serial.flush()
                time.sleep(0.1)  # Give MCU time to process
            except Exception as e:
                print(f"Error resetting voltages: {e}")
        
        # Reader durdur
        if self.reader: 
            self.reader.disconnect_reader()
            
        # Serial kapat
        if self.serial:
            if self.serial.is_open: self.serial.close()
        
        # Uygulamayi oldur
        self.root.destroy()
        try:
            import os; os._exit(0)
        except: pass

    # --- LOGIC ---
    def run_calib(self):
        if not self.serial or not self.serial.is_open:
            messagebox.showerror("Error", "MCU Not Connected!\nPlease connect MCU before calibration.")
            return

        if not self.reader.target_epc: 
            messagebox.showerror("Error", "Select Target First!")
            return
            
        if not self.reader.inventory_running: self.start_inv()
        
        self.btn_calib.config(state=tk.DISABLED); self.root.update()
        best_rssi, best_v = -100, 0
        sweep_range = np.arange(0, 8.6, 0.05) # Fine step for better finding
        self.progress['maximum'] = len(sweep_range)
        
        print(f"Starting robust calibration for Target: {self.reader.target_epc}")

        for i, v in enumerate(sweep_range):
            self.set_volts(v)
            self.lbl_calib_res.config(text=f"Scanning: {v:.2f}V...", foreground="blue")
            self.root.update()

            # Robust Settling Time
            time.sleep(0.5) 
            
            # Robust Averaging
            rs = []
            
            # Take 10 samples
            for _ in range(10): 
                r, _, _ = self.reader.get_target_data()
                # Use a stricter filter for valid reads - assuming typical RSSI > -80 in near field
                if r > -90: 
                    rs.append(r)
                time.sleep(0.02)
            
            # Outlier removal logic (simple)
            if len(rs) > 2:
                # Remove min/max to reduce noise spikes
                rs.remove(max(rs))
                rs.remove(min(rs))
                avg_r = sum(rs)/len(rs)
            elif len(rs) > 0:
                avg_r = sum(rs)/len(rs)
            else:
                avg_r = -99.0
            
            print(f"V: {v:.1f} | Avg RSSI: {avg_r:.2f} (n={len(rs)})")

            if avg_r > best_rssi: best_rssi, best_v = avg_r, v
            self.progress['value'] = i+1; self.root.update()
            
        self.boresight_p = best_v
        
        self.set_volts(best_v); self.calibrated = True
        self.lbl_calib_res.config(text=f"Calib: {best_v:.2f}V ({best_rssi:.1f}dBm)", foreground="green")
        self.btn_calib.config(state=tk.NORMAL)
        
        # Auto-update Calculator Offset
        if hasattr(self, 'ent_offset'): 
            self.ent_offset.delete(0, tk.END)
            self.ent_offset.insert(0, f"{best_v:.2f}")

    def update_voltage(self, val, v_p4=None):
        v1 = float(val)
        
        # Determine v4 based on Sync Mode
        if v_p4 is None:
            if hasattr(self, 'var_st_sync') and self.var_st_sync.get():
                target_phi = float(self.lut.get_phase(v1, channel=1))
                v2 = self.lut.get_voltage(target_phi, channel=4)
            else:
                v2 = v1
        else:
            v2 = v_p4

        # Safety Cap
        v1 = max(0.0, min(8.5, v1))
        v2 = max(0.0, min(8.5, v2))
        
        # Update UI Controls and Phase Displays
        phi_ui = self.lut.get_phase(v1, channel=1)
        if hasattr(self, 'lbl_manual_phi'): self.lbl_manual_phi.config(text=f"Phase: {phi_ui:.1f}°")
        if hasattr(self, 'lbl_phi_inv'): self.lbl_phi_inv.config(text=f"Phase: {phi_ui:.1f}°")
        
        self.val_angle.config(text=f"V: {v1:.2f}") 
        self.lbl_volt.config(text=f"Applied: P1={v1:.2f}V, P4={v2:.2f}V")
        
        self.set_volts(v1, v2)
        
        # Always update live data on steering tab with coloring
        r, p, _ = self.reader.get_target_data()
        if hasattr(self, 'lbl_st_rssi'):
            self.lbl_st_rssi.config(text=f"RSSI: {r:.1f} dBm")
            # Coloring Live RSSI
            if r > -55: self.lbl_st_rssi.config(foreground="#006100") # Green
            elif r > -72: self.lbl_st_rssi.config(foreground="#9c6500") # Orange
            else: self.lbl_st_rssi.config(foreground="#9c0006") # Red
            
            self.lbl_st_phase.config(text=f"RF Phase: {p:.1f}°")

        if self.logging_active:
             # Log Voltage instead of Angle in the Angle column for now, or just V column
            self.log_data(0, volts, r, p)

    def update_experiments(self, val, source):
        # Prevent recursion if we are already updating from set_volts
        if getattr(self, '_updating_v', False): return
        self._updating_v = True
        try:
            v_input = float(val)
            v1, v2 = self.scale_p1.get(), self.scale_p4.get()
            
            if self.var_sync_manual.get():
                if source == "p1":
                    v1 = v_input
                    phi = float(self.lut.func_v_to_p1(v1))
                    v2 = self.lut.get_voltage(phi, channel=4)
                    self.scale_p4.set(v2)
                else:
                    v2 = v_input
                    # This way around P1 will track P4 phase
                    # (Assuming P1 curve is the reference)
                    # For consistency, let's always make P1 the master phase reference
                    # or just match whatever phase P4 is giving.
                    v2 = v_input
                    # Wait, let's keep it simple: if syncing, P1 is the master voltage reference
                    # and P4 follows to match phase. 
                    # If user moves P4, we might want to disable sync or update P1.
                    # Let's say: match phase of whatever you just touched.
                    if source == "p4":
                        phi = float(self.lut.get_phase(v2, channel=4))
                        v1 = self.lut.get_voltage(phi, channel=1)
                        self.scale_p1.set(v1)
            
            self.lbl_p1_val.config(text=f"{v1:.2f} V")
            self.lbl_p4_val.config(text=f"{v2:.2f} V")
            self.set_volts(v1, v2)
        finally:
            self._updating_v = False

    def setup_steer_lut(self):
        f = ttk.Frame(self.tab_steer_lut, padding=20); f.pack(fill=tk.BOTH, expand=True)
        
        # --- LEFT PANEL: CONTROL ---
        left_panel = ttk.Frame(f)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))

        # 1. Port Mapping
        map_fr = ttk.LabelFrame(left_panel, text="Channel Assignment (Mapping)", padding=10)
        map_fr.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(map_fr, text="CH1 Source:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.src_ch1 = ttk.Combobox(map_fr, values=["Port 1", "Port 2", "Port 3", "Port 4"], width=10)
        self.src_ch1.current(0) # Default P1
        self.src_ch1.grid(row=0, column=1, pady=2, padx=5)
        self.src_ch1.bind("<<ComboboxSelected>>", self.update_steer_lut)

        ttk.Label(map_fr, text="CH2 Source:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.src_ch2 = ttk.Combobox(map_fr, values=["Port 1", "Port 2", "Port 3", "Port 4"], width=10)
        self.src_ch2.current(3) # Default P4
        self.src_ch2.grid(row=1, column=1, pady=2, padx=5)
        self.src_ch2.bind("<<ComboboxSelected>>", lambda e: [self.var_pair_mode.set("Custom"), self.update_steer_lut()])

        ttk.Label(map_fr, text="Pair Preset:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.var_pair_mode = tk.StringVar(value="P1-P4")
        self.cb_pair = ttk.Combobox(map_fr, textvariable=self.var_pair_mode, values=["P1-P4", "P2-P3", "Custom"], width=10, state="readonly")
        self.cb_pair.grid(row=2, column=1, pady=5, padx=5)
        self.cb_pair.bind("<<ComboboxSelected>>", self.apply_pair_preset)

        # 2. Mode & Angle
        steer_fr = ttk.LabelFrame(left_panel, text="Beam Control", padding=10)
        steer_fr.pack(fill=tk.X)

        ttk.Label(steer_fr, text="Scan Mode:").pack(anchor=tk.W)
        self.var_steer_mode = tk.StringVar(value="H-Plane")
        mode_btn_fr = ttk.Frame(steer_fr)
        mode_btn_fr.pack(fill=tk.X, pady=5)
        ttk.Radiobutton(mode_btn_fr, text="H-Plane", variable=self.var_steer_mode, value="H-Plane", command=self.update_steer_lut).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(mode_btn_fr, text="E-Plane", variable=self.var_steer_mode, value="E-Plane", command=self.update_steer_lut).pack(side=tk.LEFT, padx=5)

        ttk.Label(steer_fr, text="Target Angle (°):").pack(anchor=tk.W, pady=(10, 0))
        self.scale_steer_angle = tk.Scale(steer_fr, from_=-30, to=30, resolution=0.5, orient=tk.HORIZONTAL, 
                                   length=250, command=self.update_steer_lut)
        self.scale_steer_angle.set(0)
        self.scale_steer_angle.pack(pady=5)
        
        # Auto Sweep Mode
        sweep_fr = ttk.LabelFrame(left_panel, text="Auto Sweep Mode", padding=10)
        sweep_fr.pack(fill=tk.X, pady=(10, 0))
        
        self.sweep_running = False
        
        ttk.Label(sweep_fr, text="Sweep Type:").pack(anchor=tk.W)
        self.var_sweep_type = tk.StringVar(value="Left-Right")
        ttk.Radiobutton(sweep_fr, text="Left ↔ Right", variable=self.var_sweep_type, value="Left-Right").pack(anchor=tk.W)
        ttk.Radiobutton(sweep_fr, text="Custom Range", variable=self.var_sweep_type, value="Custom").pack(anchor=tk.W)
        
        sweep_ctrl = ttk.Frame(sweep_fr)
        sweep_ctrl.pack(fill=tk.X, pady=5)
        ttk.Label(sweep_ctrl, text="Dwell (s):").pack(side=tk.LEFT)
        self.ent_sweep_dwell = ttk.Entry(sweep_ctrl, width=6)
        self.ent_sweep_dwell.insert(0, "2.0")
        self.ent_sweep_dwell.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(sweep_ctrl, text="Step (°):").pack(side=tk.LEFT, padx=(10, 0))
        self.ent_sweep_step = ttk.Entry(sweep_ctrl, width=6)
        self.ent_sweep_step.insert(0, "5.0")
        self.ent_sweep_step.pack(side=tk.LEFT, padx=5)
        
        self.btn_start_sweep = ttk.Button(sweep_fr, text="▶ Start Auto Sweep", command=self.start_auto_sweep)
        self.btn_start_sweep.pack(fill=tk.X, pady=5)
        
        self.btn_stop_sweep = ttk.Button(sweep_fr, text="■ Stop Sweep", command=self.stop_auto_sweep, state=tk.DISABLED)
        self.btn_stop_sweep.pack(fill=tk.X)
        
        self.lbl_sweep_status = ttk.Label(sweep_fr, text="Idle", foreground="gray")
        self.lbl_sweep_status.pack(pady=5)
        
        # RSSI Filter Option
        filter_fr = ttk.Frame(sweep_fr)
        filter_fr.pack(fill=tk.X, pady=5)
        
        self.var_rssi_filter = tk.BooleanVar(value=True)
        ttk.Checkbutton(filter_fr, text="RSSI Filter", variable=self.var_rssi_filter).pack(side=tk.LEFT)
        
        ttk.Label(filter_fr, text="Min:").pack(side=tk.LEFT, padx=(10, 2))
        self.ent_rssi_threshold = ttk.Entry(filter_fr, width=6)
        self.ent_rssi_threshold.insert(0, "-70")
        self.ent_rssi_threshold.pack(side=tk.LEFT)
        ttk.Label(filter_fr, text="dBm").pack(side=tk.LEFT, padx=2)

        # --- RIGHT PANEL: FEEDBACK ---
        right_panel = ttk.Frame(f)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 3. Visualizer (Gain & SLL)
        vis_fr = ttk.LabelFrame(right_panel, text="Estimated Performance", padding=15)
        vis_fr.pack(fill=tk.X, pady=(0, 10))
        
        m_fr = ttk.Frame(vis_fr)
        m_fr.pack(fill=tk.X)
        
        ttk.Label(m_fr, text="Gain:").grid(row=0, column=0, padx=5)
        self.lbl_steer_gain = ttk.Label(m_fr, text="0.00 dBi", font=("Arial", 12, "bold"), foreground="blue")
        self.lbl_steer_gain.grid(row=0, column=1, padx=20)

        ttk.Label(m_fr, text="SLL:").grid(row=0, column=2, padx=5)
        self.lbl_steer_sll = ttk.Label(m_fr, text="0.00 dB", font=("Arial", 12, "bold"), foreground="red")
        self.lbl_steer_sll.grid(row=0, column=3, padx=20)

        # 4. Port Voltages
        volt_fr = ttk.LabelFrame(right_panel, text="Port Voltages (from LUT)", padding=10)
        volt_fr.pack(fill=tk.BOTH, expand=True)
        
        self.lbl_v_ports = []
        for i in range(1, 5):
            ttk.Label(volt_fr, text=f"Port {i}:").grid(row=i-1, column=0, sticky=tk.W, pady=2)
            lbl = ttk.Label(volt_fr, text="0.00 V", font=("Consolas", 10, "bold"))
            lbl.grid(row=i-1, column=1, sticky=tk.E, pady=2, padx=10)
            self.lbl_v_ports.append(lbl)
            
        ttk.Separator(volt_fr, orient=tk.HORIZONTAL).grid(row=4, column=0, columnspan=2, sticky="ew", pady=10)
        
        ttk.Label(volt_fr, text="CH1 OUT:").grid(row=5, column=0, sticky=tk.W)
        self.lbl_ch1_stat = ttk.Label(volt_fr, text="0.00 V", font=("Consolas", 12, "bold"), foreground="green")
        self.lbl_ch1_stat.grid(row=5, column=1, sticky=tk.E, padx=10)

        ttk.Label(volt_fr, text="CH2 OUT:").grid(row=6, column=0, sticky=tk.W)
        self.lbl_ch2_stat = ttk.Label(volt_fr, text="0.00 V", font=("Consolas", 12, "bold"), foreground="green")
        self.lbl_ch2_stat.grid(row=6, column=1, sticky=tk.E, padx=10)
        
        # Apply Button
        ttk.Button(volt_fr, text="APPLY to Hardware", command=self.apply_lut_voltages).grid(row=7, column=0, columnspan=2, pady=10, sticky="ew")
        
        self.lbl_serial_cmd = ttk.Label(right_panel, text="Last Command: None", font=("Consolas", 9), foreground="gray")
        self.lbl_serial_cmd.pack(pady=10)
        
        # --- LIVE BEAM MONITOR PANEL ---
        monitor_fr = ttk.LabelFrame(right_panel, text="Live Beam Monitor (8 Tags)", padding=10)
        monitor_fr.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        # Tag boxes in 2 rows of 4
        self.beam_monitor_labels = {}
        tag_names = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']
        
        # Single row: All 8 tags
        tags_fr = ttk.Frame(monitor_fr)
        tags_fr.pack(fill=tk.X, pady=2)
        
        for tag_name in tag_names:
            tag_box = ttk.Frame(tags_fr, relief='solid', borderwidth=1)
            tag_box.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=1)
            
            lbl_name = tk.Label(tag_box, text=tag_name, font=("Arial", 7, "bold"), bg="#f0f0f0", width=4)
            lbl_name.pack()
            
            lbl_rssi = tk.Label(tag_box, text="--", font=("Arial", 9, "bold"), bg="#d0d0d0", fg="black", width=5)
            lbl_rssi.pack(fill=tk.BOTH, expand=True)
            
            lbl_phase = tk.Label(tag_box, text="--°", font=("Arial", 6), bg="#f0f0f0")
            lbl_phase.pack()
            
            self.beam_monitor_labels[tag_name] = {'rssi': lbl_rssi, 'phase': lbl_phase, 'box': tag_box}
        
        # Snapshot save button
        ttk.Button(monitor_fr, text="💾 Save Snapshot", command=self.save_beam_snapshot).pack(pady=5)
        
        # Start live update timer
        self.update_beam_monitor()

    def setup_calib_sweep(self):
        """Setup Calibration Sweep tab - High Resolution Beam Mapping"""
        f = ttk.Frame(self.tab_calib_sweep, padding=10)
        f.pack(fill=tk.BOTH, expand=True)
        
        # --- LEFT PANEL: CONTROLS ---
        left_panel = ttk.Frame(f)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        
        # 1. Safety Zone Settings
        safety_fr = ttk.LabelFrame(left_panel, text="🛡️ Safety Zone (Grating Lobe Prevention)", padding=10)
        safety_fr.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(safety_fr, text="Safe Min (°):").grid(row=0, column=0, sticky=tk.E, pady=2)
        self.ent_safe_min = ttk.Entry(safety_fr, width=8)
        self.ent_safe_min.insert(0, "-20")
        self.ent_safe_min.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(safety_fr, text="Safe Max (°):").grid(row=0, column=2, sticky=tk.E, pady=2)
        self.ent_safe_max = ttk.Entry(safety_fr, width=8)
        self.ent_safe_max.insert(0, "20")
        self.ent_safe_max.grid(row=0, column=3, padx=5, pady=2)
        
        # 2. Offset Calibration
        offset_fr = ttk.LabelFrame(left_panel, text="🎯 Boresight Offset Calibration", padding=10)
        offset_fr.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(offset_fr, text="Global Offset (°):").pack(side=tk.LEFT)
        self.scale_global_offset = tk.Scale(offset_fr, from_=-10, to=10, resolution=0.5, 
                                            orient=tk.HORIZONTAL, length=150)
        self.scale_global_offset.set(0)
        self.scale_global_offset.pack(side=tk.LEFT, padx=10)
        
        ttk.Label(offset_fr, text="Tip: Set + to shift beam RIGHT").pack(anchor=tk.W)
        
        # 3. Sweep Parameters
        sweep_fr = ttk.LabelFrame(left_panel, text="📊 Micro-Sweep Parameters", padding=10)
        sweep_fr.pack(fill=tk.X, pady=(0, 10))
        
        param_grid = ttk.Frame(sweep_fr)
        param_grid.pack(fill=tk.X)
        
        ttk.Label(param_grid, text="Start (°):").grid(row=0, column=0, sticky=tk.E)
        self.ent_csweep_start = ttk.Entry(param_grid, width=6)
        self.ent_csweep_start.insert(0, "-20")
        self.ent_csweep_start.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(param_grid, text="End (°):").grid(row=0, column=2, sticky=tk.E)
        self.ent_csweep_end = ttk.Entry(param_grid, width=6)
        self.ent_csweep_end.insert(0, "20")
        self.ent_csweep_end.grid(row=0, column=3, padx=5, pady=2)
        
        ttk.Label(param_grid, text="Step (°):").grid(row=1, column=0, sticky=tk.E)
        self.ent_csweep_step = ttk.Entry(param_grid, width=6)
        self.ent_csweep_step.insert(0, "0.25")
        self.ent_csweep_step.grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Label(param_grid, text="Dwell (s):").grid(row=1, column=2, sticky=tk.E)
        self.ent_csweep_dwell = ttk.Entry(param_grid, width=6)
        self.ent_csweep_dwell.insert(0, "1.5")
        self.ent_csweep_dwell.grid(row=1, column=3, padx=5, pady=2)
        
        # Scan mode
        ttk.Label(sweep_fr, text="Scan Mode:").pack(anchor=tk.W, pady=(10, 0))
        self.var_csweep_mode = tk.StringVar(value="H-Plane")
        mode_fr = ttk.Frame(sweep_fr)
        mode_fr.pack(fill=tk.X)
        ttk.Radiobutton(mode_fr, text="H-Plane", variable=self.var_csweep_mode, value="H-Plane").pack(side=tk.LEFT)
        ttk.Radiobutton(mode_fr, text="E-Plane", variable=self.var_csweep_mode, value="E-Plane").pack(side=tk.LEFT)
        
        # 4. Control Buttons
        ctrl_fr = ttk.Frame(left_panel)
        ctrl_fr.pack(fill=tk.X, pady=10)
        
        self.btn_csweep_start = ttk.Button(ctrl_fr, text="▶ START CALIBRATION SWEEP", 
                                           command=self.start_calibration_sweep)
        self.btn_csweep_start.pack(fill=tk.X, pady=2)
        
        self.btn_csweep_stop = ttk.Button(ctrl_fr, text="■ STOP", 
                                          command=self.stop_calibration_sweep, state=tk.DISABLED)
        self.btn_csweep_stop.pack(fill=tk.X, pady=2)
        
        # Manual test
        test_fr = ttk.LabelFrame(left_panel, text="🔧 Manual Angle Test", padding=10)
        test_fr.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Label(test_fr, text="Angle:").pack(side=tk.LEFT)
        self.ent_test_angle = ttk.Entry(test_fr, width=8)
        self.ent_test_angle.insert(0, "0")
        self.ent_test_angle.pack(side=tk.LEFT, padx=5)
        ttk.Button(test_fr, text="Apply", command=self.apply_test_angle).pack(side=tk.LEFT)
        
        # --- RIGHT PANEL: FEEDBACK ---
        right_panel = ttk.Frame(f)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Status
        status_fr = ttk.LabelFrame(right_panel, text="📈 Sweep Status", padding=10)
        status_fr.pack(fill=tk.X)
        
        self.lbl_csweep_status = ttk.Label(status_fr, text="Ready", font=("Arial", 12, "bold"))
        self.lbl_csweep_status.pack()
        
        self.pb_csweep = ttk.Progressbar(status_fr, length=300, mode='determinate')
        self.pb_csweep.pack(fill=tk.X, pady=5)
        
        # Current values
        val_fr = ttk.Frame(status_fr)
        val_fr.pack(fill=tk.X, pady=5)
        
        ttk.Label(val_fr, text="Angle:").grid(row=0, column=0)
        self.lbl_csweep_angle = ttk.Label(val_fr, text="--°", font=("Arial", 14, "bold"), foreground="blue")
        self.lbl_csweep_angle.grid(row=0, column=1, padx=10)
        
        ttk.Label(val_fr, text="CH1:").grid(row=0, column=2)
        self.lbl_csweep_v1 = ttk.Label(val_fr, text="-- V", font=("Arial", 12))
        self.lbl_csweep_v1.grid(row=0, column=3, padx=10)
        
        ttk.Label(val_fr, text="CH2:").grid(row=0, column=4)
        self.lbl_csweep_v2 = ttk.Label(val_fr, text="-- V", font=("Arial", 12))
        self.lbl_csweep_v2.grid(row=0, column=5, padx=10)
        
        # Log display
        log_fr = ttk.LabelFrame(right_panel, text="📝 Sweep Log", padding=5)
        log_fr.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.txt_csweep_log = tk.Text(log_fr, height=15, width=60, font=("Consolas", 9))
        self.txt_csweep_log.pack(fill=tk.BOTH, expand=True)
        
        # 8-Tag Monitor (compact)
        monitor_fr = ttk.LabelFrame(right_panel, text="Live Tag Monitor", padding=5)
        monitor_fr.pack(fill=tk.X)
        
        self.csweep_tag_labels = {}
        tag_names = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']
        tags_row = ttk.Frame(monitor_fr)
        tags_row.pack(fill=tk.X)
        
        for tag in tag_names:
            tag_box = ttk.Frame(tags_row, relief='solid', borderwidth=1)
            tag_box.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=1)
            lbl = tk.Label(tag_box, text=f"{tag}\n--", font=("Arial", 8), bg="#d0d0d0", width=5)
            lbl.pack(fill=tk.BOTH, expand=True)
            self.csweep_tag_labels[tag] = lbl
        
        # Initialize sweep state
        self.csweep_running = False
        self.csweep_angles = []
        self.csweep_index = 0

    def setup_auto_mapper(self):
        """Setup Auto Tag Mapper tab - Automatic tag position discovery"""
        f = ttk.Frame(self.tab_auto_mapper, padding=10)
        f.pack(fill=tk.BOTH, expand=True)
        
        # Header
        header = ttk.Label(f, text="🗺️ Automatic Tag Position Mapper", font=("Arial", 14, "bold"))
        header.pack(pady=(0, 10))
        
        ttk.Label(f, text="Bu araç, anteni -20° ile +20° arasında tarar ve hangi tag'ın hangi pozisyonda olduğunu otomatik belirler.", 
                  wraplength=600).pack(pady=5)
        
        # --- LEFT PANEL ---
        main_fr = ttk.Frame(f)
        main_fr.pack(fill=tk.BOTH, expand=True)
        
        left_panel = ttk.Frame(main_fr)
        left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        
        # 1. Port Mode Selection (CRITICAL)
        mode_fr = ttk.LabelFrame(left_panel, text="⚡ Port Modu Seçimi (ÖNEMLİ!)", padding=10)
        mode_fr.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(mode_fr, text="Anten Görünümü (Önden):", font=("Arial", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(mode_fr, text="┌─────────────┐\n│  4       3  │  (Üst)\n│             │\n│  1       2  │  (Alt)\n└─────────────┘", 
                  font=("Consolas", 10)).pack(pady=5)
        
        self.var_port_mode = tk.StringVar(value="P1-P4")
        
        mode_opt_fr = ttk.Frame(mode_fr)
        mode_opt_fr.pack(fill=tk.X, pady=5)
        
        p14_fr = ttk.Frame(mode_opt_fr)
        p14_fr.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(p14_fr, text="P1-P4 (Sol taraf)", variable=self.var_port_mode, value="P1-P4").pack(side=tk.LEFT)
        ttk.Label(p14_fr, text="→ (-) = SAĞA, (+) = SOLA", foreground="blue").pack(side=tk.LEFT, padx=10)
        
        p23_fr = ttk.Frame(mode_opt_fr)
        p23_fr.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(p23_fr, text="P2-P3 (Sağ taraf)", variable=self.var_port_mode, value="P2-P3").pack(side=tk.LEFT)
        ttk.Label(p23_fr, text="→ (-) = SOLA, (+) = SAĞA", foreground="green").pack(side=tk.LEFT, padx=10)
        
        # 2. Scan Parameters
        scan_fr = ttk.LabelFrame(left_panel, text="📊 Tarama Ayarları", padding=10)
        scan_fr.pack(fill=tk.X, pady=(0, 10))
        
        param_grid = ttk.Frame(scan_fr)
        param_grid.pack(fill=tk.X)
        
        ttk.Label(param_grid, text="Başlangıç (°):").grid(row=0, column=0, sticky=tk.E)
        self.ent_mapper_start = ttk.Entry(param_grid, width=6)
        self.ent_mapper_start.insert(0, "-20")
        self.ent_mapper_start.grid(row=0, column=1, padx=5, pady=2)
        
        ttk.Label(param_grid, text="Bitiş (°):").grid(row=0, column=2, sticky=tk.E)
        self.ent_mapper_end = ttk.Entry(param_grid, width=6)
        self.ent_mapper_end.insert(0, "20")
        self.ent_mapper_end.grid(row=0, column=3, padx=5, pady=2)
        
        ttk.Label(param_grid, text="Adım (°):").grid(row=1, column=0, sticky=tk.E)
        self.ent_mapper_step = ttk.Entry(param_grid, width=6)
        self.ent_mapper_step.insert(0, "0.5")
        self.ent_mapper_step.grid(row=1, column=1, padx=5, pady=2)
        
        ttk.Label(param_grid, text="Bekleme (s):").grid(row=1, column=2, sticky=tk.E)
        self.ent_mapper_dwell = ttk.Entry(param_grid, width=6)
        self.ent_mapper_dwell.insert(0, "1.5")
        self.ent_mapper_dwell.grid(row=1, column=3, padx=5, pady=2)
        
        # Filter option
        self.var_mapper_filter = tk.BooleanVar(value=True)
        ttk.Checkbutton(scan_fr, text="Sadece Config'deki Tag'ları Kullan (T1-T8)", 
                        variable=self.var_mapper_filter).pack(anchor=tk.W, pady=5)
        
        # 3. Control Buttons
        ctrl_fr = ttk.Frame(left_panel)
        ctrl_fr.pack(fill=tk.X, pady=10)
        
        self.btn_mapper_start = ttk.Button(ctrl_fr, text="▶ TARAMAYİ BAŞLAT", 
                                           command=self.start_auto_mapper)
        self.btn_mapper_start.pack(fill=tk.X, pady=2)
        
        self.btn_mapper_stop = ttk.Button(ctrl_fr, text="■ DURDUR", 
                                          command=self.stop_auto_mapper, state=tk.DISABLED)
        self.btn_mapper_stop.pack(fill=tk.X, pady=2)
        
        # 4. Status
        status_fr = ttk.LabelFrame(left_panel, text="📈 Durum", padding=10)
        status_fr.pack(fill=tk.X)
        
        self.lbl_mapper_status = ttk.Label(status_fr, text="Hazır", font=("Arial", 11, "bold"))
        self.lbl_mapper_status.pack()
        
        self.pb_mapper = ttk.Progressbar(status_fr, length=200, mode='determinate')
        self.pb_mapper.pack(fill=tk.X, pady=5)
        
        self.lbl_mapper_angle = ttk.Label(status_fr, text="Açı: --°", font=("Arial", 12))
        self.lbl_mapper_angle.pack()
        
        # --- RIGHT PANEL ---
        right_panel = ttk.Frame(main_fr)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        # Results Display
        result_fr = ttk.LabelFrame(right_panel, text="📍 Otomatik Tag Sıralaması (Sol → Sağ)", padding=10)
        result_fr.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(result_fr, text="Tarama sonrası tag'lar fiziksel pozisyonlarına göre sıralanacak:", 
                  font=("Arial", 9)).pack(anchor=tk.W)
        
        # Result boxes
        result_boxes_fr = ttk.Frame(result_fr)
        result_boxes_fr.pack(fill=tk.X, pady=10)
        
        self.mapper_result_labels = []
        positions = ['← SOL', '', '', 'ORTA', '', '', '', 'SAĞ →']
        
        for i in range(8):
            box = ttk.Frame(result_boxes_fr, relief='solid', borderwidth=2)
            box.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=2)
            
            pos_lbl = ttk.Label(box, text=positions[i] if positions[i] else f"P{i+1}", font=("Arial", 7))
            pos_lbl.pack()
            
            tag_lbl = tk.Label(box, text="?", font=("Arial", 10, "bold"), bg="#e0e0e0", width=8, height=2)
            tag_lbl.pack(fill=tk.BOTH, expand=True)
            
            angle_lbl = ttk.Label(box, text="--°", font=("Arial", 8))
            angle_lbl.pack()
            
            self.mapper_result_labels.append({'tag': tag_lbl, 'angle': angle_lbl})
        
        # Peak angle per tag display
        peaks_fr = ttk.LabelFrame(result_fr, text="📊 Her Tag'ın Peak Açısı", padding=5)
        peaks_fr.pack(fill=tk.X, pady=10)
        
        self.txt_mapper_peaks = tk.Text(peaks_fr, height=8, width=50, font=("Consolas", 9))
        self.txt_mapper_peaks.pack(fill=tk.BOTH, expand=True)
        
        # Apply button
        ttk.Button(result_fr, text="✅ Bu Sıralamayı Kaydet (T1-T8 olarak ata)", 
                   command=self.apply_mapper_results).pack(pady=5)
        
        # Initialize
        self.mapper_running = False
        self.mapper_data = {}  # {epc: [(angle, rssi), ...]}

    def start_calibration_sweep(self):
        """Start high-resolution calibration sweep"""
        if self.csweep_running:
            return
        
        if not self.serial or not self.serial.is_open:
            messagebox.showwarning("MCU Not Connected", "Connect MCU first!")
            return
        
        try:
            start_angle = float(self.ent_csweep_start.get())
            end_angle = float(self.ent_csweep_end.get())
            step = float(self.ent_csweep_step.get())
            self.csweep_dwell = float(self.ent_csweep_dwell.get())
            self.csweep_safe_min = float(self.ent_safe_min.get())
            self.csweep_safe_max = float(self.ent_safe_max.get())
        except:
            messagebox.showerror("Error", "Invalid parameters")
            return
        
        # Generate angle list
        self.csweep_angles = []
        angle = start_angle
        while angle <= end_angle + step/2:
            self.csweep_angles.append(angle)
            angle += step
        
        self.csweep_index = 0
        self.csweep_running = True
        self.btn_csweep_start.config(state=tk.DISABLED)
        self.btn_csweep_stop.config(state=tk.NORMAL)
        
        # Create log file
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csweep_log_file = f"calib_sweep_{ts}.csv"
        self.csweep_txt_file = f"calib_sweep_{ts}.txt"
        
        # Write headers
        with open(self.csweep_log_file, 'w', newline='') as f:
            writer = csv.writer(f)
            header = ['Timestamp', 'Step', 'Req_Angle', 'Actual_Angle', 'CH1_V', 'CH2_V', 
                      'Gain', 'SLL', 'Clamped', 'Peak_Tag']
            for t in ['T1','T2','T3','T4','T5','T6','T7','T8']:
                header.extend([f'{t}_RSSI', f'{t}_Phase', f'{t}_Doppler'])
            writer.writerow(header)
        
        with open(self.csweep_txt_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"CALIBRATION SWEEP LOG - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 80 + "\n")
            f.write(f"Mode: {self.var_csweep_mode.get()} | Offset: {self.scale_global_offset.get()}°\n")
            f.write(f"Safe Zone: [{self.csweep_safe_min}° to {self.csweep_safe_max}°]\n")
            f.write(f"Sweep: {start_angle}° to {end_angle}° in {step}° steps\n")
            f.write("=" * 80 + "\n\n")
        
        # Clear log display
        self.txt_csweep_log.delete(1.0, tk.END)
        self.txt_csweep_log.insert(tk.END, f"Starting sweep: {len(self.csweep_angles)} steps\n")
        self.txt_csweep_log.insert(tk.END, "-" * 60 + "\n")
        
        self.pb_csweep['maximum'] = len(self.csweep_angles)
        self.pb_csweep['value'] = 0
        
        # Start sweep
        self.run_calibration_step()
    
    def run_calibration_step(self):
        """Execute one step of calibration sweep"""
        if not self.csweep_running or self.csweep_index >= len(self.csweep_angles):
            self.finish_calibration_sweep()
            return
        
        try:
            req_angle = self.csweep_angles[self.csweep_index]
            offset = self.scale_global_offset.get()
            adjusted = req_angle + offset
            
            # Safety clamping
            clamped = False
            if adjusted < self.csweep_safe_min:
                adjusted = self.csweep_safe_min
                clamped = True
            elif adjusted > self.csweep_safe_max:
                adjusted = self.csweep_safe_max
                clamped = True
            
            # Get voltages from LUT
            mode = self.var_csweep_mode.get()
            data = self.steer_lut.get_data(mode, adjusted)
            
            if data:
                v1, v2 = self.steer_lut.get_active_voltages(mode, adjusted)
                gain = data['Est_Gain_dBi']
                sll = data['Est_SLL_dB']
            else:
                v1, v2, gain, sll = 0, 0, 0, 0
            
            # Apply voltages
            self.set_volts(v1, v2)
            
            # Update UI
            status = f"⚠️ CLAMPED → {adjusted:.1f}°" if clamped else f"{adjusted:+.1f}°"
            self.lbl_csweep_angle.config(text=f"{adjusted:+.1f}°", foreground="red" if clamped else "blue")
            self.lbl_csweep_v1.config(text=f"{v1:.3f} V")
            self.lbl_csweep_v2.config(text=f"{v2:.3f} V")
            self.lbl_csweep_status.config(text=f"Step {self.csweep_index+1}/{len(self.csweep_angles)}")
            self.pb_csweep['value'] = self.csweep_index + 1
            
            # Get tag RSSI and Phase
            tag_data = self.get_calibration_tag_rssi()
            
            # Update tag monitor
            for tag, lbl in self.csweep_tag_labels.items():
                td = tag_data.get(tag)
                if td:
                    rssi = td['rssi']
                    color = "#c6efce" if rssi > -60 else ("#ffeb9c" if rssi > -68 else "#ffc7ce")
                    lbl.config(text=f"{tag}\n{rssi:.0f}", bg=color)
                else:
                    lbl.config(text=f"{tag}\n--", bg="#d0d0d0")
            
            # Find peak tag
            peak_tag = None
            peak_rssi = -100
            for t, td in tag_data.items():
                if td and td['rssi'] > peak_rssi:
                    peak_rssi = td['rssi']
                    peak_tag = t
            
            # Log line
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_line = f"[{ts}] {req_angle:+6.1f}° → {adjusted:+6.1f}° | V:{v1:.2f}/{v2:.2f} | Peak:{peak_tag or '--'}"
            if clamped:
                log_line += " ⚠️CLAMP"
            
            self.txt_csweep_log.insert(tk.END, log_line + "\n")
            self.txt_csweep_log.see(tk.END)
            
            # Write to CSV with RSSI+Phase+Doppler for all 8 tags
            with open(self.csweep_log_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                row = [ts, self.csweep_index+1, f"{req_angle:.2f}", f"{adjusted:.2f}", 
                       f"{v1:.3f}", f"{v2:.3f}", f"{gain:.2f}", f"{sll:.2f}", "Yes" if clamped else "No", peak_tag or "--"]
                for t in ['T1','T2','T3','T4','T5','T6','T7','T8']:
                    td = tag_data.get(t)
                    if td:
                        row.extend([f"{td['rssi']:.1f}", f"{td['phase']:.1f}", f"{td['doppler']:.1f}"])
                    else:
                        row.extend(['--', '--', '--'])
                writer.writerow(row)
            
            
            # Write detailed LLM report
            with open(self.csweep_txt_file, 'a', encoding='utf-8') as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write(f"STEP {self.csweep_index+1}/{len(self.csweep_angles)} | {ts}\n")
                f.write(f"Requested: {req_angle:+.2f}° | Applied: {adjusted:+.2f}°")
                if clamped:
                    f.write(" [CLAMPED]")
                f.write("\n")
                f.write(f"Voltages: CH1={v1:.3f}V, CH2={v2:.3f}V\n")
                f.write(f"LUT Expected: Gain={gain:.2f} dBi, SLL={sll:.2f} dB\n")
                f.write(f"Peak Signal: {peak_tag} ({peak_rssi:.1f} dBm)\n\n")
                f.write("TAG MEASUREMENTS (All T1-T8):\n")
                f.write(f"{'Tag':>6} | {'RSSI (dBm)':>12} | {'Phase (°)':>10} | {'Doppler (Hz)':>12} | {'EPC Suffix':>12} | Status\n")
                f.write("-" * 75 + "\n")
                for t in ['T1','T2','T3','T4','T5','T6','T7','T8']:
                    td = tag_data.get(t)
                    if td:
                        status = "★ PEAK" if t == peak_tag else ("● Good" if td['rssi'] > -65 else "○ Weak")
                        epc_suffix = td.get('epc', '')[-8:] if td.get('epc') else '--'
                        f.write(f"{t:>6} | {td['rssi']:>12.1f} | {td['phase']:>10.1f} | {td['doppler']:>12.1f} | {epc_suffix:>12} | {status}\n")
                    else:
                        f.write(f"{t:>6} | {'--':>12} | {'--':>10} | {'--':>12} | {'--':>12} | ✗ No Read\n")
            
            self.csweep_index += 1
            self.root.after(int(self.csweep_dwell * 1000), self.run_calibration_step)
        
        except Exception as e:
            print(f"Calibration Sweep Error: {e}")
            import traceback
            traceback.print_exc()
            self.stop_calibration_sweep()
            messagebox.showerror("Calibration Error", f"Sweep stopped due to error:\n{e}")
    
    def get_calibration_tag_rssi(self):
        """Get current RSSI, Phase, and Doppler for all 8 calibration tags"""
        tag_data = {}
        data = self.reader.get_all_data() if hasattr(self, 'reader') else {}
        current_time = time.time()
        timeout = 2.0
        
        for tag_name, tag_suffix in self.beam_tag_positions.items():
            if not tag_suffix:
                continue
            for epc, d in data.items():
                if epc.endswith(tag_suffix) or tag_suffix.endswith(epc[-4:]):
                    if (current_time - d['seen_time']) < timeout:
                        tag_data[tag_name] = {
                            'rssi': d['rssi'], 
                            'phase': d['phase_deg'],
                            'doppler': d.get('doppler', 0),
                            'epc': epc
                        }
                    break
        
        return tag_data
    
    def stop_calibration_sweep(self):
        """Stop calibration sweep"""
        self.csweep_running = False
        self.finish_calibration_sweep()
    
    def finish_calibration_sweep(self):
        """Finish calibration sweep"""
        self.csweep_running = False
        self.btn_csweep_start.config(state=tk.NORMAL)
        self.btn_csweep_stop.config(state=tk.DISABLED)
        self.lbl_csweep_status.config(text="Complete!" if self.csweep_index >= len(self.csweep_angles) else "Stopped")
        
        # Reset to 0
        data = self.steer_lut.get_data(self.var_csweep_mode.get(), 0)
        if data:
            if self.var_csweep_mode.get() == "H-Plane":
                self.set_volts(data['V_P3'], data['V_P4'])
            else:
                self.set_volts(data['V_P2'], data['V_P3'])
        
        if hasattr(self, 'csweep_txt_file'):
            with open(self.csweep_txt_file, 'a', encoding='utf-8') as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write("SWEEP COMPLETE\n")
                f.write("=" * 80 + "\n")
        
        self.txt_csweep_log.insert(tk.END, "-" * 60 + "\n")
        self.txt_csweep_log.insert(tk.END, f"Sweep finished! Files: {getattr(self, 'csweep_log_file', 'N/A')}\n")
    
    def apply_test_angle(self):
        """Apply manual test angle with offset and safety clamping"""
        try:
            req_angle = float(self.ent_test_angle.get())
            offset = self.scale_global_offset.get()
            adjusted = req_angle + offset
            
            safe_min = float(self.ent_safe_min.get())
            safe_max = float(self.ent_safe_max.get())
            
            clamped = False
            if adjusted < safe_min:
                adjusted = safe_min
                clamped = True
            elif adjusted > safe_max:
                adjusted = safe_max
                clamped = True
            
            mode = self.var_csweep_mode.get()
            data = self.steer_lut.get_data(mode, adjusted)
            
            if data:
                if mode == "H-Plane":
                    v1, v2 = data['V_P3'], data['V_P4']
                else:
                    v1, v2 = data['V_P2'], data['V_P3']
                
                self.set_volts(v1, v2)
                
                self.lbl_csweep_angle.config(text=f"{adjusted:+.1f}°", foreground="red" if clamped else "blue")
                self.lbl_csweep_v1.config(text=f"{v1:.3f} V")
                self.lbl_csweep_v2.config(text=f"{v2:.3f} V")
                
                status = f"Applied: {adjusted:+.1f}°"
                if clamped:
                    status += f" (CLAMPED from {req_angle+offset:.1f}°)"
                self.lbl_csweep_status.config(text=status)
                
        except Exception as e:
            messagebox.showerror("Error", f"Invalid angle: {e}")

    def start_auto_mapper(self):
        """Start automatic tag position mapper sweep"""
        if self.mapper_running:
            return
        
        if not self.serial or not self.serial.is_open:
            messagebox.showwarning("MCU Bağlı Değil", "Önce MCU'yu bağlayın!")
            return
        
        try:
            start_angle = float(self.ent_mapper_start.get())
            end_angle = float(self.ent_mapper_end.get())
            step = float(self.ent_mapper_step.get())
            self.mapper_dwell = float(self.ent_mapper_dwell.get())
        except:
            messagebox.showerror("Hata", "Geçersiz parametreler")
            return
        
        step = float(self.ent_mapper_step.get())
        self.mapper_dwell = float(self.ent_mapper_dwell.get())
        
        # NEW: Two-phase sweep approach
        # Phase 1: 0 → +20 (LEFT direction for P1-P4, RIGHT for P2-P3)
        # Phase 2: 0 → -20 (RIGHT direction for P1-P4, LEFT for P2-P3)
        
        self.mapper_phase = 1  # Start with phase 1
        self.mapper_step = step
        
        # Phase 1 angles: 0 to +20
        self.mapper_phase1_angles = []
        angle = 0
        while angle <= 20 + step/2:
            self.mapper_phase1_angles.append(angle)
            angle += step
        
        # Phase 2 angles: 0 to -20
        self.mapper_phase2_angles = []
        angle = 0
        while angle >= -20 - step/2:
            self.mapper_phase2_angles.append(angle)
            angle -= step
        
        self.mapper_angles = self.mapper_phase1_angles
        self.mapper_index = 0
        self.mapper_running = True
        
        # Data storage: {epc: {'phase1_rssi': [], 'phase2_rssi': []}}
        self.mapper_data = {}
        self.mapper_port_mode = self.var_port_mode.get()
        
        self.btn_mapper_start.config(state=tk.DISABLED)
        self.btn_mapper_stop.config(state=tk.NORMAL)
        
        # Clear previous results
        for lbl_dict in self.mapper_result_labels:
            lbl_dict['tag'].config(text="?", bg="#e0e0e0")
            lbl_dict['angle'].config(text="--")
        self.txt_mapper_peaks.delete(1.0, tk.END)
        self.txt_mapper_peaks.insert(tk.END, ">>> İki Yönlü Hareket Testi <<<\n")
        self.txt_mapper_peaks.insert(tk.END, f"Faz 1: 0° → +20° (Sola itme)\n")
        self.txt_mapper_peaks.insert(tk.END, f"Faz 2: 0° → -20° (Sağa itme)\n\n")
        
        total_steps = len(self.mapper_phase1_angles) + len(self.mapper_phase2_angles)
        self.pb_mapper['maximum'] = total_steps
        self.pb_mapper['value'] = 0
        
        self.lbl_mapper_status.config(text=f"Faz 1/2: Sola hareket... ({self.mapper_port_mode})")
        
        # Set beam to center first
        data = self.steer_lut.get_data("H-Plane", 0)
        if data:
            if self.mapper_port_mode == "P1-P4":
                self.set_volts(data['V_P3'], data['V_P4'])
            else:
                self.set_volts(data['V_P2'], data['V_P3'])
        
        self.root.after(500, self.run_mapper_step)
    
    def run_mapper_step(self):
        """Execute one step of auto mapper - two phase approach"""
        if not self.mapper_running:
            self.finish_auto_mapper()
            return
        
        # Check if current phase is complete
        if self.mapper_index >= len(self.mapper_angles):
            if self.mapper_phase == 1:
                # Phase 1 complete, start Phase 2
                self.mapper_phase = 2
                self.mapper_angles = self.mapper_phase2_angles
                self.mapper_index = 0
                self.lbl_mapper_status.config(text=f"Faz 2/2: Sağa hareket...")
                self.txt_mapper_peaks.insert(tk.END, "Faz 1 tamamlandı. Faz 2 başlıyor...\n")
                
                # Go back to center first
                data = self.steer_lut.get_data("H-Plane", 0)
                if data:
                    if self.mapper_port_mode == "P1-P4":
                        self.set_volts(data['V_P3'], data['V_P4'])
                    else:
                        self.set_volts(data['V_P2'], data['V_P3'])
                
                self.root.after(500, self.run_mapper_step)
                return
            else:
                # Both phases complete
                self.finish_auto_mapper()
                return
        
        angle = self.mapper_angles[self.mapper_index]
        
        # Get voltages from LUT based on port mode
        data = self.steer_lut.get_data("H-Plane", angle)
        
        if data:
            if self.mapper_port_mode == "P1-P4":
                v1, v2 = data['V_P3'], data['V_P4']
            else:
                v1, v2 = data['V_P2'], data['V_P3']
        else:
            v1, v2 = 0, 0
        
        # Apply voltages
        self.set_volts(v1, v2)
        
        # Update UI
        phase_label = "SOLA→" if self.mapper_phase == 1 else "SAĞA→"
        total = len(self.mapper_phase1_angles) + len(self.mapper_phase2_angles)
        current = (len(self.mapper_phase1_angles) if self.mapper_phase == 2 else 0) + self.mapper_index + 1
        
        self.lbl_mapper_angle.config(text=f"{phase_label} {angle:+.1f}°")
        self.lbl_mapper_status.config(text=f"Faz {self.mapper_phase}/2 | Adım {current}/{total}")
        self.pb_mapper['value'] = current
        
        # Collect tag data at this angle
        self.root.after(int(self.mapper_dwell * 800), lambda a=angle, p=self.mapper_phase: self.collect_mapper_data(a, p))
    
    def collect_mapper_data(self, angle, phase):
        """Collect tag data at current angle for specified phase"""
        if not self.mapper_running:
            return
        
        data = self.reader.get_all_data() if hasattr(self, 'reader') else {}
        current_time = time.time()
        timeout = 2.0
        
        # Get config filter suffixes if filtering is enabled
        filter_enabled = self.var_mapper_filter.get() if hasattr(self, 'var_mapper_filter') else False
        config_suffixes = []
        if filter_enabled and self.beam_tag_positions:
            config_suffixes = [v for v in self.beam_tag_positions.values() if v]
        
        for epc, d in data.items():
            if (current_time - d['seen_time']) < timeout:
                # Apply filter if enabled
                if filter_enabled and config_suffixes:
                    matched = False
                    for suffix in config_suffixes:
                        if epc.endswith(suffix) or suffix in epc[-6:]:
                            matched = True
                            break
                    if not matched:
                        continue  # Skip tags not in config
                
                if epc not in self.mapper_data:
                    self.mapper_data[epc] = {'phase1': [], 'phase2': []}
                
                phase_key = 'phase1' if phase == 1 else 'phase2'
                self.mapper_data[epc][phase_key].append({
                    'angle': angle,
                    'rssi': d['rssi']
                })
        
        self.mapper_index += 1
        remaining_dwell = int(self.mapper_dwell * 200)
        self.root.after(remaining_dwell, self.run_mapper_step)
    
    def stop_auto_mapper(self):
        """Stop auto mapper"""
        self.mapper_running = False
        self.finish_auto_mapper()
    
    def finish_auto_mapper(self):
        """Finish auto mapper - Two Phase Directional Analysis"""
        self.mapper_running = False
        self.btn_mapper_start.config(state=tk.NORMAL)
        self.btn_mapper_stop.config(state=tk.DISABLED)
        
        if not self.mapper_data:
            self.lbl_mapper_status.config(text="Veri yok!")
            return
        
        self.lbl_mapper_status.config(text="İki Yönlü Hareket Analizi...")
        
        # TWO-PHASE DIRECTIONAL ANALYSIS:
        # Phase 1: 0 → +20 = SOLA hareket (P1-P4 için)
        # Phase 2: 0 → -20 = SAĞA hareket (P1-P4 için)
        #
        # Soldaki tag'lar: Phase 1'de RSSI ARTAR
        # Sağdaki tag'lar: Phase 2'de RSSI ARTAR
        #
        # Pozisyon Skoru = (Phase1 RSSI kazancı) - (Phase2 RSSI kazancı)
        #   Pozitif = SOLDA
        #   Negatif = SAĞDA
        
        tag_analysis = {}
        
        for epc, phase_data in self.mapper_data.items():
            phase1_readings = phase_data.get('phase1', [])
            phase2_readings = phase_data.get('phase2', [])
            
            if len(phase1_readings) < 3 or len(phase2_readings) < 3:
                continue
            
            # Phase 1 analysis: 0 → +20 (Sola)
            # Calculate RSSI change: end - start
            p1_start_rssi = [r['rssi'] for r in phase1_readings if abs(r['angle']) < 3]
            p1_end_rssi = [r['rssi'] for r in phase1_readings if r['angle'] > 15]
            
            if p1_start_rssi and p1_end_rssi:
                p1_gain = (sum(p1_end_rssi)/len(p1_end_rssi)) - (sum(p1_start_rssi)/len(p1_start_rssi))
            else:
                # Fallback: use first vs last
                p1_gain = phase1_readings[-1]['rssi'] - phase1_readings[0]['rssi']
            
            # Phase 2 analysis: 0 → -20 (Sağa)
            p2_start_rssi = [r['rssi'] for r in phase2_readings if abs(r['angle']) < 3]
            p2_end_rssi = [r['rssi'] for r in phase2_readings if r['angle'] < -15]
            
            if p2_start_rssi and p2_end_rssi:
                p2_gain = (sum(p2_end_rssi)/len(p2_end_rssi)) - (sum(p2_start_rssi)/len(p2_start_rssi))
            else:
                p2_gain = phase2_readings[-1]['rssi'] - phase2_readings[0]['rssi']
            
            # Position Score: How much does tag prefer LEFT vs RIGHT movement?
            # Positive = tag on LEFT (gains RSSI when going LEFT)
            # Negative = tag on RIGHT (gains RSSI when going RIGHT)
            position_score = p1_gain - p2_gain
            
            # Average RSSI for reference
            all_rssi = [r['rssi'] for r in phase1_readings + phase2_readings]
            avg_rssi = sum(all_rssi) / len(all_rssi)
            
            tag_analysis[epc] = {
                'position_score': position_score,
                'phase1_gain': p1_gain,  # RSSI gain when going LEFT
                'phase2_gain': p2_gain,  # RSSI gain when going RIGHT
                'avg_rssi': avg_rssi,
                'suffix': epc[-4:],
                'total_readings': len(phase1_readings) + len(phase2_readings)
            }
        
        # Sort by position score
        # IMPORTANT: Account for port mode
        # P1-P4: Phase1 (+angles) = LEFT movement
        #        Positive position_score = tag on LEFT
        # P2-P3: Phase1 (+angles) = RIGHT movement (inverted)
        
        if self.mapper_port_mode == "P1-P4":
            # Positive score = LEFT, so highest = leftmost
            sorted_tags = sorted(tag_analysis.items(), key=lambda x: -x[1]['position_score'])
        else:
            # P2-P3: +angles = RIGHT, so we need to invert
            # Positive score = RIGHT, so lowest = leftmost
            sorted_tags = sorted(tag_analysis.items(), key=lambda x: x[1]['position_score'])
        
        # Display results
        self.txt_mapper_peaks.delete(1.0, tk.END)
        self.txt_mapper_peaks.insert(tk.END, "═" * 60 + "\n")
        self.txt_mapper_peaks.insert(tk.END, "   İKİ YÖNLÜ HAREKET ANALİZİ\n")
        self.txt_mapper_peaks.insert(tk.END, "═" * 60 + "\n")
        self.txt_mapper_peaks.insert(tk.END, f"Port: {self.mapper_port_mode}\n")
        self.txt_mapper_peaks.insert(tk.END, f"Faz 1 (0→+20): Beam SOLA\n")
        self.txt_mapper_peaks.insert(tk.END, f"Faz 2 (0→-20): Beam SAĞA\n")
        self.txt_mapper_peaks.insert(tk.END, "-" * 60 + "\n")
        self.txt_mapper_peaks.insert(tk.END, f"{'#':>2} | {'Tag':>6} | {'Poz.Skor':>9} | {'←Kazanç':>8} | {'Kazanç→':>8}\n")
        self.txt_mapper_peaks.insert(tk.END, "-" * 60 + "\n")
        
        self.mapper_sorted_tags = sorted_tags  # Save for apply
        
        for i, (epc, info) in enumerate(sorted_tags[:8]):
            suffix = info['suffix']
            pos_score = info['position_score']
            p1_gain = info['phase1_gain']
            p2_gain = info['phase2_gain']
            
            # Determine position indicator
            if pos_score > 4:
                pos = "◀◀ SOL"
            elif pos_score > 1.5:
                pos = " ◀ SOL"
            elif pos_score < -4:
                pos = "SAĞ ▶▶"
            elif pos_score < -1.5:
                pos = "SAĞ ▶ "
            else:
                pos = " ORTA "
            
            self.txt_mapper_peaks.insert(tk.END, f"{i+1:>2} | {suffix:>6} | {pos_score:>+9.2f} | {p1_gain:>+8.2f} | {p2_gain:>+8.2f} | {pos}\n")
            
            # Update result boxes
            if i < len(self.mapper_result_labels):
                self.mapper_result_labels[i]['tag'].config(text=suffix, bg="#c6efce")
                self.mapper_result_labels[i]['angle'].config(text=pos.strip())
        
        self.txt_mapper_peaks.insert(tk.END, "-" * 60 + "\n")
        self.txt_mapper_peaks.insert(tk.END, f"Toplam: {len(sorted_tags)} tag tespit edildi\n")
        self.lbl_mapper_status.config(text=f"Tamamlandı! {len(sorted_tags)} tag bulundu")
        
        # Reset to 0 degrees
        data = self.steer_lut.get_data("H-Plane", 0)
        if data:
            if self.mapper_port_mode == "P1-P4":
                self.set_volts(data['V_P3'], data['V_P4'])
            else:
                self.set_volts(data['V_P2'], data['V_P3'])
    
    def apply_mapper_results(self):
        """Apply mapper results as T1-T8 configuration"""
        if not hasattr(self, 'mapper_sorted_tags') or not self.mapper_sorted_tags:
            messagebox.showwarning("Veri Yok", "Önce tarama yapın!")
            return
        
        # Create new config
        new_config = {}
        tag_entries = [self.ent_tag_1, self.ent_tag_2, self.ent_tag_3, self.ent_tag_4,
                       self.ent_tag_5, self.ent_tag_6, self.ent_tag_7, self.ent_tag_8]
        
        for i, (epc, info) in enumerate(self.mapper_sorted_tags[:8]):
            tag_name = f"T{i+1}"
            suffix = info['suffix']
            new_config[tag_name] = suffix
            
            # Update entry fields
            if i < len(tag_entries):
                tag_entries[i].delete(0, tk.END)
                tag_entries[i].insert(0, suffix)
        
        self.beam_tag_positions = new_config
        
        # Save to file
        try:
            import json
            with open('beam_tags_config.json', 'w') as f:
                json.dump(new_config, f, indent=2)
            messagebox.showinfo("Kaydedildi", f"Tag sıralaması güncellendi ve kaydedildi!\n\nT1 (Sol): {new_config.get('T1', '?')}\n...\nT8 (Sağ): {new_config.get('T8', '?')}")
        except Exception as e:
            messagebox.showerror("Hata", f"Kaydetme hatası: {e}")

    def apply_pair_preset(self, *args):
        mode = self.var_pair_mode.get()
        if mode == "P1-P4":
            self.src_ch1.current(0) # P1
            self.src_ch2.current(3) # P4
        elif mode == "P2-P3":
            self.src_ch1.current(1) # P2
            self.src_ch2.current(2) # P3
        self.update_steer_lut()

    def start_auto_sweep(self):
        """Start automatic left-right sweep mode"""
        if self.sweep_running:
            return
        
        if not self.serial or not self.serial.is_open:
            messagebox.showwarning("MCU Not Connected", "Please connect MCU first")
            return
        
        self.sweep_running = True
        self.sweep_angle_index = 0
        self.btn_start_sweep.config(state=tk.DISABLED)
        self.btn_stop_sweep.config(state=tk.NORMAL)
        self.run_auto_sweep_cycle()
    
    def run_auto_sweep_cycle(self):
        """Execute one cycle of the auto sweep"""
        if not self.sweep_running:
            return
        
        try:
            try:
                dwell_time = float(self.ent_sweep_dwell.get())
            except:
                dwell_time = 2.0

            try:
                step_val = float(self.ent_sweep_step.get())
                if step_val <= 0.1: step_val = 5.0
            except:
                step_val = 5.0
            
            sweep_type = self.var_sweep_type.get()
            
            if sweep_type == "Left-Right":
                # Full range: -30 to +30 with adjustable steps
                # use numpy for float range, or simple loop
                start, end = -30, 30
                angles = []
                curr = start
                while curr <= end + 0.01:
                    angles.append(curr)
                    curr += step_val
            else:
                current_angle = self.scale_steer_angle.get()
                angles = [current_angle - 15, current_angle, current_angle + 15, current_angle]
            
            if self.sweep_angle_index >= len(angles):
                self.lbl_sweep_status.config(text="Sweep Complete!", foreground="green")
                self.sweep_running = False
                self.btn_start_sweep.config(state=tk.NORMAL)
                self.btn_stop_sweep.config(state=tk.DISABLED)
                return

            angle = angles[self.sweep_angle_index]
            self.scale_steer_angle.set(angle)
            self.update_steer_lut()
            
            # Get voltages and LUT data
            try:
                v1_str = self.lbl_ch1_stat.cget('text').replace(' V', '')
                v2_str = self.lbl_ch2_stat.cget('text').replace(' V', '')
                v1 = float(v1_str)
                v2 = float(v2_str)
                self.set_volts(v1, v2)
            except:
                v1, v2 = 0, 0
            
            # Get Gain and SLL from LUT
            mode = self.var_steer_mode.get()
            lut_data = self.steer_lut.get_data(mode, angle) if hasattr(self, 'steer_lut') else None
            gain = lut_data['Est_Gain_dBi'] if lut_data else 0
            sll = lut_data['Est_SLL_dB'] if lut_data else 0
            
            self.lbl_sweep_status.config(text=f"Sweeping: {angle:.1f}° (Step {self.sweep_angle_index + 1}/{len(angles)})", foreground="blue")
            
            # Save detailed sweep snapshot
            self.save_sweep_snapshot(angle, v1, v2, gain, sll, mode, self.sweep_angle_index + 1, len(angles))
            
            self.sweep_angle_index += 1
            
            self.root.after(int(dwell_time * 1000), self.run_auto_sweep_cycle)

        except Exception as e:
            print(f"Sweep Error: {e}")
            import traceback
            traceback.print_exc()
            self.stop_auto_sweep()
            messagebox.showerror("Sweep Error", f"Auto Sweep stopped due to error:\n{e}")
    
    def save_sweep_snapshot(self, angle, v1, v2, gain, sll, mode, step_num, total_steps):
        """Save detailed sweep data with LLM-ready format"""
        data = self.reader.get_all_data() if hasattr(self, 'reader') else {}
        current_time = time.time()
        timeout = 2.0
        tag_order = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']
        
        # Get RSSI filter settings
        rssi_filter_enabled = self.var_rssi_filter.get() if hasattr(self, 'var_rssi_filter') else False
        try:
            rssi_threshold = float(self.ent_rssi_threshold.get()) if hasattr(self, 'ent_rssi_threshold') else -70
        except:
            rssi_threshold = -70
        
        # Collect tag data with RSSI filtering
        snapshot_data = {}
        for tag_name in tag_order:
            tag_suffix = self.beam_tag_positions.get(tag_name, '')
            if not tag_suffix:
                continue
            for epc, d in data.items():
                if epc.endswith(tag_suffix) or tag_suffix.endswith(epc[-4:]):
                    if (current_time - d['seen_time']) < timeout:
                        rssi = d['rssi']
                        # Apply RSSI filter if enabled
                        if rssi_filter_enabled and rssi < rssi_threshold:
                            continue  # Skip this tag - below threshold
                        snapshot_data[tag_name] = {'rssi': rssi, 'phase': d['phase_deg']}
                    break
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Include milliseconds
        
        # Create unique filename for this sweep session
        if not hasattr(self, 'sweep_session_file'):
            session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.sweep_session_file = f"auto_sweep_{session_ts}.txt"
            self.sweep_csv_file = f"auto_sweep_{session_ts}.csv"
            # Write CSV header with timestamp
            with open(self.sweep_csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                header = ['Timestamp', 'Step', 'Angle', 'CH1_V', 'CH2_V', 'Est_Gain', 'Est_SLL']
                for tag in tag_order:
                    header.extend([f'{tag}_RSSI', f'{tag}_Phase'])
                if rssi_filter_enabled:
                    header.append('RSSI_Filter')
                writer.writerow(header)
        
        # Write CSV row
        with open(self.sweep_csv_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            row = [timestamp, step_num, f"{angle:.0f}", f"{v1:.3f}", f"{v2:.3f}", f"{gain:.2f}", f"{sll:.2f}"]
            for tag in tag_order:
                if tag in snapshot_data:
                    row.extend([f"{snapshot_data[tag]['rssi']:.1f}", f"{snapshot_data[tag]['phase']:.0f}"])
                else:
                    row.extend(['--', '--'])
            if rssi_filter_enabled:
                row.append(f">{rssi_threshold}")
            writer.writerow(row)
        
        # Find beam direction
        rssi_values = [(tag, snapshot_data[tag]['rssi']) for tag in tag_order if tag in snapshot_data]
        if rssi_values:
            best_tag = max(rssi_values, key=lambda x: x[1])[0]
            max_rssi = max(rssi_values, key=lambda x: x[1])[1]
            min_rssi = min(rssi_values, key=lambda x: x[1])[1]
            rssi_range = max_rssi - min_rssi
        else:
            best_tag = "N/A"
            max_rssi = min_rssi = rssi_range = 0
        
        if 'Left' in best_tag:
            direction = "← LEFT"
        elif 'Right' in best_tag:
            direction = "RIGHT →"
        elif 'Middle' in best_tag:
            direction = "● CENTER"
        else:
            direction = "?"
        
        # Create detailed report entry
        report = []
        report.append("")
        report.append("=" * 80)
        report.append(f"STEP {step_num}/{total_steps} | ANGLE: {angle:.0f}° | {timestamp}")
        report.append("=" * 80)
        report.append(f"Mode: {mode} | Voltages: CH1={v1:.3f}V, CH2={v2:.3f}V")
        report.append(f"LUT Predicted: Gain={gain:.2f} dBi | SLL={sll:.2f} dB")
        report.append("")
        report.append("MEASURED RSSI DISTRIBUTION:")
        report.append("-" * 60)
        
        # Create bar chart
        for tag in tag_order:
            if tag in snapshot_data:
                rssi = snapshot_data[tag]['rssi']
                phase = snapshot_data[tag]['phase']
                if rssi_range > 0:
                    rel_strength = ((rssi - min_rssi) / rssi_range) * 100
                else:
                    rel_strength = 50
                bar_length = int(rel_strength / 2.5)
                
                indicator = "★" if tag == best_tag else ("●" if rssi >= max_rssi - 3 else "○")
                bar = "█" * bar_length
                report.append(f"{tag:10s} {indicator} [{bar:<40s}] {rssi:6.1f} dBm (∠{phase:3.0f}°)")
            else:
                report.append(f"{tag:10s}   [{'':40s}]   -- dBm")
        
        report.append("")
        report.append(f">>> BEAM DIRECTION: {direction} (Strongest: {best_tag})")
        report.append(f">>> Signal Range: {rssi_range:.1f} dB")
        
        # LLM Analysis section
        report.append("")
        report.append("-" * 80)
        report.append(f"LLM ANALYSIS - Step {step_num}:")
        report.append("-" * 80)
        report.append(f"At target angle {angle}° with {mode} scanning:")
        report.append(f"  - Applied: CH1={v1:.3f}V, CH2={v2:.3f}V")
        report.append(f"  - Expected: Gain={gain:.2f} dBi, SLL={sll:.2f} dB")
        report.append(f"  - Observed beam at: {direction}")
        
        for tag in tag_order:
            if tag in snapshot_data:
                report.append(f"    {tag}: {snapshot_data[tag]['rssi']:.1f} dBm")
        
        
        # Append to session file
        with open(self.sweep_session_file, 'a', encoding='utf-8') as f:
            f.write('\n'.join(report) + '\n')
    
    def stop_auto_sweep(self):
        """Stop the auto sweep"""
        self.sweep_running = False
        self.sweep_angle_index = 0
        self.btn_start_sweep.config(state=tk.NORMAL)
        self.btn_stop_sweep.config(state=tk.DISABLED)
        self.lbl_sweep_status.config(text="Stopped", foreground="red")
        self.scale_steer_angle.set(0)
        self.update_steer_lut()
        # Reset session files for next sweep
        if hasattr(self, 'sweep_session_file'):
            delattr(self, 'sweep_session_file')
        if hasattr(self, 'sweep_csv_file'):
            delattr(self, 'sweep_csv_file')

    def update_steer_lut(self, *args):
        # 1. Yükleme Kontrolü
        if not hasattr(self, 'steer_lut') or not self.steer_lut.loaded: 
            return
        
        # 2. Arayüzden Değerleri Al
        mode = self.var_steer_mode.get()
        angle = self.scale_steer_angle.get()
        
        # 3. LUT Verisini Çek
        data = self.steer_lut.get_data(mode, angle)
        if not data: 
            return
        
        # 4. Kazanç ve SLL Güncelle
        self.lbl_steer_gain.config(text=f"{data['Est_Gain_dBi']:.2f} dBi")
        self.lbl_steer_sll.config(text=f"{data['Est_SLL_dB']:.2f} dB")
        
        # 5. Voltajları Al (Yeni CSV yapısı V_CH1 ve V_CH2 kullanıyor)
        v1 = data.get('V_CH1', 0.0)
        v2 = data.get('V_CH2', 0.0)

        # 6. Sağ alttaki "CH1 OUT" ve "CH2 OUT" etiketlerini güncelle
        if hasattr(self, 'lbl_ch1_stat'):
            self.lbl_ch1_stat.config(text=f"{v1:.3f} V")
        if hasattr(self, 'lbl_ch2_stat'):
            self.lbl_ch2_stat.config(text=f"{v2:.3f} V")
        
        # 7. "Port Voltages" Kısmını Hesapla ve Göster
        # Burası CH1 ve CH2'nin hangi fiziksel portlara gittiğini simüle eder.
        if hasattr(self, 'lbl_v_ports') and len(self.lbl_v_ports) == 4:
            # Önce hepsini sıfırla
            p_vals = [0.0, 0.0, 0.0, 0.0]
            
            # Mod'a göre dağıtım mantığı (Mapping)
            if mode == 'H-Plane':
                # H-Plane (Yatay) genelde 3. ve 4. portları kullanır
                # CH1 -> Port 3
                # CH2 -> Port 4
                p_vals[2] = v1  # Port 3 (Liste indeksi 2)
                p_vals[3] = v2  # Port 4 (Liste indeksi 3)
            
            elif mode == 'E-Plane':
                # E-Plane (Dikey) genelde 2. ve 3. portları kullanır
                # CH1 -> Port 2
                # CH2 -> Port 3
                p_vals[1] = v1  # Port 2
                p_vals[2] = v2  # Port 3
            
            # Etiketleri Döngüyle Güncelle
            for i, val in enumerate(p_vals):
                if val > 0.001:
                    # Aktif portları Mavi ve Kalın yap
                    self.lbl_v_ports[i].config(text=f"{val:.3f} V", foreground="blue", font=("Consolas", 10, "bold"))
                else:
                    # Pasif portları Siyah ve Normal yap
                    self.lbl_v_ports[i].config(text="0.000 V", foreground="black", font=("Consolas", 10))

    def set_volts(self, v1, v2=None):
        if v2 is None: v2 = v1
        
        # Safety Cap
        v1 = max(0.0, min(8.5, float(v1)))
        v2 = max(0.0, min(8.5, float(v2)))
        
        # MCU CONNECTION CHECK
        if not self.serial or not self.serial.is_open:
            messagebox.showwarning(
                "MCU Not Connected", 
                f"Cannot apply voltages (CH1={v1:.2f}V, CH2={v2:.2f}V)\n\n"
                "Please connect MCU first from Hardware Setup panel."
            )
            return  # Don't proceed without MCU connection
        
        # Hardware Update
        cmd = f"SET1:{v1:.2f}\nSET2:{v2:.2f}\n"
        try:
            self.serial.write(cmd.encode())
            self.serial.flush()
            if hasattr(self, 'lbl_serial_cmd'):
                display_cmd = cmd.strip().replace('\n', ' ')
                self.lbl_serial_cmd.config(text=f"Last Command: {display_cmd}")
            # print(f"Serial Send: {cmd.strip()}") 
        except Exception as e:
            print(f"Serial Write Error: {e}")
            messagebox.showerror("Serial Error", f"Failed to send command: {e}")
            
        # --- UI SYNCHRONIZATION ---
        if hasattr(self, 'scale_az'):
             if abs(self.scale_az.get() - v1) > 0.01: 
                 self.scale_az.set(v1)
             if abs(v1 - v2) < 0.01:
                 self.lbl_volt.config(text=f"Applied: {v1:.2f}V (Synced)")
             else:
                 self.lbl_volt.config(text=f"Applied: CH1={v1:.2f}V, CH2={v2:.2f}V")
                 
        if hasattr(self, 'scale_p1'):
             if abs(self.scale_p1.get() - v1) > 0.01: 
                 self.scale_p1.set(v1)
             self.lbl_p1_val.config(text=f"{v1:.2f} V")
                 
        if hasattr(self, 'scale_p4'):
             if abs(self.scale_p4.get() - v2) > 0.01: 
                 self.scale_p4.set(v2)
             self.lbl_p4_val.config(text=f"{v2:.2f} V")

        if hasattr(self, 'scale_az_inv'):
             if abs(self.scale_az_inv.get() - v1) > 0.01: 
                 self.scale_az_inv.set(v1)
                
        # Update Manual Correction Entries if they were not the source?
        # Actually no, entries are offsets. Use them to display final V? 
        # No, entries are inputs.
        
        # Update Labels in Manual Control (Experiments)
        # Already done above (lbl_p1_val)

    def run_plot(self):
        angles = range(0, 85, 5); rssis = [] # 0 to 8.5V scan basically if re-mapped
        self.ax.clear(); self.ax.set_title("Scanning..."); self.canvas.draw(); self.root.update()
        # Scan Logic Placeholder using Voltage for now
        # ...


    def toggle_log(self):
        if not self.logging_active:
            fname = filedialog.asksaveasfilename(defaultextension=".csv")
            if not fname: return
            self.csv_file = open(fname, 'w', newline=''); self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(["Time", "Angle", "Voltage", "RSSI", "Phase"])
            self.logging_active = True; self.lbl_log.config(text="RECORDING", foreground="red"); self.btn_log.config(text="STOP")
        else:
            self.logging_active = False; self.csv_file.close(); self.lbl_log.config(text="Saved", foreground="green"); self.btn_log.config(text="START")

    def log_data(self, a, v, r, p):
        if self.csv_writer: self.csv_writer.writerow([datetime.now().strftime("%H:%M:%S.%f"), a, v, r, p])

    # =========================================================================
    # ML DATA COLLECTION TAB
    # =========================================================================
    def setup_ml_tab(self):
        f = ttk.Frame(self.tab_ml, padding=10)
        f.pack(fill=tk.BOTH, expand=True)

        # --- LEFT PANEL: CONFIGURATION ---
        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))

        # 1. Environment & Hardware
        env_fr = ttk.LabelFrame(left, text="1. Environment & Config", padding=10)
        env_fr.pack(fill=tk.X, pady=(0, 10))

        # Power (Read-only/Auto)
        ttk.Label(env_fr, text="TX Power (dBm):").grid(row=0, column=0, sticky=tk.E)
        self.lbl_ml_pwr = ttk.Label(env_fr, text="--", font=("Arial", 10, "bold"), foreground="blue")
        self.lbl_ml_pwr.grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Button(env_fr, text="🔄 Refresh", command=self.update_ml_pwr_display).grid(row=0, column=2, padx=5)

        # Distance
        ttk.Label(env_fr, text="Distance (cm):").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.ent_ml_dist = ttk.Entry(env_fr, width=8); self.ent_ml_dist.insert(0, "100")
        self.ent_ml_dist.grid(row=1, column=1, sticky=tk.W, padx=5)

        # Marble Separation
        ttk.Label(env_fr, text="Marble Sep (cm):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.ent_ml_sep = ttk.Entry(env_fr, width=8); self.ent_ml_sep.insert(0, "10")
        self.ent_ml_sep.grid(row=2, column=1, sticky=tk.W, padx=5)

        # Tag Orientation
        ttk.Label(env_fr, text="Tag Orientation:").grid(row=3, column=0, sticky=tk.E, pady=5)
        self.var_ml_orient = tk.StringVar(value="Vertical")
        orient_fr = ttk.Frame(env_fr)
        orient_fr.grid(row=3, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(orient_fr, text="Vertical", variable=self.var_ml_orient, value="Vertical").pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(orient_fr, text="Horizontal", variable=self.var_ml_orient, value="Horizontal").pack(side=tk.LEFT, padx=2)

        # Port Pair
        ttk.Label(env_fr, text="Port Pair:").grid(row=4, column=0, sticky=tk.E, pady=5)
        self.var_ml_pair = tk.StringVar(value="P1-P4")
        pp_fr = ttk.Frame(env_fr)
        pp_fr.grid(row=4, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(pp_fr, text="P1 & P4 (Left)", variable=self.var_ml_pair, value="P1-P4").pack(anchor=tk.W)
        ttk.Radiobutton(pp_fr, text="P2 & P3 (Right)", variable=self.var_ml_pair, value="P2-P3").pack(anchor=tk.W)

        # 2. Sweep Parameters
        param_fr = ttk.LabelFrame(left, text="2. Sweep Parameters", padding=10)
        param_fr.pack(fill=tk.X, pady=(0, 10))

        # Range
        ttk.Label(param_fr, text="Range (°):").grid(row=0, column=0, sticky=tk.E)
        r_fr = ttk.Frame(param_fr)
        r_fr.grid(row=0, column=1, columnspan=2, sticky=tk.W)
        self.ent_ml_start = ttk.Entry(r_fr, width=5); self.ent_ml_start.insert(0, "-30")
        self.ent_ml_start.pack(side=tk.LEFT)
        ttk.Label(r_fr, text=" to ").pack(side=tk.LEFT)
        self.ent_ml_end = ttk.Entry(r_fr, width=5); self.ent_ml_end.insert(0, "30")
        self.ent_ml_end.pack(side=tk.LEFT)

        # Step Size
        ttk.Label(param_fr, text="Step Size (°):").grid(row=1, column=0, sticky=tk.E, pady=5)
        self.ent_ml_step = ttk.Entry(param_fr, width=6); self.ent_ml_step.insert(0, "1.0")
        self.ent_ml_step.grid(row=1, column=1, sticky=tk.W, padx=5)
        
        # Dwell
        ttk.Label(param_fr, text="Dwell (s):").grid(row=2, column=0, sticky=tk.E, pady=5)
        self.ent_ml_dwell = ttk.Entry(param_fr, width=6); self.ent_ml_dwell.insert(0, "0.5")
        self.ent_ml_dwell.grid(row=2, column=1, sticky=tk.W, padx=5)

        # Scan mode (Added to match Calibration Sweep)
        ttk.Label(param_fr, text="Scan Mode:").grid(row=3, column=0, sticky=tk.E, pady=(10, 5))
        self.var_ml_mode = tk.StringVar(value="E-Plane")
        mode_fr = ttk.Frame(param_fr)
        mode_fr.grid(row=3, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(mode_fr, text="H-Plane", variable=self.var_ml_mode, value="H-Plane").pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(mode_fr, text="E-Plane", variable=self.var_ml_mode, value="E-Plane").pack(side=tk.LEFT, padx=2)

        # 3. Controls
        ctrl_fr = ttk.Frame(left, padding=10)
        ctrl_fr.pack(fill=tk.X)
        self.btn_ml_start = ttk.Button(ctrl_fr, text="▶ START ML CAPTURE", command=self.start_ml_sweep)
        self.btn_ml_start.pack(fill=tk.X, pady=5)
        
        self.btn_ml_stop = ttk.Button(ctrl_fr, text="■ STOP", command=self.stop_ml_sweep, state=tk.DISABLED)
        self.btn_ml_stop.pack(fill=tk.X, pady=5)
        
        # --- RIGHT PANEL: FEEDBACK ---
        right = ttk.Frame(f)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.lbl_ml_status = ttk.Label(right, text="Ready to create dataset", font=("Arial", 12))
        self.lbl_ml_status.pack(pady=10)
        
        self.pb_ml = ttk.Progressbar(right, length=400, mode='determinate')
        self.pb_ml.pack(fill=tk.X, pady=10)
        
        log_lb = ttk.LabelFrame(right, text="Log Output", padding=5)
        log_lb.pack(fill=tk.BOTH, expand=True)
        self.txt_ml_log = tk.Text(log_lb, height=20, font=("Consolas", 9))
        self.txt_ml_log.pack(fill=tk.BOTH, expand=True)

        self.ml_running = False

    def update_ml_pwr_display(self):
        """Update power label from main hardware input"""
        pwr = self.ent_pwr.get()
        self.lbl_ml_pwr.config(text=f"{pwr} dBm")
        return pwr

    def start_ml_sweep(self):
        if self.ml_running: return
        
        # 0. Check MCU Connection
        if not hasattr(self, 'serial') or not self.serial or not self.serial.is_open:
            messagebox.showerror("Connection Error", "MCU is not connected!\nPlease connect the hardware first.")
            return
        
        # Update Power
        pwr_str = self.update_ml_pwr_display()
        
        try:
            # Parse params
            dist = float(self.ent_ml_dist.get())
            step_size = float(self.ent_ml_step.get())
            start_ang = float(self.ent_ml_start.get())
            end_ang = float(self.ent_ml_end.get())
            self.ml_dwell = float(self.ent_ml_dwell.get())
            
            # Generate angle list
            self.ml_angles = []
            curr = start_ang
            while curr <= end_ang + 0.001:
                self.ml_angles.append(curr)
                curr += step_size
            
            if not self.ml_angles:
                messagebox.showerror("Error", "No angles generated. Check range/step.")
                return

        except ValueError as e:
            messagebox.showerror("Input Error", f"Invalid number format: {e}")
            return

        # Prepare Folders
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Descriptive Folder Name
        sep = self.ent_ml_sep.get()
        mode = self.var_ml_mode.get()
        orient = self.var_ml_orient.get()
        # Clean power string (remove dot if needed for cleaner path)
        p_clean = pwr_str.replace('.', 'd') 
        
        folder_name = f"ML_Dataset_{ts}_D{int(float(dist))}cm_S{sep}cm_{mode}_{orient}_{p_clean}dBm"
        # Sanitize folder name (replace spaces with _, remove invalid chars if any)
        folder_name = folder_name.replace(" ", "_")
        
        self.ml_dir = folder_name
        try:
            os.makedirs(self.ml_dir, exist_ok=True)
            self.ml_steps_dir = os.path.join(self.ml_dir, "Individual_Steps")
            os.makedirs(self.ml_steps_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("FS Error", f"Cannot create folders: {e}")
            return

        # Main CSV Header
        self.ml_master_file = os.path.join(self.ml_dir, "Master_Dataset.csv")
        header = ['Timestamp', 'Input_Power_dBm', 'Distance_cm', 'Sep_Dist_cm', 'Tag_Orientation', 'Port_Config', 'Scan_Mode', 
                  'Angle_Deg', 'V_CH1_Applied', 'V_CH2_Applied']
        # Add T1-T8 fields
        for t in ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']:
            header.extend([f'{t}_RSSI', f'{t}_Phase', f'{t}_Doppler'])
        
        try:
            with open(self.ml_master_file, 'w', newline='', encoding='utf-8') as f:
                # Metadata Header
                f.write(f"# Angle Range: {start_ang} to {end_ang}\n")
                f.write(f"# Angle Step: {step_size}\n")
                f.write(f"# Input Power: {pwr_str}\n")
                f.write(f"# Dwell: {self.ml_dwell}\n")
                f.write(f"# Scan Mode: {self.var_ml_mode.get()}\n")
                f.write(f"# Tag Orientation: {self.var_ml_orient.get()}\n")
                csv.writer(f).writerow(header)
        except Exception as e:
            messagebox.showerror("File Error", str(e)); return

        # UI State
        self.ml_running = True
        self.ml_index = 0
        self.btn_ml_start.config(state=tk.DISABLED)
        self.btn_ml_stop.config(state=tk.NORMAL)
        self.txt_ml_log.delete(1.0, tk.END)
        self.txt_ml_log.insert(tk.END, f"Started ML Collection.\nSaving to: {self.ml_dir}\n")
        
        self.run_ml_step()

    def run_ml_step(self):
        if not self.ml_running: return
        
        if self.ml_index >= len(self.ml_angles):
            self.finish_ml_sweep()
            return
            
        try:
            angle = self.ml_angles[self.ml_index]
            
            # 1. Get Voltages
            # Use user-selected Scan Mode (same as Calibration Sweep)
            mode = self.var_ml_mode.get()
            pair_mode = self.var_ml_pair.get()
            orient = self.var_ml_orient.get()
            v1, v2 = self.steer_lut.get_active_voltages(mode, angle)
            
            # Log what we are applying
            self.txt_ml_log.insert(tk.END, f"Step {self.ml_index}: {angle:.1f}° -> {v1:.2f}V / {v2:.2f}V\n")
            self.txt_ml_log.see(tk.END)
            
            # 2. Apply
            self.set_volts(v1, v2)
            
            # 3. Wait Dwell
            self.root.after(int(self.ml_dwell * 1000), lambda: self.ml_read_and_log(angle, v1, v2, pair_mode, mode, orient))
            
            # Update Status immediately
            self.lbl_ml_status.config(text=f"Setting: {angle:.1f}° ({pair_mode})")
            self.pb_ml['value'] = (self.ml_index / len(self.ml_angles)) * 100
            
        except Exception as e:
            print(f"ML Step Error: {e}")
            self.stop_ml_sweep()

    def ml_read_and_log(self, angle, v1, v2, pair_mode, mode, orient):
        if not self.ml_running: return
        
        try:
            # 4. Read Data
            data = self.reader.get_all_data()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            
            row = [ts, self.lbl_ml_pwr.cget("text").replace(" dBm", ""), 
                   self.ent_ml_dist.get(), self.ent_ml_sep.get(), orient, pair_mode, mode, 
                   f"{angle:.2f}", f"{v1:.3f}", f"{v2:.3f}"]
            
            current_step_rows = []
            step_header = ['Timestamp', 'EPC', 'TagID', 'RSSI', 'Phase', 'Doppler']
            
            # Re-read timeout 
            limit_time = time.time() - 2.0
            
            # Process T1-T8
            for t in ['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8']:
                suffix = self.beam_tag_positions.get(t, "XXXX")
                
                # Find matching EPC
                val_rssi, val_phase, val_dopp = '--', '--', '--'
                
                for epc, d in data.items():
                    if (epc.endswith(suffix) or suffix in epc) and d['seen_time'] > limit_time:
                        val_rssi = f"{d['rssi']:.1f}"
                        val_phase = f"{d['phase_deg']:.1f}"
                        val_dopp = f"{d['doppler']:.1f}"
                        
                        current_step_rows.append([ts, epc, t, val_rssi, val_phase, val_dopp])
                        break # Only one match per tag ID
                
                row.extend([val_rssi, val_phase, val_dopp])
                
            # 5. Write Master CSV
            with open(self.ml_master_file, 'a', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow(row)
                
            # 6. Write Step CSV
            step_file = os.path.join(self.ml_steps_dir, f"step_{self.ml_index}_angle_{angle:.1f}.csv")
            with open(step_file, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(step_header)
                w.writerows(current_step_rows)

            self.txt_ml_log.insert(tk.END, f"Captured {angle}° -> CSVs updated.\n")
            self.txt_ml_log.see(tk.END)
            
            # Next loop
            self.ml_index += 1
            self.run_ml_step() # Next step immediately (UI loop handles responsiveness)

        except Exception as e:
            print(f"ML Log Error: {e}")
            import traceback; traceback.print_exc()
            self.stop_ml_sweep()

    def stop_ml_sweep(self):
        self.ml_running = False
        self.btn_ml_start.config(state=tk.NORMAL)
        self.btn_ml_stop.config(state=tk.DISABLED)
        self.lbl_ml_status.config(text="Stopped.")

    def finish_ml_sweep(self):
        self.ml_running = False
        self.btn_ml_start.config(state=tk.NORMAL)
        self.btn_ml_stop.config(state=tk.DISABLED)
        self.lbl_ml_status.config(text="Dataset Collection Complete!")
        self.pb_ml['value'] = 100
        messagebox.showinfo("Done", f"ML Dataset collected in:\n{self.ml_dir}")


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterGUI(root)
    root.mainloop()