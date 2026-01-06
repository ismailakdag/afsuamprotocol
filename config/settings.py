"""
Settings and configuration management for AFSUAM Measurement System.

This module provides centralized configuration with dataclasses for
reader settings, MCU settings, and application-wide settings.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import json
import os


@dataclass
class ReaderSettings:
    """RFID Reader configuration settings."""
    ip_address: str = "169.254.1.1"
    port: int = 5084  # LLRP default port
    tx_power_dbm: float = 26.5
    antennas: List[int] = field(default_factory=lambda: [1, 2])
    
    # Advanced Reader Settings (from calibv2)
    mode_identifier: int = 1002  # AutoSet DenseRdr
    session: int = 0  # Fast cycle
    search_mode: str = "2"  # Dual Target (Continuous)
    
    # Available presets for quick configuration
    MODES = {
        1002: "AutoSet DenseRdr",
        1000: "AutoSet",
        1003: "AutoSet Static Fast",
        1004: "AutoSet Static Dense",
        0: "Max Throughput",
        1: "Hybrid",
        2: "Dense Reader M4",
        3: "Dense Reader M8",
        4: "Max Miller"
    }
    
    SESSIONS = {
        0: "Fast cycle",
        1: "Auto reset",
        2: "Extended persist",
        3: "Extended persist"
    }
    
    SEARCH_MODES = {
        "2": "Dual Target (Continuous)",
        "1": "Single Target",
        "3": "TagFocus",
        "0": "Reader Selected"
    }
    
    def get_mode_display(self) -> str:
        return f"{self.mode_identifier} - {self.MODES.get(self.mode_identifier, 'Unknown')}"
    
    def get_session_display(self) -> str:
        return f"{self.session} - {self.SESSIONS.get(self.session, 'Unknown')}"
    
    def get_search_mode_display(self) -> str:
        return f"{self.search_mode} - {self.SEARCH_MODES.get(self.search_mode, 'Unknown')}"
    
    def apply_preset(self, preset_name: str):
        """Apply a preset configuration."""
        presets = {
            "beam_analysis": {
                "mode_identifier": 1002,
                "session": 0,
                "search_mode": "2"
            },
            "stationary_tags": {
                "mode_identifier": 1002,
                "session": 2,
                "search_mode": "1"
            },
            "portal": {
                "mode_identifier": 4,
                "session": 2,
                "search_mode": "1"
            },
            "dense_environment": {
                "mode_identifier": 1004,
                "session": 2,
                "search_mode": "2"
            }
        }
        
        if preset_name in presets:
            for key, value in presets[preset_name].items():
                setattr(self, key, value)


@dataclass
class MCUSettings:
    """Microcontroller configuration settings."""
    port: Optional[str] = None
    baud_rate: int = 115200
    timeout: float = 0.1
    
    # Preferred ports (ordered by priority)
    preferred_ports: List[str] = field(default_factory=lambda: [
        "/dev/cu.usbmodem1201",
        "/dev/cu/.usbmodem1201",
        "COM3",
        "COM4"
    ])
    
    # Voltage limits
    voltage_min: float = 0.0
    voltage_max: float = 8.5


@dataclass
class ProtocolSettings:
    """Protocol execution settings."""
    default_dwell_s: float = 3.0
    default_repeats: int = 3
    default_port_config: int = 0
    station_name: str = "AFSUAM Test-Bed"
    ref_antenna_name: str = "REF_ANT"


@dataclass
class Settings:
    """Main application settings container."""
    reader: ReaderSettings = field(default_factory=ReaderSettings)
    mcu: MCUSettings = field(default_factory=MCUSettings)
    protocol: ProtocolSettings = field(default_factory=ProtocolSettings)
    
    # Paths
    tag_config_file: str = "tag_config.json"
    lut_file: str = "corrected_lut_final.csv"
    
    # Application info
    app_name: str = "AFSUAM Measurement System"
    version: str = "2.0.0"
    
    def save_to_file(self, filepath: str = "settings.json"):
        """Save current settings to JSON file."""
        data = {
            "reader": {
                "ip_address": self.reader.ip_address,
                "tx_power_dbm": self.reader.tx_power_dbm,
                "antennas": self.reader.antennas,
                "mode_identifier": self.reader.mode_identifier,
                "session": self.reader.session,
                "search_mode": self.reader.search_mode
            },
            "mcu": {
                "port": self.mcu.port,
                "baud_rate": self.mcu.baud_rate
            },
            "protocol": {
                "default_dwell_s": self.protocol.default_dwell_s,
                "default_repeats": self.protocol.default_repeats,
                "station_name": self.protocol.station_name
            },
            "paths": {
                "tag_config_file": self.tag_config_file,
                "lut_file": self.lut_file
            }
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    
    @classmethod
    def load_from_file(cls, filepath: str = "settings.json") -> 'Settings':
        """Load settings from JSON file."""
        settings = cls()
        
        if not os.path.exists(filepath):
            return settings
        
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            # Reader settings
            if "reader" in data:
                for key, value in data["reader"].items():
                    if hasattr(settings.reader, key):
                        setattr(settings.reader, key, value)
            
            # MCU settings
            if "mcu" in data:
                for key, value in data["mcu"].items():
                    if hasattr(settings.mcu, key):
                        setattr(settings.mcu, key, value)
            
            # Protocol settings
            if "protocol" in data:
                for key, value in data["protocol"].items():
                    if hasattr(settings.protocol, key):
                        setattr(settings.protocol, key, value)
            
            # Paths
            if "paths" in data:
                settings.tag_config_file = data["paths"].get("tag_config_file", settings.tag_config_file)
                settings.lut_file = data["paths"].get("lut_file", settings.lut_file)
                
        except Exception as e:
            print(f"Error loading settings from {filepath}: {e}")
        
        return settings
