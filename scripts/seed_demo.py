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
from app.models import TestRun, TestResult, Measurement

random.seed(42)

LOCAL_LOG_DIR = os.environ.get("LOCAL_LOG_DIR", "./logs")

PRODUCTS  = ["C1", "C2"]
FIXTURES  = ["BOX-01", "BOX-02", "BOX-03"]

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
    "BOX-01":  0.00,
    "BOX-02": +0.18,
    "BOX-03": -0.22,
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
            lines.append(f"  [{ts(t_start + timedelta(seconds=2))}]   Scanning LED ROIs via AS7341...")
            if passed:
                lines.append(f"  [{ts(t_start + timedelta(seconds=10))}]   All LEDs detected within delta threshold")
            else:
                lines.append(f"  [{ts(t_start + timedelta(seconds=10))}]   FAIL: RED_FAULT LED below threshold")

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


# ── Main seed ────────────────────────────────────────────────────────────────

def seed(days=7, runs_per_day_range=(10, 20)):
    app = create_app()
    with app.app_context():
        print("Clearing existing data...")
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
                fixture  = random.choice(FIXTURES)
                phase    = random.choice(["fct", "box"])
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
        print(f"\nDone. {total} test runs over {days} days.")
        print(f"Log files: {Path(LOCAL_LOG_DIR).resolve()}/logs/")


if __name__ == "__main__":
    seed()
