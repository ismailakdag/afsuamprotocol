"""
MCU Controller for AFSUAM Measurement System.

This module provides the MCUController class for serial communication
with the microcontroller that controls the phase shifters.
"""

import serial
import serial.tools.list_ports
from typing import Optional, List, Tuple
import time


class MCUError(Exception):
    """Base exception for MCU errors."""
    pass


class MCUConnectionError(MCUError):
    """Raised when MCU connection fails."""
    pass


class MCUController:
    """
    Controller for phase shifter MCU via serial connection.
    
    This class provides:
    - Automatic port detection with priority ordering
    - Connection management
    - Voltage setting commands for beam steering
    """
    
    # Preferred ports (ordered by priority)
    PREFERRED_PORTS = [
        "/dev/cu.usbmodem1201",
        "/dev/cu/.usbmodem1201",
        "COM3",
        "COM4",
        "COM5"
    ]
    
    def __init__(
        self,
        port: Optional[str] = None,
        baud_rate: int = 115200,
        timeout: float = 0.1
    ):
        """
        Initialize MCU controller.
        
        Args:
            port: Serial port (auto-detect if None)
            baud_rate: Serial baud rate
            timeout: Read timeout in seconds
        """
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        
        self._serial: Optional[serial.Serial] = None
        
        # Voltage limits
        self.voltage_min = 0.0
        self.voltage_max = 8.5
    
    @property
    def is_connected(self) -> bool:
        """Check if MCU is connected."""
        return self._serial is not None and self._serial.is_open
    
    @staticmethod
    def list_ports() -> List[str]:
        """List all available serial ports."""
        return [p.device for p in serial.tools.list_ports.comports()]
    
    @classmethod
    def find_preferred_port(cls, available_ports: Optional[List[str]] = None) -> Optional[str]:
        """
        Find the preferred MCU port from available ports.
        
        Args:
            available_ports: List of available ports (auto-detect if None)
        
        Returns:
            Preferred port or first available, None if no ports
        """
        if available_ports is None:
            available_ports = cls.list_ports()
        
        if not available_ports:
            return None
        
        # Check exact matches first
        for preferred in cls.PREFERRED_PORTS:
            if preferred in available_ports:
                return preferred
        
        # Check partial matches (e.g., contains 'usbmodem1201')
        for port in available_ports:
            if "usbmodem1201" in port:
                return port
        
        # Return first available
        return available_ports[0]
    
    def connect(self, port: Optional[str] = None) -> bool:
        """
        Connect to MCU.
        
        Args:
            port: Serial port (uses stored port or auto-detect if None)
        
        Returns:
            True if connection successful
        
        Raises:
            MCUConnectionError: If connection fails
        """
        if port:
            self.port = port
        elif self.port is None:
            self.port = self.find_preferred_port()
        
        if self.port is None:
            raise MCUConnectionError("No serial port detected/selected")
        
        try:
            self._serial = serial.Serial(
                self.port,
                self.baud_rate,
                timeout=self.timeout
            )
            print(f"MCU connected: {self.port}")
            return True
            
        except serial.SerialException as e:
            raise MCUConnectionError(f"Connection failed: {e}")
    
    def disconnect(self):
        """Disconnect from MCU, resetting voltages to 0."""
        if self._serial and self._serial.is_open:
            try:
                # Reset voltages before disconnecting
                self.set_voltage(0.0, 0.0)
                time.sleep(0.05)
                self._serial.close()
                print("MCU disconnected")
            except Exception as e:
                print(f"Disconnect error: {e}")
        
        self._serial = None
    
    def set_voltage(self, v_ch1: float, v_ch2: float) -> bool:
        """
        Set voltages on both channels.
        
        Args:
            v_ch1: Channel 1 voltage (clamped to 0-8.5V)
            v_ch2: Channel 2 voltage (clamped to 0-8.5V)
        
        Returns:
            True if command sent successfully
        """
        if not self.is_connected:
            print("MCU not connected: voltages not applied")
            return False
        
        # Clamp voltages
        v_ch1 = max(self.voltage_min, min(self.voltage_max, v_ch1))
        v_ch2 = max(self.voltage_min, min(self.voltage_max, v_ch2))
        
        try:
            cmd = f"SET1:{v_ch1:.3f}\nSET2:{v_ch2:.3f}\n"
            self._serial.write(cmd.encode())
            return True
        except Exception as e:
            print(f"Serial write error: {e}")
            return False
    
    def set_channel(self, channel: int, voltage: float) -> bool:
        """
        Set voltage on a single channel.
        
        Args:
            channel: Channel number (1 or 2)
            voltage: Voltage value (clamped to 0-8.5V)
        
        Returns:
            True if command sent successfully
        """
        if not self.is_connected:
            return False
        
        voltage = max(self.voltage_min, min(self.voltage_max, voltage))
        
        try:
            cmd = f"SET{channel}:{voltage:.3f}\n"
            self._serial.write(cmd.encode())
            return True
        except Exception as e:
            print(f"Serial write error: {e}")
            return False
    
    def reset_voltages(self) -> bool:
        """Reset both channels to 0V."""
        return self.set_voltage(0.0, 0.0)
    
    def send_raw(self, command: str) -> bool:
        """
        Send raw command to MCU.
        
        Args:
            command: Raw command string (newline appended if missing)
        
        Returns:
            True if command sent successfully
        """
        if not self.is_connected:
            return False
        
        if not command.endswith("\n"):
            command += "\n"
        
        try:
            self._serial.write(command.encode())
            return True
        except Exception as e:
            print(f"Serial write error: {e}")
            return False
