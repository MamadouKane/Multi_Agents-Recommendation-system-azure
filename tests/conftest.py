"""Test-wide safety: no test may export telemetry to a real Application Insights resource.

Environment variables win over `.env` in pydantic-settings, so an empty value here beats the
connection string that `make env` writes for local runs.
"""

import os

os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = ""
