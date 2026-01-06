"""
Calibration Sweep Protocol.

This protocol sweeps through angle ranges collecting RSSI data
for LUT calibration and beam characterization.
"""

import time
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .base import BaseProtocol, ProtocolResult


@dataclass
class CalibrationPoint:
    """Single calibration measurement point."""
    angle: float
    v_ch1: float
    v_ch2: float
    port_config: int
    
    ant1_rssi_avg: float = -99.0
    ant1_rssi_min: float = -99.0
    ant1_rssi_max: float = -99.0
    ant1_read_count: int = 0
    
    ant2_rssi_avg: float = -99.0
    ant2_rssi_min: float = -99.0
    ant2_rssi_max: float = -99.0
    ant2_read_count: int = 0


@dataclass
class CalibrationResult:
    """Complete calibration sweep result."""
    success: bool = True
    error_message: str = ""
    
    start_time: str = ""
    end_time: str = ""
    
    port_config: int = 0
    angle_start: float = -30.0
    angle_end: float = 30.0
    angle_step: float = 5.0
    dwell_s: float = 2.0
    
    points: List[CalibrationPoint] = field(default_factory=list)
    
    # Best angles found
    ant1_best_angle: float = 0.0
    ant1_best_rssi: float = -99.0
    ant2_best_angle: float = 0.0
    ant2_best_rssi: float = -99.0


class CalibrationSweepProtocol(BaseProtocol):
    """
    Calibration Sweep Protocol.
    
    Sweeps through a range of angles, collecting RSSI statistics
    at each point for beam characterization.
    """
    
    def run(
        self,
        port_config: int = 0,
        angle_start: float = -30.0,
        angle_end: float = 30.0,
        angle_step: float = 5.0,
        dwell_s: float = 2.0,
        active_antennas: Optional[List[int]] = None
    ) -> CalibrationResult:
        """
        Execute calibration sweep.
        
        Args:
            port_config: Port configuration (0 or 1)
            angle_start: Starting angle in degrees
            angle_end: Ending angle in degrees
            angle_step: Step size in degrees
            dwell_s: Dwell time at each angle
            active_antennas: Active antenna list
        
        Returns:
            CalibrationResult with all measurements
        """
        if active_antennas is None:
            active_antennas = [1, 2]
        
        result = CalibrationResult(
            port_config=port_config,
            angle_start=angle_start,
            angle_end=angle_end,
            angle_step=angle_step,
            dwell_s=dwell_s,
            start_time=self._get_timestamp()
        )
        
        # Validate
        if not self.reader or not self.reader.connected:
            result.success = False
            result.error_message = "Reader not connected"
            return result
        
        # Generate angle list
        angles = []
        angle = angle_start
        while angle <= angle_end:
            angles.append(angle)
            angle += angle_step
        
        total = len(angles)
        
        try:
            for idx, target_angle in enumerate(angles):
                if self._stop_requested:
                    break
                
                progress = (idx + 1) / total
                self._update_progress(f"Angle {target_angle:.1f}° ({idx+1}/{total})", progress)
                
                # Apply beam
                v1, v2 = self.lut.get_voltages(port_config, target_angle)
                self.mcu.set_voltage(v1, v2)
                time.sleep(0.5)  # Settle
                
                # Collect
                self.reader.clear_data()
                time.sleep(dwell_s)
                inventory = self.reader.get_all_data()
                
                # Calculate stats
                point = self._calculate_point(
                    target_angle, v1, v2, port_config, 
                    inventory, active_antennas
                )
                result.points.append(point)
                
                # Track best
                if 1 in active_antennas and point.ant1_rssi_avg > result.ant1_best_rssi:
                    result.ant1_best_rssi = point.ant1_rssi_avg
                    result.ant1_best_angle = target_angle
                
                if 2 in active_antennas and point.ant2_rssi_avg > result.ant2_best_rssi:
                    result.ant2_best_rssi = point.ant2_rssi_avg
                    result.ant2_best_angle = target_angle
            
            result.end_time = self._get_timestamp()
            result.success = True
            
        except Exception as e:
            result.success = False
            result.error_message = str(e)
            result.end_time = self._get_timestamp()
        
        # Reset beam
        self.mcu.set_voltage(0, 0)
        self._stop_requested = False
        
        return result
    
    def _calculate_point(
        self,
        angle: float,
        v1: float,
        v2: float,
        port_config: int,
        inventory: Dict,
        active_antennas: List[int]
    ) -> CalibrationPoint:
        """Calculate statistics for a calibration point."""
        
        point = CalibrationPoint(
            angle=angle,
            v_ch1=v1,
            v_ch2=v2,
            port_config=port_config
        )
        
        # Split by antenna
        ant1_rssi, ant2_rssi = [], []
        
        for epc, info in inventory.items():
            # Only count known tags
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix not in self.tag_manager.suffixes:
                continue
            
            rssi = info.get("rssi", -99)
            ant = info.get("antenna", 1)
            count = info.get("count", 1)
            
            if ant == 1 and 1 in active_antennas:
                ant1_rssi.extend([rssi] * count)
            elif ant == 2 and 2 in active_antennas:
                ant2_rssi.extend([rssi] * count)
        
        # Calculate ant1 stats
        if ant1_rssi:
            point.ant1_rssi_avg = sum(ant1_rssi) / len(ant1_rssi)
            point.ant1_rssi_min = min(ant1_rssi)
            point.ant1_rssi_max = max(ant1_rssi)
            point.ant1_read_count = len(ant1_rssi)
        
        # Calculate ant2 stats
        if ant2_rssi:
            point.ant2_rssi_avg = sum(ant2_rssi) / len(ant2_rssi)
            point.ant2_rssi_min = min(ant2_rssi)
            point.ant2_rssi_max = max(ant2_rssi)
            point.ant2_read_count = len(ant2_rssi)
        
        return point
