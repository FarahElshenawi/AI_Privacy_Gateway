"""Pytest configuration — ensures `app` package is importable from tests."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))