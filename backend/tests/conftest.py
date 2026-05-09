"""Test configuration.

Default Bedrock mode is REPLAY so tests cannot make real Bedrock calls by accident.
"""

from __future__ import annotations

import os

os.environ.setdefault("BEDROCK_MODE", "replay")
