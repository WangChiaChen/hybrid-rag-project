// 前端全部靠 /api 拿資料，自己不做任何財務判斷。
// unit / type / cumulative 都是後端算好送來的——前端無從得知 0.62 不該拿去跟 2.12 比。

const $ = (id) => document.getElementById(id);
let COMPANIES = [];

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function showError(msg) {
  const el = $("dash-error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

// ---------- 分頁切換 ----------
document.querySelectorAll(".nav button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".nav button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    ["dashboard", "compare", "chat", "sources"].forEach((id) =>
      $(id).classList.toggle("hidden", id !== btn.dataset.tab)
    );
  };
});

// ---------- 下拉選單 ----------
function fillSelect(sel, items, selected) {
  sel.innerHTML = "";
  items.forEach((v) => {
    const o = document.createElement("option");
    o.value = o.textContent = v;
    if (v === selected) o.selected = true;
    sel.appendChild(o);
  });
}

function periodsOf(name) {
  return (COMPANIES.find((c) => c.name === name) || {}).periods || [];
}

function onCompanyChange() {
  const ps = periodsOf($("company").value);
  // 預設選最新一期，比較期間預設選前一期
  fillSelect($("period"), ps, ps[ps.length - 1]);
  fillSelect($("lastPeriod"), ["（不比較）", ...ps], ps[ps.length - 2]);
  load();
}

// ---------- 指標卡 ----------
const CHIP = { "比率": "chip-ratio", "每股": "chip-share", "金額": "chip-amount" };

function deltaHtml(m) {
  if (m.change === null || m.change === undefined) {
    // 累計指標跨季比較會被後端擋掉——那不是缺資料，是刻意不給假數字
    return m.cumulative
      ? `<span class="delta na">累計值，跨季不可比</span>`
      : `<span class="delta na">—</span>`;
  }
  // 台灣慣例：正為紅、負為綠
  const cls = m.change > 0 ? "up" : m.change < 0 ? "down" : "na";
  const sign = m.change > 0 ? "+" : "";
  return `<span class="delta ${cls}">${sign}${m.change}%</span>`;
}

function renderCards(data) {
  const box = $("cards");
  box.innerHTML = "";
  data.metrics.forEach((m) => {
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `
      <div class="name">${m.metric}</div>
      <div class="val">${m.value}${m.unit ? `<span class="unit">${m.unit}</span>` : ""}</div>
      <div class="meta">
        <span class="chip ${CHIP[m.type] || "chip-amount"}">${m.type}</span>
        ${m.cumulative ? '<span class="chip chip-cum">累計</span>' : ""}
        ${deltaHtml(m)}
      </div>`;
    box.appendChild(el);
  });
  $("metric-count").textContent =
    `${data.metrics.length} 個　·　${data.company} ${data.period}` +
    (data.last_period ? ` vs ${data.last_period}` : "");
}

// ---------- 圖表 ----------
function renderChart(data) {
  // 只畫比率／每股。絕對金額各家單位可能不同（千元 vs 億元），
  // 畫在同一個座標軸上會誤導。
  const rows = data.metrics
    .filter((m) => m.comparable)
    .map((m) => ({ ...m, num: parseFloat(String(m.value).replace(/,/g, "")) }))
    .filter((m) => !isNaN(m.num))
    .slice(0, 18)
    .reverse();

  if (!rows.length) {
    Plotly.purge("chart");
    $("chart").innerHTML = '<p class="loading">這個期間沒有比率／每股類指標</p>';
    return;
  }

  Plotly.newPlot(
    "chart",
    [{
      type: "bar",
      orientation: "h", // 指標名稱很長，橫擺才讀得了
      x: rows.map((m) => m.num),
      y: rows.map((m) => (m.metric.length > 22 ? m.metric.slice(0, 22) + "…" : m.metric)),
      text: rows.map((m) => m.num.toLocaleString()),
      textposition: "outside",
      marker: { color: "#1E3A5F" },
      hovertemplate: "%{y}<br>%{x}<extra></extra>",
    }],
    {
      margin: { l: 190, r: 60, t: 10, b: 40 },
      height: Math.max(360, rows.length * 30),
      paper_bgcolor: "#FFFFFF",
      plot_bgcolor: "#FFFFFF",
      font: { family: '"PingFang TC","Microsoft JhengHei",sans-serif', size: 12, color: "#434343" },
      xaxis: { gridcolor: "#EDEDED", zerolinecolor: "#DFDFDF" },
      yaxis: { automargin: true },
      bargap: 0.35,
    },
    { displayModeBar: false, responsive: true }
  );
}

// ---------- 主流程 ----------
async function load() {
  const company = $("company").value;
  const period = $("period").value;
  const lastRaw = $("lastPeriod").value;
  const last = lastRaw && lastRaw !== "（不比較）" ? lastRaw : null;
  if (!company || !period) return;

  $("dash-error").classList.add("hidden");
  $("cards").innerHTML = '<p class="loading">載入中…</p>';

  try {
    const qs = new URLSearchParams({ company, period });
    if (last) qs.set("last_period", last);
    const data = await api(`/api/metrics?${qs}`);
    renderCards(data);
    renderChart(data);

    const cum = data.metrics.filter((m) => m.cumulative).length;
    $("cum-note").textContent = cum
      ? `※ 其中 ${cum} 個是「年初至今累計」指標。累計值只有同一季跨年度才能比（例如去年 Q1 vs 今年 Q1）；` +
        `跨季比較沒有意義——新年度第一季必然低於前一年第四季，那是重新起算不是衰退，所以系統不給變化率。`
      : "";
  } catch (e) {
    $("cards").innerHTML = "";
    showError(`載入失敗：${e.message}`);
  }
}

// ---------- 啟動 ----------
(async () => {
  try {
    COMPANIES = await api("/api/companies");
    if (!COMPANIES.length) return showError("知識庫是空的");
    fillSelect($("company"), COMPANIES.map((c) => c.name));
    $("company").onchange = onCompanyChange;
    $("period").onchange = load;
    $("lastPeriod").onchange = load;
    onCompanyChange();
  } catch (e) {
    showError(`無法連線到 API：${e.message}　（後端有啟動嗎？）`);
  }
})();
