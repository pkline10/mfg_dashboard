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
