"""
Beam Check Protocol.

Quick beam verification protocol to ensure beam steering is working.
"""

import time
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .base import BaseProtocol


@dataclass
class BeamCheckResult:
    """Result from beam check."""
    success: bool = True
    error_message: str = ""
    
    timestamp: str = ""
    port_config: int = 0
    
    # L-C-R RSSI values
    left_angle: float = 0.0
    left_rssi: float = -99.0
    left_reads: int = 0
    
    center_angle: float = 0.0
    center_rssi: float = -99.0
    center_reads: int = 0
    
    right_angle: float = 0.0
    right_rssi: float = -99.0
    right_reads: int = 0
    
    # Beam steering quality metrics
    beam_spread: float = 0.0  # difference between left and right
    beam_symmetry: float = 0.0  # how symmetric L-R around C
    is_steering_ok: bool = False  # basic check if steering works


class BeamCheckProtocol(BaseProtocol):
    """
    Quick Beam Check Protocol.
    
    Performs a rapid L-C-R sweep to verify beam steering functionality.
    """
    
    def run(
        self,
        port_config: int = 0,
        dwell_s: float = 1.0,
        active_antennas: Optional[List[int]] = None
    ) -> BeamCheckResult:
        """
        Execute quick beam check.
        
        Args:
            port_config: Port configuration (0 or 1)
            dwell_s: Dwell time at each position
            active_antennas: Active antenna list
        
        Returns:
            BeamCheckResult with steering metrics
        """
        if active_antennas is None:
            active_antennas = [1]  # Beam check typically uses phased array only
        
        result = BeamCheckResult(
            port_config=port_config,
            timestamp=self._get_timestamp()
        )
        
        # Validate
        if not self.reader or not self.reader.connected:
            result.success = False
            result.error_message = "Reader not connected"
            return result
        
        if not self.mcu or not self.mcu.is_connected:
            result.success = False
            result.error_message = "MCU not connected"
            return result
        
        presets = self.lut.get_beam_presets(port_config)
        
        try:
            # Check LEFT
            self._update_progress("Checking LEFT beam...", 0.2)
            result.left_angle = presets["LEFT"]
            result.left_rssi, result.left_reads = self._measure_beam(
                presets["LEFT"], port_config, dwell_s, active_antennas
            )
            
            # Check CENTER
            self._update_progress("Checking CENTER beam...", 0.5)
            result.center_angle = presets["CENTER"]
            result.center_rssi, result.center_reads = self._measure_beam(
                presets["CENTER"], port_config, dwell_s, active_antennas
            )
            
            # Check RIGHT
            self._update_progress("Checking RIGHT beam...", 0.8)
            result.right_angle = presets["RIGHT"]
            result.right_rssi, result.right_reads = self._measure_beam(
                presets["RIGHT"], port_config, dwell_s, active_antennas
            )
            
            # Calculate metrics
            result.beam_spread = abs(result.left_rssi - result.right_rssi)
            
            left_diff = abs(result.left_rssi - result.center_rssi)
            right_diff = abs(result.right_rssi - result.center_rssi)
            if left_diff + right_diff > 0:
                result.beam_symmetry = 1.0 - abs(left_diff - right_diff) / (left_diff + right_diff)
            else:
                result.beam_symmetry = 1.0
            
            # Basic steering check: should see RSSI variation
            rssi_values = [result.left_rssi, result.center_rssi, result.right_rssi]
            rssi_range = max(rssi_values) - min(rssi_values)
            result.is_steering_ok = rssi_range > 1.0 and all(r > -90 for r in rssi_values)
            
            self._update_progress("Beam check complete", 1.0)
            result.success = True
            
        except Exception as e:
            result.success = False
            result.error_message = str(e)
        
        # Reset beam
        self.mcu.set_voltage(0, 0)
        
        return result
    
    def _measure_beam(
        self,
        angle: float,
        port_config: int,
        dwell_s: float,
        active_antennas: List[int]
    ) -> tuple:
        """Measure RSSI at given beam angle."""
        
        # Apply beam
        v1, v2 = self.lut.get_voltages(port_config, angle)
        self.mcu.set_voltage(v1, v2)
        time.sleep(0.3)  # Settle
        
        # Collect
        self.reader.clear_data()
        time.sleep(dwell_s)
        inventory = self.reader.get_all_data()
        
        # Calculate average RSSI for target antenna
        rssi_values = []
        read_count = 0
        
        for epc, info in inventory.items():
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix not in self.tag_manager.suffixes:
                continue
            
            ant = info.get("antenna", 1)
            if ant in active_antennas:
                rssi_values.append(info.get("rssi", -99))
                read_count += info.get("count", 1)
        
        avg_rssi = sum(rssi_values) / len(rssi_values) if rssi_values else -99.0
        
        return avg_rssi, read_count
