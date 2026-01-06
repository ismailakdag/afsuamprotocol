"""
Main Application for AFSUAM Measurement System.

This is the main GUI application that ties together all components.
"""

import tkinter as tk
from tkinter import ttk

# Local imports
from config.settings import Settings
from core.rfid_reader import RFIDReader
from core.mcu_controller import MCUController
from core.beam_lut import CorrectedBeamLUT
from core.tag_manager import TagManager
from protocols.afsuam import AFSUAMProtocol
from utils.csv_exporter import CSVExporter
from utils.logging import Logger

from gui.styles import setup_styles
from gui.widgets.hardware_panel import HardwarePanel
from gui.widgets.beam_control import BeamControlPanel
from gui.widgets.status_bar import StatusBar
from gui.tabs.live_monitor import LiveMonitorTab
from gui.tabs.protocol_runner import ProtocolRunnerTab
from gui.tabs.export import ExportTab


class MeasurementApp:
    """
    Main measurement application.
    
    Integrates all components:
    - Hardware panel (MCU + Reader connections)
    - Beam control
    - Live monitor
    - Protocol runner
    - Export functionality
    """
    
    def __init__(self, root: tk.Tk):
        """
        Initialize the application.
        
        Args:
            root: Tkinter root window
        """
        self.root = root
        self.root.title("AFSUAM Measurement System v2.0")
        self.root.geometry("1600x980")
        
        # Initialize settings
        self.settings = Settings.load_from_file()
        
        # Initialize core components
        self.lut = CorrectedBeamLUT(self.settings.lut_file)
        self.mcu = MCUController()
        self.reader = RFIDReader() if RFIDReader.is_available() else None
        self.tag_manager = TagManager(self.settings.tag_config_file)
        
        # Initialize utilities
        self.exporter = CSVExporter()
        self.logger = Logger()
        
        # Initialize protocol
        self.protocol = AFSUAMProtocol(
            reader=self.reader,
            mcu=self.mcu,
            lut=self.lut,
            tag_manager=self.tag_manager
        ) if self.reader else None
        
        # Setup styles
        setup_styles(root)
        
        # Build UI
        self._build_ui()
        
        # Start update loop
        self._update_id = None
        self._start_update_loop()
        
        # Bind cleanup
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _build_ui(self):
        """Build the main UI layout."""
        # Main container
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # LEFT SIDEBAR
        sidebar = ttk.Frame(main_pane, width=350)
        main_pane.add(sidebar, weight=0)
        
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
        
        # Status Bar
        self.status_bar = StatusBar(sidebar)
        self.status_bar.pack(fill=tk.X, pady=5)
        
        # RIGHT MAIN AREA
        main_area = ttk.Frame(main_pane)
        main_pane.add(main_area, weight=1)
        
        # Notebook with tabs
        self.notebook = ttk.Notebook(main_area)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Live Monitor Tab
        self.live_monitor = LiveMonitorTab(
            self.notebook,
            reader=self.reader,
            tag_manager=self.tag_manager
        )
        self.notebook.add(self.live_monitor, text="📡 Live Monitor")
        
        # Protocol Runner Tab
        self.protocol_runner = ProtocolRunnerTab(
            self.notebook,
            protocol=self.protocol,
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
    
    def _start_update_loop(self):
        """Start the UI update loop."""
        self._update_ui()
    
    def _update_ui(self):
        """Update UI periodically."""
        try:
            # Update live monitor
            if self.reader and self.reader.connected:
                self.live_monitor.update()
                
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
    
    def _on_reader_disconnected(self):
        """Handle reader disconnection."""
        self.status_bar.set_status("Reader disconnected", "warning")
        self.export_tab.log("Reader disconnected")
    
    def _on_angle_changed(self, angle: float):
        """Handle beam angle change."""
        self.status_bar.set_status(f"Beam: {angle:.1f}°", "info")
    
    def _on_protocol_export(self, result):
        """Handle protocol export request."""
        self.export_tab.set_protocol_result(result)
        self.notebook.select(2)  # Switch to export tab
    
    def _on_close(self):
        """Handle window close."""
        # Stop update loop
        if self._update_id:
            self.root.after_cancel(self._update_id)
        
        # Disconnect hardware
        if self.mcu and self.mcu.is_connected:
            self.mcu.disconnect()
        
        if self.reader and self.reader.connected:
            self.reader.disconnect()
        
        # Save settings
        try:
            self.settings.save_to_file()
        except Exception:
            pass
        
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
