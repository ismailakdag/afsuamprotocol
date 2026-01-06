"""
Beam Control Panel Widget.

This widget provides beam steering controls.
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable


class BeamControlPanel(ttk.LabelFrame):
    """
    Beam steering control panel.
    
    Provides:
    - Port config selection
    - Angle slider
    - L/C/R preset buttons
    - Voltage display
    """
    
    def __init__(
        self,
        parent,
        lut,
        mcu_controller,
        on_angle_changed: Optional[Callable[[float], None]] = None,
        **kwargs
    ):
        """
        Initialize beam control panel.
        
        Args:
            parent: Parent widget
            lut: CorrectedBeamLUT instance
            mcu_controller: MCUController instance
            on_angle_changed: Callback when angle changes
        """
        super().__init__(parent, text="Beam Control", padding=10, **kwargs)
        
        self.lut = lut
        self.mcu = mcu_controller
        self._on_angle_changed = on_angle_changed
        
        self._port_config = tk.IntVar(value=0)
        self._current_angle = 0.0
        self._current_mode = "CENTER"
        
        self._build_ui()
    
    def _build_ui(self):
        """Build UI components."""
        # Port Config
        ttk.Label(self, text="Port Config:").pack(anchor=tk.W)
        
        config_frame = ttk.Frame(self)
        config_frame.pack(fill=tk.X)
        
        ttk.Radiobutton(
            config_frame,
            text="0 (P1-P4)",
            variable=self._port_config,
            value=0,
            command=self._on_config_change
        ).pack(side=tk.LEFT)
        
        ttk.Radiobutton(
            config_frame,
            text="1 (P2-P3)",
            variable=self._port_config,
            value=1,
            command=self._on_config_change
        ).pack(side=tk.LEFT)
        
        # Angle slider
        ttk.Label(self, text="Angle (deg):").pack(anchor=tk.W, pady=(8, 0))
        
        self.scale_angle = tk.Scale(
            self,
            from_=-30,
            to=30,
            resolution=0.5,
            orient=tk.HORIZONTAL,
            length=300,
            command=self._on_angle_slider
        )
        self.scale_angle.set(0)
        self.scale_angle.pack(fill=tk.X, pady=2)
        
        # L/C/R buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=4)
        
        ttk.Button(
            btn_frame,
            text="LEFT",
            command=lambda: self._set_mode("LEFT")
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        ttk.Button(
            btn_frame,
            text="CENTER",
            command=lambda: self._set_mode("CENTER")
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        ttk.Button(
            btn_frame,
            text="RIGHT",
            command=lambda: self._set_mode("RIGHT")
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        # Voltage display
        volt_frame = ttk.Frame(self)
        volt_frame.pack(fill=tk.X, pady=4)
        
        ttk.Label(volt_frame, text="V_CH1:").pack(side=tk.LEFT)
        self.lbl_v1 = ttk.Label(
            volt_frame,
            text="0.000 V",
            font=("Arial", 11, "bold"),
            foreground="#1e40af"
        )
        self.lbl_v1.pack(side=tk.LEFT, padx=(6, 16))
        
        ttk.Label(volt_frame, text="V_CH2:").pack(side=tk.LEFT)
        self.lbl_v2 = ttk.Label(
            volt_frame,
            text="0.000 V",
            font=("Arial", 11, "bold"),
            foreground="#16a34a"
        )
        self.lbl_v2.pack(side=tk.LEFT, padx=(6, 0))
        
        # Mode display
        self.lbl_mode = ttk.Label(
            self,
            text="Mode: CENTER",
            font=("Arial", 12, "bold")
        )
        self.lbl_mode.pack(pady=4)
    
    def _on_config_change(self):
        """Handle port config change."""
        self._update_voltages()
    
    def _on_angle_slider(self, val):
        """Handle angle slider change."""
        try:
            self._current_angle = float(val)
        except ValueError:
            self._current_angle = 0.0
        
        self._current_mode = "MANUAL"
        self.lbl_mode.config(text=f"Mode: MANUAL ({self._current_angle:.1f}°)")
        self._update_voltages()
        
        if self._on_angle_changed:
            self._on_angle_changed(self._current_angle)
    
    def _set_mode(self, mode: str):
        """Set beam mode preset."""
        mode = mode.upper()
        self._current_mode = mode
        
        pc = self._port_config.get()
        presets = self.lut.get_beam_presets(pc)
        
        if mode in presets:
            self._current_angle = float(presets[mode])
            self.scale_angle.set(self._current_angle)
        
        self.lbl_mode.config(text=f"Mode: {mode}")
        self._update_voltages()
        
        if self._on_angle_changed:
            self._on_angle_changed(self._current_angle)
    
    def _update_voltages(self):
        """Calculate and apply voltages."""
        pc = self._port_config.get()
        v1, v2 = self.lut.get_voltages(pc, self._current_angle)
        
        self.lbl_v1.config(text=f"{v1:.3f} V")
        self.lbl_v2.config(text=f"{v2:.3f} V")
        
        # Apply to MCU
        self.mcu.set_voltage(v1, v2)
    
    @property
    def current_angle(self) -> float:
        """Get current beam angle."""
        return self._current_angle
    
    @property
    def port_config(self) -> int:
        """Get current port configuration."""
        return self._port_config.get()
    
    def get_voltages(self) -> tuple:
        """Get current voltages."""
        pc = self._port_config.get()
        return self.lut.get_voltages(pc, self._current_angle)
