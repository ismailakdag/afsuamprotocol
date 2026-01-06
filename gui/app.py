"""
Main Application for AFSUAM Measurement System.

This is the main GUI application that ties together all components.
Features:
- Dark Mode theme toggle
- Keyboard shortcuts for beam control
- Real-time RSSI graph
- Auto-save settings on close
- Status LED indicators
"""

import tkinter as tk
from tkinter import ttk, messagebox
import json
import os

# Local imports
from config.settings import Settings
from core.rfid_reader import RFIDReader
from core.mcu_controller import MCUController
from core.beam_lut import CorrectedBeamLUT
from core.tag_manager import TagManager
from protocols.afsuam import AFSUAMProtocol
from protocols.calibration import CalibrationSweepProtocol
from protocols.beam_check import BeamCheckProtocol
from utils.csv_exporter import CSVExporter
from utils.logging import Logger

from gui.styles import setup_styles, ThemeManager, StatusIndicator
from gui.widgets.hardware_panel import HardwarePanel
from gui.widgets.beam_control import BeamControlPanel
from gui.widgets.status_bar import StatusBar
from gui.widgets.realtime_graph import RealTimeGraph
from gui.tabs.live_monitor import LiveMonitorTab
from gui.tabs.protocol_runner import ProtocolRunnerTab
from gui.tabs.export import ExportTab


