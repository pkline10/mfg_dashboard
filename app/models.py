from datetime import datetime
from app import db


class TestRun(db.Model):
    """Top-level record for one full manufacturing suite run on a DUT."""
    __tablename__ = "test_runs"

    id = db.Column(db.Integer, primary_key=True)
    serial_number = db.Column(db.String(64), nullable=False, index=True)
    product = db.Column(db.String(8), nullable=False)   # C1, C2
    fixture_id = db.Column(db.String(32))               # e.g. "FCT-01", "BOX-01"
    phase = db.Column(db.String(16))                    # diagnostic, charging, fct
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    duration_s = db.Column(db.Float)
    overall_pass = db.Column(db.Boolean, nullable=False)
    failure_reason = db.Column(db.Text)                 # first failing test name or message
    log_s3_key = db.Column(db.String(256))              # e.g. logs/2024/07/SN123456/42.log

    results = db.relationship("TestResult", back_populates="run", cascade="all, delete-orphan")

    @property
    def week_key(self):
        return self.started_at.strftime("%Y-W%W")

    @property
    def month_key(self):
        return self.started_at.strftime("%Y-%m")


class TestResult(db.Model):
    """Result for one individual test within a run (intercom, buzzer, voltage, etc.)."""
    __tablename__ = "test_results"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    test_name = db.Column(db.String(64), nullable=False)
    started_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)
    duration_s = db.Column(db.Float)
    passed = db.Column(db.Boolean, nullable=False)
    failure_reason = db.Column(db.Text)

    run = db.relationship("TestRun", back_populates="results")
    measurements = db.relationship("Measurement", back_populates="test_result", cascade="all, delete-orphan")
    led_results = db.relationship("LedResult", back_populates="test_result", cascade="all, delete-orphan")


class Measurement(db.Model):
    """A single numeric measurement captured during a test (voltage, current, duty cycle, etc.)."""
    __tablename__ = "measurements"

    id = db.Column(db.Integer, primary_key=True)
    test_result_id = db.Column(db.Integer, db.ForeignKey("test_results.id", ondelete="CASCADE"), nullable=False, index=True)
    metric_name = db.Column(db.String(64), nullable=False)  # e.g. voltage_rms, current_rms, duty_cycle_pct
    value = db.Column(db.Float, nullable=False)
    nominal = db.Column(db.Float)                            # reference/expected value (e.g. Chroma reading); enables error_pct = (value-nominal)/nominal*100
    unit = db.Column(db.String(16))                          # V, A, %, Hz, dB
    tolerance_min = db.Column(db.Float)
    tolerance_max = db.Column(db.Float)
    passed = db.Column(db.Boolean)

    test_result = db.relationship("TestResult", back_populates="measurements")


class LedResult(db.Model):
    """Per-LED result from the FCT LED factory test."""
    __tablename__ = "led_results"

    id = db.Column(db.Integer, primary_key=True)
    test_result_id = db.Column(db.Integer, db.ForeignKey("test_results.id", ondelete="CASCADE"), nullable=False, index=True)
    led_name = db.Column(db.String(32), nullable=False)   # RED_FAULT, GREEN_GRID, etc.
    brightness = db.Column(db.Float)
    ratio = db.Column(db.Float)
    attempt = db.Column(db.Integer)
    status = db.Column(db.String(16))                     # PASS, MARGINAL, FAIL

    test_result = db.relationship("TestResult", back_populates="led_results")


class Fixture(db.Model):
    """A physical manufacturing fixture or station computer."""
    __tablename__ = "fixtures"

    fixture_id = db.Column(db.String(32), primary_key=True)
    display_name = db.Column(db.String(64))
    station_type = db.Column(db.String(16), nullable=False)  # fct, box, hipot
    site = db.Column(db.String(64), nullable=False, default="Guadalajara")
    line = db.Column(db.String(64))
    location = db.Column(db.String(128))
    hostname = db.Column(db.String(128), index=True)
    product_family = db.Column(db.String(32), default="C1/C2")
    asset_tag = db.Column(db.String(64))
    serial_number = db.Column(db.String(64))
    owner = db.Column(db.String(64))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    health_snapshots = db.relationship(
        "FixtureHealthSnapshot",
        back_populates="fixture",
        cascade="all, delete-orphan",
        order_by="FixtureHealthSnapshot.captured_at.desc()",
    )
    components = db.relationship(
        "FixtureComponent",
        back_populates="fixture",
        cascade="all, delete-orphan",
        order_by="FixtureComponent.name",
    )

    @property
    def latest_snapshot(self):
        return self.health_snapshots[0] if self.health_snapshots else None


