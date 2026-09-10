#!/usr/bin/env python3
"""
Seed the database with one week of realistic demo data including log files.
Run from the repo root:  python scripts/seed_demo.py

Log files are written to LOCAL_LOG_DIR (default: ./logs/).
The dashboard must have LOCAL_LOG_DIR set to the same path to serve them.
"""
import gzip
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql://emporia:emporia_dev@localhost/mfg_dashboard")

from app import create_app, db
from app.models import (
    Fixture,
    FixtureAlertEvent,
    FixtureComponent,
    FixtureHealthCheck,
    FixtureHealthSnapshot,
    Measurement,
    TestRun,
    TestResult,
)

random.seed(42)

LOCAL_LOG_DIR = os.environ.get("LOCAL_LOG_DIR", "./logs")

PRODUCTS  = ["C1", "C2"]
FIXTURE_DEFS = [
    {
        "fixture_id": "GDL-FCT-01",
        "display_name": "GDL FCT 01",
        "station_type": "fct",
        "hostname": "gdl-fct-01",
        "line": "Guadalajara Line 1",
        "location": "FCT Cell A",
        "asset_tag": "MX-FCT-001",
        "serial_number": "FX-CX-FCT-001",
    },
    {
        "fixture_id": "GDL-FCT-02",
        "display_name": "GDL FCT 02",
        "station_type": "fct",
        "hostname": "gdl-fct-02",
        "line": "Guadalajara Line 1",
        "location": "FCT Cell B",
        "asset_tag": "MX-FCT-002",
        "serial_number": "FX-CX-FCT-002",
    },
    {
        "fixture_id": "GDL-BOX-01",
        "display_name": "GDL Box 01",
        "station_type": "box",
        "hostname": "gdl-box-01",
        "line": "Guadalajara Line 1",
        "location": "Box-Level Cell A",
        "asset_tag": "MX-BOX-001",
        "serial_number": "FX-CX-BOX-001",
    },
    {
        "fixture_id": "GDL-BOX-02",
        "display_name": "GDL Box 02",
        "station_type": "box",
        "hostname": "gdl-box-02",
        "line": "Guadalajara Line 1",
        "location": "Box-Level Cell B",
        "asset_tag": "MX-BOX-002",
        "serial_number": "FX-CX-BOX-002",
    },
    {
        "fixture_id": "GDL-HIPOT-01",
        "display_name": "GDL Hipot 01",
        "station_type": "hipot",
        "hostname": "gdl-hipot-01",
        "line": "Guadalajara Line 1",
        "location": "Hipot Cell",
        "asset_tag": "MX-HIPOT-001",
        "serial_number": "FX-CX-HIPOT-001",
    },
]
FCT_FIXTURES = [f["fixture_id"] for f in FIXTURE_DEFS if f["station_type"] == "fct"]
BOX_FIXTURES = [f["fixture_id"] for f in FIXTURE_DEFS if f["station_type"] == "box"]

FCT_TESTS = [
    "intercom_test",
    "buzzer_function_test",
    "relay_function_test",
    "test_cp_sense",
    "test_cp_pwm",
]
BOX_TESTS = [
    "led_comms_test",
    "voltage_accuracy_test",
    "current_accuracy_test",
    "charging_check_test",
]

TEST_PASS_RATES = {
    "intercom_test":         0.99,
    "buzzer_function_test":  0.97,
    "relay_function_test":   0.98,
    "test_cp_sense":         0.93,
    "test_cp_pwm":           0.98,
    "led_comms_test":        0.95,
    "voltage_accuracy_test": 0.99,
    "current_accuracy_test": 0.97,
    "charging_check_test":   0.96,
}

TEST_DURATIONS = {
    "intercom_test":         (4,   1),
    "buzzer_function_test":  (8,   2),
    "relay_function_test":   (12,  3),
    "test_cp_sense":         (20,  4),
    "test_cp_pwm":           (35,  8),
    "led_comms_test":        (30,  5),
    "voltage_accuracy_test": (15,  3),
    "current_accuracy_test": (45, 10),
    "charging_check_test":   (75, 10),
}

