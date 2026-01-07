"""
Base Protocol interface for AFSUAM Measurement System.

This module defines the base protocol class and result structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime


@dataclass
class StepResult:
    """Result from a single protocol step."""
    timestamp: str
    beam_state: str
    angle_deg: float
    port_config: int
    v_ch1: float
    v_ch2: float
    dwell_s: float
    
    # Active antennas for this step
    active_antennas: List[int] = field(default_factory=lambda: [1, 2])
    
    # Target tag totals
    tags_total: int = 0
    
    # Antenna 1 results
    ant1_unique_epc_n: int = 0
    ant1_targets_seen_n: int = 0
    ant1_target_data: List[Dict] = field(default_factory=list)
    ant1_total_reads: int = 0
    ant1_rssi_min: Optional[float] = None
    ant1_rssi_max: Optional[float] = None
    ant1_rssi_avg: Optional[float] = None
    ant1_missed: List[str] = field(default_factory=list)
    ant1_missed_suffixes: List[str] = field(default_factory=list)
    ant1_missed_labels: List[str] = field(default_factory=list)
    ant1_missed_locations: List[str] = field(default_factory=list)
    
    # Antenna 2 results  
    ant2_unique_epc_n: int = 0
    ant2_targets_seen_n: int = 0
    ant2_target_data: List[Dict] = field(default_factory=list)
    ant2_total_reads: int = 0
    ant2_rssi_min: Optional[float] = None
    ant2_rssi_max: Optional[float] = None
    ant2_rssi_avg: Optional[float] = None
    ant2_missed: List[str] = field(default_factory=list)
    ant2_missed_suffixes: List[str] = field(default_factory=list)
    ant2_missed_labels: List[str] = field(default_factory=list)
    ant2_missed_locations: List[str] = field(default_factory=list)


@dataclass
class TagStepResult:
    """Per-tag result for a protocol step."""
    timestamp: str
    beam_state: str
    tag_label: str
    tag_suffix: str
    tag_location: str
    
    # Active antennas for this step
    active_antennas: List[int] = field(default_factory=lambda: [1, 2])
    
    ant1_seen: bool = False
    ant1_rssi: Optional[float] = None
    ant1_count: int = 0
    ant1_phase: Optional[float] = None
    
    ant2_seen: bool = False
    ant2_rssi: Optional[float] = None
    ant2_count: int = 0
    ant2_phase: Optional[float] = None


@dataclass
class UnionResult:
    """Union (aggregate) result across all steps in a repeat."""
    timestamp: str
    repeat: int
    port_config: int
    dwell_s: float
    
    # Active antennas
    active_antennas: List[int] = field(default_factory=lambda: [1, 2])
    tags_total: int = 0
    
    # Union coverage
    ant1_unique_epcs: int = 0
    ant2_unique_epcs: int = 0
    ant1_targets_seen: int = 0
    ant2_targets_seen: int = 0
    
    # Missed tags - separated
    ant1_missed: List[str] = field(default_factory=list)
    ant2_missed: List[str] = field(default_factory=list)
    ant1_missed_suffixes: List[str] = field(default_factory=list)
    ant1_missed_labels: List[str] = field(default_factory=list)
    ant2_missed_suffixes: List[str] = field(default_factory=list)
    ant2_missed_labels: List[str] = field(default_factory=list)
    
    # Best beam per tag (suffix -> beam_state)
    ant1_best_beam: Dict[str, str] = field(default_factory=dict)
    ant2_best_beam: Dict[str, str] = field(default_factory=dict)
    
    # Best RSSI per tag (suffix -> rssi)
    ant1_best_rssi: Dict[str, float] = field(default_factory=dict)
    ant2_best_rssi: Dict[str, float] = field(default_factory=dict)
    
    # Seen beams count per tag (suffix -> count of beams that saw this tag)
    ant1_seen_beams_n: Dict[str, int] = field(default_factory=dict)
    ant2_seen_beams_n: Dict[str, int] = field(default_factory=dict)
    
    # Best beam margin (suffix -> margin_db, None if single beam)
    ant1_best_margin: Dict[str, Optional[float]] = field(default_factory=dict)
    ant2_best_margin: Dict[str, Optional[float]] = field(default_factory=dict)
    
    # Tie flag (suffix -> 1 if margin=0 tie, 0 otherwise)
    ant1_tie_flag: Dict[str, int] = field(default_factory=dict)
    ant2_tie_flag: Dict[str, int] = field(default_factory=dict)
    
    # Best beam confidence (suffix -> HIGH/MED/LOW/SINGLE/NONE)
    ant1_best_confidence: Dict[str, str] = field(default_factory=dict)
    ant2_best_confidence: Dict[str, str] = field(default_factory=dict)


@dataclass
class ProtocolResult:
    """Complete result from protocol execution."""
    success: bool = True
    error_message: str = ""
    
    # Run identification
    run_id: str = ""
    
    # Metadata
    station_name: str = ""
    ref_antenna_name: str = ""
    start_time: str = ""
    end_time: str = ""
    
    # Protocol configuration
    protocol_dwell_s: float = 3.0
    protocol_repeats: int = 3
    port_config: int = 0
    beam_sequence: str = "LEFT|CENTER|RIGHT"
    active_antennas: List[int] = field(default_factory=lambda: [1, 2])
    tie_break_rule: str = "prefer_higher_rssi"
    
    # Target configuration (for validation)
    targets_configured_suffixes: List[str] = field(default_factory=list)
    targets_configured_labels: List[str] = field(default_factory=list)
    
    # Hardware status
    mcu_connected: bool = False
    reader_connected: bool = False
    port2_enabled: bool = True
    
    # Antenna health diagnostics
    ant1_health: str = "OK"  # OK, NO_TAG_REPORTS, DISABLED
    ant2_health: str = "OK"
    ant1_warning: str = ""
    ant2_warning: str = ""
    
    # Data validation
    data_valid: bool = True
    validation_errors: List[str] = field(default_factory=list)
    
    # Optional notes
    notes: str = ""
    
    # Results
    step_results: List[StepResult] = field(default_factory=list)
    tag_step_results: List[TagStepResult] = field(default_factory=list)
    union_results: List[UnionResult] = field(default_factory=list)


class BaseProtocol(ABC):
    """
    Abstract base class for measurement protocols.
    
    All protocols should inherit from this class and implement
    the run() method.
    """
    
    def __init__(
        self,
        reader,  # RFIDReader
        mcu,     # MCUController
        lut,     # CorrectedBeamLUT
        tag_manager  # TagManager
    ):
        """
        Initialize protocol with required components.
        
        Args:
            reader: RFIDReader instance
            mcu: MCUController instance
            lut: CorrectedBeamLUT instance
            tag_manager: TagManager instance
        """
        self.reader = reader
        self.mcu = mcu
        self.lut = lut
        self.tag_manager = tag_manager
        
        self._stop_requested = False
        self._progress_callback: Optional[Callable[[str, float], None]] = None
    
    def set_progress_callback(self, callback: Callable[[str, float], None]):
        """
        Set callback for progress updates.
        
        Callback receives (status_message, progress_fraction)
        """
        self._progress_callback = callback
    
    def _update_progress(self, message: str, fraction: float = 0.0):
        """Report progress to callback if set."""
        if self._progress_callback:
            self._progress_callback(message, fraction)
    
    def stop(self):
        """Request protocol to stop."""
        self._stop_requested = True
    
    @abstractmethod
    def run(self, **kwargs) -> ProtocolResult:
        """
        Execute the protocol.
        
        Returns:
            ProtocolResult with execution results
        """
        pass
    
    def _split_inventory_by_antenna(self, inventory: Dict) -> tuple:
        """Split inventory data by antenna ID."""
        inv1, inv2 = {}, {}
        for epc, info in inventory.items():
            try:
                ant = int(info.get("antenna", 1))
            except Exception:
                ant = 1
            
            if ant == 2:
                inv2[epc] = info
            else:
                inv1[epc] = info
        
        return inv1, inv2
    
    def _find_tag_info(self, inventory: Dict, suffix: str) -> Dict:
        """Find tag info in inventory by suffix."""
        for epc, info in inventory.items():
            if epc.endswith(suffix):
                return {
                    "seen": True,
                    "epc": epc,
                    "rssi": float(info.get("rssi", -99.0)),
                    "count": int(info.get("count", 0)),
                    "phase": float(info.get("phase", 0.0))
                }
        return {"seen": False, "epc": "", "rssi": None, "count": 0, "phase": None}
    
    def _get_timestamp(self) -> str:
        """Get current timestamp string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
