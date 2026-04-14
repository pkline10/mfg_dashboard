"""
Thin client for posting manufacturing test results to the dashboard.

Drop this file next to mfg_tests.py and call DashboardClient.submit() after
each suite run.  Set DASHBOARD_URL env var or pass it to the constructor.

Usage example (in mfg_tests.py):
    from ingest_client import DashboardClient
    client = DashboardClient()
    client.submit(suite_result)
"""
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://100.109.84.94:5000")


class DashboardClient:
    def __init__(self, base_url: str = DASHBOARD_URL, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def submit(
        self,
        serial_number: str,
        product: str,
        fixture_id: Optional[str],
        phase: str,
        started_at: datetime,
        ended_at: datetime,
        overall_pass: bool,
        failure_reason: Optional[str],
        results: list,
    ) -> Optional[int]:
        """
        Post a completed test run to /api/ingest.

        Parameters
        ----------
        results : list of dicts with keys:
            test_name, started_at (datetime), ended_at (datetime),
            duration_s (float), passed (bool), failure_reason (str|None),
            measurements (list of {metric, value, unit, tolerance_min,
                                   tolerance_max, passed})

        Returns run_id on success, None on failure (never raises).
        """
        payload = {
            "serial_number": serial_number,
            "product": product,
            "fixture_id": fixture_id,
            "phase": phase,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_s": (ended_at - started_at).total_seconds(),
            "overall_pass": overall_pass,
            "failure_reason": failure_reason,
            "results": [
                {
                    "test_name": r["test_name"],
                    "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
                    "ended_at":   r["ended_at"].isoformat()   if r.get("ended_at")   else None,
                    "duration_s": r.get("duration_s"),
                    "passed": r["passed"],
                    "failure_reason": r.get("failure_reason"),
                    "measurements": r.get("measurements", []),
                }
                for r in results
            ],
        }

        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.base_url}/api/ingest",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
                logger.info("Dashboard ingest OK — run_id=%s", data.get("run_id"))
                return data.get("run_id")
        except urllib.error.HTTPError as exc:
            logger.warning("Dashboard ingest HTTP error %s: %s", exc.code, exc.read())
        except Exception as exc:
            logger.warning("Dashboard ingest failed (non-fatal): %s", exc)
        return None
