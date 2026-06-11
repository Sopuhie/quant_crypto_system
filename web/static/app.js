const POLL_MS = 3000;

const els = {
  healthBadge: document.getElementById("health-badge"),
  btnRefresh: document.getElementById("btn-refresh"),
  statStatus: document.getElementById("stat-status"),
  statDb: document.getElementById("stat-db"),
  statStrategies: document.getElementById("stat-strategies"),
  statOrders: document.getElementById("stat-orders"),
  strategiesBody: document.getElementById("strategies-body"),
  positionsBody: document.getElementById("positions-body"),
  ordersBody: document.getElementById("orders-body"),
  toast: document.getElementById("toast"),
};

function showToast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.style.borderColor = isError ? "#ef4444" : "#374151";
  els.toast.classList.remove("hidden");
  setTimeout(() => els.toast.classList.add("hidden"), 3200);
}

async function api(path) {
  const res = await fetch(path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `请求失败 (${res.status})`);
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
  els.statStatus.textContent = status.status || "-";
  els.statStatus.className = status.database_connected ? "status-running" : "status-stopped";
  els.statDb.textContent = status.database_connected ? "已连接" : "断开";
  els.statStrategies.textContent = status.active_strategies_count ?? 0;
  els.statOrders.textContent = status.total_orders_recorded ?? 0;

  if (status.database_connected) {
    els.healthBadge.textContent = "在线";
    els.healthBadge.className = "badge badge-ok";
  } else {
    els.healthBadge.textContent = "离线";
    els.healthBadge.className = "badge badge-warn";
  }
}

function renderStrategies(rows) {
  if (!rows.length) {
    els.strategiesBody.innerHTML = `<tr><td colspan="6" class="empty">暂无策略配置</td></tr>`;
    return;
  }

  els.strategiesBody.innerHTML = rows
    .map(
      (row) => `
      <tr>
        <td>${row.strategy_name || "-"}</td>
        <td>${row.symbol || "-"}</td>
        <td>${row.status || "-"}</td>
        <td>${fmtNum(row.target_profit)}</td>
        <td>${fmtNum(row.target_loss)}</td>
        <td>${fmtTime(row.updated_at)}</td>
      </tr>`
    )
    .join("");
}

function renderPositions(rows) {
  if (!rows.length) {
    els.positionsBody.innerHTML = `<tr><td colspan="7" class="empty">暂无持仓快照</td></tr>`;
    return;
  }

  els.positionsBody.innerHTML = rows
    .map(
      (row) => `
      <tr>
        <td>${row.account_type || "-"}</td>
        <td>${row.asset || "-"}</td>
        <td>${row.symbol || "-"}</td>
        <td>${fmtNum(row.free, 6)}</td>
        <td>${fmtNum(row.locked, 6)}</td>
        <td>${fmtNum(row.total, 6)}</td>
        <td>${fmtNum(row.unrealized_pnl, 4)}</td>
      </tr>`
    )
    .join("");
}

function renderOrders(rows) {
  if (!rows.length) {
    els.ordersBody.innerHTML = `<tr><td colspan="8" class="empty">暂无订单</td></tr>`;
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
        <td>${row.order_type || "-"}</td>
        <td>${fmtNum(row.quantity, 6)}</td>
        <td>${fmtNum(row.price)}</td>
        <td>${row.status || "-"}</td>
      </tr>`
    )
    .join("");
}

async function refreshAll() {
  try {
    const [status, orders, positions, strategies] = await Promise.all([
      api("/api/status"),
      api("/api/orders"),
      api("/api/positions"),
      api("/api/strategies"),
    ]);

    renderStatus(status);
    renderOrders(orders);
    renderPositions(positions);
    renderStrategies(strategies);
  } catch (err) {
    els.healthBadge.textContent = "离线";
    els.healthBadge.className = "badge badge-warn";
    showToast(err.message, true);
  }
}

els.btnRefresh.addEventListener("click", refreshAll);

refreshAll();
setInterval(refreshAll, POLL_MS);