class MeasurementApp:
    """
    Main measurement application.
    
    Integrates all components with enhanced features:
    - Dark Mode theme toggle (Ctrl+D)
    - Keyboard shortcuts (L/C/R for beam, Ctrl+Q to quit)
    - Real-time RSSI graph
    - Status LED indicators
    - Auto-save settings on close
    """
    
    SETTINGS_FILE = "app_settings.json"
    
    def __init__(self, root: tk.Tk):
        """
        Initialize the application.
        
        Args:
            root: Tkinter root window
        """
        self.root = root
        self.root.title("AFSUAM Measurement System v2.0")
        self.root.geometry("1700x1000")
        
        # Initialize theme manager
        ThemeManager.init(root)
        
        # Initialize settings
        self.settings = Settings.load_from_file()
        
        # Load app preferences
        self._app_prefs = self._load_app_preferences()
        
        # Initialize core components
        self.lut = CorrectedBeamLUT(self.settings.lut_file)
        self.mcu = MCUController()
        self.reader = RFIDReader() if RFIDReader.is_available() else None
        self.tag_manager = TagManager(self.settings.tag_config_file)
        
        # Initialize utilities
        self.exporter = CSVExporter()
        self.logger = Logger()
        
        # Initialize protocols
        if self.reader:
            self.protocol_afsuam = AFSUAMProtocol(
                reader=self.reader,
                mcu=self.mcu,
                lut=self.lut,
                tag_manager=self.tag_manager
            )
            self.protocol_calib = CalibrationSweepProtocol(
                reader=self.reader,
                mcu=self.mcu,
                lut=self.lut,
                tag_manager=self.tag_manager
            )
            self.protocol_beam_check = BeamCheckProtocol(
                reader=self.reader,
                mcu=self.mcu,
                lut=self.lut,
                tag_manager=self.tag_manager
            )
        else:
            self.protocol_afsuam = None
            self.protocol_calib = None
            self.protocol_beam_check = None
        
        # Apply saved theme
        theme = self._app_prefs.get("theme", "light")
        ThemeManager.set_theme(theme)
        
        # Setup styles
        setup_styles(root, theme)
        
        # Build UI
        self._build_ui()
        
        # Setup keyboard shortcuts
        self._setup_keyboard_shortcuts()
        
        # Start update loop
        self._update_id = None
        self._start_update_loop()
        
        # Bind cleanup
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        
        # Apply last used settings
        self._apply_last_settings()
    
    def _build_ui(self):
        """Build the main UI layout."""
        # Menu bar
        self._build_menu()
        
        # Main container
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # LEFT SIDEBAR
        sidebar = ttk.Frame(main_pane, width=360)
        main_pane.add(sidebar, weight=0)
        
        # Status indicators
        status_frame = ttk.Frame(sidebar)
        status_frame.pack(fill=tk.X, pady=5)
        
        self.led_mcu = StatusIndicator(status_frame)
        self.led_mcu.pack(side=tk.LEFT, padx=4)
        ttk.Label(status_frame, text="MCU").pack(side=tk.LEFT)
        
        self.led_reader = StatusIndicator(status_frame)
        self.led_reader.pack(side=tk.LEFT, padx=(16, 4))
        ttk.Label(status_frame, text="Reader").pack(side=tk.LEFT)
        
        # Theme toggle button
        self.btn_theme = ttk.Button(
            status_frame,
            text="🌙",
            width=3,
            command=self._toggle_theme
        )
        self.btn_theme.pack(side=tk.RIGHT, padx=4)
        
        # Hardware Panel
        self.hardware_panel = HardwarePanel(
            sidebar,
            mcu_controller=self.mcu,
            rfid_reader=self.reader,
            settings=self.settings,
            on_reader_connected=self._on_reader_connected,
            on_reader_disconnected=self._on_reader_disconnected
        )
        self.hardware_panel.pack(fill=tk.X, pady=5)
        
        # Beam Control
        self.beam_control = BeamControlPanel(
            sidebar,
            lut=self.lut,
            mcu_controller=self.mcu,
            on_angle_changed=self._on_angle_changed
        )
        self.beam_control.pack(fill=tk.X, pady=5)
        
        # Quick beam check button
        ttk.Button(
            sidebar,
            text="⚡ Quick Beam Check",
            command=self._run_beam_check
        ).pack(fill=tk.X, pady=5)
        
        # Status Bar
        self.status_bar = StatusBar(sidebar)
        self.status_bar.pack(fill=tk.X, pady=5)
        
        # RIGHT MAIN AREA
        main_area = ttk.Frame(main_pane)
        main_pane.add(main_area, weight=1)
        
        # Notebook with tabs
        self.notebook = ttk.Notebook(main_area)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Live Monitor Tab with Graph
        live_frame = ttk.Frame(self.notebook)
        self.notebook.add(live_frame, text="📡 Live Monitor")
        
        # Graph at top - collapsible with fixed max height
        self._graph_visible = tk.BooleanVar(value=True)
        
        graph_header = ttk.Frame(live_frame)
        graph_header.pack(fill=tk.X, padx=5, pady=2)
        
        ttk.Checkbutton(
            graph_header,
            text="📊 Real-time RSSI Graph",
            variable=self._graph_visible,
            command=self._toggle_graph
        ).pack(side=tk.LEFT)
        
        ttk.Button(
            graph_header,
            text="Clear",
            width=6,
            command=lambda: self.rssi_graph.clear() if hasattr(self, 'rssi_graph') else None
        ).pack(side=tk.RIGHT)
        
        self.graph_frame = ttk.Frame(live_frame)
        self.graph_frame.pack(fill=tk.X, padx=5, pady=2)
        
        theme = ThemeManager.get_current_theme()
        self.rssi_graph = RealTimeGraph(self.graph_frame, dark_mode=(theme == "dark"))
        self.rssi_graph.pack(fill=tk.X, pady=2)
        
        # Scrollable Live Monitor content
        monitor_container = ttk.Frame(live_frame)
        monitor_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.live_monitor = LiveMonitorTab(
            monitor_container,
            reader=self.reader,
            tag_manager=self.tag_manager
        )
        self.live_monitor.pack(fill=tk.BOTH, expand=True)
        
        # Protocol Runner Tab
        self.protocol_runner = ProtocolRunnerTab(
            self.notebook,
            protocol=self.protocol_afsuam,
            tag_manager=self.tag_manager,
            on_export=self._on_protocol_export
        )
        self.notebook.add(self.protocol_runner, text="🔬 AFSUAM Protocol")
        
        # Export Tab
        self.export_tab = ExportTab(
            self.notebook,
            reader=self.reader,
            csv_exporter=self.exporter
        )
        self.notebook.add(self.export_tab, text="📤 Export & Log")
        
        # Keyboard shortcuts hint
        hint_label = ttk.Label(
            self.root,
            text="⌨️ Shortcuts: L/C/R=Beam | Ctrl+D=Dark Mode | Ctrl+Q=Quit",
            font=("Arial", 9),
            style="Status.TLabel"
        )
        hint_label.pack(side=tk.BOTTOM, pady=2)
    
    def _build_menu(self):
        """Build menu bar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Export Snapshot (CSV)", command=lambda: self._quick_export("csv"))
        file_menu.add_command(label="Export Snapshot (JSON)", command=lambda: self._quick_export("json"))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close, accelerator="Ctrl+Q")
        
        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Dark Mode", command=self._toggle_theme, accelerator="Ctrl+D")
        view_menu.add_separator()
        view_menu.add_command(label="Clear Graph", command=lambda: self.rssi_graph.clear())
        
        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Quick Beam Check", command=self._run_beam_check)
        tools_menu.add_command(label="Calibration Sweep", command=self._run_calibration)
    
    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Beam control
        self.root.bind("<l>", lambda e: self.beam_control._set_mode("LEFT"))
        self.root.bind("<L>", lambda e: self.beam_control._set_mode("LEFT"))
        self.root.bind("<c>", lambda e: self.beam_control._set_mode("CENTER"))
        self.root.bind("<C>", lambda e: self.beam_control._set_mode("CENTER"))
        self.root.bind("<r>", lambda e: self.beam_control._set_mode("RIGHT"))
        self.root.bind("<R>", lambda e: self.beam_control._set_mode("RIGHT"))
        
        # App controls
        self.root.bind("<Control-d>", lambda e: self._toggle_theme())
        self.root.bind("<Control-D>", lambda e: self._toggle_theme())
        self.root.bind("<Control-q>", lambda e: self._on_close())
        self.root.bind("<Control-Q>", lambda e: self._on_close())
    
    def _toggle_theme(self):
        """Toggle between light and dark themes."""
        new_theme = ThemeManager.toggle_theme()
        
        # Update graph theme
        if hasattr(self, 'rssi_graph'):
            self.rssi_graph.set_dark_mode(new_theme == "dark")
        
        # Update button icon
        self.btn_theme.config(text="☀️" if new_theme == "dark" else "🌙")
        
        # Update LED backgrounds
        colors = ThemeManager.get_colors()
        self.led_mcu.configure(bg=colors["bg"])
        self.led_reader.configure(bg=colors["bg"])
        
        # Save preference
        self._app_prefs["theme"] = new_theme
        self._save_app_preferences()
    
    def _toggle_graph(self):
        """Toggle graph visibility."""
        if self._graph_visible.get():
            self.graph_frame.pack(fill=tk.X, padx=5, pady=2, after=self.graph_frame.master.winfo_children()[0])
        else:
            self.graph_frame.pack_forget()
    
    def _start_update_loop(self):
        """Start the UI update loop."""
        self._update_ui()
    
    def _update_ui(self):
        """Update UI periodically."""
        try:
            # Update LED indicators
            if self.mcu and self.mcu.is_connected:
                self.led_mcu.set_state("connected")
            else:
                self.led_mcu.set_state("off")
            
            if self.reader and self.reader.connected:
                self.led_reader.set_state("connected")
            else:
                self.led_reader.set_state("off")
            
            # Update live monitor and graph
            if self.reader and self.reader.connected:
                self.live_monitor.update()
                
                # Update graph
                inventory = self.reader.get_all_data()
                self.rssi_graph.update_from_inventory(
                    inventory, 
                    self.tag_manager.suffixes
                )
                self.rssi_graph.refresh()
                
                # Update beam info for export
                pc = self.beam_control.port_config
                angle = self.beam_control.current_angle
                v1, v2 = self.beam_control.get_voltages()
                self.export_tab.set_beam_info(pc, angle, v1, v2)
                
        except Exception as e:
            print(f"Update error: {e}")
        
        # Schedule next update
        self._update_id = self.root.after(500, self._update_ui)
    
    def _on_reader_connected(self):
        """Handle reader connection."""
        antennas = self.hardware_panel.current_antennas
        self.live_monitor.set_current_antennas(antennas)
        self.protocol_runner.set_current_antennas(antennas)
        self.status_bar.set_status("Reader connected", "success")
        self.export_tab.log("Reader connected")
        self.led_reader.set_state("connected")
    
    def _on_reader_disconnected(self):
        """Handle reader disconnection."""
        self.status_bar.set_status("Reader disconnected", "warning")
        self.export_tab.log("Reader disconnected")
        self.led_reader.set_state("off")
    
    def _on_angle_changed(self, angle: float):
        """Handle beam angle change."""
        self.status_bar.set_status(f"Beam: {angle:.1f}°", "info")
    
    def _on_protocol_export(self, result):
        """Handle protocol export request."""
        self.export_tab.set_protocol_result(result)
        self.notebook.select(2)  # Switch to export tab
    
    def _quick_export(self, format: str = "csv"):
        """Quick export current snapshot."""
        if not self.reader or not self.reader.connected:
            messagebox.showwarning("Export", "Reader not connected")
            return
        
        inventory = self.reader.get_all_data()
        if not inventory:
            messagebox.showwarning("Export", "No data to export")
            return
        
        pc = self.beam_control.port_config
        angle = self.beam_control.current_angle
        v1, v2 = self.beam_control.get_voltages()
        
        try:
            path = self.exporter.export_live_snapshot(
                inventory,
                format=format,
                port_config=pc,
                angle=angle,
                v_ch1=v1,
                v_ch2=v2
            )
            self.export_tab.log(f"Exported: {path}")
            messagebox.showinfo("Export", f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Export", str(e))
    
    def _run_beam_check(self):
        """Run quick beam check."""
        if not self.protocol_beam_check:
            messagebox.showerror("Beam Check", "Reader not available")
            return
        
        if not self.reader.connected:
            messagebox.showerror("Beam Check", "Reader not connected")
            return
        
        self.status_bar.set_status("Running beam check...", "info")
        
        result = self.protocol_beam_check.run(
            port_config=self.beam_control.port_config,
            dwell_s=1.0
        )
        
        if result.success:
            msg = (
                f"Beam Check Complete\n\n"
                f"LEFT ({result.left_angle:.0f}°): {result.left_rssi:.1f} dBm\n"
                f"CENTER ({result.center_angle:.0f}°): {result.center_rssi:.1f} dBm\n"
                f"RIGHT ({result.right_angle:.0f}°): {result.right_rssi:.1f} dBm\n\n"
                f"Spread: {result.beam_spread:.1f} dB\n"
                f"Symmetry: {result.beam_symmetry:.1%}\n"
                f"Steering OK: {'✅' if result.is_steering_ok else '❌'}"
            )
            messagebox.showinfo("Beam Check", msg)
            self.status_bar.set_status("Beam check complete", "success")
        else:
            messagebox.showerror("Beam Check", result.error_message)
            self.status_bar.set_status("Beam check failed", "error")
    
    def _run_calibration(self):
        """Run calibration sweep."""
        if not self.protocol_calib:
            messagebox.showerror("Calibration", "Reader not available")
            return
        
        if not self.reader.connected:
            messagebox.showerror("Calibration", "Reader not connected")
            return
        
        # TODO: Add calibration dialog
        messagebox.showinfo("Calibration", "Calibration sweep coming soon!")
    
    def _load_app_preferences(self) -> dict:
        """Load application preferences."""
        try:
            if os.path.exists(self.SETTINGS_FILE):
                with open(self.SETTINGS_FILE, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"theme": "light"}
    
    def _save_app_preferences(self):
        """Save application preferences."""
        try:
            with open(self.SETTINGS_FILE, "w") as f:
                json.dump(self._app_prefs, f, indent=2)
        except Exception as e:
            print(f"Error saving preferences: {e}")
    
    def _apply_last_settings(self):
        """Apply last used settings."""
        # Apply saved reader IP if available
        last_ip = self._app_prefs.get("last_reader_ip")
        if last_ip and hasattr(self.hardware_panel, 'ent_ip'):
            self.hardware_panel.ent_ip.delete(0, tk.END)
            self.hardware_panel.ent_ip.insert(0, last_ip)
    
    def _on_close(self):
        """Handle window close."""
        # Save current settings
        try:
            if hasattr(self.hardware_panel, 'ent_ip'):
                self._app_prefs["last_reader_ip"] = self.hardware_panel.ent_ip.get()
            self._save_app_preferences()
            self.settings.save_to_file()
        except Exception:
            pass
        
        # Stop update loop
        if self._update_id:
            self.root.after_cancel(self._update_id)
        
        # Disconnect hardware
        if self.mcu and self.mcu.is_connected:
            self.mcu.disconnect()
        
        if self.reader and self.reader.connected:
            self.reader.disconnect()
        
        self.root.destroy()
    
    def run(self):
        """Start the application main loop."""
        self.root.mainloop()


def main():
    """Application entry point."""
    root = tk.Tk()
    app = MeasurementApp(root)
    app.run()


if __name__ == "__main__":
    main()
