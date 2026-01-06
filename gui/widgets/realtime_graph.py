"""
Real-time RSSI Graph Widget.

Provides live plotting of RSSI data using matplotlib.
"""

import tkinter as tk
from tkinter import ttk
from collections import deque
from datetime import datetime
import time

# Try to import matplotlib
try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class RealTimeGraph(ttk.Frame):
    """
    Real-time RSSI/Phase graph widget using matplotlib.
    
    Shows rolling window of RSSI values for multiple tags.
    """
    
    MAX_POINTS = 50
    UPDATE_INTERVAL_MS = 500
    
    def __init__(self, parent, dark_mode: bool = False, **kwargs):
        """
        Initialize real-time graph.
        
        Args:
            parent: Parent widget
            dark_mode: Use dark theme colors
        """
        super().__init__(parent, **kwargs)
        
        self.dark_mode = dark_mode
        self._data = {}  # tag_suffix -> deque of (time, rssi)
        self._is_running = False
        
        if not MATPLOTLIB_AVAILABLE:
            ttk.Label(
                self, 
                text="📊 Matplotlib not available.\nInstall with: pip install matplotlib",
                font=("Arial", 11)
            ).pack(expand=True)
            return
        
        self._build_graph()
    
    def _build_graph(self):
        """Create matplotlib figure and canvas."""
        # Set colors based on theme
        if self.dark_mode:
            bg_color = '#1e1e2e'
            fg_color = '#cdd6f4'
            grid_color = '#45475a'
        else:
            bg_color = '#ffffff'
            fg_color = '#0f172a'
            grid_color = '#e2e8f0'
        
        # Create figure
        self.fig = Figure(figsize=(6, 3), dpi=100, facecolor=bg_color)
        self.ax = self.fig.add_subplot(111)
        
        # Style the axes
        self.ax.set_facecolor(bg_color)
        self.ax.tick_params(colors=fg_color)
        self.ax.spines['bottom'].set_color(fg_color)
        self.ax.spines['top'].set_color(bg_color)
        self.ax.spines['left'].set_color(fg_color)
        self.ax.spines['right'].set_color(bg_color)
        self.ax.xaxis.label.set_color(fg_color)
        self.ax.yaxis.label.set_color(fg_color)
        self.ax.title.set_color(fg_color)
        
        self.ax.set_xlabel('Time (s)', fontsize=9)
        self.ax.set_ylabel('RSSI (dBm)', fontsize=9)
        self.ax.set_title('Real-time RSSI', fontsize=10, fontweight='bold')
        self.ax.grid(True, alpha=0.3, color=grid_color)
        self.ax.set_ylim(-80, -30)
        
        # Create canvas
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Color palette for tags
        self._colors = [
            '#3b82f6', '#ef4444', '#22c55e', '#f59e0b',
            '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'
        ]
        self._lines = {}
    
    def set_dark_mode(self, enabled: bool):
        """Switch dark mode."""
        if not MATPLOTLIB_AVAILABLE:
            return
        
        self.dark_mode = enabled
        
        if enabled:
            bg_color = '#1e1e2e'
            fg_color = '#cdd6f4'
        else:
            bg_color = '#ffffff'
            fg_color = '#0f172a'
        
        self.fig.set_facecolor(bg_color)
        self.ax.set_facecolor(bg_color)
        self.ax.tick_params(colors=fg_color)
        self.ax.spines['bottom'].set_color(fg_color)
        self.ax.spines['left'].set_color(fg_color)
        self.ax.xaxis.label.set_color(fg_color)
        self.ax.yaxis.label.set_color(fg_color)
        self.ax.title.set_color(fg_color)
        
        self.canvas.draw()
    
    def add_data_point(self, tag_suffix: str, rssi: float):
        """Add a data point for a tag."""
        if not MATPLOTLIB_AVAILABLE:
            return
        
        now = time.time()
        
        if tag_suffix not in self._data:
            self._data[tag_suffix] = deque(maxlen=self.MAX_POINTS)
        
        self._data[tag_suffix].append((now, rssi))
    
    def update_from_inventory(self, inventory: dict, tag_suffixes: list):
        """
        Update graph from inventory data.
        
        Args:
            inventory: Current reader inventory
            tag_suffixes: List of tag suffixes to track
        """
        if not MATPLOTLIB_AVAILABLE:
            return
        
        for epc, info in inventory.items():
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix in tag_suffixes:
                rssi = info.get("rssi", -99)
                if rssi > -99:
                    self.add_data_point(suffix, rssi)
    
    def refresh(self):
        """Redraw the graph with current data."""
        if not MATPLOTLIB_AVAILABLE or not self._data:
            return
        
        self.ax.clear()
        
        # Reconfigure axes
        if self.dark_mode:
            self.ax.set_facecolor('#1e1e2e')
            self.ax.grid(True, alpha=0.3, color='#45475a')
        else:
            self.ax.set_facecolor('#ffffff')
            self.ax.grid(True, alpha=0.3, color='#e2e8f0')
        
        self.ax.set_xlabel('Time (s)', fontsize=9)
        self.ax.set_ylabel('RSSI (dBm)', fontsize=9)
        self.ax.set_title('Real-time RSSI', fontsize=10, fontweight='bold')
        self.ax.set_ylim(-80, -30)
        
        # Plot each tag
        now = time.time()
        color_idx = 0
        
        for suffix, data in self._data.items():
            if not data:
                continue
            
            times = [now - t for t, r in data]
            rssi_values = [r for t, r in data]
            
            color = self._colors[color_idx % len(self._colors)]
            self.ax.plot(
                times, rssi_values,
                marker='o', markersize=3,
                linewidth=1.5, label=suffix,
                color=color
            )
            color_idx += 1
        
        # Invert x-axis (recent on right)
        self.ax.invert_xaxis()
        
        if self._data:
            self.ax.legend(loc='upper left', fontsize=8)
        
        self.fig.tight_layout()
        self.canvas.draw()
    
    def clear(self):
        """Clear all data."""
        self._data = {}
        if MATPLOTLIB_AVAILABLE:
            self.ax.clear()
            self.canvas.draw()
