"use strict";

// ── Chart.js global defaults ───────────────────────────────────────────────
Chart.defaults.color = "#7b7fa8";
Chart.defaults.borderColor = "#2e3150";
Chart.defaults.font.family = "'Inter', 'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 12;

const PASS_COLOR    = "#27c98a";
const FAIL_COLOR    = "#ef4444";
const C1_COLOR      = "#4f8ef7";
const C2_COLOR      = "#a78bfa";
const GRID_COLOR    = "rgba(46,49,80,.6)";
const FIXTURE_COLORS = ["#4f8ef7", "#a78bfa", "#f59e0b", "#34d399", "#fb7185", "#38bdf8"];

// ── State ──────────────────────────────────────────────────────────────────
let charts = {};
let runsPage = 1;
let runsTotal = 0;

// ── Utility helpers ────────────────────────────────────────────────────────
const fmtDur = s => s == null ? "—" : `${Math.floor(s/60)}m ${Math.round(s%60)}s`;
const fmtDt  = iso => iso ? new Date(iso).toLocaleString() : "—";
const qs     = id => document.getElementById(id);

function makeDataset(label, data, color, extra = {}) {
  return { label, data, backgroundColor: color + "cc", borderColor: color,
           borderWidth: 2, ...extra };
}

function gridOpts() {
  return { color: GRID_COLOR };
}

