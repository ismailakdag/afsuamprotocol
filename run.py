#!/usr/bin/env python3
"""
AFSUAM Measurement System - Entry Point

Run this script to start the measurement application.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.app import main

if __name__ == "__main__":
    main()
