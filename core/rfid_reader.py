"""
RFID Reader Interface for AFSUAM Measurement System.

This module provides the RFIDReader class which wraps the SLLURP library
for communication with Impinj RFID readers using the LLRP protocol.
"""

import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any

# LLRP / SLLURP imports
try:
    from sllurp.llrp import (
        LLRPReaderConfig,
        LLRPReaderClient,
        LLRP_DEFAULT_PORT,
        LLRPReaderState
    )
    from twisted.internet import reactor
    SLLURP_AVAILABLE = True
except ImportError:
    SLLURP_AVAILABLE = False
    print("Warning: sllurp library not available. Reader functionality disabled.")


class RFIDReaderError(Exception):
    """Base exception for RFID reader errors."""
    pass


class ConnectionError(RFIDReaderError):
    """Raised when connection to reader fails."""
    pass


class RFIDReader:
    """
    RFID Reader interface using SLLURP/LLRP protocol.
    
    This class provides a high-level interface for:
    - Connecting/disconnecting from Impinj readers
    - Starting/stopping inventory
    - Collecting tag reports with RSSI, phase, and doppler data
    - Thread-safe inventory data access
    """
    
    def __init__(self):
        self.inventory: Dict[str, Dict] = {}
        self.connected: bool = False
        self.inventory_running: bool = False
        
        self._reader_client: Optional[Any] = None
        self._reactor_thread: Optional[threading.Thread] = None
        self._last_disconnect_time: float = 0
        self._lock = threading.Lock()
        
        # Callbacks
        self._on_tag_callback: Optional[Callable] = None
        self._on_state_change_callback: Optional[Callable] = None
    
    @staticmethod
    def is_available() -> bool:
        """Check if SLLURP library is available."""
        return SLLURP_AVAILABLE
    
    def set_on_tag_callback(self, callback: Callable[[str, Dict], None]):
        """Set callback for new tag reports. Called with (epc, tag_data)."""
        self._on_tag_callback = callback
    
    def set_on_state_change_callback(self, callback: Callable[[bool], None]):
        """Set callback for connection state changes. Called with (is_connected)."""
        self._on_state_change_callback = callback
    
    def connect(
        self,
        ip_address: str,
        power_dbm: float = 26.5,
        antennas: Optional[List[int]] = None,
        mode_identifier: int = 1002,
        session: int = 0,
        search_mode: str = "2"
    ) -> bool:
        """
        Connect to RFID reader.
        
        Args:
            ip_address: Reader IP address
            power_dbm: Transmit power in dBm (10-33)
            antennas: List of antenna ports to enable (e.g., [1], [2], [1,2])
            mode_identifier: Reader mode (1002=AutoSet DenseRdr, etc.)
            session: RFID session (0=Fast cycle, 2=Extended persist)
            search_mode: Impinj search mode ("2"=Dual Target Continuous)
        
        Returns:
            True if connection successful, False otherwise
        """
        if not SLLURP_AVAILABLE:
            print("SLLURP not available - cannot connect")
            return False
        
        if antennas is None:
            antennas = [1]
        
        # Throttle reconnection attempts
        time_since_disconnect = time.time() - self._last_disconnect_time
        if time_since_disconnect < 3.0:
            time.sleep(3.0 - time_since_disconnect)
        
        if self.connected:
            self.disconnect()
            time.sleep(1.0)
        
        try:
            # Calculate power index (10dBm=1, 33dBm=93, step=0.25dBm)
            power_idx = max(1, min(93, int((power_dbm - 10.0) / 0.25) + 1))
            
            factory_args = {
                "tx_power": power_idx,
                "mode_identifier": mode_identifier,
                "report_every_n_tags": 10,
                "start_inventory": True,
                "tag_content_selector": {
                    "EnableROSpecID": True,
                    "EnableAntennaID": True,
                    "EnablePeakRSSI": True,
                    "EnableFirstSeenTimestamp": True,
                    "EnableLastSeenTimestamp": True,
                    "EnableTagSeenCount": True,
                    "EnableRFDopplerFrequency": True,
                },
                "impinj_extended_configuration": True,
                "impinj_reports": True,
                "impinj_tag_content_selector": {
                    "EnableRFPhaseAngle": True,
                    "EnablePeakRSSI": True,
                    "EnableRFDopplerFrequency": True,
                    "EnableOptimizerOne": False,
                },
                "impinj_search_mode": str(search_mode),
                "session": session,
                "antennas": antennas,
            }
            
            config = LLRPReaderConfig(factory_args)
            self._reader_client = LLRPReaderClient(ip_address, LLRP_DEFAULT_PORT, config)
            self._reader_client.add_tag_report_callback(self._handle_tag_report)
            self._reader_client.add_state_callback(
                LLRPReaderState.STATE_CONNECTED, 
                self._handle_state_change
            )
            self._reader_client.add_state_callback(
                LLRPReaderState.STATE_DISCONNECTED, 
                self._handle_state_change
            )
            
            # Start reactor thread if not running
            if self._reactor_thread is None:
                self._reactor_thread = threading.Thread(
                    target=self._run_reactor, 
                    daemon=True
                )
                self._reactor_thread.start()
            
            self._reader_client.connect()
            print(f"Connecting to reader at {ip_address}...")
            print(f"  Power: {power_dbm} dBm (index {power_idx})")
            print(f"  Mode: {mode_identifier}, Session: {session}, Search: {search_mode}")
            print(f"  Antennas: {antennas}")
            return True
            
        except Exception as e:
            print(f"Connection error: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from reader."""
        if self._reader_client:
            try:
                self._reader_client.disconnect()
            except Exception as e:
                print(f"Disconnect error: {e}")
        
        self.connected = False
        self.inventory_running = False
        self._last_disconnect_time = time.time()
    
    def start_inventory(self):
        """Start/resume inventory collection."""
        self.inventory_running = True
        if self._reader_client:
            try:
                self._reader_client.update_config({'start_inventory': True})
            except Exception as e:
                print(f"Start inventory error: {e}")
    
    def stop_inventory(self):
        """Stop/pause inventory collection."""
        self.inventory_running = False
        if self._reader_client:
            try:
                self._reader_client.update_config({'start_inventory': False})
            except Exception as e:
                print(f"Stop inventory error: {e}")
    
    def clear_data(self):
        """Clear all collected inventory data."""
        with self._lock:
            self.inventory = {}
    
    def get_all_data(self) -> Dict[str, Dict]:
        """Get copy of all inventory data (thread-safe)."""
        with self._lock:
            return self.inventory.copy()
    
    def get_tag_data(self, epc: str) -> Optional[Dict]:
        """Get data for specific tag by EPC."""
        with self._lock:
            return self.inventory.get(epc)
    
    def get_tags_by_antenna(self, antenna_id: int) -> Dict[str, Dict]:
        """Get all tags seen by specific antenna."""
        with self._lock:
            return {
                epc: data for epc, data in self.inventory.items()
                if data.get("antenna", 1) == antenna_id
            }
    
    def _run_reactor(self):
        """Run Twisted reactor in background thread."""
        try:
            if not reactor.running:
                reactor.run(installSignalHandlers=False)
        except Exception as e:
            print(f"Reactor error: {e}")
    
    def _handle_state_change(self, reader, state):
        """Handle reader state changes."""
        if state == LLRPReaderState.STATE_CONNECTED:
            self.connected = True
            self.inventory_running = True
            print("Reader connected successfully")
        elif state == LLRPReaderState.STATE_DISCONNECTED:
            self.connected = False
            self.inventory_running = False
            print("Reader disconnected")
        
        if self._on_state_change_callback:
            self._on_state_change_callback(self.connected)
    
    def _handle_tag_report(self, reader, tag_reports):
        """Handle incoming tag reports."""
        if not self.inventory_running:
            return
        
        for tag in tag_reports:
            try:
                tag_data = self._parse_tag_report(tag)
                if tag_data:
                    epc = tag_data["epc"]
                    
                    with self._lock:
                        # Update count if tag exists
                        prev_count = self.inventory.get(epc, {}).get("count", 0)
                        tag_data["count"] = prev_count + 1
                        self.inventory[epc] = tag_data
                    
                    if self._on_tag_callback:
                        self._on_tag_callback(epc, tag_data)
                        
            except Exception as e:
                print(f"Tag parse error: {e}")
    
    def _parse_tag_report(self, tag: Dict) -> Optional[Dict]:
        """Parse raw tag report into structured data."""
        # Extract EPC
        epc_raw = tag.get("EPC-96") or tag.get("EPCUnknown")
        if not epc_raw:
            return None
        
        if isinstance(epc_raw, bytes):
            try:
                epc = epc_raw.decode("utf-8").upper()
            except Exception:
                epc = epc_raw.hex().upper()
        else:
            epc = str(epc_raw).upper()
        
        # Extract RSSI
        rssi = float(tag.get("ImpinjPeakRSSI", tag.get("PeakRSSI", -90)))
        if rssi < -150:  # Impinj high-res RSSI (x100)
            rssi = rssi / 100.0
        
        # Extract Phase
        phase = self._extract_phase(tag)
        
        # Extract other fields
        doppler = float(tag.get("RFDopplerFrequency", 
                                tag.get("DopplerFrequency", 
                                tag.get("ImpinjRFDopplerFrequency", 0.0))))
        antenna_id = tag.get("AntennaID", 1)
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        
        return {
            "epc": epc,
            "rssi": rssi,
            "phase": phase,
            "doppler": doppler,
            "antenna": antenna_id,
            "timestamp": timestamp,
            "seen_time": time.time(),
            "count": 1
        }
    
    def _extract_phase(self, tag: Dict) -> float:
        """Extract phase angle from tag report."""
        def get_val(obj, keys, default=None):
            for k in keys:
                if k in obj:
                    v = obj[k]
                    if isinstance(v, dict):
                        return v.get("Value", default)
                    return v
            return default
        
        phase_keys = ["ImpinjRFPhaseAngle", "RFPhaseAngle", "PhaseAngle", "Phase"]
        p_val = get_val(tag, phase_keys)
        
        # Check Custom field as fallback
        if p_val is None and "Custom" in tag:
            for item in tag["Custom"]:
                if isinstance(item, dict):
                    p_val = get_val(item, phase_keys)
                    if p_val is not None:
                        break
        
        if p_val is not None:
            try:
                v_final = p_val.get("Value") if isinstance(p_val, dict) else p_val
                return (float(v_final) / 4096.0 * 360.0) % 360.0
            except Exception:
                pass
        
        return 0.0
