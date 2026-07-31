const state = { data: null, selected: null };

const $ = (selector) => document.querySelector(selector);

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const safeUrl = (value) => {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : "#";
  } catch {
    return "#";
  }
};

const number = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const money = (value, currency, digits = 2) => new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: digits,
  maximumFractionDigits: digits,
}).format(number(value)) + ` ${currency || ""}`;

const percent = (value, signed = false) => {
  const parsed = number(value) * 100;
  const sign = signed && parsed > 0 ? "+" : "";
  return `${sign}${parsed.toFixed(1)}%`;
};

const dailyPercent = (value) => {
  const parsed = number(value);
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(2)}%`;
};

const dateTime = (value) => {
  if (!value) return "待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
};

const shortDate = (value) => {
  if (!value) return "待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
};

const severityClass = (severity) => {
  if (["red", "critical"].includes(severity)) return "red";
  if (["yellow", "warning"].includes(severity)) return "yellow";
  return "information";
};

const changeClass = (value) => {
  const parsed = number(value);
  if (parsed > 0) return "positive";
  if (parsed < 0) return "negative";
  return "neutral";
};

function renderOverview(data) {
  const degraded = data.status !== "success";
  const status = $("#run-status");
  status.textContent = degraded ? "部分数据源降级" : "数据链路完整";
  status.className = `status-pill ${degraded ? "degraded" : "ok"}`;
  $("#updated-at").textContent = `更新于 ${dateTime(data.run_at)}`;
  $("#company-count").textContent = data.stocks.length;
  $("#risk-count").textContent = data.stocks.flatMap((stock) => stock.signals || [])
    .filter((signal) => ["yellow", "red", "critical"].includes(signal.severity)).length;
  $("#event-count").textContent = (data.upcoming_events || []).length;
  $("#schema-version").textContent = `Schema ${data.schema_version || "—"} · 研究用途`;

  const warningPanel = $("#warning-panel");
  if (data.warnings?.length) {
    warningPanel.hidden = false;
    warningPanel.innerHTML = `<strong>数据质量提示</strong>${data.warnings.map(escapeHtml).join("<br>")}`;
  } else {
    warningPanel.hidden = true;
  }
}

function stockCard(stock) {
  const valuation = stock.recommendation.valuation;
  const currency = stock.price.currency;
  const dailyChange = number(stock.price.change_pct);
  return `
    <article class="stock-card ${state.selected === stock.symbol ? "active" : ""}" tabindex="0" role="button" data-symbol="${escapeHtml(stock.symbol)}" aria-label="查看 ${escapeHtml(stock.name)} 详情">
      <div class="card-top">
        <span class="symbol">${escapeHtml(stock.symbol)} · ${escapeHtml(stock.market)}</span>
        <span class="action-tag">${escapeHtml(stock.recommendation.action)}</span>
      </div>
      <h3>${escapeHtml(stock.name)}</h3>
      <div class="price-row">
        <div>
          <strong>${money(stock.price.value, "", 2)}</strong>
          <small>${escapeHtml(currency)} · 临时行情</small>
        </div>
        <span class="change ${changeClass(dailyChange)}">${dailyPercent(dailyChange)}</span>
      </div>
      <div class="valuation-line">
        <span>基准内在价值</span>
        <strong>${money(valuation.base.value, currency)}</strong>
      </div>
      <div class="mini-stat-row">
        <div><span>安全边际</span><strong>${percent(valuation.margin_of_safety)}</strong></div>
        <div><span>预期年化</span><strong>${percent(valuation.base.expected_return)}</strong></div>
        <div><span>信号</span><strong>${(stock.signals || []).length} 条</strong></div>
      </div>
    </article>`;
}

function renderStocks(data) {
  $("#stock-cards").innerHTML = data.stocks.map(stockCard).join("");
  $("#stock-tabs").innerHTML = data.stocks.map((stock) => `
    <button class="stock-tab" role="tab" data-symbol="${escapeHtml(stock.symbol)}" aria-selected="${state.selected === stock.symbol}">${escapeHtml(stock.name)}</button>
  `).join("");

  document.querySelectorAll("[data-symbol]").forEach((element) => {
    const select = () => selectStock(element.dataset.symbol);
    element.addEventListener("click", select);
    element.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        select();
      }
    });
  });
}

function marker(label, value, min, max, kind = "") {
  const rawPosition = ((number(value) - min) / Math.max(max - min, 1)) * 100;
  const position = Math.min(96, Math.max(4, rawPosition));
  return `<span class="marker ${kind}" style="--pos:${position}%"><b>${escapeHtml(label)}</b><small>${number(value).toFixed(0)}</small></span>`;
}

function renderDetail(stock) {
  const valuation = stock.recommendation.valuation;
  const bear = number(valuation.bear.value);
  const base = number(valuation.base.value);
  const bull = number(valuation.bull.value);
  const current = number(stock.price.value);
  const currency = stock.price.currency;
  const min = Math.min(bear, current) * 0.9;
  const max = Math.max(bull, current) * 1.08;
  const basePosition = Math.min(100, Math.max(0, ((base - min) / Math.max(max - min, 1)) * 100));
  const metrics = Object.entries(stock.metrics || {}).slice(0, 10);
  const nextReview = stock.recommendation.next_review_date ? dateTime(stock.recommendation.next_review_date) : "下一份重大披露";

  $("#stock-detail").innerHTML = `
    <div class="detail-header">
      <div><p class="kicker">DEEP DIVE · ${escapeHtml(stock.symbol)}</p><h2>${escapeHtml(stock.name)}</h2></div>
      <p>置信度 ${escapeHtml(stock.recommendation.confidence)} · 下次复核 ${escapeHtml(nextReview)}</p>
    </div>
    <div class="detail-grid">
      <div class="stack">
        <section class="panel dark">
          <div class="valuation-head">
            <div><h3>三情景估值区间</h3><span>当前 ${money(current, currency)}</span></div>
            <div><span>基准年化回报</span><strong>${percent(valuation.base.expected_return)}</strong></div>
          </div>
          <div class="range-chart">
            <div class="range-track" style="--base-pos:${basePosition}%"></div>
            ${marker("悲观", bear, min, max)}
            ${marker("当前", current, min, max, "current")}
            ${marker("基准", base, min, max)}
            ${marker("乐观", bull, min, max)}
          </div>
          <div class="scenario-grid">
            <article><span>悲观情景</span><strong>${money(bear, currency)}</strong><small>${percent(valuation.bear.expected_return)} 年化</small></article>
            <article><span>基准情景</span><strong>${money(base, currency)}</strong><small>${percent(valuation.base.expected_return)} 年化</small></article>
            <article><span>乐观情景</span><strong>${money(bull, currency)}</strong><small>${percent(valuation.bull.expected_return)} 年化</small></article>
          </div>
        </section>
        <section class="panel">
          <h3>财务与经营信号</h3>
          <ul class="signal-list">
            ${(stock.signals || []).map((signal) => `
              <li class="signal-item">
                <i class="signal-dot ${severityClass(signal.severity)}"></i>
                <div>
                  <strong>${escapeHtml(signal.title)}</strong>
                  <p>${escapeHtml(signal.detail)}</p>
                  <a href="${safeUrl(signal.evidence_url)}" target="_blank" rel="noreferrer">查看原始证据 ↗</a>
                </div>
              </li>`).join("") || "<li>暂无信号</li>"}
          </ul>
        </section>
        <section class="panel">
          <h3>关键指标</h3>
          <ul class="metric-list">${metrics.map(([label, value]) => `<li><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></li>`).join("")}</ul>
        </section>
      </div>
      <aside class="stack">
        <section class="panel">
          <h3>为什么是“${escapeHtml(stock.recommendation.action)}”</h3>
          <ul class="recommendation-list">${(stock.recommendation.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>
        </section>
        <section class="panel">
          <h3>主要风险</h3>
          <ul class="risk-list">${(stock.recommendation.risks || []).map((risk) => `<li>${escapeHtml(risk)}</li>`).join("")}</ul>
        </section>
        <section class="panel">
          <h3>逻辑失效条件</h3>
          <ul class="risk-list">${(stock.recommendation.invalidation || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </section>
      </aside>
    </div>`;
}

function renderEvents(data) {
  const events = data.upcoming_events || [];
  $("#event-list").innerHTML = events.length ? events.map((event) => `
    <article class="event-card">
      <span class="event-date">${escapeHtml(shortDate(event.start))}</span>
      <h3>${escapeHtml(event.title || event.name)}</h3>
      <p>${escapeHtml(event.symbol || "")} · ${event.tentative ? "推测日期，待确认" : "官方确认"}</p>
      <a href="${safeUrl(event.source_url)}" target="_blank" rel="noreferrer">查看来源 ↗</a>
    </article>`).join("") : '<p class="neutral">未来 30 天暂无已登记事件。</p>';
}

function selectStock(symbol) {
  const stock = state.data.stocks.find((item) => item.symbol === symbol);
  if (!stock) return;
  state.selected = symbol;
  renderStocks(state.data);
  renderDetail(stock);
}

async function boot() {
  try {
    const response = await fetch(`data/latest.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    state.selected = state.data.stocks?.[0]?.symbol;
    if (!state.selected) throw new Error("empty watchlist");
    renderOverview(state.data);
    renderStocks(state.data);
    renderDetail(state.data.stocks[0]);
    renderEvents(state.data);
  } catch (error) {
    console.error("Dashboard bootstrap failed", error);
    $("#run-status").textContent = "数据读取失败";
    $("#run-status").className = "status-pill degraded";
    $("#fatal-error").hidden = false;
  }
}

boot();
