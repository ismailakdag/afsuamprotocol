"""
AFSUAM Protocol Implementation.

This module implements the L-C-R (Left-Center-Right) beam sweep protocol
for phased array RFID measurements.
"""

import time
from typing import Dict, List, Set
from datetime import datetime

from .base import (
    BaseProtocol, 
    ProtocolResult, 
    StepResult, 
    TagStepResult, 
    UnionResult
)


class AFSUAMProtocol(BaseProtocol):
    """
    AFSUAM L-C-R Beam Sweep Protocol.
    
    This protocol sweeps through LEFT, CENTER, and RIGHT beam states
    while collecting RFID tag data from both antennas.
    """
    
    def run(
        self,
        station_name: str = "AFSUAM Test-Bed",
        ref_antenna_name: str = "REF_ANT",
        dwell_s: float = 3.0,
        repeats: int = 3,
        port_config: int = 0,
        active_antennas: List[int] = None
    ) -> ProtocolResult:
        """
        Execute L-C-R sweep protocol.
        
        Args:
            station_name: Name of measurement station
            ref_antenna_name: Name of reference antenna
            dwell_s: Dwell time per beam state in seconds
            repeats: Number of sweep repeats
            port_config: Port configuration (0 or 1)
            active_antennas: List of active antenna ports
        
        Returns:
            ProtocolResult with all collected data
        """
        if active_antennas is None:
            active_antennas = [1, 2]
        
        result = ProtocolResult(
            station_name=station_name,
            ref_antenna_name=ref_antenna_name,
            start_time=self._get_timestamp()
        )
        
        # Validate prerequisites
        if not self.reader.connected:
            result.success = False
            result.error_message = "Reader is not connected"
            return result
        
        if not self.mcu.is_connected:
            result.success = False
            result.error_message = "MCU is not connected"
            return result
        
        # Get beam presets
        presets = self.lut.get_beam_presets(port_config)
        steps = [
            ("LEFT", presets["LEFT"]),
            ("CENTER", presets["CENTER"]),
            ("RIGHT", presets["RIGHT"])
        ]
        
        total_steps = repeats * len(steps)
        current_step = 0
        
        try:
            for repeat in range(1, repeats + 1):
                if self._stop_requested:
                    break
                
                # Track union coverage for this repeat
                union_ant1_epcs: Set[str] = set()
                union_ant2_epcs: Set[str] = set()
                union_ant1_targets: Set[str] = set()
                union_ant2_targets: Set[str] = set()
                tag_best_beam: Dict[str, Dict] = {
                    s: {"ant1": {"beam": "MISS", "rssi": None}, 
                        "ant2": {"beam": "MISS", "rssi": None}}
                    for s in self.tag_manager.suffixes
                }
                
                for beam_state, angle in steps:
                    if self._stop_requested:
                        break
                    
                    current_step += 1
                    progress = current_step / total_steps
                    self._update_progress(
                        f"Repeat {repeat}/{repeats}: {beam_state} ({angle}°)",
                        progress
                    )
                    
                    # Collect step data
                    step_result, tag_steps, raw = self._collect_step(
                        beam_state=beam_state,
                        angle=angle,
                        port_config=port_config,
                        dwell_s=dwell_s,
                        active_antennas=active_antennas
                    )
                    
                    result.step_results.append(step_result)
                    result.tag_step_results.extend(tag_steps)
                    
                    # Update union tracking
                    union_ant1_epcs |= raw["ant1_epcs"]
                    union_ant2_epcs |= raw["ant2_epcs"]
                    union_ant1_targets |= raw["ant1_targets"]
                    union_ant2_targets |= raw["ant2_targets"]
                    
                    # Track best beam per tag
                    for ts in tag_steps:
                        suffix = ts.tag_suffix
                        
                        if ts.ant1_seen and ts.ant1_rssi is not None:
                            curr = tag_best_beam[suffix]["ant1"]
                            if curr["rssi"] is None or ts.ant1_rssi > curr["rssi"]:
                                tag_best_beam[suffix]["ant1"] = {
                                    "beam": beam_state,
                                    "rssi": ts.ant1_rssi
                                }
                        
                        if ts.ant2_seen and ts.ant2_rssi is not None:
                            curr = tag_best_beam[suffix]["ant2"]
                            if curr["rssi"] is None or ts.ant2_rssi > curr["rssi"]:
                                tag_best_beam[suffix]["ant2"] = {
                                    "beam": beam_state,
                                    "rssi": ts.ant2_rssi
                                }
                
                # Build union result
                ant1_missed = [t.suffix for t in self.tag_manager.get_missed_tags(union_ant1_targets)]
                ant2_missed = [t.suffix for t in self.tag_manager.get_missed_tags(union_ant2_targets)]
                
                union = UnionResult(
                    timestamp=self._get_timestamp(),
                    repeat=repeat,
                    port_config=port_config,
                    dwell_s=dwell_s,
                    ant1_unique_epcs=len(union_ant1_epcs),
                    ant2_unique_epcs=len(union_ant2_epcs),
                    ant1_targets_seen=len(union_ant1_targets),
                    ant2_targets_seen=len(union_ant2_targets),
                    ant1_missed=ant1_missed,
                    ant2_missed=ant2_missed,
                    ant1_best_beam={s: v["ant1"]["beam"] for s, v in tag_best_beam.items()},
                    ant2_best_beam={s: v["ant2"]["beam"] for s, v in tag_best_beam.items()}
                )
                result.union_results.append(union)
            
            result.end_time = self._get_timestamp()
            result.success = True
            
        except Exception as e:
            result.success = False
            result.error_message = str(e)
            result.end_time = self._get_timestamp()
        
        self._stop_requested = False
        return result
    
    def _collect_step(
        self,
        beam_state: str,
        angle: float,
        port_config: int,
        dwell_s: float,
        active_antennas: List[int]
    ) -> tuple:
        """Collect data for a single beam step."""
        
        # Apply beam voltages
        v1, v2 = self.lut.get_voltages(port_config, angle)
        self.mcu.set_voltage(v1, v2)
        
        # Settle time
        time.sleep(0.8)
        
        # Collect data
        self.reader.clear_data()
        time.sleep(dwell_s)
        inventory = self.reader.get_all_data()
        
        # Split by antenna
        inv1, inv2 = self._split_inventory_by_antenna(inventory)
        
        # Calculate targets
        ant1_targets: Set[str] = set()
        ant2_targets: Set[str] = set()
        ant1_target_data: List[Dict] = []
        ant2_target_data: List[Dict] = []
        
        for epc, info in inv1.items():
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix in self.tag_manager.suffixes:
                ant1_targets.add(suffix)
                ant1_target_data.append({
                    "suffix": suffix,
                    "rssi": info.get("rssi", -99.0),
                    "count": info.get("count", 0)
                })
        
        for epc, info in inv2.items():
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix in self.tag_manager.suffixes:
                ant2_targets.add(suffix)
                ant2_target_data.append({
                    "suffix": suffix,
                    "rssi": info.get("rssi", -99.0),
                    "count": info.get("count", 0)
                })
        
        # Build step result
        step_result = StepResult(
            timestamp=self._get_timestamp(),
            beam_state=beam_state,
            angle_deg=angle,
            port_config=port_config,
            v_ch1=v1,
            v_ch2=v2,
            dwell_s=dwell_s,
            ant1_unique_epc_n=len(inv1),
            ant1_targets_seen_n=len(ant1_targets),
            ant1_target_data=ant1_target_data,
            ant1_missed=[s for s in self.tag_manager.suffixes if s not in ant1_targets],
            ant2_unique_epc_n=len(inv2),
            ant2_targets_seen_n=len(ant2_targets),
            ant2_target_data=ant2_target_data,
            ant2_missed=[s for s in self.tag_manager.suffixes if s not in ant2_targets]
        )
        
        # Build per-tag results
        tag_steps: List[TagStepResult] = []
        for tag in self.tag_manager.tags:
            t1 = self._find_tag_info(inv1, tag.suffix)
            t2 = self._find_tag_info(inv2, tag.suffix)
            
            tag_steps.append(TagStepResult(
                timestamp=step_result.timestamp,
                beam_state=beam_state,
                tag_label=tag.label,
                tag_suffix=tag.suffix,
                tag_location=tag.location,
                ant1_seen=t1["seen"],
                ant1_rssi=t1["rssi"],
                ant1_count=t1["count"],
                ant2_seen=t2["seen"],
                ant2_rssi=t2["rssi"],
                ant2_count=t2["count"]
            ))
        
        raw = {
            "ant1_epcs": set(inv1.keys()),
            "ant2_epcs": set(inv2.keys()),
            "ant1_targets": ant1_targets,
            "ant2_targets": ant2_targets
        }
        
        return step_result, tag_steps, raw
