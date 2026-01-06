"""
Logging utilities for AFSUAM Measurement System.
"""

from datetime import datetime
from typing import Optional, Callable


class Logger:
    """
    Simple logger with callback support for GUI integration.
    """
    
    def __init__(self, callback: Optional[Callable[[str], None]] = None):
        """
        Initialize logger.
        
        Args:
            callback: Optional callback for log messages (e.g., to update GUI)
        """
        self._callback = callback
        self._messages = []
    
    def set_callback(self, callback: Callable[[str], None]):
        """Set callback for log messages."""
        self._callback = callback
    
    def log(self, message: str, level: str = "INFO"):
        """
        Log a message.
        
        Args:
            message: Log message
            level: Log level (INFO, WARNING, ERROR)
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] [{level}] {message}"
        
        self._messages.append(formatted)
        print(formatted)
        
        if self._callback:
            self._callback(formatted)
    
    def info(self, message: str):
        """Log info message."""
        self.log(message, "INFO")
    
    def warning(self, message: str):
        """Log warning message."""
        self.log(message, "WARNING")
    
    def error(self, message: str):
        """Log error message."""
        self.log(message, "ERROR")
    
    def get_messages(self, count: int = 100) -> list:
        """Get recent log messages."""
        return self._messages[-count:]
    
    def clear(self):
        """Clear log messages."""
        self._messages = []