function destroyChart(key) {
  if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

// ── Main refresh ───────────────────────────────────────────────────────────
async function refreshAll() {
  const days = qs("period-select").value;
  const gran = qs("gran-select").value;
  qs("gran-label").textContent = gran === "month" ? "Monthly" : "Weekly";

  await Promise.all([
    loadSummary(days),
    loadDaily(days),
    loadProduction(days, gran),
    loadCycleTime(days),
    loadFpyRty(days, gran),
    loadFailures(days),
    loadMeasurements(days, gran),
  ]);

  runsPage = 1;
  await loadRuns(true);

  qs("last-refresh").textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

// ── Summary cards ──────────────────────────────────────────────────────────
async function loadSummary(days) {
  const data = await fetch(`/api/summary?days=${days}`).then(r => r.json());

  qs("val-total").textContent  = data.total.toLocaleString();
  qs("val-passed").textContent = data.passed.toLocaleString();
  qs("val-failed").textContent = data.failed.toLocaleString();
  qs("sub-total").textContent  = `Last ${days} days`;
  qs("sub-pass-rate").textContent = `${data.pass_rate}% pass rate`;
  qs("sub-fail-rate").textContent = `${(100 - data.pass_rate).toFixed(1)}% fail rate`;

  const avgCycle = data.by_product.reduce((s, r) => s + (r.avg_cycle_s * r.total), 0) /
                   (data.total || 1);
  qs("val-cycle").textContent  = fmtDur(avgCycle);
  qs("sub-cycle").textContent  = "across all products";

  // Product bar chart
  destroyChart("product");
  const products = data.by_product.map(r => r.product);
  charts.product = new Chart(qs("chart-product-bar"), {
    type: "bar",
    data: {
      labels: products,
      datasets: [
        makeDataset("Passed", data.by_product.map(r => r.passed), PASS_COLOR),
        makeDataset("Failed", data.by_product.map(r => r.failed), FAIL_COLOR),
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { stacked: true, grid: gridOpts() },
        y: { stacked: true, grid: gridOpts(), beginAtZero: true },
      },
    },
  });

  // Pass/fail donut
  destroyChart("donut");
  charts.donut = new Chart(qs("chart-passfail-donut"), {
    type: "doughnut",
    data: {
      labels: ["Passed", "Failed"],
      datasets: [{
        data: [data.passed, data.failed],
        backgroundColor: [PASS_COLOR + "cc", FAIL_COLOR + "cc"],
        borderColor: [PASS_COLOR, FAIL_COLOR],
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      cutout: "65%",
      plugins: {
        legend: { position: "bottom" },
        tooltip: {
          callbacks: {
            label: ctx => {
              const pct = ((ctx.parsed / data.total) * 100).toFixed(1);
              return ` ${ctx.label}: ${ctx.parsed} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

// ── Daily throughput ───────────────────────────────────────────────────────
async function loadDaily(days) {
  const rows = await fetch(`/api/daily?days=${days}`).then(r => r.json());

  // Pivot by product
  const days_set = [...new Set(rows.map(r => r.day))].sort();
  const products = [...new Set(rows.map(r => r.product))];
  const byProduct = {};
  products.forEach(p => { byProduct[p] = { passed: {}, failed: {} }; });
  rows.forEach(r => {
    byProduct[r.product].passed[r.day] = r.passed;
    byProduct[r.product].failed[r.day] = r.failed;
  });

  const colors = { C1: C1_COLOR, C2: C2_COLOR };
  const datasets = [];
  products.forEach((p, i) => {
    const col = colors[p] || `hsl(${i * 80}, 70%, 60%)`;
    datasets.push(makeDataset(`${p} Pass`, days_set.map(d => byProduct[p].passed[d] || 0), col, { stack: p }));
    datasets.push(makeDataset(`${p} Fail`, days_set.map(d => byProduct[p].failed[d] || 0), FAIL_COLOR, { stack: p, borderColor: FAIL_COLOR }));
  });

  destroyChart("daily");
  charts.daily = new Chart(qs("chart-daily"), {
    type: "bar",
    data: { labels: days_set, datasets },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { grid: gridOpts() },
        y: { grid: gridOpts(), beginAtZero: true },
      },
    },
  });
}

// ── Production trend ───────────────────────────────────────────────────────
async function loadProduction(days, gran) {
  const rows = await fetch(`/api/production?days=${days}&granularity=${gran}`).then(r => r.json());

  const periods  = [...new Set(rows.map(r => r.period))].sort();
  const products = [...new Set(rows.map(r => r.product))];
  const byProduct = {};
  products.forEach(p => { byProduct[p] = {}; });
  rows.forEach(r => { byProduct[r.product][r.period] = r.total; });

  const colors = { C1: C1_COLOR, C2: C2_COLOR };
  const datasets = products.map((p, i) => {
    const col = colors[p] || `hsl(${i * 80}, 70%, 60%)`;
    return makeDataset(p, periods.map(pd => byProduct[p][pd] || 0), col, {
      type: "line", fill: true, tension: 0.3, pointRadius: 4,
    });
  });

  destroyChart("production");
  charts.production = new Chart(qs("chart-production"), {
    type: "line",
    data: { labels: periods, datasets },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { grid: gridOpts() },
        y: { grid: gridOpts(), beginAtZero: true },
      },
    },
  });
}

// ── Cycle time ─────────────────────────────────────────────────────────────
async function loadCycleTime(days) {
  const rows = await fetch(`/api/cycle_time?days=${days}`).then(r => r.json());

  const days_set = [...new Set(rows.map(r => r.day))].sort();
  const products = [...new Set(rows.map(r => r.product))];
  const byProduct = {};
  products.forEach(p => { byProduct[p] = {}; });
  rows.forEach(r => { byProduct[r.product][r.day] = r.avg_s; });

  const colors = { C1: C1_COLOR, C2: C2_COLOR };
  const datasets = products.map((p, i) => {
    const col = colors[p] || `hsl(${i * 80}, 70%, 60%)`;
    return makeDataset(`${p} avg`, days_set.map(d => byProduct[p][d] || null), col, {
      tension: 0.3, pointRadius: 3, fill: false, spanGaps: true,
    });
  });

  destroyChart("cycle");
  charts.cycle = new Chart(qs("chart-cycle"), {
    type: "line",
    data: { labels: days_set, datasets },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { grid: gridOpts() },
        y: {
          grid: gridOpts(),
          beginAtZero: false,
          ticks: { callback: v => fmtDur(v) },
        },
      },
    },
  });
}

// ── FPY / RTY ──────────────────────────────────────────────────────────────
async function loadFpyRty(days, gran) {
  const [fpyRows, rtyRows, rtyTrend] = await Promise.all([
    fetch(`/api/fpy?days=${days}`).then(r => r.json()),
    fetch(`/api/rty?days=${days}`).then(r => r.json()),
    fetch(`/api/rty_trend?days=${days}&granularity=${gran}`).then(r => r.json()),
  ]);

  // ── RTY summary cards ────────────────────────────────────────────────────
  const container = qs("rty-cards");
  container.innerHTML = rtyRows.map(f => {
    const rty = f.rty;
    const cls = rty >= 90 ? "pass" : rty >= 75 ? "warn" : "fail";
    const overall = rtyRows.find(x => x.fixture_id === "Overall");
    const delta = f.fixture_id !== "Overall" && overall
      ? (rty - overall.rty).toFixed(1)
      : null;
    const deltaHtml = delta !== null
      ? `<span class="rty-delta ${parseFloat(delta) >= 0 ? "pos" : "neg"}">${parseFloat(delta) >= 0 ? "+" : ""}${delta}% vs avg</span>`
      : "";
    return `
      <div class="rty-card ${cls}">
        <div class="rty-fixture">${f.fixture_id}</div>
        <div class="rty-value">${rty}%</div>
        ${deltaHtml}
        <div class="rty-stages">${f.stages.map(s =>
          `<div class="rty-stage ${s.fpy < 90 ? 'low' : ''}">
             <span>${s.test_name.replace(/_test$|_check$/, "")}</span>
             <span>${s.fpy}%</span>
           </div>`
        ).join("")}</div>
      </div>`;
  }).join("");

  // ── FPY grouped bar (test stage on y-axis, one dataset per fixture) ──────
  const fixtures = [...new Set(fpyRows.map(r => r.fixture_id))].sort();
  const testNames = [...new Set(fpyRows.map(r => r.test_name))].sort();

  // Index for quick lookup
  const idx = {};
  fpyRows.forEach(r => { idx[`${r.fixture_id}::${r.test_name}`] = r.fpy; });

  const fpyDatasets = fixtures.map((fid, i) => ({
    label: fid,
    data: testNames.map(t => idx[`${fid}::${t}`] ?? null),
    backgroundColor: FIXTURE_COLORS[i % FIXTURE_COLORS.length] + "bb",
    borderColor:     FIXTURE_COLORS[i % FIXTURE_COLORS.length],
    borderWidth: 1,
  }));

  destroyChart("fpy");
  charts.fpy = new Chart(qs("chart-fpy"), {
    type: "bar",
    data: {
      labels: testNames.map(t => t.replace(/_test$|_check$/, "")),
      datasets: fpyDatasets,
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: {
        legend: { position: "top" },
        tooltip: {
          callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.x ?? "—"}%` },
        },
      },
      scales: {
        x: {
          grid: gridOpts(),
          min: 80,
          max: 100,
          ticks: { callback: v => `${v}%` },
        },
        y: { grid: gridOpts() },
      },
    },
  });

  // ── RTY trend line (one line per fixture) ────────────────────────────────
  const periods   = [...new Set(rtyTrend.map(r => r.period))].sort();
  const tFixtures = [...new Set(rtyTrend.map(r => r.fixture_id))].sort();
  const rtyIdx    = {};
  rtyTrend.forEach(r => { rtyIdx[`${r.fixture_id}::${r.period}`] = r.rty; });

  const rtyDatasets = tFixtures.map((fid, i) => ({
    label: fid,
    data: periods.map(p => rtyIdx[`${fid}::${p}`] ?? null),
    borderColor: FIXTURE_COLORS[i % FIXTURE_COLORS.length],
    backgroundColor: FIXTURE_COLORS[i % FIXTURE_COLORS.length] + "22",
    tension: 0.3,
    pointRadius: 4,
    fill: false,
    spanGaps: true,
  }));

  destroyChart("rtyTrend");
  charts.rtyTrend = new Chart(qs("chart-rty-trend"), {
    type: "line",
    data: { labels: periods, datasets: rtyDatasets },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { grid: gridOpts() },
        y: {
          grid: gridOpts(),
          min: 50,
          max: 100,
          ticks: { callback: v => `${v}%` },
        },
      },
    },
  });
}

// ── Measurement Quality ────────────────────────────────────────────────────
async function loadMeasurements(days, gran) {
  const metricSel = qs("mq-metric-select");

  // Populate metric dropdown once (or when empty after a period change with no data)
  if (!metricSel.dataset.loaded) {
    const metrics = await fetch(`/api/measurement_metrics?days=${days}`).then(r => r.json());
    metricSel.innerHTML = metrics.length
      ? metrics.map(m => `<option value="${m.metric}">${m.metric}${m.unit ? " (" + m.unit + ")" : ""}</option>`).join("")
      : `<option value="">No data for this period</option>`;
    if (metrics.length) metricSel.dataset.loaded = "1";
  }

  const metric = metricSel.value;
  if (!metric) return;

  const [mqData, trendData] = await Promise.all([
    fetch(`/api/measurements?metric=${encodeURIComponent(metric)}&days=${days}`).then(r => r.json()),
    fetch(`/api/measurement_trend?metric=${encodeURIComponent(metric)}&days=${days}&granularity=${gran}`).then(r => r.json()),
  ]);

  const fixtures = mqData.fixtures || [];

  // ── Scatter: x = fixture index + deterministic jitter, y = error_pct ────
  const scatterDatasets = fixtures.map((f, fi) => ({
    label: f.fixture_id,
    data: f.points.map((p, pi) => ({
      x: fi + ((pi % 9) - 4) * 0.04,   // deterministic jitter ±0.16
      y: p.error_pct,
      run_id: p.run_id,
      serial: p.serial,
    })),
    backgroundColor: FIXTURE_COLORS[fi % FIXTURE_COLORS.length] + "99",
    borderColor:     FIXTURE_COLORS[fi % FIXTURE_COLORS.length],
    pointRadius: 4,
    pointHoverRadius: 6,
  }));

  // Add mean markers as a separate dataset per fixture
  fixtures.forEach((f, fi) => {
    scatterDatasets.push({
      label: `${f.fixture_id} mean`,
      data: [{ x: fi, y: f.mean_error_pct }],
      backgroundColor: "#ffffff",
      borderColor:     FIXTURE_COLORS[fi % FIXTURE_COLORS.length],
      pointRadius: 8,
      pointStyle: "crossRot",
      pointHoverRadius: 10,
    });
  });

  destroyChart("mqScatter");
  charts.mqScatter = new Chart(qs("chart-mq-scatter"), {
    type: "scatter",
    data: { datasets: scatterDatasets },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              if (ctx.raw.serial) return ` ${ctx.raw.serial}: ${ctx.raw.y?.toFixed(3)}%`;
              return ` mean: ${ctx.raw.y?.toFixed(3)}%`;
            },
          },
        },
      },
      scales: {
        x: {
          grid: gridOpts(),
          min: -0.5,
          max: fixtures.length - 0.5,
          ticks: {
            stepSize: 1,
            callback: v => {
              const idx = Math.round(v);
              return fixtures[idx] ? fixtures[idx].fixture_id : "";
            },
          },
        },
        y: {
          grid: gridOpts(),
          ticks: { callback: v => `${v}%` },
          title: { display: true, text: "Error %", color: "#7b7fa8" },
        },
      },
    },
  });

  // ── Trend: one line per fixture ───────────────────────────────────────────
  const periods   = [...new Set(trendData.map(r => r.period))].sort();
  const tFixtures = [...new Set(trendData.map(r => r.fixture_id))].sort();
  const tIdx = {};
  trendData.forEach(r => { tIdx[`${r.fixture_id}::${r.period}`] = r.mean_error_pct; });

  const trendDatasets = tFixtures.map((fid, i) => ({
    label: fid,
    data: periods.map(p => tIdx[`${fid}::${p}`] ?? null),
    borderColor:     FIXTURE_COLORS[i % FIXTURE_COLORS.length],
    backgroundColor: FIXTURE_COLORS[i % FIXTURE_COLORS.length] + "22",
    tension: 0.3,
    pointRadius: 4,
    fill: false,
    spanGaps: true,
  }));

  // Zero-error reference line
  trendDatasets.push({
    label: "Zero error",
    data: periods.map(() => 0),
    borderColor: "#ffffff33",
    borderDash: [4, 4],
    pointRadius: 0,
    fill: false,
  });

  destroyChart("mqTrend");
  charts.mqTrend = new Chart(qs("chart-mq-trend"), {
    type: "line",
    data: { labels: periods, datasets: trendDatasets },
    options: {
      responsive: true,
      plugins: { legend: { position: "top" } },
      scales: {
        x: { grid: gridOpts() },
        y: {
          grid: gridOpts(),
          ticks: { callback: v => `${v}%` },
          title: { display: true, text: "Mean Error %", color: "#7b7fa8" },
        },
      },
    },
  });

  // ── Summary table ─────────────────────────────────────────────────────────
  const tbody = qs("tbl-mq").querySelector("tbody");
  if (!fixtures.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-muted)">No measurement data for this metric/period</td></tr>`;
    return;
  }
  tbody.innerHTML = fixtures.map(f => {
    const cpkCol = f.cpk === null ? "" :
      f.cpk >= 1.33 ? `color:${PASS_COLOR}` :
      f.cpk >= 1.0  ? `color:#f59e0b` : `color:${FAIL_COLOR}`;
    const errSign = v => (v > 0 ? "+" : "") + v.toFixed(3) + "%";
    return `<tr>
      <td>${f.fixture_id}</td>
      <td>${f.n}</td>
      <td>${errSign(f.mean_error_pct)}</td>
      <td>±${f.std_error_pct.toFixed(3)}%</td>
      <td>${errSign(f.min_error_pct)}</td>
      <td>${errSign(f.max_error_pct)}</td>
      <td style="${cpkCol}">${f.cpk ?? "—"}</td>
    </tr>`;
  }).join("");
}

