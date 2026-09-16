"""Disable outbound heartbeat pushes during unit tests."""

import os

os.environ["HEARTBEAT_PUSH"] = "0"
