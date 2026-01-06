"""
Export Tab.

Provides export controls and log display.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Optional


class ExportTab(ttk.Frame):
    """
    Export and logging tab.
    """
    
    def __init__(
        self,
        parent,
        reader,
        csv_exporter,
        **kwargs
    ):
        """
        Initialize export tab.
        
        Args:
            parent: Parent widget
            reader: RFIDReader instance
            csv_exporter: CSVExporter instance
        """
        super().__init__(parent, padding=10, **kwargs)
        
        self.reader = reader
        self.exporter = csv_exporter
        
        self._current_result = None
        self._beam_info = {"port_config": 0, "angle": 0, "v1": 0, "v2": 0}
        
        self._build_ui()
    
    def _build_ui(self):
        """Build UI components."""
        # Export buttons
        exp_fr = ttk.LabelFrame(self, text="Quick Export", padding=10)
        exp_fr.pack(fill=tk.X)
        
        ttk.Button(
            exp_fr,
            text="Export Live Snapshot (CSV)",
            command=self._export_snapshot
        ).pack(side=tk.LEFT, padx=4)
        
        ttk.Button(
            exp_fr,
            text="Export Protocol Results (CSV)",
            command=self._export_protocol
        ).pack(side=tk.LEFT, padx=4)
        
        # Log display
        log_fr = ttk.LabelFrame(self, text="Log", padding=10)
        log_fr.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.txt_log = tk.Text(log_fr, height=20, font=("Courier New", 10))
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        
        # Log controls
        btn_fr = ttk.Frame(log_fr)
        btn_fr.pack(fill=tk.X, pady=4)
        
        ttk.Button(
            btn_fr,
            text="Clear Log",
            command=self._clear_log
        ).pack(side=tk.LEFT)
    
    def set_protocol_result(self, result):
        """Set current protocol result for export."""
        self._current_result = result
    
    def set_beam_info(self, port_config: int, angle: float, v1: float, v2: float):
        """Set current beam info for snapshot export."""
        self._beam_info = {
            "port_config": port_config,
            "angle": angle,
            "v1": v1,
            "v2": v2
        }
    
    def log(self, message: str):
        """Add message to log display."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self.txt_log.insert(tk.END, line)
        self.txt_log.see(tk.END)
    
    def _clear_log(self):
        """Clear log display."""
        self.txt_log.delete("1.0", tk.END)
    
    def _export_snapshot(self):
        """Export live inventory snapshot."""
        if not self.reader or not self.reader.connected:
            messagebox.showwarning("Export", "Reader not connected")
            return
        
        inventory = self.reader.get_all_data()
        if not inventory:
            messagebox.showwarning("Export", "No inventory data")
            return
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        
        if not filename:
            return
        
        try:
            path = self.exporter.export_live_snapshot(
                inventory,
                filename=filename.split("/")[-1].split("\\")[-1],
                **self._beam_info
            )
            self.log(f"Exported snapshot: {path}")
            messagebox.showinfo("Export", f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Export", str(e))
    
    def _export_protocol(self):
        """Export protocol results."""
        if not self._current_result:
            messagebox.showwarning("Export", "No protocol results to export")
            return
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"protocol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        
        if not filename:
            return
        
        try:
            path = self.exporter.export_protocol_result(
                self._current_result,
                filename=filename.split("/")[-1].split("\\")[-1]
            )
            self.log(f"Exported protocol: {path}")
            messagebox.showinfo("Export", f"Saved: {path}")
        except Exception as e:
            messagebox.showerror("Export", str(e))
