"""
Beam Steering Look-Up Table (LUT) Engines.

This module provides LUT classes for converting beam angles to
phase shifter control voltages.
"""

import os
from typing import Tuple, Dict, List, Optional
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d


class LUTError(Exception):
    """Base exception for LUT errors."""
    pass


class LUTLoadError(LUTError):
    """Raised when LUT file cannot be loaded."""
    pass


class CorrectedBeamLUT:
    """
    Beam steering LUT with Port_Config support.
    
    This class handles the corrected_lut_final.csv format which contains
    voltage mappings for different port configurations.
    
    Attributes:
        loaded (bool): Whether LUT was loaded successfully
        csv_path (str): Path to the LUT CSV file
    """
    
    def __init__(self, csv_path: str = "corrected_lut_final.csv"):
        """
        Initialize LUT from CSV file.
        
        Args:
            csv_path: Path to LUT CSV file
        """
        self.loaded = False
        self.csv_path = csv_path
        self.df: Optional[pd.DataFrame] = None
        
        self._config_0 = pd.DataFrame()
        self._config_1 = pd.DataFrame()
        self._interp: Dict[int, Dict[str, interp1d]] = {0: {}, 1: {}}
        
        self._load()
    
    def _load(self):
        """Load and process LUT file."""
        try:
            if not os.path.exists(self.csv_path):
                print(f"WARNING: LUT file not found: {self.csv_path}")
                return
            
            self.df = pd.read_csv(self.csv_path)
            self.df.columns = [c.strip() for c in self.df.columns]
            
            # Split by port config
            self._config_0 = self.df[self.df["Port_Config"] == 0].copy()
            self._config_1 = self.df[self.df["Port_Config"] == 1].copy()
            
            # Build interpolators
            for config_num, config_df in [(0, self._config_0), (1, self._config_1)]:
                if not config_df.empty:
                    angles = config_df["Angle_Cmd_Deg"].values
                    v_ch1 = config_df["V_CH1"].values
                    v_ch2 = config_df["V_CH2"].values
                    
                    self._interp[config_num]["V_CH1"] = interp1d(
                        angles, v_ch1, kind="linear", fill_value="extrapolate"
                    )
                    self._interp[config_num]["V_CH2"] = interp1d(
                        angles, v_ch2, kind="linear", fill_value="extrapolate"
                    )
            
            self.loaded = True
            print(f"LUT Loaded: Config 0 has {len(self._config_0)} points, "
                  f"Config 1 has {len(self._config_1)} points")
                  
        except Exception as e:
            print(f"Error loading LUT: {e}")
            import traceback
            traceback.print_exc()
    
    def get_voltages(self, port_config: int, target_angle: float) -> Tuple[float, float]:
        """
        Get voltages for given port config and angle.
        
        Args:
            port_config: Port configuration (0 or 1)
            target_angle: Target beam angle in degrees
        
        Returns:
            Tuple of (V_CH1, V_CH2)
        """
        if not self.loaded:
            return 0.0, 0.0
        
        config = port_config if port_config in [0, 1] else 0
        if config not in self._interp or not self._interp[config]:
            return 0.0, 0.0
        
        try:
            v1 = float(self._interp[config]["V_CH1"](target_angle))
            v2 = float(self._interp[config]["V_CH2"](target_angle))
            
            # Clamp to valid range
            v1 = max(0.0, min(8.5, v1))
            v2 = max(0.0, min(8.5, v2))
            return v1, v2
            
        except Exception as e:
            print(f"Interpolation error: {e}")
            return 0.0, 0.0
    
    def get_available_angles(self, port_config: int) -> List[float]:
        """
        Get list of available angles for port config.
        
        Args:
            port_config: Port configuration (0 or 1)
        
        Returns:
            Sorted list of available angles
        """
        config_df = self._config_0 if port_config == 0 else self._config_1
        if config_df.empty:
            return []
        return sorted(config_df["Angle_Cmd_Deg"].unique().tolist())
    
    def get_beam_presets(self, port_config: int) -> Dict[str, float]:
        """
        Get LEFT/CENTER/RIGHT angle presets from LUT coverage.
        
        Args:
            port_config: Port configuration (0 or 1)
        
        Returns:
            Dict with LEFT, CENTER, RIGHT angles
        """
        angles = self.get_available_angles(port_config)
        if not angles:
            return {"LEFT": 30.0, "CENTER": 0.0, "RIGHT": -30.0}
        
        return {
            "LEFT": max(angles),
            "CENTER": min(angles, key=abs),
            "RIGHT": min(angles),
        }
    
    def get_angle_range(self, port_config: int) -> Tuple[float, float]:
        """
        Get min/max angle range for port config.
        
        Args:
            port_config: Port configuration (0 or 1)
        
        Returns:
            Tuple of (min_angle, max_angle)
        """
        angles = self.get_available_angles(port_config)
        if not angles:
            return (-30.0, 30.0)
        return (min(angles), max(angles))


