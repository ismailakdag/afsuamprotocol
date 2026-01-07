"""
Protocol Runner Tab.

Provides UI for running AFSUAM protocols.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
from typing import Optional, Callable


class ProtocolRunnerTab(ttk.Frame):
    """
    Protocol runner tab for executing measurement protocols.
    
    Switches between L-C-R sweep (for Ant1 or Both) and Simple Inventory (for Ant2 only).
    """
    
    def __init__(
        self,
        parent,
        protocol,
        tag_manager,
        simple_inventory_protocol=None,
        csv_exporter=None,
        reader=None,
        hardware_panel=None,
        on_export: Optional[Callable] = None,
        **kwargs
    ):
        """
        Initialize protocol runner tab.
        
        Args:
            parent: Parent widget
            protocol: AFSUAMProtocol instance (L-C-R sweep)
            tag_manager: TagManager instance
            simple_inventory_protocol: SimpleInventoryProtocol instance (for Ant2 only)
            csv_exporter: CSVExporter instance for file output
            reader: RFID reader instance for antenna reconfiguration
            hardware_panel: Hardware panel for reconnection
            on_export: Callback for export action
        """
        super().__init__(parent, padding=10, **kwargs)
        
        self.protocol = protocol
        self.simple_inventory_protocol = simple_inventory_protocol
        self.csv_exporter = csv_exporter
        self.reader = reader
        self.hardware_panel = hardware_panel
        self.tag_manager = tag_manager
        self._on_export = on_export
        
        self._results = []
        self._current_antennas = [1, 2]
        self._antenna_mode = "BOTH"  # Track mode string
        
        self._build_ui()
    
    def set_current_antennas(self, antennas: list):
        """Update current antenna list and update UI accordingly."""
        self._current_antennas = antennas
        self._update_antenna_label()
        self._update_run_button()
    
    def set_antenna_mode(self, mode: str):
        """Set antenna mode string."""
        self._antenna_mode = mode
        self._update_antenna_label()
        self._update_run_button()
    
    def _build_ui(self):
        """Build UI components."""
        # Controls frame
        ctrl = ttk.LabelFrame(self, text="Protocol Controls", padding=10)
        ctrl.pack(fill=tk.X)
        
        # Row 1: Station and ref antenna
        row1 = ttk.Frame(ctrl)
        row1.pack(fill=tk.X, pady=2)
        
        ttk.Label(row1, text="Station:").pack(side=tk.LEFT)
        self.ent_station = ttk.Entry(row1, width=30)
        self.ent_station.insert(0, "AFSUAM Test-Bed")
        self.ent_station.pack(side=tk.LEFT, padx=6)
        
        ttk.Label(row1, text="Ref Antenna:").pack(side=tk.LEFT, padx=(12, 0))
        self.ent_ref = ttk.Entry(row1, width=15)
        self.ent_ref.insert(0, "REF_ANT")
        self.ent_ref.pack(side=tk.LEFT, padx=6)
        
        # Row 2: Dwell, repeats, port config
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
        
        ttk.Label(row2, text="Port Config:").pack(side=tk.LEFT, padx=(12, 0))
        self.cb_pc = ttk.Combobox(row2, values=["0", "1"], width=5, state="readonly")
        self.cb_pc.current(0)
        self.cb_pc.pack(side=tk.LEFT, padx=6)
        
        ttk.Label(row2, text="Beam Steps:").pack(side=tk.LEFT, padx=(12, 0))
        self.spn_steps = ttk.Spinbox(row2, from_=1, to=36, width=4)
        self.spn_steps.set(3)
        self.spn_steps.pack(side=tk.LEFT, padx=6)
        
        # Antenna mode display
        row_ant = ttk.Frame(ctrl)
        row_ant.pack(fill=tk.X, pady=4)
        
        ttk.Label(row_ant, text="📡 Active Antennas:", font=("Arial", 10, "bold")).pack(side=tk.LEFT)
        self.lbl_ant_mode = ttk.Label(
            row_ant,
            text="Ant1 + Ant2",
            font=("Arial", 10, "bold"),
            foreground="#7c3aed"
        )
        self.lbl_ant_mode.pack(side=tk.LEFT, padx=6)
        
        # Buttons
        btn_row = ttk.Frame(ctrl)
        btn_row.pack(fill=tk.X, pady=6)
        
        self.btn_run = ttk.Button(
            btn_row,
            text="Run L-C-R Protocol",
            command=self._run_protocol
        )
        self.btn_run.pack(side=tk.LEFT, padx=2)
        
        ttk.Button(
            btn_row,
            text="Clear Results",
            command=self._clear_results
        ).pack(side=tk.LEFT, padx=8)
        
        ttk.Button(
            btn_row,
            text="Export CSV",
            command=self._export_csv
        ).pack(side=tk.LEFT, padx=2)
        
        # Progress
        self.lbl_progress = ttk.Label(ctrl, text="Ready")
        self.lbl_progress.pack(anchor=tk.W, pady=4)
        
        self.progress = ttk.Progressbar(ctrl, length=400, mode='determinate')
        self.progress.pack(fill=tk.X, pady=2)
        
        # Results table
        self._build_results_table()
    
    def _build_results_table(self):
        """Build union results table."""
        frame = ttk.LabelFrame(self, text="Union Results", padding=10)
        frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        cols = (
            "Station", "RefName", "Repeat", "Config", "Dwell",
            "Ant1 Seen", "Ant2 Seen", "Ant1 EPCs", "Ant2 EPCs",
            "Ant1 Missed", "Ant2 Missed"
        )
        
        self.tree_results = ttk.Treeview(frame, columns=cols, show="headings", height=10)
        
        col_widths = {
            "Station": 150, "RefName": 100, "Repeat": 60, "Config": 60,
            "Dwell": 60, "Ant1 Seen": 80, "Ant2 Seen": 80,
            "Ant1 EPCs": 80, "Ant2 EPCs": 80,
            "Ant1 Missed": 150, "Ant2 Missed": 150
        }
        
        for c in cols:
            self.tree_results.heading(c, text=c)
            self.tree_results.column(c, width=col_widths.get(c, 100), anchor=tk.CENTER)
        
        self.tree_results.column("Ant1 Missed", anchor=tk.W)
        self.tree_results.column("Ant2 Missed", anchor=tk.W)
        
        self.tree_results.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree_results.yview)
        self.tree_results.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _update_antenna_label(self):
        """Update antenna mode label."""
        if self._antenna_mode == "INDIVIDUAL_BOTH":
            text = "Individual Both (Separate Runs)"
            color = "#dc2626"
        elif self._current_antennas == [1]:
            text = "Ant1 Only (L-C-R)"
            color = "#2563eb"
        elif self._current_antennas == [2]:
            text = "Ant2 Only (Inventory)"
            color = "#16a34a"
        else:
            text = "Ant1 + Ant2 (L-C-R)"
            color = "#7c3aed"
        
        self.lbl_ant_mode.config(text=text, foreground=color)
    
    def _update_run_button(self):
        """Update run button text based on antenna mode."""
        if self._antenna_mode == "INDIVIDUAL_BOTH":
            self.btn_run.config(text="Run Individual Both")
        elif self._current_antennas == [2]:
            self.btn_run.config(text="Run Simple Inventory")
        else:
            self.btn_run.config(text="Run L-C-R Protocol")
    
    def _run_protocol(self):
        """Run the appropriate protocol based on antenna mode."""
        station = self.ent_station.get().strip()
        ref = self.ent_ref.get().strip() or "REF_ANT"
        
        try:
            dwell = float(self.ent_dwell.get().strip())
        except ValueError:
            dwell = 3.0
        
        try:
            repeats = int(self.ent_repeats.get().strip())
        except ValueError:
            repeats = 3
        
        try:
            port_config = int(self.cb_pc.get())
        except ValueError:
            port_config = 0
        
        try:
            beam_steps = int(self.spn_steps.get().strip())
        except ValueError:
            beam_steps = 3
        
        self.btn_run.config(state=tk.DISABLED)
        self.progress['value'] = 0
        
        def update_progress(msg: str, fraction: float):
            self.lbl_progress.config(text=msg)
            self.progress['value'] = fraction * 100
            self.update_idletasks()
        
        # Check if Individual Both mode
        if self._antenna_mode == "INDIVIDUAL_BOTH":
            # Start in a separate thread to avoid freezing UI
            thread = threading.Thread(
                target=self._run_individual_both,
                args=(station, ref, dwell, repeats, port_config, update_progress, beam_steps)
            )
            thread.start()
            return
        
        # Use AFSUAM Protocol for all modes
        # Logic in AFSUAMProtocol.run handles Ant2-only (non-steering) case automatically
        protocol_to_run = self.protocol
        protocol_name = "Measurement Protocol"
        
        protocol_to_run.set_progress_callback(update_progress)
        
        def worker():
            try:
                result = protocol_to_run.run(
                    station_name=station,
                    ref_antenna_name=ref,
                    dwell_s=dwell,
                    repeats=repeats,
                    port_config=port_config,
                    active_antennas=self._current_antennas,
                    beam_steps=beam_steps
                )
                
                self._results.append(result)
                if result.success:
                    self.after(0, lambda: self._display_result(result))
                    self.after(0, lambda: self.lbl_progress.config(text=f"{protocol_name} Complete"))
                else:
                    self.after(0, lambda: messagebox.showerror("Protocol Error", result.error_message))
                    self.after(0, lambda: self.lbl_progress.config(text="Failed"))
                
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
                self.after(0, lambda: self.lbl_progress.config(text="Failed"))
            
            finally:
                self.after(0, lambda: self.btn_run.config(state=tk.NORMAL))
        
        threading.Thread(target=worker, daemon=True).start()
    
    def _run_individual_both(self, station, ref, dwell, repeats, port_config, update_progress, beam_steps=3):
        """Run Individual Both mode: Ant1 L-C-R then Ant2 Inventory with auto-export."""
        
        # Get reader IP safely in main thread
        reader_ip = "192.168.1.100"  # Default
        if self.hardware_panel:
            reader_ip = self.hardware_panel.ent_ip.get().strip()
        
        def worker():
            exported_files = []
            subfolder = None
            
            try:
                # Create subfolder for this run: PhasedArray_{RefName}
                if self.csv_exporter:
                    from pathlib import Path
                    date_folder = self.csv_exporter._get_date_folder()
                    subfolder_name = f"PhasedArray_{self.csv_exporter._sanitize_name(ref)}"
                    subfolder = date_folder / subfolder_name
                    subfolder.mkdir(parents=True, exist_ok=True)
                
                import time
                
                # Phase 0: Reconfigure for Ant1 (Phased Array Only)
                self.after(0, lambda: self.lbl_progress.config(text="[0.5/2] Switching to Ant1..."))
                
                if self.reader and self.reader.connected:
                    self.reader.disconnect()
                    time.sleep(0.5)
                    # Connect with Ant1 ONLY
                    self.reader.connect(reader_ip, antennas=[1])
                    time.sleep(0.5)
                
                # Phase 1: Ant1 L-C-R Sweep (Phased Array)
                self.after(0, lambda: self.lbl_progress.config(text="[1/2] Running PhasedArray Sweep..."))
                
                self.protocol.set_progress_callback(
                    lambda msg, frac: update_progress(f"[1/2] {msg}", frac * 0.45)
                )
                
                result_ant1 = self.protocol.run(
                    station_name=station,
                    ref_antenna_name="PhasedArray",
                    dwell_s=dwell,
                    repeats=repeats,
                    port_config=port_config,
                    active_antennas=[1],
                    beam_steps=beam_steps
                )
                
                self._results.append(result_ant1)
                self.after(0, lambda: self._display_result(result_ant1))
                
                # Auto-export Ant1 result
                if self.csv_exporter and subfolder:
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%H%M%S")
                    filepath1 = subfolder / f"PhasedArray_LCR_{timestamp}.csv"
                    # Use export_to_path for absolute path (no output_dir prepend)
                    self.csv_exporter.export_to_path(result_ant1, filepath1)
                    exported_files.append(str(filepath1))
                
                # Phase 2: Reconfigure reader for Ant2
                self.after(0, lambda: self.lbl_progress.config(text="[1.5/2] Reconfiguring for Ant2..."))
                update_progress("[1.5/2] Switching to Ant2...", 0.47)
                
                # Disconnect and reconnect reader with Ant2 only
                if self.reader and self.reader.connected:
                    self.reader.disconnect()
                    time.sleep(0.5)
                    
                    # Reconnect with Ant2 only (using safely captured IP)
                    self.reader.connect(reader_ip, antennas=[2])
                    time.sleep(0.5)
                
                # Phase 3: Ant2 Simple Inventory (Reference Antenna)
                self.after(0, lambda: self.lbl_progress.config(text=f"[2/2] Running {ref} Inventory..."))
                
                # Use AFSUAM Protocol in non-steering mode for Ant2
                self.protocol.set_progress_callback(
                    lambda msg, frac: update_progress(f"[2/2] {msg}", 0.5 + frac * 0.45)
                )
                
                result_ant2 = self.protocol.run(
                    station_name=station,
                    ref_antenna_name=ref,
                    dwell_s=dwell,
                    repeats=repeats,
                    active_antennas=[2],
                    beam_steps=1,   # Forces FIXED mode in AFSUAM
                    port_config=port_config
                )
                
                self._results.append(result_ant2)
                if result_ant2.success:
                    self.after(0, lambda: self._display_result(result_ant2))
                else:
                    self.after(0, lambda: messagebox.showerror("Phase 2 Error", result_ant2.error_message))
                
                # Auto-export Ant2 result
                if self.csv_exporter and subfolder:
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%H%M%S")
                    ref_safe = self.csv_exporter._sanitize_name(ref)
                    filepath2 = subfolder / f"{ref_safe}_Inventory_{timestamp}.csv"
                    # Use export_to_path for absolute path (no output_dir prepend)
                    self.csv_exporter.export_to_path(result_ant2, filepath2)
                    exported_files.append(str(filepath2))
                
                # Restore original antenna configuration
                self.after(0, lambda: self.lbl_progress.config(text="Restoring configuration..."))
                update_progress("Restoring...", 0.98)
                
                if self.reader and self.reader.connected:
                    self.reader.disconnect()
                    time.sleep(0.3)
                    
                    # Reconnect with Both antennas (using safely captured IP)
                    self.reader.connect(reader_ip, antennas=[1, 2])
                
                # Show completion message
                files_msg = "\n".join(exported_files) if exported_files else "No files exported"
                self.after(0, lambda: self.lbl_progress.config(text="Individual Both Complete"))
                self.after(0, lambda: messagebox.showinfo(
                    "Individual Both Complete",
                    f"Saved to: {subfolder}\n\nFiles:\n{files_msg}"
                ))
                
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
                self.after(0, lambda: self.lbl_progress.config(text="Failed"))
            
            finally:
                self.after(0, lambda: self.btn_run.config(state=tk.NORMAL))
        
        threading.Thread(target=worker, daemon=True).start()
    
    def _display_result(self, result):
        """Display protocol result in table."""
        for union in result.union_results:
            self.tree_results.insert("", tk.END, values=(
                result.station_name,
                result.ref_antenna_name,
                union.repeat,
                union.port_config,
                union.dwell_s,
                union.ant1_targets_seen,
                union.ant2_targets_seen,
                union.ant1_unique_epcs,
                union.ant2_unique_epcs,
                "|".join(union.ant1_missed[:3]),
                "|".join(union.ant2_missed[:3])
            ))
    
    def _clear_results(self):
        """Clear all results."""
        self._results = []
        self.tree_results.delete(*self.tree_results.get_children())
        self.lbl_progress.config(text="Cleared")
        self.progress['value'] = 0
    
    def _export_csv(self):
        """Trigger CSV export."""
        if not self._results:
            messagebox.showwarning("Export", "No results to export")
            return
        
        if self._on_export:
            self._on_export(self._results[-1])
        else:
            messagebox.showinfo("Export", "Export handler not configured")
    
    def get_results(self):
        """Get all collected results."""
        return self._results