FIXTURE_BIAS = {
    "GDL-FCT-01":  0.00,
    "GDL-FCT-02": +0.09,
    "GDL-BOX-01": +0.18,
    "GDL-BOX-02": -0.22,
    "GDL-HIPOT-01": 0.00,
}

MEASUREMENT_SPECS = {
    "voltage_accuracy_test": [
        ("voltage_rms_240v", 240.0, 0.5,   "V"),
    ],
    "current_accuracy_test": [
        ("current_rms_40a",  40.0, 0.40,  "A"),
        ("current_rms_6a",    6.0, 0.06,  "A"),
    ],
    "buzzer_function_test": [
        ("dominant_freq_hz", 2500.0, 250.0, "Hz"),
    ],
    "test_cp_pwm": [
        ("duty_cycle_6a",   10.0, 0.5, "%"),
        ("duty_cycle_32a",  53.3, 0.5, "%"),
        ("duty_cycle_48a",  80.0, 0.5, "%"),
    ],
}


# ── Measurement generation ───────────────────────────────────────────────────

def _make_measurements(test_name, passed, fixture_id):
    bias_pct = FIXTURE_BIAS.get(fixture_id, 0.0)
    out = []
    for metric, target, tol, unit in MEASUREMENT_SPECS.get(test_name, []):
        nominal = round(target + random.gauss(0, tol * 0.05), 4)
        bias    = target * bias_pct / 100.0
        if passed:
            value = round(nominal + random.gauss(bias, tol * 0.25), 4)
        else:
            value = round(target + tol * random.uniform(1.2, 2.5) * random.choice([-1, 1]), 4)
        out.append({
            "metric":        metric,
            "value":         value,
            "nominal":       nominal,
            "unit":          unit,
            "tolerance_min": round(nominal - tol, 4),
            "tolerance_max": round(nominal + tol, 4),
            "passed":        abs(value - nominal) <= tol,
        })
    return out


# ── Log file generation ──────────────────────────────────────────────────────

def _log_measurement_lines(meas_list, ts_offset):
    lines = []
    for m in meas_list:
        err = (m["value"] - m["nominal"]) / abs(m["nominal"]) * 100
        sign = "+" if err >= 0 else ""
        ok = "PASS" if m["passed"] else "FAIL"
        lines.append(
            f"  [{ts_offset}]   {m['metric']:<22}  "
            f"meas={m['value']:.4f}{m['unit']}  "
            f"nominal={m['nominal']:.4f}{m['unit']}  "
            f"error={sign}{err:.3f}%  "
            f"tol=±{abs(m['tolerance_max']-m['nominal']):.4f}{m['unit']}  "
            f"{ok}"
        )
    return lines