class PhaseLUT:
    """
    Phase-to-voltage LUT for individual phase shifter channels.
    
    This class provides conversion between phase angles and
    control voltages using measured calibration data.
    """
    
    def __init__(self, csv_path: str = "phase_lut.csv"):
        """
        Initialize Phase LUT.
        
        Args:
            csv_path: Path to phase LUT CSV file
        """
        self.csv_path = csv_path
        self.loaded = False
        
        self._voltage: np.ndarray = np.array([])
        self._phase1: np.ndarray = np.array([])
        self._phase4: np.ndarray = np.array([])
        
        self._func_p1_to_v: Optional[interp1d] = None
        self._func_p4_to_v: Optional[interp1d] = None
        self._func_v_to_p1: Optional[interp1d] = None
        self._func_v_to_p4: Optional[interp1d] = None
        
        self._load()
    
    def _load(self):
        """Load and process phase LUT file."""
        try:
            if os.path.exists(self.csv_path):
                df = pd.read_csv(self.csv_path)
                print(f"Loaded {self.csv_path} successfully.")
            else:
                # Fallback data
                print(f"Warning: {self.csv_path} not found. Using fallback data.")
                import io
                fallback_data = """Control Voltage (V),Olcum1_Shift,Olcum2_Shift
0.0,0.0,0.0
8.5,363.42,363.43"""
                df = pd.read_csv(io.StringIO(fallback_data))
            
            self._voltage = df['Control Voltage (V)'].values
            
            if 'Olcum1_Shift' in df.columns and 'Olcum2_Shift' in df.columns:
                self._phase1 = df['Olcum1_Shift'].values
                self._phase4 = df['Olcum2_Shift'].values
            else:
                self._phase1 = df.iloc[:, 1].values
                self._phase4 = df.iloc[:, 1].values
            
            # Build interpolators
            self._func_p1_to_v = interp1d(
                self._phase1, self._voltage, 
                kind='linear', fill_value="extrapolate"
            )
            self._func_p4_to_v = interp1d(
                self._phase4, self._voltage, 
                kind='linear', fill_value="extrapolate"
            )
            self._func_v_to_p1 = interp1d(
                self._voltage, self._phase1, 
                kind='linear', fill_value="extrapolate"
            )
            self._func_v_to_p4 = interp1d(
                self._voltage, self._phase4, 
                kind='linear', fill_value="extrapolate"
            )
            
            self.loaded = True
            
        except Exception as e:
            print(f"Error loading Phase LUT: {e}")
    
    def get_voltage(self, target_phase: float, channel: int = 1) -> float:
        """
        Convert phase to voltage.
        
        Args:
            target_phase: Target phase in degrees
            channel: Channel number (1 or 4)
        
        Returns:
            Voltage value (clamped to 0-8.5V)
        """
        if not self.loaded:
            return 0.0
        
        if channel == 4:
            v = float(self._func_p4_to_v(target_phase))
        else:
            v = float(self._func_p1_to_v(target_phase))
        
        return max(0.0, min(8.5, v))
    
    def get_phase(self, voltage: float, channel: int = 1) -> float:
        """
        Convert voltage to phase.
        
        Args:
            voltage: Control voltage (clamped to 0-8.5V)
            channel: Channel number (1 or 4)
        
        Returns:
            Phase in degrees (0-360)
        """
        if not self.loaded:
            return 0.0
        
        v = max(0.0, min(8.5, float(voltage)))
        
        if channel == 4:
            return float(self._func_v_to_p4(v)) % 360.0
        return float(self._func_v_to_p1(v)) % 360.0
