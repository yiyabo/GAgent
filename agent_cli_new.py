#\!/usr/bin/env python3
"""
New modular CLI entry point.
"""

import sys
import os

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