def generate_log(run, results_data):
    """
    Build a realistic plain-text log for a test run.
    results_data: list of (test_name, passed, duration_s, meas_list, t_start)
    """
    started = run.started_at
    ts = lambda dt: dt.strftime("%H:%M:%S.") + f"{dt.microsecond//1000:03d}"

    lines = [
        "=" * 80,
        "Emporia Energy — C1/C2 Manufacturing Test Suite",
        "=" * 80,
        f"Serial Number : {run.serial_number}",
        f"Product       : {run.product}",
        f"Fixture       : {run.fixture_id}",
        f"Phase         : {run.phase}",
        f"Started       : {started.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "-" * 80,
        "",
    ]

    elapsed = 0.0
    for test_name, passed, duration_s, meas_list, t_start in results_data:
        t_end = t_start + timedelta(seconds=duration_s)
        status = "PASS" if passed else "FAIL"
        lines += [
            f"[{ts(t_start)}] {'─' * 2} {test_name} {'─' * max(0, 52 - len(test_name))}",
        ]

        # Per-test detail lines
        if test_name == "voltage_accuracy_test":
            src = next((m["nominal"] for m in meas_list if "voltage" in m["metric"]), 240.0)
            lines.append(f"  [{ts(t_start + timedelta(seconds=1.2))}]   Source: {src:.3f}V L-L (Chroma 61815)")
            lines.append(f"  [{ts(t_start + timedelta(seconds=2.0))}]   Collecting 5 samples from EVSE...")
            for i, m in enumerate(meas_list):
                t_s = t_start + timedelta(seconds=2.5 + i * 0.5)
                err = (m["value"] - m["nominal"]) / abs(m["nominal"]) * 100
                lines.append(
                    f"  [{ts(t_s)}]   Sample {i+1}: EVSE={m['value']:.3f}V  "
                    f"source={m['nominal']:.3f}V  error={err:+.3f}%"
                )

        elif test_name == "current_accuracy_test":
            for m in meas_list:
                target = 40.0 if "40a" in m["metric"] else 6.0
                err = (m["value"] - m["nominal"]) / abs(m["nominal"]) * 100
                lines.append(
                    f"  [{ts(t_start + timedelta(seconds=5))}]   "
                    f"Target={target:.0f}A  load={m['nominal']:.3f}A  "
                    f"EVSE={m['value']:.3f}A  error={err:+.3f}%"
                )

        elif test_name == "buzzer_function_test":
            for m in meas_list:
                err = m["value"] - m["nominal"]
                lines.append(
                    f"  [{ts(t_start + timedelta(seconds=2))}]   "
                    f"Dominant freq: {m['value']:.1f}Hz  (expected {m['nominal']:.0f}Hz  "
                    f"delta={err:+.1f}Hz)"
                )

        elif test_name == "test_cp_pwm":
            for m in meas_list:
                err = m["value"] - m["nominal"]
                lines.append(
                    f"  [{ts(t_start + timedelta(seconds=2))}]   "
                    f"{m['metric']}: meas={m['value']:.2f}%  "
                    f"expected={m['nominal']:.2f}%  delta={err:+.3f}pp"
                )

        elif test_name == "relay_function_test":
            lines.append(f"  [{ts(t_start + timedelta(seconds=2))}]   Relay close: voltage present (>200V)")
            lines.append(f"  [{ts(t_start + timedelta(seconds=5))}]   Relay open: voltage absent (<10V)")

        elif test_name == "test_cp_sense":
            states = ["A (12V)", "B (9V)", "C (6V)"] if passed else ["A (12V)", "B (9V)", "E (0V — ERROR)"]
            for i, state in enumerate(states):
                t_s = t_start + timedelta(seconds=i * 5)
                ok = "OK" if passed or i < 2 else "FAIL — unexpected state"
                lines.append(f"  [{ts(t_s)}]   CP state: {state}  {ok}")

        elif test_name == "charging_check_test":
            lines.append(f"  [{ts(t_start + timedelta(seconds=3))}]   Vehicle connected, EV Sim State B")
            lines.append(f"  [{ts(t_start + timedelta(seconds=6))}]   Charging started, CS_CHARGING (4)")
            if passed:
                lines.append(f"  [{ts(t_start + timedelta(seconds=30))}]   Load stable  voltage≈240V  current≈40A")
                lines.append(f"  [{ts(t_start + timedelta(seconds=66))}]   60s hold complete")
            else:
                lines.append(f"  [{ts(t_start + timedelta(seconds=15))}]   FAULT: current dropped below 90% threshold")

        elif test_name == "led_comms_test":
            lines.append(f"  [{ts(t_start + timedelta(seconds=2))}]   Reading LED Sensor Board channels...")
            if passed:
                lines.append(f"  [{ts(t_start + timedelta(seconds=10))}]   All LED Sensor Board channels within delta threshold")
            else:
                lines.append(f"  [{ts(t_start + timedelta(seconds=10))}]   FAIL: RED_FAULT channel below threshold")

        lines += _log_measurement_lines(meas_list, ts(t_end))

        result_line = f"[{ts(t_end)}] {test_name}  {status}  ({duration_s:.1f}s)"
        lines.append(result_line)
        lines.append("")
        elapsed += duration_s

    lines += [
        "-" * 80,
        f"OVERALL: {'PASS' if run.overall_pass else 'FAIL'}",
        f"Duration: {int(run.duration_s // 60)}m {run.duration_s % 60:.0f}s",
        f"Ended:    {(started + timedelta(seconds=run.duration_s)).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "=" * 80,
    ]
    if not run.overall_pass and run.failure_reason:
        lines.insert(-1, f"Failure:  {run.failure_reason}")

    return "\n".join(lines)


