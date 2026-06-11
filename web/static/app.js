const POLL_MS = 3000;
let currentLogType = "info";

const els = {
  healthBadge: document.getElementById("health-badge"),
  btnStart: document.getElementById("btn-start"),
  btnStop: document.getElementById("btn-stop"),
  btnResetBreaker: document.getElementById("btn-reset-breaker"),
  statRunning: document.getElementById("stat-running"),
  statSandbox: document.getElementById("stat-sandbox"),
  statEquity: document.getElementById("stat-equity"),
  statDrawdown: document.getElementById("stat-drawdown"),
  statBreaker: document.getElementById("stat-breaker"),
  statStrategies: document.getElementById("stat-strategies"),
  riskMeta: document.getElementById("risk-meta"),
  ordersBody: document.getElementById("orders-body"),
  klinesBody: document.getElementById("klines-body"),
  logView: document.getElementById("log-view"),
  toast: document.getElementById("toast"),
};

function showToast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.style.borderColor = isError ? "#ef4444" : "#374151";
  els.toast.classList.remove("hidden");
  setTimeout(() => els.toast.classList.add("hidden"), 3200);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `请求失败 (${res.status})`);
  }
  return data;
}

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(digits);
}

function fmtTime(ts) {
  if (!ts) return "-";
  if (typeof ts === "number") {
    return new Date(ts).toLocaleString("zh-CN");
  }
  return ts;
}

function renderStatus(status) {
  const risk = status.risk || {};
  const running = Boolean(status.running);

  els.statRunning.textContent = running ? "运行中" : "已停止";
  els.statRunning.className = running ? "status-running" : "status-stopped";

  els.statSandbox.textContent = status.sandbox ? "沙盒 / 测试网" : "实盘";
  els.statEquity.textContent = fmtNum(risk.current_equity, 4);
  els.statDrawdown.textContent =
    risk.drawdown_pct === null || risk.drawdown_pct === undefined
      ? "-"
      : `${fmtNum(risk.drawdown_pct)}%`;
  els.statBreaker.textContent = risk.circuit_breaker_tripped ? "已触发" : "正常";
  els.statBreaker.className = risk.circuit_breaker_tripped ? "status-breaker" : "status-running";
  els.statStrategies.textContent = status.strategy_count ?? 0;

  els.btnStart.disabled = running;
  els.btnStop.disabled = !running;
  els.btnResetBreaker.disabled = !status.task_alive && !running;

  els.riskMeta.innerHTML = `
    <span>最大杠杆: <strong>${risk.max_leverage ?? "-"}</strong></span>
    <span>日回撤上限: <strong>${fmtNum(risk.max_daily_drawdown_pct)}%</strong></span>
    <span>单笔名义上限: <strong>$${fmtNum(risk.max_order_notional_usd)}</strong></span>
    <span>下单频率: <strong>${risk.orders_last_minute ?? 0} / ${risk.max_orders_per_minute ?? "-"} 每分钟</strong></span>
    <span>白名单: <strong>${(risk.symbol_whitelist || []).join(", ") || "-"}</strong></span>
    <span>Tick 间隔: <strong>${status.tick_interval ?? "-"}s</strong></span>
  `;

  if (status.last_error) {
    els.healthBadge.textContent = "异常";
    els.healthBadge.className = "badge badge-warn";
  } else if (running) {
    els.healthBadge.textContent = "运行中";
    els.healthBadge.className = "badge badge-ok";
  } else {
    els.healthBadge.textContent = "就绪";
    els.healthBadge.className = "badge badge-muted";
  }
}

function renderOrders(rows) {
  if (!rows.length) {
    els.ordersBody.innerHTML = `<tr><td colspan="6" class="empty">暂无订单</td></tr>`;
    return;
  }

  els.ordersBody.innerHTML = rows
    .map(
      (row) => `
      <tr>
        <td>${fmtTime(row.created_at)}</td>
        <td>${row.strategy_name || "-"}</td>
        <td>${row.symbol || "-"}</td>
        <td class="${row.side === "buy" ? "side-buy" : "side-sell"}">${row.side || "-"}</td>
        <td>${fmtNum(row.quantity, 6)}</td>
        <td>${row.status || "-"}</td>
      </tr>`
    )
    .join("");
}

function renderKlines(rows) {
  if (!rows.length) {
    els.klinesBody.innerHTML = `<tr><td colspan="7" class="empty">暂无 K 线</td></tr>`;
    return;
  }

  els.klinesBody.innerHTML = rows
    .map(
      (row) => `
      <tr>
        <td>${fmtTime(row.open_time)}</td>
        <td>${row.symbol || "-"}</td>
        <td>${row.interval || "-"}</td>
        <td>${fmtNum(row.open)}</td>
        <td>${fmtNum(row.high)}</td>
        <td>${fmtNum(row.low)}</td>
        <td>${fmtNum(row.close)}</td>
      </tr>`
    )
    .join("");
}

function renderLogs(lines) {
  els.logView.textContent = lines.length ? lines.join("\n") : "暂无日志";
}

async function refreshAll() {
  try {
    await api("/api/health");
    const status = await api("/api/system/status");
    renderStatus(status);

    const [orders, klines, logs] = await Promise.all([
      api("/api/orders?limit=15"),
      api("/api/klines?limit=15"),
      api(`/api/logs?log_type=${currentLogType}&lines=100`),
    ]);

    renderOrders(orders);
    renderKlines(klines);
    renderLogs(logs.lines || []);
  } catch (err) {
    els.healthBadge.textContent = "离线";
    els.healthBadge.className = "badge badge-warn";
    showToast(err.message, true);
  }
}

els.btnStart.addEventListener("click", async () => {
  try {
    await api("/api/system/start", {
      method: "POST",
      body: JSON.stringify({ sandbox: true, tick_interval: 5 }),
    });
    showToast("交易系统已启动");
    await refreshAll();
  } catch (err) {
    showToast(err.message, true);
  }
});

els.btnStop.addEventListener("click", async () => {
  try {
    await api("/api/system/stop", { method: "POST" });
    showToast("交易系统已停止");
    await refreshAll();
  } catch (err) {
    showToast(err.message, true);
  }
});

els.btnResetBreaker.addEventListener("click", async () => {
  try {
    await api("/api/risk/reset-circuit-breaker", { method: "POST" });
    showToast("熔断器已重置");
    await refreshAll();
  } catch (err) {
    showToast(err.message, true);
  }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", async () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentLogType = tab.dataset.log;
    await refreshAll();
  });
});

document.querySelectorAll("[data-refresh]").forEach((btn) => {
  btn.addEventListener("click", refreshAll);
});

refreshAll();
setInterval(refreshAll, POLL_MS);
