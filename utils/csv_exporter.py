"""
CSV Export Utilities for AFSUAM Measurement System.
"""

import csv
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from protocols.base import ProtocolResult


class CSVExporter:
    """
    Exports protocol results to CSV files.
    """
    
    # STEP record headers
    STEP_HEADERS = [
        "record_type", "timestamp", "station", "ref_antenna_name", "repeat",
        "beam_state", "port_config", "angle_deg", "v_ch1", "v_ch2", "dwell_s",
        "active_antennas", "tags_total",
        "ant1_unique_epc_n", "ant2_unique_epc_n",
        "ant1_targets_seen_n", "ant2_targets_seen_n",
        "ant1_missed", "ant2_missed"
    ]
    
    # TAGSTEP record headers
    TAGSTEP_HEADERS = [
        "record_type", "timestamp", "station", "ref_antenna_name", "repeat",
        "beam_state", "port_config", "angle_deg", "dwell_s",
        "active_antennas",
        "tag_label", "tag_suffix", "tag_location",
        "ant1_seen", "ant1_rssi", "ant1_count",
        "ant2_seen", "ant2_rssi", "ant2_count"
    ]
    
    # UNION record headers
    UNION_HEADERS = [
        "record_type", "timestamp", "station", "ref_antenna_name", "repeat",
        "port_config", "dwell_s", "active_antennas", "tags_total",
        "ant1_unique_epcs", "ant2_unique_epcs",
        "ant1_targets_seen", "ant2_targets_seen",
        "ant1_missed", "ant2_missed",
        "ant1_best_beam", "ant2_best_beam"
    ]
    
    def __init__(self, output_dir: str = "."):
        """
        Initialize exporter.
        
        Args:
            output_dir: Directory for output files
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export_protocol_result(
        self,
        result: ProtocolResult,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Export protocol result to CSV file.
        
        Args:
            result: ProtocolResult to export
            filename: Output filename (auto-generated if None)
            metadata: Additional metadata to include
        
        Returns:
            Path to exported file
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            station = result.station_name.replace(" ", "_")
            filename = f"protocol_{station}_{timestamp}.csv"
        
        filepath = self.output_dir / filename
        
        # Build metadata
        run_metadata = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "station": result.station_name,
            "ref_antenna": result.ref_antenna_name,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "success": str(result.success),
            **(metadata or {})
        }
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            
            # Write metadata
            writer.writerow(["# RUN_METADATA"])
            for key, value in run_metadata.items():
                writer.writerow([f"# {key}", value])
            writer.writerow([])
            
            # Write step results
            if result.step_results:
                writer.writerow(["# STEP_ROWS"])
                writer.writerow(self.STEP_HEADERS)
                for step in result.step_results:
                    row = self._step_to_row(step, result)
                    writer.writerow(row)
                writer.writerow([])
            
            # Write tag step results
            if result.tag_step_results:
                writer.writerow(["# TAGSTEP_ROWS"])
                writer.writerow(self.TAGSTEP_HEADERS)
                for ts in result.tag_step_results:
                    row = self._tagstep_to_row(ts, result)
                    writer.writerow(row)
                writer.writerow([])
            
            # Write union results
            if result.union_results:
                writer.writerow(["# UNION_ROWS"])
                writer.writerow(self.UNION_HEADERS)
                for union in result.union_results:
                    row = self._union_to_row(union, result)
                    writer.writerow(row)
        
        print(f"Exported to: {filepath}")
        return str(filepath)
    
    def _step_to_row(self, step, result: ProtocolResult) -> List:
        """Convert StepResult to CSV row."""
        return [
            "STEP",
            step.timestamp,
            result.station_name,
            result.ref_antenna_name,
            "",  # repeat - not tracked in step
            step.beam_state,
            step.port_config,
            step.angle_deg,
            step.v_ch1,
            step.v_ch2,
            step.dwell_s,
            "",  # active_antennas
            "",  # tags_total
            step.ant1_unique_epc_n,
            step.ant2_unique_epc_n,
            step.ant1_targets_seen_n,
            step.ant2_targets_seen_n,
            "|".join(step.ant1_missed),
            "|".join(step.ant2_missed)
        ]
    
    def _tagstep_to_row(self, ts, result: ProtocolResult) -> List:
        """Convert TagStepResult to CSV row."""
        return [
            "TAGSTEP",
            ts.timestamp,
            result.station_name,
            result.ref_antenna_name,
            "",  # repeat
            ts.beam_state,
            "",  # port_config
            "",  # angle_deg
            "",  # dwell_s
            "",  # active_antennas
            ts.tag_label,
            ts.tag_suffix,
            ts.tag_location,
            1 if ts.ant1_seen else 0,
            f"{ts.ant1_rssi:.1f}" if ts.ant1_rssi is not None else "",
            ts.ant1_count,
            1 if ts.ant2_seen else 0,
            f"{ts.ant2_rssi:.1f}" if ts.ant2_rssi is not None else "",
            ts.ant2_count
        ]
    
    def _union_to_row(self, union, result: ProtocolResult) -> List:
        """Convert UnionResult to CSV row."""
        return [
            "UNION",
            union.timestamp,
            result.station_name,
            result.ref_antenna_name,
            union.repeat,
            union.port_config,
            union.dwell_s,
            "",  # active_antennas
            "",  # tags_total
            union.ant1_unique_epcs,
            union.ant2_unique_epcs,
            union.ant1_targets_seen,
            union.ant2_targets_seen,
            "|".join(union.ant1_missed),
            "|".join(union.ant2_missed),
            "|".join(f"{k}:{v}" for k, v in union.ant1_best_beam.items()),
            "|".join(f"{k}:{v}" for k, v in union.ant2_best_beam.items())
        ]
    
    def export_live_snapshot(
        self,
        inventory: Dict[str, Dict],
        filename: Optional[str] = None,
        port_config: int = 0,
        angle: float = 0.0,
        v_ch1: float = 0.0,
        v_ch2: float = 0.0
    ) -> str:
        """
        Export live inventory snapshot to CSV.
        
        Args:
            inventory: Current inventory data
            filename: Output filename
            port_config: Current port configuration
            angle: Current beam angle
            v_ch1, v_ch2: Current voltages
        
        Returns:
            Path to exported file
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"live_snapshot_{timestamp}.csv"
        
        filepath = self.output_dir / filename
        
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "epc", "suffix", "count", "rssi", 
                "phase", "doppler", "antenna",
                "port_config", "angle_deg", "v_ch1", "v_ch2"
            ])
            
            for epc, info in inventory.items():
                writer.writerow([
                    info.get("timestamp", ""),
                    epc,
                    epc[-4:] if len(epc) >= 4 else epc,
                    info.get("count", 0),
                    info.get("rssi", -99.0),
                    info.get("phase", 0.0),
                    info.get("doppler", 0.0),
                    info.get("antenna", 1),
                    port_config,
                    angle,
                    f"{v_ch1:.3f}",
                    f"{v_ch2:.3f}"
                ])
        
        print(f"Exported snapshot: {filepath}")
        return str(filepath)