def write_log(run, log_text, log_dir):
    dt = run.started_at
    rel_key = f"logs/{dt.year}/{dt.month:02d}/{run.serial_number}/run_{run.id}.log.gz"
    full_path = Path(log_dir) / rel_key
    full_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(full_path, "wt", encoding="utf-8") as f:
        f.write(log_text)
    return rel_key


def _check(category, name, status="ok", severity="info", blocks=False,
           message=None, observed=None, expected=None, duration_ms=None, details=None):
    return {
        "category": category,
        "name": name,
        "status": status,
        "severity": severity,
        "blocks_production": blocks,
        "message": message,
        "observed": observed,
        "expected": expected,
        "duration_ms": duration_ms,
        "details": details,
    }


def _component(component_type, name, status="ok", version=None, serial=None,
               address=None, notes=None, details=None):
    return {
        "component_type": component_type,
        "name": name,
        "status": status,
        "version": version,
        "serial_number": serial,
        "address": address,
        "notes": notes,
        "details": details,
    }


def _base_checks(fixture_id):
    return [
        _check("pc", "disk_space", "ok", observed="62 GB free / 41% used", expected="> 20 GB free", duration_ms=18),
        _check("pc", "time_sync", "ok", observed="+0.8 s from NTP", expected="< 5 s", duration_ms=24),
        _check("software", "repo_release", "ok", observed="main@9f3a7c2 clean", expected="approved release SHA", duration_ms=42),
        _check("software", "config_parse", "ok", observed="/etc/emporia-fixture/config.yaml", expected="valid station config", duration_ms=31),
        _check("network", "dashboard_ingest", "ok", observed="100.109.84.94:5001 in 48 ms", expected="HTTP 2xx", duration_ms=48),
        _check("network", "pfs_port", "ok", observed="pfs-gw-mxp4.corp.bench.com:41123 in 76 ms", expected="TCP connect < 500 ms", duration_ms=76),
        _check("testpilot", "http_info", "ok", observed="192.168.1.58:8080 firmware 0.6.3", expected="/info responds", duration_ms=36),
        _check("testpilot", "fixture_identity", "ok", observed=fixture_id, expected="JP1 identity matches configured fixture", duration_ms=12),
        _check("operator_io", "barcode_scanner", "ok", observed="/dev/barcode_scanner read in 90 ms", expected="scan response < 1 s", duration_ms=90),
    ]


def _fixture_specific_checks(fixture_id):
    if fixture_id == "GDL-FCT-01":
        return _base_checks(fixture_id) + [
            _check("fixture", "pogo_continuity", "ok", observed="42/42 nets closed", expected="all fixture pogo nets closed"),
            _check("audio", "buzzer_sensor", "ok", observed="piezo pickup 2.48 kHz / 18 dB SNR", expected="2.5 kHz +/- 250 Hz, SNR > 12 dB"),
            _check("testpilot", "safe_relay_self_test", "ok", observed="6 relays toggled, returned off", expected="all relays off at end"),
        ]
    if fixture_id == "GDL-FCT-02":
        checks = _base_checks(fixture_id)
        for c in checks:
            if c["category"] == "network" and c["name"] == "pfs_port":
                c.update(status="warn", severity="warning", observed="pfs-gw-mxp4.corp.bench.com:41123 in 438 ms",
                         message="PFS reachable but near latency warning threshold", duration_ms=438)
        checks += [
            _check("fixture", "pogo_continuity", "ok", observed="42/42 nets closed", expected="all fixture pogo nets closed"),
            _check("audio", "buzzer_sensor", "warn", "warning", False,
                   "Piezo candidate is usable but noise floor is high on this fixture",
                   observed="piezo pickup 2.52 kHz / 10 dB SNR", expected="SNR > 12 dB"),
            _check("testpilot", "safe_relay_self_test", "ok", observed="6 relays toggled, returned off", expected="all relays off at end"),
        ]
        return checks
    if fixture_id == "GDL-BOX-01":
        return _base_checks(fixture_id) + [
            _check("led_sensor_board", "board_identity", "ok", observed="LED Sensor Board v1.1 SN LSB-014", expected="board present"),
            _check("led_sensor_board", "dark_reading", "ok", observed="max dark count 11", expected="< 25 counts"),
            _check("led_sensor_board", "channel_response", "fail", "critical", True,
                   "Channel 3 is saturated during known GREEN_GRID response",
                   observed="channel_3=65535", expected="< 52000 counts"),
            _check("instrument", "chroma_idn", "ok", observed="Chroma 61815 SN 61815-042", expected="IDN response"),
            _check("instrument", "chroma_error_queue", "warn", "warning", False,
                   "Chroma reported one stale command warning after the last cycle",
                   observed="-113 Undefined header", expected="0,No error"),
        ]
    if fixture_id == "GDL-BOX-02":
        return _base_checks(fixture_id) + [
            _check("led_sensor_board", "board_identity", "ok", observed="LED Sensor Board v1.1 SN LSB-009", expected="board present"),
            _check("led_sensor_board", "dark_reading", "ok", observed="max dark count 8", expected="< 25 counts"),
            _check("led_sensor_board", "channel_response", "ok", observed="all channels within learned window", expected="known LED response windows"),
            _check("instrument", "chroma_idn", "ok", observed="Chroma 61815 SN 61815-038", expected="IDN response"),
            _check("instrument", "chroma_error_queue", "ok", observed="0,No error", expected="0,No error"),
        ]
    return [
        _check("pc", "fixture_agent_heartbeat", "fail", "critical", True,
               "No fresh heartbeat from hipot fixture PC",
               observed="last report 94 minutes ago", expected="< 5 minutes"),
        _check("pc", "disk_space", "unknown", "warning", False, observed="stale", expected="fresh health report"),
        _check("network", "dashboard_ingest", "unknown", "warning", False, observed="stale", expected="HTTP 2xx"),
        _check("instrument", "vitrek_idn", "ok", observed="Vitrek 951i SN VTK-951-018", expected="IDN response"),
        _check("instrument", "vitrek_interlock", "fail", "critical", True,
               "Hipot interlock is open; production must remain blocked",
               observed="OPEN", expected="CLOSED"),
        _check("safety", "hipot_output_state", "ok", observed="output off", expected="output off while idle"),
    ]


