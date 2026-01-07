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
        active_antennas: List[int] = None,
        beam_steps: int = 3
    ) -> ProtocolResult:
        """
        Execute AFSUAM phased-array measurement protocol.
        
        Args:
            station_name: Name of measurement station
            ref_antenna_name: Name of reference antenna
            dwell_s: Dwell time per beam state in seconds
            repeats: Number of sweep repeats
            port_config: Port configuration (0 or 1)
            active_antennas: List of active antenna ports
            beam_steps: Number of beam positions to sweep (default 3)
        
        Returns:
            ProtocolResult with all collected data
        """
        if active_antennas is None:
            active_antennas = [1, 2]
        
        # Determine antenna health/status
        ant1_enabled = 1 in active_antennas
        ant2_enabled = 2 in active_antennas
        
        # Generate unique run ID
        import uuid
        run_id = f"{self._get_timestamp().replace(':', '').replace('-', '').replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
        
        # Validation errors list
        validation_errors = []
        
        # Check steering needed (only if Ant1 is active)
        steering_enabled = 1 in active_antennas

        # Generate beam sequence
        beam_sequence_str = ""
        steps = []
        
        if not steering_enabled:
            # Non-steering mode (Ant2 only) - Single Fixed Step
            steps = [("FIXED", 0.0)]
            beam_sequence_str = "FIXED"
        elif beam_steps == 3:
            presets = self.lut.get_beam_presets(port_config)
            steps = [
                ("LEFT", presets["LEFT"]),
                ("CENTER", presets["CENTER"]),
                ("RIGHT", presets["RIGHT"])
            ]
            beam_sequence_str = "LEFT|CENTER|RIGHT"
        else:
            # Dynamic steps from +30 to -30
            import numpy as np
            if beam_steps < 2: 
                angles = [0.0]
            else:
                angles = np.linspace(30.0, -30.0, beam_steps)
            
            beam_sequence_list = []
            for angle in angles:
                # Round to 1 decimal place
                angle = float(round(angle, 1))
                name = f"BEAM_{int(angle) if angle.is_integer() else angle}"
                steps.append((name, angle))
                beam_sequence_list.append(name)
            
            beam_sequence_str = "|".join(beam_sequence_list)
        
        result = ProtocolResult(
            station_name=station_name,
            ref_antenna_name=ref_antenna_name,
            start_time=self._get_timestamp(),
            run_id=run_id,
            # Protocol configuration
            protocol_dwell_s=dwell_s,
            protocol_repeats=repeats,
            port_config=port_config,
            beam_sequence=beam_sequence_str,
            active_antennas=active_antennas,
            tie_break_rule="prefer_higher_rssi",
            # Target configuration
            targets_configured_suffixes=self.tag_manager.suffixes.copy(),
            targets_configured_labels=[t.label for t in self.tag_manager.tags],
            # Hardware status
            mcu_connected=self.mcu.is_connected if self.mcu else False,
            reader_connected=self.reader.connected if self.reader else False,
            port2_enabled=ant2_enabled,
            # Antenna health
            ant1_health="OK" if ant1_enabled else "DISABLED",
            ant2_health="OK" if ant2_enabled else "DISABLED",
            ant1_warning="" if ant1_enabled else "Antenna 1 disabled by user",
            ant2_warning="" if ant2_enabled else "Antenna 2 disabled by user",
            # Data validation (will be set at end)
            data_valid=True,
            validation_errors=validation_errors
        )
        
        # Validate prerequisites
        if not self.reader.connected:
            result.success = False
            result.error_message = "Reader is not connected"
            return result
        
        if steering_enabled and not self.mcu.is_connected:
            result.success = False
            result.error_message = "MCU is not connected"
            return result
        
        total_steps = repeats * len(steps)
        current_step = 0
        
        # Track if we ever see tags on each antenna
        any_ant1_tags = False
        any_ant2_tags = False
        
        try:
            for repeat in range(1, repeats + 1):
                if self._stop_requested:
                    break
                
                # Track union coverage for this repeat
                union_ant1_epcs: Set[str] = set()
                union_ant2_epcs: Set[str] = set()
                union_ant1_targets: Set[str] = set()
                union_ant2_targets: Set[str] = set()
                # Pre-calculate beam names for initialization
                beam_names = [s[0] for s in steps]
                
                tag_best_beam: Dict[str, Dict] = {
                    s: {
                        "ant1": {name: None for name in beam_names},
                        "ant2": {name: None for name in beam_names}
                    }
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
                    
                    # Track if we ever see any tags
                    if step_result.ant1_unique_epc_n > 0:
                        any_ant1_tags = True
                    if step_result.ant2_unique_epc_n > 0:
                        any_ant2_tags = True
                    
                    # Update union tracking
                    union_ant1_epcs |= raw["ant1_epcs"]
                    union_ant2_epcs |= raw["ant2_epcs"]
                    union_ant1_targets |= raw["ant1_targets"]
                    union_ant2_targets |= raw["ant2_targets"]
                    
                    # Track RSSI per beam per tag
                    for ts in tag_steps:
                        suffix = ts.tag_suffix
                        
                        if ts.ant1_seen and ts.ant1_rssi is not None:
                            tag_best_beam[suffix]["ant1"][beam_state] = ts.ant1_rssi
                        
                        if ts.ant2_seen and ts.ant2_rssi is not None:
                            tag_best_beam[suffix]["ant2"][beam_state] = ts.ant2_rssi
                
                # Build missed tag lists with full info (empty if antenna disabled)
                ant1_missed_tags = self.tag_manager.get_missed_tags(union_ant1_targets) if ant1_enabled else []
                ant2_missed_tags = self.tag_manager.get_missed_tags(union_ant2_targets) if ant2_enabled else []
                
                # Helper to compute best beam, RSSI, margin, confidence
                def compute_best(beam_data, is_disabled=False):
                    """Compute best beam, rssi, seen_n, margin, tie_flag, confidence from beam RSSI dict."""
                    if is_disabled:
                        return "DISABLED", None, 0, None, 0, "DISABLED"
                    
                    rssi_list = [(b, r) for b, r in beam_data.items() if r is not None]
                    seen_n = len(rssi_list)
                    
                    if seen_n == 0:
                        return "MISS", None, 0, None, 0, "NONE"
                    
                    # Sort by RSSI descending
                    rssi_list.sort(key=lambda x: x[1], reverse=True)
                    best_beam, best_rssi = rssi_list[0]
                    
                    # Calculate margin and tie_flag
                    if seen_n >= 2:
                        second_rssi = rssi_list[1][1]
                        margin = best_rssi - second_rssi
                        tie_flag = 1 if margin == 0.0 else 0
                        
                        # Confidence based on margin
                        if margin >= 3.0:
                            confidence = "HIGH"
                        elif margin >= 1.0:
                            confidence = "MED"
                        else:
                            confidence = "LOW"
                    else:
                        # Single beam - margin is NA
                        margin = None
                        tie_flag = 0
                        confidence = "SINGLE"
                    
                    return best_beam, best_rssi, seen_n, margin, tie_flag, confidence
                
                # Check if antennas are disabled
                ant1_disabled = 1 not in active_antennas
                ant2_disabled = 2 not in active_antennas
                
                # Compute for each tag
                ant1_best_beam = {}
                ant1_best_rssi = {}
                ant1_seen_beams_n = {}
                ant1_best_margin = {}
                ant1_tie_flag = {}
                ant1_best_confidence = {}
                ant2_best_beam = {}
                ant2_best_rssi = {}
                ant2_seen_beams_n = {}
                ant2_best_margin = {}
                ant2_tie_flag = {}
                ant2_best_confidence = {}
                
                for suffix, data in tag_best_beam.items():
                    # Ant1
                    beam, rssi, seen_n, margin, tie, conf = compute_best(data["ant1"], ant1_disabled)
                    ant1_best_beam[suffix] = beam
                    ant1_seen_beams_n[suffix] = seen_n
                    ant1_tie_flag[suffix] = tie
                    ant1_best_confidence[suffix] = conf
                    if rssi is not None:
                        ant1_best_rssi[suffix] = rssi
                    if margin is not None:
                        ant1_best_margin[suffix] = margin
                    
                    # Ant2
                    beam, rssi, seen_n, margin, tie, conf = compute_best(data["ant2"], ant2_disabled)
                    ant2_best_beam[suffix] = beam
                    ant2_seen_beams_n[suffix] = seen_n
                    ant2_tie_flag[suffix] = tie
                    ant2_best_confidence[suffix] = conf
                    if rssi is not None:
                        ant2_best_rssi[suffix] = rssi
                    if margin is not None:
                        ant2_best_margin[suffix] = margin
                
                union = UnionResult(
                    timestamp=self._get_timestamp(),
                    repeat=repeat,
                    port_config=port_config,
                    dwell_s=dwell_s,
                    active_antennas=active_antennas,
                    tags_total=self.tag_manager.count,
                    ant1_unique_epcs=len(union_ant1_epcs),
                    ant2_unique_epcs=len(union_ant2_epcs),
                    ant1_targets_seen=len(union_ant1_targets),
                    ant2_targets_seen=len(union_ant2_targets),
                    # Missed - legacy format
                    ant1_missed=[t.suffix for t in ant1_missed_tags],
                    ant2_missed=[t.suffix for t in ant2_missed_tags],
                    # Missed - separated
                    ant1_missed_suffixes=[t.suffix for t in ant1_missed_tags],
                    ant1_missed_labels=[t.label for t in ant1_missed_tags],
                    ant2_missed_suffixes=[t.suffix for t in ant2_missed_tags],
                    ant2_missed_labels=[t.label for t in ant2_missed_tags],
                    # Best beam and RSSI
                    ant1_best_beam=ant1_best_beam,
                    ant2_best_beam=ant2_best_beam,
                    ant1_best_rssi=ant1_best_rssi,
                    ant2_best_rssi=ant2_best_rssi,
                    # Seen beams count
                    ant1_seen_beams_n=ant1_seen_beams_n,
                    ant2_seen_beams_n=ant2_seen_beams_n,
                    # Margin (None if single beam)
                    ant1_best_margin=ant1_best_margin,
                    ant2_best_margin=ant2_best_margin,
                    # Tie flag
                    ant1_tie_flag=ant1_tie_flag,
                    ant2_tie_flag=ant2_tie_flag,
                    # Confidence (HIGH/MED/LOW/SINGLE/DISABLED/NONE)
                    ant1_best_confidence=ant1_best_confidence,
                    ant2_best_confidence=ant2_best_confidence
                )
                result.union_results.append(union)
            
            # Update antenna health based on observed data
            if ant1_enabled and not any_ant1_tags:
                result.ant1_health = "NO_TAG_REPORTS"
                result.ant1_warning = "AntennaID=1 never observed in reports"
            if ant2_enabled and not any_ant2_tags:
                result.ant2_health = "NO_TAG_REPORTS"
                result.ant2_warning = "AntennaID=2 never observed in reports"
            
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
        
        v1, v2 = None, None
        
        # Apply beam voltages only if not FIXED mode
        if beam_state != "FIXED":
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
        
        # Calculate targets and statistics for Ant1
        ant1_targets: Set[str] = set()
        ant1_target_data: List[Dict] = []
        ant1_rssi_list: List[float] = []
        ant1_total_reads: int = 0
        
        for epc, info in inv1.items():
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix in self.tag_manager.suffixes:
                ant1_targets.add(suffix)
                rssi = float(info.get("rssi", -99.0))
                count = int(info.get("count", 0))
                ant1_target_data.append({
                    "suffix": suffix,
                    "rssi": rssi,
                    "count": count
                })
                ant1_rssi_list.append(rssi)
                ant1_total_reads += count
        
        # Calculate targets and statistics for Ant2
        ant2_targets: Set[str] = set()
        ant2_target_data: List[Dict] = []
        ant2_rssi_list: List[float] = []
        ant2_total_reads: int = 0
        
        for epc, info in inv2.items():
            suffix = epc[-4:] if len(epc) >= 4 else ""
            if suffix in self.tag_manager.suffixes:
                ant2_targets.add(suffix)
                rssi = float(info.get("rssi", -99.0))
                count = int(info.get("count", 0))
                ant2_target_data.append({
                    "suffix": suffix,
                    "rssi": rssi,
                    "count": count
                })
                ant2_rssi_list.append(rssi)
                ant2_total_reads += count
        
        # Calculate RSSI statistics
        ant1_rssi_min = min(ant1_rssi_list) if ant1_rssi_list else None
        ant1_rssi_max = max(ant1_rssi_list) if ant1_rssi_list else None
        ant1_rssi_avg = sum(ant1_rssi_list) / len(ant1_rssi_list) if ant1_rssi_list else None
        
        ant2_rssi_min = min(ant2_rssi_list) if ant2_rssi_list else None
        ant2_rssi_max = max(ant2_rssi_list) if ant2_rssi_list else None
        ant2_rssi_avg = sum(ant2_rssi_list) / len(ant2_rssi_list) if ant2_rssi_list else None
        
        # Get missed tags with full info
        ant1_missed_tags = [t for t in self.tag_manager.tags if t.suffix not in ant1_targets]
        ant2_missed_tags = [t for t in self.tag_manager.tags if t.suffix not in ant2_targets]
        
        # Build step result
        step_result = StepResult(
            timestamp=self._get_timestamp(),
            beam_state=beam_state,
            angle_deg=angle,
            port_config=port_config,
            v_ch1=v1,
            v_ch2=v2,
            dwell_s=dwell_s,
            active_antennas=active_antennas,
            tags_total=self.tag_manager.count,
            # Ant1 stats
            ant1_unique_epc_n=len(inv1),
            ant1_targets_seen_n=len(ant1_targets),
            ant1_target_data=ant1_target_data,
            ant1_total_reads=ant1_total_reads,
            ant1_rssi_min=ant1_rssi_min,
            ant1_rssi_max=ant1_rssi_max,
            ant1_rssi_avg=ant1_rssi_avg,
            ant1_missed=[t.suffix for t in ant1_missed_tags],
            ant1_missed_suffixes=[t.suffix for t in ant1_missed_tags],
            ant1_missed_labels=[t.label for t in ant1_missed_tags],
            ant1_missed_locations=[t.location for t in ant1_missed_tags],
            # Ant2 stats
            ant2_unique_epc_n=len(inv2),
            ant2_targets_seen_n=len(ant2_targets),
            ant2_target_data=ant2_target_data,
            ant2_total_reads=ant2_total_reads,
            ant2_rssi_min=ant2_rssi_min,
            ant2_rssi_max=ant2_rssi_max,
            ant2_rssi_avg=ant2_rssi_avg,
            ant2_missed=[t.suffix for t in ant2_missed_tags],
            ant2_missed_suffixes=[t.suffix for t in ant2_missed_tags],
            ant2_missed_labels=[t.label for t in ant2_missed_tags],
            ant2_missed_locations=[t.location for t in ant2_missed_tags]
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
                active_antennas=active_antennas,
                ant1_seen=t1["seen"],
                ant1_rssi=t1["rssi"],
                ant1_count=t1["count"],
                ant1_phase=t1["phase"],
                ant2_seen=t2["seen"],
                ant2_rssi=t2["rssi"],
                ant2_count=t2["count"],
                ant2_phase=t2["phase"]
            ))
        
        raw = {
            "ant1_epcs": set(inv1.keys()),
            "ant2_epcs": set(inv2.keys()),
            "ant1_targets": ant1_targets,
            "ant2_targets": ant2_targets
        }
        
        return step_result, tag_steps, raw
