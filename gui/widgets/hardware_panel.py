"""
Hardware Panel Widget.

This widget provides MCU and Reader connection controls.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable


class HardwarePanel(ttk.LabelFrame):
    """
    Hardware connection and control panel.
    
    Provides:
    - MCU port selection and connection
    - Reader IP/power configuration
    - Advanced reader settings (mode, session, search)
    - Antenna mode selection
    """
    
    def __init__(
        self,
        parent,
        mcu_controller,
        rfid_reader,
        settings,
        on_reader_connected: Optional[Callable] = None,
        on_reader_disconnected: Optional[Callable] = None,
        **kwargs
    ):
        """
        Initialize hardware panel.
        
        Args:
            parent: Parent widget
            mcu_controller: MCUController instance
            rfid_reader: RFIDReader instance
            settings: Settings instance
            on_reader_connected: Callback when reader connects
            on_reader_disconnected: Callback when reader disconnects
        """
        super().__init__(parent, text="Hardware", padding=10, **kwargs)
        
        self.mcu = mcu_controller
        self.reader = rfid_reader
        self.settings = settings
        
        self._on_connected = on_reader_connected
        self._on_disconnected = on_reader_disconnected
        
        self._antenna_mode = tk.StringVar(value="BOTH")
        self._current_antennas = [1, 2]
        
        self._build_ui()
    
    def _build_ui(self):
        """Build the UI components."""
        # MCU Section
        self._build_mcu_section()
        
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        
        # Reader Section
        self._build_reader_section()
        
        # Antenna Mode Section
        self._build_antenna_section()
        
        # Connect/Disconnect Buttons
        self._build_connection_buttons()
    
    def _build_mcu_section(self):
        """Build MCU connection section."""
        ttk.Label(self, text="MCU Port:").pack(anchor=tk.W)
        
        ports = self.mcu.list_ports()
        self.cb_port = ttk.Combobox(self, values=ports)
        
        preferred = self.mcu.find_preferred_port(ports)
        if preferred:
            self.cb_port.set(preferred)
        elif ports:
            self.cb_port.current(0)
        
        self.cb_port.pack(fill=tk.X, pady=2)
        
        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, pady=4)
        
        ttk.Button(
            btn_row, 
            text="Connect MCU",
            command=self._connect_mcu
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        ttk.Button(
            btn_row,
            text="Refresh Ports",
            command=self._refresh_ports
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
    
    def _build_reader_section(self):
        """Build reader connection section."""
        ttk.Label(self, text="Reader IP:").pack(anchor=tk.W)
        self.ent_ip = ttk.Entry(self)
        self.ent_ip.insert(0, self.settings.reader.ip_address)
        self.ent_ip.pack(fill=tk.X, pady=2)
        
        ttk.Label(self, text="Power (dBm):").pack(anchor=tk.W)
        self.ent_power = ttk.Entry(self)
        self.ent_power.insert(0, str(self.settings.reader.tx_power_dbm))
        self.ent_power.pack(fill=tk.X, pady=2)
        
        # Advanced settings toggle
        self._show_advanced = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="⚙️ Advanced Reader Settings",
            variable=self._show_advanced,
            command=self._toggle_advanced
        ).pack(anchor=tk.W, pady=4)
        
        # Advanced settings frame (hidden by default)
        self._advanced_frame = ttk.Frame(self)
        self._build_advanced_settings()
    
    def _build_advanced_settings(self):
        """Build advanced reader settings."""
        frame = self._advanced_frame
        
        # Mode
        ttk.Label(frame, text="Mode:", font=("Arial", 9)).grid(row=0, column=0, sticky=tk.W)
        self.cmb_mode = ttk.Combobox(frame, width=22, state="readonly")
        self.cmb_mode['values'] = [
            "1002 - AutoSet DenseRdr",
            "1000 - AutoSet",
            "1003 - AutoSet Static Fast",
            "1004 - AutoSet Static Dense",
            "0 - Max Throughput",
            "1 - Hybrid",
            "2 - Dense Reader M4",
            "4 - Max Miller"
        ]
        self.cmb_mode.current(0)
        self.cmb_mode.grid(row=0, column=1, padx=2, pady=1)
        
        # Session
        ttk.Label(frame, text="Session:", font=("Arial", 9)).grid(row=1, column=0, sticky=tk.W)
        self.cmb_session = ttk.Combobox(frame, width=22, state="readonly")
        self.cmb_session['values'] = [
            "0 - Fast cycle",
            "1 - Auto reset",
            "2 - Extended persist"
        ]
        self.cmb_session.current(0)
        self.cmb_session.grid(row=1, column=1, padx=2, pady=1)
        
        # Search Mode
        ttk.Label(frame, text="Search:", font=("Arial", 9)).grid(row=2, column=0, sticky=tk.W)
        self.cmb_search = ttk.Combobox(frame, width=22, state="readonly")
        self.cmb_search['values'] = [
            "2 - Dual Target (Cont.)",
            "1 - Single Target",
            "3 - TagFocus"
        ]
        self.cmb_search.current(0)
        self.cmb_search.grid(row=2, column=1, padx=2, pady=1)
        
        # Presets
        ttk.Label(frame, text="Preset:", font=("Arial", 9)).grid(row=3, column=0, sticky=tk.W)
        self.cmb_preset = ttk.Combobox(frame, width=22, state="readonly")
        self.cmb_preset['values'] = [
            "📊 Beam Analysis",
            "📦 Stationary Tags",
            "🚪 Portal (Moving)",
            "🔍 Dense Environment"
        ]
        self.cmb_preset.current(0)
        self.cmb_preset.bind("<<ComboboxSelected>>", self._apply_preset)
        self.cmb_preset.grid(row=3, column=1, padx=2, pady=1)
    
    def _build_antenna_section(self):
        """Build antenna mode selection."""
        ant_frame = ttk.LabelFrame(self, text="📡 Antenna Mode", padding=5)
        ant_frame.pack(fill=tk.X, pady=4)
        
        ttk.Radiobutton(
            ant_frame,
            text="Both (Ant1 + Ant2)",
            variable=self._antenna_mode,
            value="BOTH"
        ).pack(anchor=tk.W)
        
        ttk.Radiobutton(
            ant_frame,
            text="Ant1 Only (Phased Array)",
            variable=self._antenna_mode,
            value="ANT1_ONLY"
        ).pack(anchor=tk.W)
        
        ttk.Radiobutton(
            ant_frame,
            text="Ant2 Only (Reference)",
            variable=self._antenna_mode,
            value="ANT2_ONLY"
        ).pack(anchor=tk.W)
        
        self.lbl_antenna_status = ttk.Label(
            ant_frame,
            text="Active: Ant1 + Ant2",
            foreground="#1e40af",
            font=("Arial", 9, "bold")
        )
        self.lbl_antenna_status.pack(anchor=tk.W, pady=(4, 0))
    
    def _build_connection_buttons(self):
        """Build connect/disconnect buttons."""
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=4)
        
        self.btn_connect = ttk.Button(
            btn_frame,
            text="Connect Reader",
            command=self._connect_reader
        )
        self.btn_connect.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        self.btn_disconnect = ttk.Button(
            btn_frame,
            text="Disconnect",
            command=self._disconnect_reader,
            state=tk.DISABLED
        )
        self.btn_disconnect.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
    
    def _toggle_advanced(self):
        """Toggle advanced settings visibility."""
        if self._show_advanced.get():
            self._advanced_frame.pack(fill=tk.X, pady=2)
        else:
            self._advanced_frame.pack_forget()
    
    def _apply_preset(self, event=None):
        """Apply reader preset configuration."""
        preset = self.cmb_preset.get()
        
        if "Beam Analysis" in preset:
            self.cmb_mode.set("1002 - AutoSet DenseRdr")
            self.cmb_session.set("0 - Fast cycle")
            self.cmb_search.set("2 - Dual Target (Cont.)")
        elif "Stationary" in preset:
            self.cmb_mode.set("1002 - AutoSet DenseRdr")
            self.cmb_session.set("2 - Extended persist")
            self.cmb_search.set("1 - Single Target")
        elif "Portal" in preset:
            self.cmb_mode.set("4 - Max Miller")
            self.cmb_session.set("2 - Extended persist")
            self.cmb_search.set("1 - Single Target")
        elif "Dense" in preset:
            self.cmb_mode.set("1004 - AutoSet Static Dense")
            self.cmb_session.set("2 - Extended persist")
            self.cmb_search.set("2 - Dual Target (Cont.)")
    
    def _refresh_ports(self):
        """Refresh available serial ports."""
        ports = self.mcu.list_ports()
        self.cb_port['values'] = ports
        
        preferred = self.mcu.find_preferred_port(ports)
        if preferred:
            self.cb_port.set(preferred)
        elif ports:
            self.cb_port.current(0)
    
    def _connect_mcu(self):
        """Connect to MCU."""
        port = self.cb_port.get().strip()
        if not port:
            messagebox.showerror("MCU", "No serial port selected.")
            return
        
        try:
            self.mcu.connect(port)
            messagebox.showinfo("MCU", f"Connected: {port}")
        except Exception as e:
            messagebox.showerror("MCU", f"Connection failed: {e}")
    
    def _connect_reader(self):
        """Connect to RFID reader."""
        if not self.reader.is_available():
            messagebox.showerror("Reader", "SLLURP library not available.")
            return
        
        ip = self.ent_ip.get().strip()
        try:
            power = float(self.ent_power.get().strip())
        except ValueError:
            power = 26.5
        
        # Get advanced settings
        mode = int(self.cmb_mode.get().split(" - ")[0])
        session = int(self.cmb_session.get().split(" - ")[0])
        search = self.cmb_search.get().split(" - ")[0]
        
        # Get antennas
        mode_val = self._antenna_mode.get()
        if mode_val == "ANT1_ONLY":
            antennas = [1]
        elif mode_val == "ANT2_ONLY":
            antennas = [2]
        else:
            antennas = [1, 2]
        
        self._current_antennas = antennas
        
        ok = self.reader.connect(
            ip_address=ip,
            power_dbm=power,
            antennas=antennas,
            mode_identifier=mode,
            session=session,
            search_mode=search
        )
        
        if ok:
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.NORMAL)
            self._update_antenna_label()
            
            if self._on_connected:
                self._on_connected()
            
            messagebox.showinfo("Reader", f"Connected: {ip}\nAntennas: {antennas}")
        else:
            messagebox.showerror("Reader", "Connection failed.")
    
    def _disconnect_reader(self):
        """Disconnect from reader."""
        if self.reader:
            self.reader.disconnect()
        
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_disconnect.config(state=tk.DISABLED)
        
        if self._on_disconnected:
            self._on_disconnected()
    
    def _update_antenna_label(self):
        """Update antenna status label."""
        if self._current_antennas == [1]:
            text = "Active: Ant1 Only"
            color = "#2563eb"
        elif self._current_antennas == [2]:
            text = "Active: Ant2 Only"
            color = "#16a34a"
        else:
            text = "Active: Ant1 + Ant2"
            color = "#7c3aed"
        
        self.lbl_antenna_status.config(text=text, foreground=color)
    
    @property
    def current_antennas(self):
        """Get current active antennas."""
        return self._current_antennas