def _fixture_components(fixture_id):
    common = [
        _component("computer", "Fixture PC", "ok", version="Ubuntu 24.04", serial=f"PC-{fixture_id[-2:]}",
                   address=f"{fixture_id.lower()}.local"),
        _component("controller", "TestPilot W5500-EVB-Pico", "ok", version="0.6.3",
                   serial=f"TP-{fixture_id[-2:]}", address="192.168.1.58:8080"),
        _component("operator_io", "Barcode Scanner", "ok", version="USB HID", serial=f"SCAN-{fixture_id[-2:]}",
                   address="/dev/barcode_scanner"),
    ]
    if "FCT" in fixture_id:
        status = "warn" if fixture_id.endswith("02") else "ok"
        return common + [
            _component("sensor", "Piezoelectric Audio Pickup", status, version="prototype A",
                       serial=f"PZ-{fixture_id[-2:]}", address="USB audio adapter",
                       notes="Audio diagnostics treat this as the buzzer sensor backend."),
        ]
    if "BOX" in fixture_id:
        led_status = "fail" if fixture_id.endswith("01") else "ok"
        chroma_status = "warn" if fixture_id.endswith("01") else "ok"
        return common + [
            _component("sensor", "LED Sensor Board", led_status, version="1.1",
                       serial="LSB-014" if fixture_id.endswith("01") else "LSB-009",
                       address="/dev/led_sensor_board"),
            _component("instrument", "Chroma 61815", chroma_status, version="FW 1.22",
                       serial="61815-042" if fixture_id.endswith("01") else "61815-038",
                       address="192.168.1.71:5025"),
        ]
    return [
        _component("computer", "Fixture PC", "fail", version="Ubuntu 24.04", serial="PC-HP-01",
                   address="gdl-hipot-01.local", notes="Heartbeat stale"),
        _component("instrument", "Vitrek 951i", "fail", version="FW 3.8", serial="VTK-951-018",
                   address="192.168.1.81:5025", notes="Interlock open"),
    ]


