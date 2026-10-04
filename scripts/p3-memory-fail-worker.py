#!/usr/bin/env python3
"""Fixed failed Memory process for the explicit isolated G06 fixture."""
import sys
# Consume the bounded request so the caller observes an actual process failure.
sys.stdin.buffer.read(65537)
raise SystemExit(2)
