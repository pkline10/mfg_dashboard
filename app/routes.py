import gzip
import json
import os
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify, request, current_app, Response
from sqlalchemy import func, case, and_
from app import db
from app.models import (
    Fixture,
    FixtureAlertEvent,
    FixtureAlertRecipient,
    FixtureComponent,
    FixtureHealthCheck,
    FixtureHealthSnapshot,
    Measurement,
    TestRun,
    TestResult,
)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _boto3_available = True
except ImportError:
    _boto3_available = False

main = Blueprint("main", __name__)


_ENV_FILE_CACHE = None


def _env_file_values():
    global _ENV_FILE_CACHE
    if _ENV_FILE_CACHE is not None:
        return _ENV_FILE_CACHE

    path = os.getenv("FIXTURE_ALERT_ENV_FILE", "/home/aramirez/toolhub/config/.env")
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        values = {}
    _ENV_FILE_CACHE = values
    return values


def _alert_env(*names, default=None):
    values = _env_file_values()
    for name in names:
        value = os.getenv(name) or values.get(name)
        if value:
            return value
    return default


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _fixture_email_enabled():
    return _truthy(_alert_env("FIXTURE_ALERT_EMAIL_ENABLED", default="false"))

# ---------------------------------------------------------------------------
# Helper: date-range from query param or default (last 30 days)
# ---------------------------------------------------------------------------

def _date_range():
    end = datetime.utcnow()
    days = int(request.args.get("days", 30))
    start = end - timedelta(days=days)
    return start, end


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _iso(value):
    return value.isoformat() if value else None


def _check_counts(snapshot):
    counts = {"ok": 0, "warn": 0, "fail": 0, "unknown": 0}
    if not snapshot:
        return counts
    for check in snapshot.checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _snapshot_payload(snapshot, include_checks=False):
    if not snapshot:
        return None
    data = {
        "id": snapshot.id,
        "fixture_id": snapshot.fixture_id,
        "captured_at": _iso(snapshot.captured_at),
        "reported_at": _iso(snapshot.reported_at),
        "source": snapshot.source,
        "overall_status": snapshot.overall_status,
        "can_run_production": snapshot.can_run_production,
        "health_score": snapshot.health_score,
        "summary": snapshot.summary,
        "support_bundle_uri": snapshot.support_bundle_uri,
        "repo_sha": snapshot.repo_sha,
        "repo_branch": snapshot.repo_branch,
        "repo_dirty": snapshot.repo_dirty,
        "software_version": snapshot.software_version,
        "agent_version": snapshot.agent_version,
        "config_hash": snapshot.config_hash,
        "uptime_s": snapshot.uptime_s,
        "load_avg": snapshot.load_avg,
        "cpu_temp_c": snapshot.cpu_temp_c,
        "disk_free_gb": snapshot.disk_free_gb,
        "disk_used_pct": snapshot.disk_used_pct,
        "time_sync_ok": snapshot.time_sync_ok,
        "check_counts": _check_counts(snapshot),
    }
    if include_checks:
        data["checks"] = [_check_payload(c) for c in snapshot.checks]
    return data


def _fixture_payload(fixture, include_snapshot=False):
    latest = fixture.latest_snapshot
    data = {
        "fixture_id": fixture.fixture_id,
        "display_name": fixture.display_name or fixture.fixture_id,
        "station_type": fixture.station_type,
        "site": fixture.site,
        "line": fixture.line,
        "location": fixture.location,
        "hostname": fixture.hostname,
        "product_family": fixture.product_family,
        "asset_tag": fixture.asset_tag,
        "serial_number": fixture.serial_number,
        "owner": fixture.owner,
        "notes": fixture.notes,
        "updated_at": _iso(fixture.updated_at),
        "latest_status": latest.overall_status if latest else "unknown",
        "can_run_production": bool(latest.can_run_production) if latest else False,
        "health_score": latest.health_score if latest else None,
        "last_heartbeat_at": _iso(latest.captured_at) if latest else None,
        "summary": latest.summary if latest else "No fixture health snapshots have been received.",
        "check_counts": _check_counts(latest),
        "blocking_checks": sum(
            1 for c in latest.checks if c.blocks_production and c.status != "ok"
        ) if latest else None,
    }
    if include_snapshot:
        data["latest_snapshot"] = _snapshot_payload(latest, include_checks=True)
    return data


def _check_payload(check):
    return {
        "id": check.id,
        "category": check.category,
        "name": check.name,
        "status": check.status,
        "severity": check.severity,
        "blocks_production": check.blocks_production,
        "message": check.message,
        "observed": check.observed,
        "expected": check.expected,
        "duration_ms": check.duration_ms,
        "details": check.details or {},
    }


def _component_payload(component):
    return {
        "id": component.id,
        "component_type": component.component_type,
        "name": component.name,
        "status": component.status,
        "version": component.version,
        "serial_number": component.serial_number,
        "address": component.address,
        "last_seen_at": _iso(component.last_seen_at),
        "notes": component.notes,
        "details": component.details or {},
    }


STATUS_LEVELS = {
    "ok": 0,
    "degraded": 1,
    "warn": 1,
    "warning": 1,
    "needs_attention": 2,
    "fail": 3,
    "failed": 3,
    "down": 3,
    "critical": 3,
    "unknown": 0,
}


def _status_level(status, can_run_production=True):
    level = STATUS_LEVELS.get((status or "unknown").lower(), 0)
    if can_run_production is False:
        level = max(level, STATUS_LEVELS["needs_attention"])
    return level


def _recipient_payload(recipient):
    return {
        "id": recipient.id,
        "name": recipient.name,
        "email": recipient.email,
        "slack_user_id": recipient.slack_user_id,
        "slack_channel": recipient.slack_channel,
        "notify_email": recipient.notify_email,
        "notify_slack": recipient.notify_slack,
        "enabled": recipient.enabled,
        "min_status": recipient.min_status,
        "fixture_id": recipient.fixture_id,
        "station_type": recipient.station_type,
        "notes": recipient.notes,
        "created_at": _iso(recipient.created_at),
        "updated_at": _iso(recipient.updated_at),
    }