def _add_fixture_snapshot(fixture_id, captured_at, status, can_run, score, summary, checks=None):
    snapshot = FixtureHealthSnapshot(
        fixture_id=fixture_id,
        captured_at=captured_at,
        reported_at=captured_at + timedelta(seconds=random.randint(2, 20)),
        source="fixture-agent",
        overall_status=status,
        can_run_production=can_run,
        health_score=score,
        summary=summary,
        support_bundle_uri=f"support/{fixture_id}/{captured_at:%Y%m%d_%H%M%S}.tar.gz",
        repo_sha="9f3a7c2d4b1e9c0a6f8c52b2dcb9a9e3e44f1f2a",
        repo_branch="main",
        repo_dirty=False,
        software_version="cx-mfg-2026.05.18",
        agent_version="fixture-agent-demo-0.1",
        config_hash=f"cfg-{fixture_id.lower()}-84d1",
        uptime_s=random.randint(30_000, 480_000),
        load_avg=round(random.uniform(0.18, 1.45), 2),
        cpu_temp_c=round(random.uniform(41.5, 59.0), 1),
        disk_free_gb=round(random.uniform(48.0, 168.0), 1),
        disk_used_pct=round(random.uniform(28.0, 62.0), 1),
        time_sync_ok=status != "down",
    )
    db.session.add(snapshot)
    db.session.flush()

    for c in checks or []:
        db.session.add(FixtureHealthCheck(
            snapshot_id=snapshot.id,
            category=c["category"],
            name=c["name"],
            status=c["status"],
            severity=c["severity"],
            blocks_production=c["blocks_production"],
            message=c["message"],
            observed=c["observed"],
            expected=c["expected"],
            duration_ms=c["duration_ms"],
            details=c["details"],
        ))
    return snapshot


def seed_fixture_health(now):
    profiles = {
        "GDL-FCT-01": ("ok", True, 96, "FCT fixture healthy; all remote checks passing."),
        "GDL-FCT-02": ("degraded", True, 82, "Production can run; piezo audio SNR and PFS latency need watching."),
        "GDL-BOX-01": ("needs_attention", False, 64, "Production blocked: LED Sensor Board channel response is saturated."),
        "GDL-BOX-02": ("ok", True, 93, "Box-level fixture healthy; LED Sensor Board and Chroma checks passing."),
        "GDL-HIPOT-01": ("down", False, 28, "Production blocked: heartbeat stale and Vitrek interlock is open."),
    }

    for f in FIXTURE_DEFS:
        db.session.add(Fixture(
            fixture_id=f["fixture_id"],
            display_name=f["display_name"],
            station_type=f["station_type"],
            site="Guadalajara",
            line=f["line"],
            location=f["location"],
            hostname=f["hostname"],
            product_family="C1/C2",
            asset_tag=f["asset_tag"],
            serial_number=f["serial_number"],
            owner="Benchmark Guadalajara",
            notes="Demo fixture health record seeded for dashboard development.",
        ))

    db.session.flush()

    for f in FIXTURE_DEFS:
        fixture_id = f["fixture_id"]
        for c in _fixture_components(fixture_id):
            db.session.add(FixtureComponent(
                fixture_id=fixture_id,
                component_type=c["component_type"],
                name=c["name"],
                status=c["status"],
                version=c["version"],
                serial_number=c["serial_number"],
                address=c["address"],
                last_seen_at=now - timedelta(minutes=94 if fixture_id == "GDL-HIPOT-01" else random.randint(1, 8)),
                notes=c["notes"],
                details=c["details"],
            ))

        status, can_run, score, summary = profiles[fixture_id]
        for hours_ago in [24, 18, 12, 6]:
            hist_score = max(20, min(99, score + random.randint(-5, 5)))
            _add_fixture_snapshot(
                fixture_id,
                now - timedelta(hours=hours_ago),
                status if hours_ago <= 12 else ("ok" if score >= 80 else "degraded"),
                can_run if hours_ago <= 12 else score >= 70,
                hist_score,
                summary if hours_ago <= 12 else "Scheduled health snapshot.",
            )

        latest_time = now - timedelta(minutes=94 if fixture_id == "GDL-HIPOT-01" else random.randint(1, 8))
        _add_fixture_snapshot(
            fixture_id,
            latest_time,
            status,
            can_run,
            score,
            summary,
            checks=_fixture_specific_checks(fixture_id),
        )


# ── Main seed ────────────────────────────────────────────────────────────────

