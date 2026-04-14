"""
Upload a test run log file to S3 and return the S3 key.

Designed to run on the test PC before calling DashboardClient.submit().
AWS credentials are picked up from the environment automatically (same
credential chain used by the provisioning flow).

Usage:
    from log_uploader import LogUploader

    uploader = LogUploader()
    s3_key = uploader.upload(
        log_path="/home/paul/Projects/automated_testing/ev_sim/logs/auto_loop_20240715_093012.log",
        serial_number="SN123456",
        run_id=42,          # optional — omit before the run_id is known, attach later
    )
    # pass s3_key to DashboardClient.submit(log_s3_key=s3_key)
"""
import gzip
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "emporia-mfg-logs")
AWS_REGION    = os.environ.get("AWS_REGION",    "us-east-1")


class LogUploader:
    def __init__(
        self,
        bucket: str = AWS_S3_BUCKET,
        region: str = AWS_REGION,
        compress: bool = True,
    ):
        self.bucket   = bucket
        self.region   = region
        self.compress = compress

        try:
            import boto3
            self._s3 = boto3.client("s3", region_name=region)
        except ImportError as exc:
            raise RuntimeError("boto3 is required for log uploads: pip install boto3") from exc

    def upload(
        self,
        log_path: str | Path,
        serial_number: str,
        run_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        Upload log_path to S3.  Returns the S3 key on success, None on failure
        (never raises — a missing log should not block test result reporting).

        S3 key structure:
            logs/{YYYY}/{MM}/{serial}/{run_id_or_timestamp}.log[.gz]
        """
        log_path = Path(log_path)
        if not log_path.exists():
            logger.warning("Log file not found, skipping upload: %s", log_path)
            return None

        now = datetime.now(timezone.utc)
        stem = str(run_id) if run_id is not None else now.strftime("%Y%m%d_%H%M%S")
        ext  = ".log.gz" if self.compress else ".log"
        key  = f"logs/{now.year}/{now.month:02d}/{serial_number}/{stem}{ext}"

        try:
            if self.compress:
                data = gzip.compress(log_path.read_bytes(), compresslevel=6)
                self._s3.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType="text/plain",
                    ContentEncoding="gzip",
                    Metadata={"serial": serial_number},
                )
            else:
                self._s3.upload_file(
                    str(log_path),
                    self.bucket,
                    key,
                    ExtraArgs={
                        "ContentType": "text/plain",
                        "Metadata": {"serial": serial_number},
                    },
                )
            logger.info("Log uploaded to s3://%s/%s", self.bucket, key)
            return key

        except Exception as exc:
            logger.warning("Log upload failed (non-fatal): %s", exc)
            return None