// ── Failures ───────────────────────────────────────────────────────────────
async function loadFailures(days) {
  const rows = await fetch(`/api/failures?days=${days}`).then(r => r.json());

  const top10 = rows.slice(0, 10);

  destroyChart("failures");
  charts.failures = new Chart(qs("chart-failures"), {
    type: "bar",
    data: {
      labels: top10.map(r => `${r.test_name} (${r.product})`),
      datasets: [makeDataset("Failures", top10.map(r => r.failures), FAIL_COLOR)],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: gridOpts(), beginAtZero: true },
        y: { grid: gridOpts() },
      },
    },
  });

  // Table
  const tbody = qs("tbl-failures").querySelector("tbody");
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${r.test_name}</td>
      <td>${r.product}</td>
      <td>${r.total_runs}</td>
      <td>${r.failures}</td>
      <td style="color:${r.fail_rate > 10 ? FAIL_COLOR : r.fail_rate > 5 ? "#f59e0b" : PASS_COLOR}">
        ${r.fail_rate}%
      </td>
    </tr>
  `).join("");
}

// ── Recent runs ────────────────────────────────────────────────────────────
async function loadRuns(reset = false) {
  if (reset) {
    runsPage = 1;
    qs("tbl-runs").querySelector("tbody").innerHTML = "";
  }

  const product  = qs("filter-product").value;
  const passed   = qs("filter-pass").value;
  const params   = new URLSearchParams({ page: runsPage, per_page: 50 });
  if (product) params.set("product", product);
  if (passed !== "") params.set("passed", passed);

  const data = await fetch(`/api/runs?${params}`).then(r => r.json());
  runsTotal = data.total;

  const tbody = qs("tbl-runs").querySelector("tbody");
  data.runs.forEach(r => {
    const tr = document.createElement("tr");
    const logCell = r.has_log
      ? `<a class="log-link" href="#" data-run-id="${r.id}">Download</a>`
      : `<span style="color:var(--text-muted)">—</span>`;
    tr.innerHTML = `
      <td>${r.id}</td>
      <td><a href="http://devserver:5000/device/${r.serial}" target="_blank" rel="noopener" style="color:var(--accent);font-family:monospace">${r.serial}</a></td>
      <td>${r.product}</td>
      <td>${r.fixture || "—"}</td>
      <td>${r.phase || "—"}</td>
      <td>${fmtDt(r.started_at)}</td>
      <td>${fmtDur(r.duration_s)}</td>
      <td><span class="badge ${r.pass ? 'pass' : 'fail'}">${r.pass ? "PASS" : "FAIL"}</span></td>
      <td style="color:#ef4444;font-size:.78rem">${r.failure_reason || ""}</td>
      <td>${logCell}</td>
    `;
    tbody.appendChild(tr);
  });

  const loaded = (runsPage - 1) * 50 + data.runs.length;
  qs("load-more-btn").style.display = loaded >= runsTotal ? "none" : "inline-block";
  qs("load-more-btn").textContent = `Load more (${runsTotal - loaded} remaining)`;
}

// ── Log download (presigned S3 URL, fetched on click) ─────────────────────
document.addEventListener("click", async e => {
  const link = e.target.closest(".log-link");
  if (!link) return;
  e.preventDefault();

  const runId = link.dataset.runId;
  link.textContent = "…";

  try {
    const resp = await fetch(`/api/runs/${runId}/log_url`);
    if (!resp.ok) {
      const err = await resp.json();
      alert(`Could not get log URL: ${err.error}`);
      link.textContent = "Download";
      return;
    }
    const { url } = await resp.json();
    window.open(url, "_blank");
  } catch (err) {
    alert(`Log fetch failed: ${err}`);
  }
  link.textContent = "Download";
});

// ── Event listeners ────────────────────────────────────────────────────────
qs("refresh-btn").addEventListener("click", refreshAll);
qs("period-select").addEventListener("change", refreshAll);
qs("gran-select").addEventListener("change", refreshAll);

qs("load-more-btn").addEventListener("click", () => {
  runsPage++;
  loadRuns(false);
});

qs("filter-product").addEventListener("change", () => loadRuns(true));
qs("filter-pass").addEventListener("change",   () => loadRuns(true));

qs("mq-metric-select").addEventListener("change", () => {
  const days = qs("period-select").value;
  const gran = qs("gran-select").value;
  loadMeasurements(days, gran);
});

// ── Initial load ───────────────────────────────────────────────────────────
refreshAll();
