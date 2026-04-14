from collections import defaultdict
from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify, request, current_app
from sqlalchemy import func, case, and_
from app import db
from app.models import TestRun, TestResult, Measurement

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _boto3_available = True
except ImportError:
    _boto3_available = False

main = Blueprint("main", __name__)

# ---------------------------------------------------------------------------
# Helper: date-range from query param or default (last 30 days)
# ---------------------------------------------------------------------------

def _date_range():
    end = datetime.utcnow()
    days = int(request.args.get("days", 30))
    start = end - timedelta(days=days)
    return start, end


# ---------------------------------------------------------------------------
# Dashboard index
# ---------------------------------------------------------------------------

@main.route("/")
def index():
    return render_template("index.html")


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
# API: presigned S3 URL for a run's log file
# ---------------------------------------------------------------------------

@main.route("/api/runs/<int:run_id>/log_url")
def api_log_url(run_id):
    run = db.get_or_404(TestRun, run_id)
    if not run.log_s3_key:
        return jsonify({"error": "no log attached to this run"}), 404

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