def _alert_event_payload(event):
    return {
        "id": event.id,
        "fixture_id": event.fixture_id,
        "snapshot_id": event.snapshot_id,
        "status": event.status,
        "alert_level": event.alert_level,
        "event_key": event.event_key,
        "message": event.message,
        "delivery_state": event.delivery_state,
        "channels": event.channels or [],
        "email_recipients": event.email_recipients or [],
        "slack_recipients": event.slack_recipients or [],
        "provider_response": event.provider_response or {},
        "error": event.error,
        "sent_at": _iso(event.sent_at),
        "created_at": _iso(event.created_at),
    }


def _recipient_matches(recipient, fixture, alert_level):
    if not recipient.enabled:
        return False
    if _status_level(recipient.min_status) > alert_level:
        return False
    if recipient.fixture_id and recipient.fixture_id != fixture.fixture_id:
        return False
    if recipient.station_type and recipient.station_type != fixture.station_type:
        return False
    return True


def _alert_recipients(fixture, alert_level):
    recipients = FixtureAlertRecipient.query.order_by(FixtureAlertRecipient.name).all()
    return [r for r in recipients if _recipient_matches(r, fixture, alert_level)]


def _fixture_url(fixture_id):
    base_url = (
        _alert_env("FIXTURE_ALERT_BASE_URL", "MFG_DASHBOARD_URL", default="http://devserver:5001")
    )
    return f"{base_url.rstrip('/')}/fixtures/{fixture_id}"


def _alert_message(fixture, snapshot):
    status = (snapshot.overall_status or "unknown").replace("_", " ").upper()
    lines = [
        f"Fixture {fixture.fixture_id} is {status}",
        f"Production ready: {'yes' if snapshot.can_run_production else 'no'}",
    ]
    if snapshot.health_score is not None:
        lines.append(f"Health score: {snapshot.health_score}")
    if snapshot.summary:
        lines.append(snapshot.summary)
    lines.append(f"Open: {_fixture_url(fixture.fixture_id)}")
    return "\n".join(lines)


