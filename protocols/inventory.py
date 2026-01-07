"""
Simple Inventory Protocol Implementation.

This module implements a simple inventory collection protocol
without beam steering (for Ant2-only mode).
"""

import time
from typing import Dict, List
from dataclasses import dataclass, field

from .base import BaseProtocol, ProtocolResult


@dataclass
class SimpleInventoryResult:
    """Result from simple inventory collection."""
    timestamp: str = ""
    repeat: int = 0
    dwell_s: float = 0.0
    
    tags_seen: int = 0
    tags_total: int = 0
    total_reads: int = 0
    
    rssi_min: float = 0.0
    rssi_max: float = 0.0
    rssi_avg: float = 0.0
    
    tag_details: List[Dict] = field(default_factory=list)


class SimpleInventoryProtocol(BaseProtocol):
    """
    Simple Inventory Protocol.
    
    Collects tag data without beam steering. Suitable for
    single antenna or reference antenna measurements.
    """
    
    def run(
        self,
        station_name: str = "AFSUAM Test-Bed",
        ref_antenna_name: str = "REF_ANT",
        dwell_s: float = 3.0,
        repeats: int = 3,
        active_antennas: List[int] = None
    ) -> ProtocolResult:
        """
        Execute simple inventory protocol.
        
        Args:
            station_name: Name of measurement station
            ref_antenna_name: Name of reference antenna
            dwell_s: Dwell time per collection in seconds
            repeats: Number of collection repeats
            active_antennas: List of active antenna ports
        
        Returns:
            ProtocolResult with collected data
        """
        if active_antennas is None:
            active_antennas = [2]
            
        # Determine antenna health/status
        ant1_enabled = 1 in active_antennas
        ant2_enabled = 2 in active_antennas
        
        # Generate unique run ID
        import uuid
        run_id = f"{self._get_timestamp().replace(':', '').replace('-', '').replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
        
        # Validation errors list
        validation_errors = []
        
        result = ProtocolResult(
            station_name=station_name,
            ref_antenna_name=ref_antenna_name,
            start_time=self._get_timestamp(),
            run_id=run_id,
            # Protocol configuration
            protocol_dwell_s=dwell_s,
            protocol_repeats=repeats,
            port_config=0,
            beam_sequence="OMNI",
            active_antennas=active_antennas,
            tie_break_rule="NONE",
            # Target configuration
            targets_configured_suffixes=self.tag_manager.suffixes.copy(),
            targets_configured_labels=[t.label for t in self.tag_manager.tags],
            # Hardware status
            mcu_connected=False,  # Simple inventory doesn't use MCU
            reader_connected=self.reader.connected if self.reader else False,
            port2_enabled=ant2_enabled,
            # Antenna health
            ant1_health="OK" if ant1_enabled else "DISABLED",
            ant2_health="OK" if ant2_enabled else "DISABLED",
            ant1_warning="" if ant1_enabled else "Antenna 1 disabled by user",
            ant2_warning="" if ant2_enabled else "Antenna 2 disabled by user",
            # Data validation
            data_valid=True,
            validation_errors=validation_errors
        )
        
        # Validate prerequisites
        if not self.reader.connected:
            result.success = False
            result.error_message = "Reader is not connected"
            return result
        
        try:
            for repeat in range(1, repeats + 1):
                if self._stop_requested:
                    break
                
                progress = repeat / repeats
                self._update_progress(f"Repeat {repeat}/{repeats}", progress)
                
                # Collect data
                inv_result = self._collect_inventory(
                    repeat=repeat,
                    dwell_s=dwell_s,
                    active_antennas=active_antennas
                )
                
                # --- Create StepResult ---
                from .base import StepResult, TagStepResult
                
                # Calculate nontargets
                ant2_targets_seen_n = inv_result.tags_seen
                ant2_unique_epc_n = inv_result.tags_seen  # Assuming only known tags tracked for now
                
                step = StepResult(
                    timestamp=inv_result.timestamp,
                    beam_state="OMNI",
                    angle_deg=0.0,
                    dwell_s=dwell_s,
                    active_antennas=active_antennas,
                    tags_total=self.tag_manager.count,
                    # Ant2 stats
                    ant2_unique_epc_n=ant2_unique_epc_n,
                    ant2_targets_seen_n=ant2_targets_seen_n,
                    ant2_total_reads=inv_result.total_reads,
                    ant2_rssi_min=inv_result.rssi_min,
                    ant2_rssi_max=inv_result.rssi_max,
                    ant2_rssi_avg=inv_result.rssi_avg,
                    # Simple inventory misses
                    ant2_missed_suffixes=[t.suffix for t in self.tag_manager.get_missed_tags([self.tag_manager.get_tag(d["suffix"]) for d in inv_result.tag_details if d["seen"]])],
                    ant2_missed_labels=[t.label for t in self.tag_manager.get_missed_tags([self.tag_manager.get_tag(d["suffix"]) for d in inv_result.tag_details if d["seen"]])]
                )
                result.step_results.append(step)
                
                # --- Create TagStepResults ---
                for detail in inv_result.tag_details:
                    ts = TagStepResult(
                        timestamp=inv_result.timestamp,
                        beam_state="OMNI",
                        active_antennas=active_antennas,
                        tag_label=detail["label"],
                        tag_suffix=detail["suffix"],
                        tag_location=detail["location"],
                        # Ant2 data
                        ant2_seen=detail["seen"],
                        ant2_rssi=detail.get("rssi"),
                        ant2_count=detail.get("count"),
                        ant2_phase=detail.get("phase"),
                        angle_deg=0.0
                    )
                    result.tag_step_results.append(ts)
                
                # --- Process tag details for UnionResult ---
                ant2_best_beam = {}
                ant2_best_rssi = {}
                ant2_seen_beams_n = {}
                ant2_best_margin = {}
                ant2_tie_flag = {}
                ant2_best_confidence = {}
                
                seen_tags = []
                
                for detail in inv_result.tag_details:
                    suffix = detail["suffix"]
                    if detail["seen"]:
                        seen_tags.append(self.tag_manager.get_tag(suffix))
                        
                        # Populate Ant2 stats (assuming Ant2 active)
                        if ant2_enabled:
                            ant2_best_beam[suffix] = "OMNI"
                            ant2_best_rssi[suffix] = detail["rssi"]
                            ant2_seen_beams_n[suffix] = 1
                            ant2_best_margin[suffix] = None  # Single beam
                            ant2_tie_flag[suffix] = 0
                            ant2_best_confidence[suffix] = "SINGLE"
                    else:
                        # Not seen
                        if ant2_enabled:
                            ant2_seen_beams_n[suffix] = 0
                            ant2_best_confidence[suffix] = "NONE"
                
                # Missed tags calculation
                ant2_missed_tags = self.tag_manager.get_missed_tags(seen_tags) if ant2_enabled else []
                
                # Store in results
                from .base import UnionResult
                union = UnionResult(
                    timestamp=inv_result.timestamp,
                    repeat=repeat,
                    port_config=0,
                    dwell_s=dwell_s,
                    active_antennas=active_antennas,
                    tags_total=self.tag_manager.count,
                    ant2_unique_epcs=inv_result.tags_seen,
                    ant2_targets_seen=inv_result.tags_seen,
                    # Missed tags
                    ant2_missed_suffixes=[t.suffix for t in ant2_missed_tags],
                    ant2_missed_labels=[t.label for t in ant2_missed_tags],
                    # Per-tag stats
                    ant2_best_beam=ant2_best_beam,
                    ant2_best_rssi=ant2_best_rssi,
                    ant2_seen_beams_n=ant2_seen_beams_n,
                    ant2_best_margin=ant2_best_margin,
                    ant2_tie_flag=ant2_tie_flag,
                    ant2_best_confidence=ant2_best_confidence
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
    
    def _collect_inventory(
        self,
        repeat: int,
        dwell_s: float,
        active_antennas: List[int]
    ) -> SimpleInventoryResult:
        """Collect single inventory."""
        
        self.reader.clear_data()
        time.sleep(dwell_s)
        inventory = self.reader.get_all_data()
        
        # Filter by active antenna
        if 2 in active_antennas and 1 not in active_antennas:
            _, inventory = self._split_inventory_by_antenna(inventory)
        elif 1 in active_antennas and 2 not in active_antennas:
            inventory, _ = self._split_inventory_by_antenna(inventory)
        
        # Calculate statistics
        tag_details = []
        all_rssi = []
        total_reads = 0
        tags_seen = 0
        
        for tag in self.tag_manager.tags:
            tag_info = self._find_tag_info(inventory, tag.suffix)
            
            detail = {
                "label": tag.label,
                "suffix": tag.suffix,
                "location": tag.location,
                "seen": tag_info["seen"]
            }
            
            if tag_info["seen"]:
                detail["rssi"] = tag_info["rssi"]
                detail["count"] = tag_info["count"]
                detail["phase"] = tag_info["phase"]
                
                all_rssi.append(tag_info["rssi"])
                total_reads += tag_info["count"]
                tags_seen += 1
            
            tag_details.append(detail)
        
        # Calculate aggregates
        rssi_min = min(all_rssi) if all_rssi else 0.0
        rssi_max = max(all_rssi) if all_rssi else 0.0
        rssi_avg = sum(all_rssi) / len(all_rssi) if all_rssi else 0.0
        
        return SimpleInventoryResult(
            timestamp=self._get_timestamp(),
            repeat=repeat,
            dwell_s=dwell_s,
            tags_seen=tags_seen,
            tags_total=self.tag_manager.count,
            total_reads=total_reads,
            rssi_min=rssi_min,
            rssi_max=rssi_max,
            rssi_avg=rssi_avg,
            tag_details=tag_details
        )
