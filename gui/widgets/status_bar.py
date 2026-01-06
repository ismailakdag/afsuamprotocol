"""
Status Bar Widget.

Simple status display and logging widget.
"""

import tkinter as tk
from tkinter import ttk
from datetime import datetime


class StatusBar(ttk.Frame):
    """
    Status bar with log display.
    """
    
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        
        # Status label
        self.lbl_status = ttk.Label(
            self,
            text="Ready.",
            font=("Arial", 10)
        )
        self.lbl_status.pack(anchor=tk.W)
    
    def set_status(self, message: str, level: str = "info"):
        """
        Update status message.
        
        Args:
            message: Status message
            level: Message level (info, warning, error)
        """
        colors = {
            "info": "#0f172a",
            "warning": "#b45309",
            "error": "#dc2626",
            "success": "#16a34a"
        }
        
        self.lbl_status.config(
            text=message,
            foreground=colors.get(level, "#0f172a")
        )
    
    def clear(self):
        """Clear status."""
        self.lbl_status.config(text="Ready.", foreground="#0f172a")
