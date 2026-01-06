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
                
                # Store in results (using union_results for compatibility)
                from .base import UnionResult
                union = UnionResult(
                    timestamp=inv_result.timestamp,
                    repeat=repeat,
                    port_config=0,
                    dwell_s=dwell_s,
                    ant2_unique_epcs=inv_result.tags_seen,
                    ant2_targets_seen=inv_result.tags_seen
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
