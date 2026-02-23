"""
SWE-PLUS Common Utility Library

This module provides utility functions shared across three sub-repositories:
- mini-swe-agent/
- swe-bench/
- SWE-bench_Pro-os/

Version: v0.1.0
Last updated: 2026-02-14
"""

__version__ = "0.1.0"

# Export commonly used utilities
from .preds_manager import ResultManager

__all__ = ["ResultManager"]