def seed(days=7, runs_per_day_range=(10, 20)):
    app = create_app()
    with app.app_context():
        db.create_all()
        print("Clearing existing data...")
        db.session.query(FixtureHealthCheck).delete()
        db.session.query(FixtureAlertEvent).delete()
        db.session.query(FixtureHealthSnapshot).delete()
        db.session.query(FixtureComponent).delete()
        db.session.query(Fixture).delete()
        db.session.query(Measurement).delete()
        db.session.query(TestResult).delete()
        db.session.query(TestRun).delete()
        db.session.commit()

        # Clear old log files
        import shutil
        log_root = Path(LOCAL_LOG_DIR) / "logs"
        if log_root.exists():
            shutil.rmtree(log_root)
        print(f"Log files → {Path(LOCAL_LOG_DIR).resolve()}/logs/")

        now   = datetime.now(timezone.utc).replace(tzinfo=None)
        total = 0

        for day_offset in range(days - 1, -1, -1):
            day_start = (now - timedelta(days=day_offset)).replace(
                hour=6, minute=0, second=0, microsecond=0
            )
            is_weekend = day_start.weekday() >= 5
            n_runs = random.randint(2, 5) if is_weekend else random.randint(*runs_per_day_range)

            for _ in range(n_runs):
                product  = random.choices(PRODUCTS, weights=[0.7, 0.3])[0]
                phase    = random.choice(["fct", "box"])
                fixture  = random.choice(FCT_FIXTURES if phase == "fct" else BOX_FIXTURES)
                tests    = FCT_TESTS if phase == "fct" else BOX_TESTS
                started  = day_start + timedelta(seconds=random.randint(0, 50400))

                results_data = []  # (test_name, passed, duration_s, meas_list, t_start)
                result_objs  = []
                all_passed   = True
                first_fail   = None
                elapsed      = 0.0

                for test_name in tests:
                    mean, std = TEST_DURATIONS.get(test_name, (10, 2))
                    dur    = max(1.0, random.gauss(mean, std))
                    passed = random.random() < TEST_PASS_RATES.get(test_name, 0.95)
                    if not passed and all_passed:
                        all_passed = False
                        first_fail = test_name

                    t_start = started + timedelta(seconds=elapsed)
                    t_end   = t_start + timedelta(seconds=dur)
                    meas_list = _make_measurements(test_name, passed, fixture)

                    results_data.append((test_name, passed, dur, meas_list, t_start))
                    result_objs.append(TestResult(
                        test_name=test_name,
                        started_at=t_start,
                        ended_at=t_end,
                        duration_s=round(dur, 2),
                        passed=passed,
                        failure_reason=None if passed else f"{test_name} threshold exceeded",
                        measurements=[
                            Measurement(
                                metric_name=m["metric"],
                                value=m["value"],
                                nominal=m["nominal"],
                                unit=m["unit"],
                                tolerance_min=m["tolerance_min"],
                                tolerance_max=m["tolerance_max"],
                                passed=m["passed"],
                            )
                            for m in meas_list
                        ],
                    ))
                    elapsed += dur

                run = TestRun(
                    serial_number=f"SN{random.randint(100000, 999999)}",
                    product=product,
                    fixture_id=fixture,
                    phase=phase,
                    started_at=started,
                    ended_at=started + timedelta(seconds=elapsed),
                    duration_s=round(elapsed, 2),
                    overall_pass=all_passed,
                    failure_reason=first_fail,
                    results=result_objs,
                )
                db.session.add(run)
                db.session.flush()  # get run.id

                # Write log file
                log_text = generate_log(run, results_data)
                log_key  = write_log(run, log_text, LOCAL_LOG_DIR)
                run.log_s3_key = log_key

                total += 1

            db.session.commit()
            day_label = day_start.strftime("%a %Y-%m-%d")
            print(f"  {day_label}  {'(weekend)' if is_weekend else '         '}  {total} runs total")

        db.session.commit()
        seed_fixture_health(now)
        db.session.commit()
        print(f"\nDone. {total} test runs over {days} days.")
        print(f"Seeded {len(FIXTURE_DEFS)} fixtures with health snapshots.")
        print(f"Log files: {Path(LOCAL_LOG_DIR).resolve()}/logs/")


if __name__ == "__main__":
    seed()