def _send_sendgrid_email(subject, text_body, html_body, recipients, custom_args=None):
    api_key = _alert_env("FIXTURE_ALERT_SENDGRID_API_KEY", "SENDGRID_API_KEY")
    sender = _alert_env("FIXTURE_ALERT_FROM", "NOTIFY_FROM", default="dperry@emporiaenergy.com")
    if not api_key:
        return False, {"ok": False, "error": "SENDGRID_API_KEY not configured"}
    if not recipients:
        return False, {"ok": False, "error": "no email recipients"}

    payload = {
        "personalizations": [
            {
                "to": [{"email": email} for email in recipients],
                "custom_args": custom_args or {},
            }
        ],
        "from": {"email": sender, "name": "Emporia Fixture Health"},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": html_body},
        ],
    }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 202), {
                "status": resp.status,
                "x_message_id": resp.headers.get("X-Message-Id"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return False, {"status": exc.code, "error": body}
    except Exception as exc:
        return False, {"error": str(exc)}


def _send_slack_webhook(fixture, snapshot, message, recipients):
    webhook_url = _alert_env("FIXTURE_ALERT_SLACK_WEBHOOK_URL", "SLACK_WEBHOOK_URL")
    if not webhook_url:
        return False, {"ok": False, "error": "SLACK_WEBHOOK_URL not configured"}

    mentions = [
        f"<@{r.slack_user_id}>"
        for r in recipients
        if r.notify_slack and r.slack_user_id
    ]
    channel = next((r.slack_channel for r in recipients if r.notify_slack and r.slack_channel), None)
    status = (snapshot.overall_status or "unknown").replace("_", " ").upper()
    color = "#dc2626" if _status_level(snapshot.overall_status, snapshot.can_run_production) >= 3 else "#d97706"
    payload = {
        "text": f"Fixture {fixture.fixture_id} is {status}",
        "attachments": [
            {
                "color": color,
                "title": f"Fixture {fixture.fixture_id} needs attention",
                "title_link": _fixture_url(fixture.fixture_id),
                "text": "\n".join(mentions + [message]) if mentions else message,
                "fields": [
                    {"title": "Station", "value": fixture.station_type or "-", "short": True},
                    {"title": "Hostname", "value": fixture.hostname or "-", "short": True},
                    {"title": "Status", "value": status, "short": True},
                    {"title": "Health Score", "value": str(snapshot.health_score or "-"), "short": True},
                ],
                "footer": "Emporia Fixture Health",
            }
        ],
    }
    if channel:
        payload["channel"] = channel

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode(errors="replace")
            return resp.status == 200, {"status": resp.status, "body": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return False, {"status": exc.code, "error": body}
    except Exception as exc:
        return False, {"error": str(exc)}


def _send_toolhub_notify(fixture, snapshot, message):
    url = _alert_env("FIXTURE_ALERT_TOOLHUB_NOTIFY_URL", default="http://127.0.0.1:8000/api/notify")
    alert_level = _status_level(snapshot.overall_status, snapshot.can_run_production)
    event = "non_operational" if alert_level >= STATUS_LEVELS["needs_attention"] else "calibration"
    payload = {
        "event": event,
        "asset_id": fixture.asset_tag or fixture.fixture_id,
        "manufacturer": "Emporia",
        "part_number": "Fixture Health",
        "serial_number": fixture.serial_number or fixture.fixture_id,
        "description": f"{fixture.display_name or fixture.fixture_id} ({fixture.station_type})",
        "location": fixture.location or fixture.site or "",
        "checked_out": fixture.owner or "",
        "cal_due": "",
        "status_notes": message,
        "triggered_by": "mfg_dashboard",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode() or "{}")
            return bool(body.get("ok")), {"status": resp.status, "body": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = body
        return False, {"status": exc.code, "error": parsed}
    except Exception as exc:
        return False, {"error": str(exc)}


def _merge_provider_response(event, key, value):
    current = dict(event.provider_response or {})
    existing = current.get(key)
    if isinstance(existing, list):
        existing.append(value)
    elif existing is None:
        current[key] = [value]
    else:
        current[key] = [existing, value]
    event.provider_response = current


def _maybe_send_fixture_alert(fixture, snapshot):
    alert_level = _status_level(snapshot.overall_status, snapshot.can_run_production)
    if alert_level < STATUS_LEVELS["needs_attention"]:
        return None

    cooldown_hours = int(_alert_env("FIXTURE_ALERT_COOLDOWN_HOURS", default="12"))
    event_key = f"{fixture.fixture_id}:{snapshot.overall_status}:{snapshot.can_run_production}"
    since = datetime.utcnow() - timedelta(hours=cooldown_hours)
    recent = (
        FixtureAlertEvent.query
        .filter(
            FixtureAlertEvent.event_key == event_key,
            FixtureAlertEvent.created_at >= since,
            FixtureAlertEvent.delivery_state.in_(["sent", "not_configured", "failed"]),
        )
        .order_by(FixtureAlertEvent.created_at.desc())
        .first()
    )
    if recent:
        event = FixtureAlertEvent(
            fixture_id=fixture.fixture_id,
            snapshot_id=snapshot.id,
            status=snapshot.overall_status,
            alert_level=alert_level,
            event_key=event_key,
            message=f"Suppressed duplicate fixture alert within {cooldown_hours}h cooldown.",
            delivery_state="suppressed",
        )
        db.session.add(event)
        return event

    recipients = _alert_recipients(fixture, alert_level)
    email_recipients = sorted({
        r.email for r in recipients
        if r.notify_email and r.email
    })
    slack_recipients = sorted({
        r.slack_user_id or r.slack_channel
        for r in recipients
        if r.notify_slack and (r.slack_user_id or r.slack_channel)
    })
    message = _alert_message(fixture, snapshot)
    event = FixtureAlertEvent(
        fixture_id=fixture.fixture_id,
        snapshot_id=snapshot.id,
        status=snapshot.overall_status,
        alert_level=alert_level,
        event_key=event_key,
        message=message,
        email_recipients=email_recipients,
        slack_recipients=slack_recipients,
        channels=[],
        provider_response={},
    )
    db.session.add(event)
    db.session.flush()

    if not recipients:
        event.delivery_state = "no_recipients"
        event.error = "No enabled recipients matched this fixture/status."
        return event

    status_label = (snapshot.overall_status or "unknown").replace("_", " ").upper()
    subject = f"[Fixture Alert] {fixture.fixture_id} — {status_label}"
    html_body = (
        "<div style='font-family:Arial,sans-serif;max-width:640px'>"
        f"<h2>Fixture {fixture.fixture_id} is {status_label}</h2>"
        f"<pre style='white-space:pre-wrap'>{message}</pre>"
        f"<p><a href='{_fixture_url(fixture.fixture_id)}'>Open fixture detail</a></p>"
        "</div>"
    )

    provider_response = {}
    channels = []
    email_ok = slack_ok = False
    if email_recipients and _fixture_email_enabled():
        email_ok, provider_response["email"] = _send_sendgrid_email(
            subject,
            message,
            html_body,
            email_recipients,
            custom_args={
                "alert_event_id": str(event.id),
                "fixture_id": fixture.fixture_id,
                "snapshot_id": str(snapshot.id),
            },
        )
        if email_ok:
            channels.append("email_accepted")
    elif email_recipients:
        provider_response["email"] = {
            "ok": False,
            "disabled": True,
            "reason": "Fixture alert email delivery disabled during development.",
        }

    if slack_recipients or recipients:
        slack_ok, provider_response["slack"] = _send_slack_webhook(fixture, snapshot, message, recipients)
        if slack_ok:
            channels.append("slack")

    if not channels and _fixture_email_enabled():
        toolhub_ok, provider_response["toolhub"] = _send_toolhub_notify(fixture, snapshot, message)
        if toolhub_ok:
            toolhub_channels = provider_response["toolhub"].get("body", {}).get("channels", [])
            channels.extend([f"toolhub:{ch}" for ch in toolhub_channels] or ["toolhub"])

    event.channels = channels
    event.provider_response = provider_response
    event.sent_at = datetime.utcnow() if channels else None

    configured_errors = [
        value.get("error")
        for value in provider_response.values()
        if isinstance(value, dict) and value.get("error")
    ]
    if channels:
        if any("slack" in channel for channel in channels):
            event.delivery_state = "sent"
        else:
            event.delivery_state = "accepted"
    elif any("not configured" in str(err) for err in configured_errors):
        event.delivery_state = "not_configured"
        event.error = "; ".join(str(err) for err in configured_errors)
    else:
        event.delivery_state = "failed"
        event.error = "; ".join(str(err) for err in configured_errors) or "No notification channel succeeded."

    return event


# ---------------------------------------------------------------------------
# Dashboard index
# ---------------------------------------------------------------------------

@main.route("/")
def index():
    return render_template("index.html")


@main.route("/fixtures/<fixture_id>")
def fixture_detail(fixture_id):
    return render_template("fixture_detail.html", fixture_id=fixture_id)


# ---------------------------------------------------------------------------
# API: summary cards
# ---------------------------------------------------------------------------

@main.route("/api/summary")
def api_summary():
    start, end = _date_range()

    q = (
        db.session.query(
            TestRun.product,
            func.count(TestRun.id).label("total"),
            func.sum(case((TestRun.overall_pass.is_(True), 1), else_=0)).label("passed"),
            func.sum(case((TestRun.overall_pass.is_(False), 1), else_=0)).label("failed"),
            func.avg(TestRun.duration_s).label("avg_cycle_s"),
            func.min(TestRun.duration_s).label("min_cycle_s"),
            func.max(TestRun.duration_s).label("max_cycle_s"),
        )
        .filter(TestRun.started_at.between(start, end))
        .group_by(TestRun.product)
        .all()
    )

    rows = []
    for r in q:
        passed = int(r.passed or 0)
        total = int(r.total or 0)
        rows.append({
            "product": r.product,
            "total": total,
            "passed": passed,
            "failed": int(r.failed or 0),
            "pass_rate": round(passed / total * 100, 1) if total else 0,
            "avg_cycle_s": round(r.avg_cycle_s or 0, 1),
            "min_cycle_s": round(r.min_cycle_s or 0, 1),
            "max_cycle_s": round(r.max_cycle_s or 0, 1),
        })

    # Overall totals
    total_all = sum(r["total"] for r in rows)
    passed_all = sum(r["passed"] for r in rows)
    return jsonify({
        "period_days": int(request.args.get("days", 30)),
        "total": total_all,
        "passed": passed_all,
        "failed": total_all - passed_all,
        "pass_rate": round(passed_all / total_all * 100, 1) if total_all else 0,
        "by_product": rows,
    })


# ---------------------------------------------------------------------------
# API: daily throughput (units tested per day)
# ---------------------------------------------------------------------------

@main.route("/api/daily")
def api_daily():
    start, end = _date_range()

    rows = (
        db.session.query(
            func.date(TestRun.started_at).label("day"),
            TestRun.product,
            func.count(TestRun.id).label("total"),
            func.sum(case((TestRun.overall_pass.is_(True), 1), else_=0)).label("passed"),
        )
        .filter(TestRun.started_at.between(start, end))
        .group_by(func.date(TestRun.started_at), TestRun.product)
        .order_by(func.date(TestRun.started_at))
        .all()
    )

    return jsonify([
        {
            "day": str(r.day),
            "product": r.product,
            "total": int(r.total),
            "passed": int(r.passed or 0),
            "failed": int(r.total) - int(r.passed or 0),
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# API: weekly/monthly production counts
# ---------------------------------------------------------------------------

@main.route("/api/production")
def api_production():
    granularity = request.args.get("granularity", "week")  # week | month
    start, _ = _date_range()

    if granularity == "month":
        period_expr = func.to_char(TestRun.started_at, "YYYY-MM")
    else:
        period_expr = func.to_char(TestRun.started_at, "IYYY-IW")

    rows = (
        db.session.query(
            period_expr.label("period"),
            TestRun.product,
            func.count(TestRun.id).label("total"),
            func.sum(case((TestRun.overall_pass.is_(True), 1), else_=0)).label("passed"),
        )
        .filter(TestRun.started_at >= start)
        .group_by("period", TestRun.product)
        .order_by("period")
        .all()
    )

    return jsonify([
        {
            "period": r.period,
            "product": r.product,
            "total": int(r.total),
            "passed": int(r.passed or 0),
            "failed": int(r.total) - int(r.passed or 0),
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# API: cycle time trend (rolling avg per day)
# ---------------------------------------------------------------------------

@main.route("/api/cycle_time")
def api_cycle_time():
    start, end = _date_range()

    rows = (
        db.session.query(
            func.date(TestRun.started_at).label("day"),
            TestRun.product,
            func.avg(TestRun.duration_s).label("avg_s"),
            func.percentile_cont(0.5).within_group(TestRun.duration_s).label("median_s"),
        )
        .filter(
            TestRun.started_at.between(start, end),
            TestRun.duration_s.isnot(None),
        )
        .group_by(func.date(TestRun.started_at), TestRun.product)
        .order_by(func.date(TestRun.started_at))
        .all()
    )

    return jsonify([
        {
            "day": str(r.day),
            "product": r.product,
            "avg_s": round(float(r.avg_s), 1),
            "median_s": round(float(r.median_s), 1),
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# API: top failing tests
# ---------------------------------------------------------------------------

@main.route("/api/failures")
def api_failures():
    start, end = _date_range()

    rows = (
        db.session.query(
            TestResult.test_name,
            TestRun.product,
            func.count(TestResult.id).label("total"),
            func.sum(case((TestResult.passed.is_(False), 1), else_=0)).label("failures"),
        )
        .join(TestRun, TestResult.run_id == TestRun.id)
        .filter(TestRun.started_at.between(start, end))
        .group_by(TestResult.test_name, TestRun.product)
        .order_by(func.sum(case((TestResult.passed.is_(False), 1), else_=0)).desc())
        .limit(20)
        .all()
    )

    return jsonify([
        {
            "test_name": r.test_name,
            "product": r.product,
            "total_runs": int(r.total),
            "failures": int(r.failures or 0),
            "fail_rate": round(int(r.failures or 0) / int(r.total) * 100, 1) if r.total else 0,
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# API: recent test runs (paginated)
# ---------------------------------------------------------------------------

@main.route("/api/runs")
def api_runs():
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))
    product = request.args.get("product")
    passed = request.args.get("passed")

    q = TestRun.query.order_by(TestRun.started_at.desc())
    if product:
        q = q.filter(TestRun.product == product.upper())
    if passed is not None:
        q = q.filter(TestRun.overall_pass == (passed.lower() == "true"))

    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "total": pagination.total,
        "pages": pagination.pages,
        "page": page,
        "runs": [
            {
                "id": r.id,
                "serial": r.serial_number,
                "product": r.product,
                "fixture": r.fixture_id,
                "phase": r.phase,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "duration_s": r.duration_s,
                "pass": r.overall_pass,
                "failure_reason": r.failure_reason,
                "has_log": bool(r.log_s3_key),
            }
            for r in pagination.items
        ],
    })


# ---------------------------------------------------------------------------
# API: First Pass Yield (FPY) per test stage per fixture
# ---------------------------------------------------------------------------

@main.route("/api/fpy")
def api_fpy():
    """
    FPY = % of units that pass a given test stage on the first attempt.
    Grouped by test_name × fixture_id so you can compare fixtures side-by-side.
    A low FPY on one fixture only → fixture problem.
    A low FPY across all fixtures → component lot problem.
    """
    start, end = _date_range()

    rows = (
        db.session.query(
            TestResult.test_name,
            TestRun.fixture_id,
            TestRun.product,
            func.count(TestResult.id).label("total"),
            func.sum(case((TestResult.passed.is_(True), 1), else_=0)).label("passed"),
        )
        .join(TestRun, TestResult.run_id == TestRun.id)
        .filter(
            TestRun.started_at.between(start, end),
            TestRun.fixture_id.isnot(None),
        )
        .group_by(TestResult.test_name, TestRun.fixture_id, TestRun.product)
        .order_by(TestResult.test_name, TestRun.fixture_id)
        .all()
    )

    return jsonify([
        {
            "test_name": r.test_name,
            "fixture_id": r.fixture_id,
            "product": r.product,
            "total": int(r.total),
            "passed": int(r.passed or 0),
            "fpy": round(int(r.passed or 0) / int(r.total) * 100, 1) if r.total else 0,
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# API: Rolled Throughput Yield (RTY) per fixture
# ---------------------------------------------------------------------------

@main.route("/api/rty")
def api_rty():
    """
    RTY = product of FPY across all test stages.
    Represents the probability a unit passes every stage on the first attempt.
    Returned for each fixture + an 'Overall' entry.
    """
    start, end = _date_range()

    def _stage_rows(extra_filters):
        return (
            db.session.query(
                TestResult.test_name,
                func.count(TestResult.id).label("total"),
                func.sum(case((TestResult.passed.is_(True), 1), else_=0)).label("passed"),
            )
            .join(TestRun, TestResult.run_id == TestRun.id)
            .filter(TestRun.started_at.between(start, end), *extra_filters)
            .group_by(TestResult.test_name)
            .all()
        )

    def _rty_from_rows(stage_rows):
        rty = 1.0
        stages = []
        for r in stage_rows:
            total = int(r.total)
            passed = int(r.passed or 0)
            fpy = passed / total if total else 0
            rty *= fpy
            stages.append({
                "test_name": r.test_name,
                "fpy": round(fpy * 100, 1),
                "total": total,
                "passed": passed,
            })
        return round(rty * 100, 1), sorted(stages, key=lambda x: x["test_name"])

    # Per-fixture
    fixtures = (
        db.session.query(TestRun.fixture_id)
        .filter(
            TestRun.started_at.between(start, end),
            TestRun.fixture_id.isnot(None),
        )
        .distinct()
        .order_by(TestRun.fixture_id)
        .all()
    )

    result = []
    for (fixture_id,) in fixtures:
        stage_rows = _stage_rows([TestRun.fixture_id == fixture_id])
        rty, stages = _rty_from_rows(stage_rows)
        result.append({"fixture_id": fixture_id, "rty": rty, "stages": stages})

    # Overall (all fixtures)
    overall_rows = _stage_rows([])
    overall_rty, overall_stages = _rty_from_rows(overall_rows)
    result.insert(0, {"fixture_id": "Overall", "rty": overall_rty, "stages": overall_stages})

    return jsonify(result)


# ---------------------------------------------------------------------------
# API: RTY trend over time per fixture
# ---------------------------------------------------------------------------

@main.route("/api/rty_trend")
def api_rty_trend():
    """
    RTY calculated per fixture per week (or month).
    Plot all fixtures on the same chart:
      - One line drops while others stay flat  →  fixture hardware issue
      - All lines drop together               →  component lot issue
    """
    start, end = _date_range()
    granularity = request.args.get("granularity", "week")

    period_expr = (
        func.to_char(TestRun.started_at, "YYYY-MM")
        if granularity == "month"
        else func.to_char(TestRun.started_at, "IYYY-IW")
    )

    rows = (
        db.session.query(
            period_expr.label("period"),
            TestResult.test_name,
            TestRun.fixture_id,
            func.count(TestResult.id).label("total"),
            func.sum(case((TestResult.passed.is_(True), 1), else_=0)).label("passed"),
        )
        .join(TestRun, TestResult.run_id == TestRun.id)
        .filter(
            TestRun.started_at.between(start, end),
            TestRun.fixture_id.isnot(None),
        )
        .group_by("period", TestResult.test_name, TestRun.fixture_id)
        .order_by("period")
        .all()
    )

    # Accumulate: period → fixture → test_name → fpy
    data = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        total = int(r.total)
        passed = int(r.passed or 0)
        data[r.period][r.fixture_id][r.test_name] = passed / total if total else 0

    result = []
    for period in sorted(data):
        for fixture_id in sorted(data[period]):
            rty = 1.0
            for fpy in data[period][fixture_id].values():
                rty *= fpy
            result.append({
                "period": period,
                "fixture_id": fixture_id,
                "rty": round(rty * 100, 1),
            })

    return jsonify(result)


# ---------------------------------------------------------------------------
# API: measurement quality — metric list, per-fixture distributions, trend
# ---------------------------------------------------------------------------

@main.route("/api/measurement_metrics")
def api_measurement_metrics():
    """Distinct metric names present in the selected period."""
    start, end = _date_range()
    rows = (
        db.session.query(Measurement.metric_name, Measurement.unit)
        .join(TestResult, Measurement.test_result_id == TestResult.id)
        .join(TestRun, TestResult.run_id == TestRun.id)
        .filter(TestRun.started_at.between(start, end))
        .group_by(Measurement.metric_name, Measurement.unit)
        .order_by(Measurement.metric_name)
        .all()
    )
    return jsonify([{"metric": r.metric_name, "unit": r.unit or ""} for r in rows])


@main.route("/api/measurements")
def api_measurements():
    """
    Per-fixture distribution for a single metric.
    Only returns rows where nominal is non-null so error_pct is meaningful.
    error_pct = (value - nominal) / |nominal| * 100
    """
    start, end = _date_range()
    metric = request.args.get("metric", "")
    product = request.args.get("product")

    if not metric:
        return jsonify({"metric": metric, "fixtures": []})

    q = (
        db.session.query(
            TestRun.fixture_id,
            Measurement.value,
            Measurement.nominal,
            Measurement.passed,
            Measurement.tolerance_min,
            Measurement.tolerance_max,
            TestRun.serial_number,
            TestRun.id.label("run_id"),
            TestRun.started_at,
        )
        .join(TestResult, Measurement.test_result_id == TestResult.id)
        .join(TestRun, TestResult.run_id == TestRun.id)
        .filter(
            TestRun.started_at.between(start, end),
            Measurement.metric_name == metric,
            Measurement.nominal.isnot(None),
            TestRun.fixture_id.isnot(None),
        )
    )
    if product:
        q = q.filter(TestRun.product == product.upper())

    rows = q.order_by(TestRun.started_at).all()

    by_fixture = defaultdict(list)
    for r in rows:
        by_fixture[r.fixture_id].append(r)

    result = []
    for fixture_id in sorted(by_fixture):
        pts = by_fixture[fixture_id]
        errors = [
            (r.value - r.nominal) / abs(r.nominal) * 100
            for r in pts if r.nominal
        ]
        values = [r.value for r in pts]
        n = len(errors)
        if n == 0:
            continue

        mean_err = sum(errors) / n
        variance = sum((e - mean_err) ** 2 for e in errors) / max(n - 1, 1)
        std_err = variance ** 0.5

        # Cpk from raw value vs tolerance limits
        cpk = None
        tol_mins = [r.tolerance_min for r in pts if r.tolerance_min is not None]
        tol_maxs = [r.tolerance_max for r in pts if r.tolerance_max is not None]
        if tol_mins and tol_maxs and n > 1:
            mean_val = sum(values) / n
            std_val = (sum((v - mean_val) ** 2 for v in values) / (n - 1)) ** 0.5
            if std_val > 0:
                lsl = sum(tol_mins) / len(tol_mins)
                usl = sum(tol_maxs) / len(tol_maxs)
                cpu = (usl - mean_val) / (3 * std_val)
                cpl = (mean_val - lsl) / (3 * std_val)
                cpk = round(min(cpu, cpl), 2)

        result.append({
            "fixture_id": fixture_id,
            "n": n,
            "mean_error_pct": round(mean_err, 3),
            "std_error_pct": round(std_err, 3),
            "min_error_pct": round(min(errors), 3),
            "max_error_pct": round(max(errors), 3),
            "cpk": cpk,
            "points": [
                {
                    "run_id": r.run_id,
                    "serial": r.serial_number,
                    "value": round(r.value, 4),
                    "nominal": round(r.nominal, 4),
                    "error_pct": round((r.value - r.nominal) / abs(r.nominal) * 100, 3),
                    "passed": r.passed,
                    "ts": r.started_at.isoformat() if r.started_at else None,
                }
                for r in pts
            ],
        })

    return jsonify({"metric": metric, "fixtures": result})


@main.route("/api/measurement_trend")
def api_measurement_trend():
    """Mean error % per fixture per period for a given metric (for drift detection)."""
    start, end = _date_range()
    metric = request.args.get("metric", "")
    granularity = request.args.get("granularity", "week")

    if not metric:
        return jsonify([])

    period_expr = (
        func.to_char(TestRun.started_at, "YYYY-MM")
        if granularity == "month"
        else func.to_char(TestRun.started_at, "IYYY-IW")
    )

    rows = (
        db.session.query(
            period_expr.label("period"),
            TestRun.fixture_id,
            func.avg(Measurement.value).label("mean_value"),
            func.avg(Measurement.nominal).label("mean_nominal"),
            func.stddev_samp(Measurement.value).label("std_value"),
            func.count(Measurement.id).label("n"),
        )
        .join(TestResult, Measurement.test_result_id == TestResult.id)
        .join(TestRun, TestResult.run_id == TestRun.id)
        .filter(
            TestRun.started_at.between(start, end),
            Measurement.metric_name == metric,
            Measurement.nominal.isnot(None),
            TestRun.fixture_id.isnot(None),
        )
        .group_by("period", TestRun.fixture_id)
        .order_by("period")
        .all()
    )

    return jsonify([
        {
            "period": r.period,
            "fixture_id": r.fixture_id,
            "mean_error_pct": round(
                (float(r.mean_value) - float(r.mean_nominal)) / abs(float(r.mean_nominal)) * 100, 3
            ) if r.mean_nominal else None,
            "std_error_pct": round(
                float(r.std_value or 0) / abs(float(r.mean_nominal or 1)) * 100, 3
            ) if r.mean_nominal else None,
            "n": int(r.n),
        }
        for r in rows
    ])


# ---------------------------------------------------------------------------
# API: fixture fleet health and drill-down data
# ---------------------------------------------------------------------------

@main.route("/api/fixtures")
def api_fixtures():
    fixtures = (
        Fixture.query
        .order_by(Fixture.site, Fixture.line, Fixture.station_type, Fixture.fixture_id)
        .all()
    )
    return jsonify([_fixture_payload(f) for f in fixtures])


@main.route("/api/fixture_alert_recipients", methods=["GET", "POST"])
def api_fixture_alert_recipients():
    if request.method == "GET":
        recipients = FixtureAlertRecipient.query.order_by(FixtureAlertRecipient.name).all()
        return jsonify([_recipient_payload(r) for r in recipients])

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400
    if not data.get("name"):
        return jsonify({"error": "missing name"}), 400
    if not data.get("email") and not data.get("slack_user_id") and not data.get("slack_channel"):
        return jsonify({"error": "provide email, slack_user_id, or slack_channel"}), 400

    recipient = FixtureAlertRecipient(
        name=data["name"],
        email=data.get("email"),
        slack_user_id=data.get("slack_user_id"),
        slack_channel=data.get("slack_channel"),
        notify_email=bool(data.get("notify_email", True)),
        notify_slack=bool(data.get("notify_slack", True)),
        enabled=bool(data.get("enabled", True)),
        min_status=data.get("min_status") or "needs_attention",
        fixture_id=data.get("fixture_id"),
        station_type=data.get("station_type"),
        notes=data.get("notes"),
    )
    db.session.add(recipient)
    db.session.commit()
    return jsonify(_recipient_payload(recipient)), 201


@main.route("/api/fixture_alert_recipients/<int:recipient_id>", methods=["PATCH", "DELETE"])
def api_fixture_alert_recipient(recipient_id):
    recipient = db.get_or_404(FixtureAlertRecipient, recipient_id)
    if request.method == "DELETE":
        db.session.delete(recipient)
        db.session.commit()
        return jsonify({"ok": True})

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400

    fields = [
        "name", "email", "slack_user_id", "slack_channel", "notify_email",
        "notify_slack", "enabled", "min_status", "fixture_id", "station_type", "notes",
    ]
    for field in fields:
        if field in data:
            setattr(recipient, field, data[field])
    db.session.commit()
    return jsonify(_recipient_payload(recipient))


@main.route("/api/fixture_alert_events")
def api_fixture_alert_events():
    fixture_id = request.args.get("fixture_id")
    limit = min(int(request.args.get("limit", 50)), 200)
    query = FixtureAlertEvent.query.order_by(FixtureAlertEvent.created_at.desc())
    if fixture_id:
        query = query.filter(FixtureAlertEvent.fixture_id == fixture_id)
    return jsonify([_alert_event_payload(e) for e in query.limit(limit).all()])


@main.route("/api/fixture_alert_sendgrid_events", methods=["POST"])
def api_fixture_alert_sendgrid_events():
    """
    Receive SendGrid Event Webhook records and attach them to fixture alerts.

    Configure SendGrid Event Webhook to POST email events here. The dashboard
    includes alert_event_id, fixture_id, and snapshot_id as custom_args on
    direct fixture-alert emails so delivery/bounce/drop status can be audited.
    """
    events = request.get_json(force=True, silent=True)
    if not isinstance(events, list):
        return jsonify({"error": "expected SendGrid event list"}), 400

    updated = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        alert_event_id = item.get("alert_event_id")
        if not alert_event_id:
            continue
        event = db.session.get(FixtureAlertEvent, int(alert_event_id))
        if not event:
            continue

        sendgrid_event = item.get("event")
        _merge_provider_response(event, "sendgrid_events", item)
        if sendgrid_event == "delivered":
            event.delivery_state = "delivered"
            event.sent_at = event.sent_at or datetime.utcnow()
        elif sendgrid_event in {"bounce", "dropped", "deferred", "spamreport"}:
            event.delivery_state = "failed"
            event.error = item.get("reason") or item.get("response") or sendgrid_event
        elif sendgrid_event in {"processed", "deferred"} and event.delivery_state == "pending":
            event.delivery_state = "accepted"
        updated += 1

    db.session.commit()
    return jsonify({"ok": True, "updated": updated})


@main.route("/api/fixtures/<fixture_id>")
def api_fixture_detail(fixture_id):
    fixture = db.get_or_404(Fixture, fixture_id)
    snapshots = (
        FixtureHealthSnapshot.query
        .filter(FixtureHealthSnapshot.fixture_id == fixture_id)
        .order_by(FixtureHealthSnapshot.captured_at.desc())
        .limit(50)
        .all()
    )
    latest = snapshots[0] if snapshots else None
    recent_runs = (
        TestRun.query
        .filter(TestRun.fixture_id == fixture_id)
        .order_by(TestRun.started_at.desc())
        .limit(20)
        .all()
    )

    return jsonify({
        "fixture": _fixture_payload(fixture, include_snapshot=True),
        "components": [_component_payload(c) for c in fixture.components],
        "checks": [_check_payload(c) for c in latest.checks] if latest else [],
        "history": [_snapshot_payload(s) for s in snapshots],
        "alert_events": [
            _alert_event_payload(e)
            for e in (
                FixtureAlertEvent.query
                .filter(FixtureAlertEvent.fixture_id == fixture_id)
                .order_by(FixtureAlertEvent.created_at.desc())
                .limit(20)
                .all()
            )
        ],
        "recent_runs": [
            {
                "id": r.id,
                "serial": r.serial_number,
                "product": r.product,
                "phase": r.phase,
                "started_at": _iso(r.started_at),
                "duration_s": r.duration_s,
                "pass": r.overall_pass,
                "failure_reason": r.failure_reason,
                "has_log": bool(r.log_s3_key),
            }
            for r in recent_runs
        ],
    })


@main.route("/api/fixture_health", methods=["POST"])
def api_fixture_health_ingest():
    """
    Accept a fixture health snapshot from a fixture PC or support script.

    Expected shape:
    {
      "fixture_id": "GDL-FCT-01",
      "fixture": {"station_type": "fct", "hostname": "gdl-fct-01", ...},
      "snapshot": {"overall_status": "ok", "can_run_production": true, ...},
      "components": [{"component_type": "sensor", "name": "LED Sensor Board", ...}],
      "checks": [{"category": "pc", "name": "disk_space", "status": "ok", ...}]
    }
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400

    fixture_data = data.get("fixture") or {}
    fixture_id = data.get("fixture_id") or fixture_data.get("fixture_id")
    if not fixture_id:
        return jsonify({"error": "missing fixture_id"}), 400

    fixture = db.session.get(Fixture, fixture_id)
    if fixture is None:
        fixture = Fixture(
            fixture_id=fixture_id,
            station_type=fixture_data.get("station_type") or data.get("station_type") or "unknown",
            site=fixture_data.get("site") or data.get("site") or "Guadalajara",
        )
        db.session.add(fixture)

    fixture_fields = [
        "display_name", "station_type", "site", "line", "location", "hostname",
        "product_family", "asset_tag", "serial_number", "owner", "notes",
    ]
    for field in fixture_fields:
        if field in fixture_data and fixture_data[field] is not None:
            setattr(fixture, field, fixture_data[field])

    snapshot_data = data.get("snapshot") or data
    snapshot = FixtureHealthSnapshot(
        fixture_id=fixture_id,
        captured_at=_parse_dt(snapshot_data.get("captured_at")) or datetime.utcnow(),
        reported_at=_parse_dt(snapshot_data.get("reported_at")) or datetime.utcnow(),
        source=snapshot_data.get("source") or "fixture-agent",
        overall_status=snapshot_data.get("overall_status") or "unknown",
        can_run_production=bool(snapshot_data.get("can_run_production", False)),
        health_score=snapshot_data.get("health_score"),
        summary=snapshot_data.get("summary"),
        support_bundle_uri=snapshot_data.get("support_bundle_uri"),
        repo_sha=snapshot_data.get("repo_sha"),
        repo_branch=snapshot_data.get("repo_branch"),
        repo_dirty=snapshot_data.get("repo_dirty"),
        software_version=snapshot_data.get("software_version"),
        agent_version=snapshot_data.get("agent_version"),
        config_hash=snapshot_data.get("config_hash"),
        uptime_s=snapshot_data.get("uptime_s"),
        load_avg=snapshot_data.get("load_avg"),
        cpu_temp_c=snapshot_data.get("cpu_temp_c"),
        disk_free_gb=snapshot_data.get("disk_free_gb"),
        disk_used_pct=snapshot_data.get("disk_used_pct"),
        time_sync_ok=snapshot_data.get("time_sync_ok"),
    )
    db.session.add(snapshot)
    db.session.flush()

    for c in data.get("checks", snapshot_data.get("checks", [])):
        db.session.add(FixtureHealthCheck(
            snapshot_id=snapshot.id,
            category=c.get("category", "general"),
            name=c.get("name", "unknown"),
            status=c.get("status", "unknown"),
            severity=c.get("severity", "info"),
            blocks_production=bool(c.get("blocks_production", False)),
            message=c.get("message"),
            observed=c.get("observed"),
            expected=c.get("expected"),
            duration_ms=c.get("duration_ms"),
            details=c.get("details") or c.get("metadata"),
        ))

    for c in data.get("components", []):
        component = (
            FixtureComponent.query
            .filter(
                FixtureComponent.fixture_id == fixture_id,
                FixtureComponent.component_type == c.get("component_type", "unknown"),
                FixtureComponent.name == c.get("name", "unknown"),
            )
            .one_or_none()
        )
        if component is None:
            component = FixtureComponent(
                fixture_id=fixture_id,
                component_type=c.get("component_type", "unknown"),
                name=c.get("name", "unknown"),
            )
            db.session.add(component)
        for field in ["status", "version", "serial_number", "address", "notes"]:
            if field in c:
                setattr(component, field, c[field])
        component.last_seen_at = _parse_dt(c.get("last_seen_at")) or snapshot.captured_at
        component.details = c.get("details") or c.get("metadata") or component.details

    alert_event = _maybe_send_fixture_alert(fixture, snapshot)
    db.session.commit()
    return jsonify({
        "fixture_id": fixture_id,
        "snapshot_id": snapshot.id,
        "alert_event_id": alert_event.id if alert_event else None,
        "alert_delivery_state": alert_event.delivery_state if alert_event else None,
    }), 201


# ---------------------------------------------------------------------------
# API: presigned S3 URL for a run's log file
# ---------------------------------------------------------------------------

@main.route("/api/runs/<int:run_id>/log_url")
def api_log_url(run_id):
    run = db.get_or_404(TestRun, run_id)
    if not run.log_s3_key:
        return jsonify({"error": "no log attached to this run"}), 404

    # Local dev mode — serve directly from filesystem
    local_dir = current_app.config.get("LOCAL_LOG_DIR", "")
    if local_dir:
        return jsonify({"url": f"/api/runs/{run_id}/log", "expires_in_s": None})

    # Production — presigned S3 URL
    if not _boto3_available:
        return jsonify({"error": "boto3 not installed on server"}), 503

    bucket = current_app.config["AWS_S3_BUCKET"]
    region = current_app.config["AWS_REGION"]
    expiry = current_app.config["LOG_URL_EXPIRY_S"]

    try:
        s3 = boto3.client("s3", region_name=region)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": run.log_s3_key},
            ExpiresIn=expiry,
        )
        return jsonify({"url": url, "expires_in_s": expiry})
    except (BotoCoreError, ClientError) as exc:
        return jsonify({"error": str(exc)}), 502


@main.route("/api/runs/<int:run_id>/log")
def api_log_file(run_id):
    """Serve a log file from the local directory (dev/staging only)."""
    local_dir = current_app.config.get("LOCAL_LOG_DIR", "")
    if not local_dir:
        return jsonify({"error": "local log serving not configured — use log_url for S3"}), 404

    run = db.get_or_404(TestRun, run_id)
    if not run.log_s3_key:
        return jsonify({"error": "no log attached to this run"}), 404

    full_path = os.path.join(local_dir, run.log_s3_key)
    if not os.path.exists(full_path):
        return jsonify({"error": "log file not found on disk"}), 404

    try:
        with gzip.open(full_path, "rt", encoding="utf-8") as f:
            content = f.read()
        filename = f"run_{run_id}_{run.serial_number}.log"
        return Response(
            content,
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        return jsonify({"error": f"could not read log: {exc}"}), 500


# ---------------------------------------------------------------------------
# API: ingest — POST test results from the fixture harness
# ---------------------------------------------------------------------------

@main.route("/api/ingest", methods=["POST"])
def api_ingest():
    """
    Accept a JSON payload from the manufacturing test harness and persist it.

    Expected payload schema:
    {
        "serial_number": "SN12345",
        "product": "C1",
        "fixture_id": "BOX-01",
        "phase": "charging",
        "started_at": "2024-01-15T09:30:00",   // optional ISO8601 UTC
        "ended_at": "2024-01-15T09:36:22",
        "duration_s": 382.1,
        "overall_pass": true,
        "failure_reason": null,
        "results": [
            {
                "test_name": "intercom_test",
                "started_at": "...",
                "ended_at": "...",
                "duration_s": 4.2,
                "passed": true,
                "failure_reason": null,
                "measurements": [
                    {"metric": "response_time_ms", "value": 42.1, "unit": "ms",
                     "tolerance_min": 0, "tolerance_max": 500, "passed": true}
                ]
            }
        ]
    }
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400

    required = {"serial_number", "product", "overall_pass"}
    missing = required - set(data.keys())
    if missing:
        return jsonify({"error": f"missing fields: {missing}"}), 400

    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    run = TestRun(
        serial_number=data["serial_number"],
        product=data["product"].upper(),
        fixture_id=data.get("fixture_id"),
        phase=data.get("phase"),
        started_at=_parse_dt(data.get("started_at")) or datetime.utcnow(),
        ended_at=_parse_dt(data.get("ended_at")),
        duration_s=data.get("duration_s"),
        overall_pass=bool(data["overall_pass"]),
        failure_reason=data.get("failure_reason"),
        log_s3_key=data.get("log_s3_key"),
    )
    db.session.add(run)
    db.session.flush()  # get run.id before inserting children

    for r in data.get("results", []):
        result = TestResult(
            run_id=run.id,
            test_name=r.get("test_name", "unknown"),
            started_at=_parse_dt(r.get("started_at")),
            ended_at=_parse_dt(r.get("ended_at")),
            duration_s=r.get("duration_s"),
            passed=bool(r.get("passed", False)),
            failure_reason=r.get("failure_reason"),
        )
        db.session.add(result)
        db.session.flush()

        for m in r.get("measurements", []):
            db.session.add(Measurement(
                test_result_id=result.id,
                metric_name=m.get("metric", "unknown"),
                value=float(m["value"]),
                nominal=float(m["nominal"]) if m.get("nominal") is not None else None,
                unit=m.get("unit"),
                tolerance_min=m.get("tolerance_min"),
                tolerance_max=m.get("tolerance_max"),
                passed=m.get("passed"),
            ))

    db.session.commit()
    return jsonify({"run_id": run.id}), 201


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@main.route("/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 500