class FixtureHealthSnapshot(db.Model):
    """Point-in-time fixture health rollup from the fixture health agent."""
    __tablename__ = "fixture_health_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.String(32), db.ForeignKey("fixtures.fixture_id", ondelete="CASCADE"), nullable=False, index=True)
    captured_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    reported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    source = db.Column(db.String(32), default="fixture-agent")
    overall_status = db.Column(db.String(24), nullable=False, default="unknown")
    can_run_production = db.Column(db.Boolean, nullable=False, default=False)
    health_score = db.Column(db.Integer)
    summary = db.Column(db.Text)
    support_bundle_uri = db.Column(db.String(256))

    repo_sha = db.Column(db.String(40))
    repo_branch = db.Column(db.String(64))
    repo_dirty = db.Column(db.Boolean)
    software_version = db.Column(db.String(64))
    agent_version = db.Column(db.String(64))
    config_hash = db.Column(db.String(64))

    uptime_s = db.Column(db.Integer)
    load_avg = db.Column(db.Float)
    cpu_temp_c = db.Column(db.Float)
    disk_free_gb = db.Column(db.Float)
    disk_used_pct = db.Column(db.Float)
    time_sync_ok = db.Column(db.Boolean)

    fixture = db.relationship("Fixture", back_populates="health_snapshots")
    checks = db.relationship(
        "FixtureHealthCheck",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by=lambda: (FixtureHealthCheck.category, FixtureHealthCheck.name),
    )


class FixtureHealthCheck(db.Model):
    """One check within a fixture health snapshot."""
    __tablename__ = "fixture_health_checks"

    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("fixture_health_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    category = db.Column(db.String(32), nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False)  # ok, warn, fail, unknown
    severity = db.Column(db.String(16), nullable=False, default="info")
    blocks_production = db.Column(db.Boolean, nullable=False, default=False)
    message = db.Column(db.Text)
    observed = db.Column(db.String(256))
    expected = db.Column(db.String(256))
    duration_ms = db.Column(db.Integer)
    details = db.Column(db.JSON)

    snapshot = db.relationship("FixtureHealthSnapshot", back_populates="checks")


class FixtureComponent(db.Model):
    """Persistent inventory/status for fixture hardware and external instruments."""
    __tablename__ = "fixture_components"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.String(32), db.ForeignKey("fixtures.fixture_id", ondelete="CASCADE"), nullable=False, index=True)
    component_type = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="unknown")
    version = db.Column(db.String(64))
    serial_number = db.Column(db.String(64))
    address = db.Column(db.String(128))
    last_seen_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    details = db.Column(db.JSON)

    fixture = db.relationship("Fixture", back_populates="components")
    __table_args__ = (
        db.UniqueConstraint("fixture_id", "component_type", "name", name="uq_fixture_component"),
    )


class FixtureAlertRecipient(db.Model):
    """Person or Slack target to notify when fixture health crosses a threshold."""
    __tablename__ = "fixture_alert_recipients"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(256))
    slack_user_id = db.Column(db.String(64))
    slack_channel = db.Column(db.String(64))
    notify_email = db.Column(db.Boolean, nullable=False, default=True)
    notify_slack = db.Column(db.Boolean, nullable=False, default=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    min_status = db.Column(db.String(24), nullable=False, default="needs_attention")
    fixture_id = db.Column(db.String(32), db.ForeignKey("fixtures.fixture_id", ondelete="CASCADE"))
    station_type = db.Column(db.String(16))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class FixtureAlertEvent(db.Model):
    """Audit log for fixture alert notification attempts and suppressions."""
    __tablename__ = "fixture_alert_events"

    id = db.Column(db.Integer, primary_key=True)
    fixture_id = db.Column(db.String(32), db.ForeignKey("fixtures.fixture_id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("fixture_health_snapshots.id", ondelete="SET NULL"), index=True)
    status = db.Column(db.String(24), nullable=False)
    alert_level = db.Column(db.Integer, nullable=False)
    event_key = db.Column(db.String(128), nullable=False, index=True)
    message = db.Column(db.Text)
    delivery_state = db.Column(db.String(24), nullable=False, default="pending")
    channels = db.Column(db.JSON)
    email_recipients = db.Column(db.JSON)
    slack_recipients = db.Column(db.JSON)
    provider_response = db.Column(db.JSON)
    error = db.Column(db.Text)
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
