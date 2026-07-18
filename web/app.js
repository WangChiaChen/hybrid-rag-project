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

const num = (v) => parseFloat(String(v).replace(/,/g, "").replace(/[（(]/, "-").replace(/[）)]/, ""));

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
const periodsOf = (name) => (COMPANIES.find((c) => c.name === name) || {}).periods || [];

function onCompanyChange() {
  const ps = periodsOf($("company").value);
  fillSelect($("period"), ps, ps[ps.length - 1]);
  fillSelect($("lastPeriod"), ["（不比較）", ...ps], ps[ps.length - 2]);
  load();
}

// ---------- 指標分組 ----------
// 16 個指標平鋪就已經有壓迫感，60 個更不用說。依財務意義分區，
// 使用者才能照邏輯瀏覽而不是逐張掃描。順序＝重要性。
const GROUPS = [
  { key: "獲利能力", test: (n) => /淨利|獲利|盈餘|EPS|收益|營收|報酬率|ROE|ROA|股利|配發/.test(n) },
  { key: "資本結構", test: (n) => /資本適足|權益|淨值|槓桿|清償|CSM|RBC/.test(n) },
  { key: "資產品質", test: (n) => /逾期|呆帳|覆蓋|減損|信用/.test(n) },
  { key: "業務規模", test: (n) => /放款|存款|資產|保費|手續費|財富管理|信用卡|規模|市占|市佔/.test(n) },
  { key: "現金流量", test: (n) => /現金流|現金及約當/.test(n) },
  { key: "其他", test: () => true },
];
const groupOf = (name) => (GROUPS.find((g) => g.test(name)) || GROUPS[GROUPS.length - 1]).key;

// 金控層級的門面數字。這幾個要比其他項目搶眼。
const HERO = /^(合併稅後淨利|本期淨利|稅後淨利|基本每股盈餘|每股稅後盈餘|EPS|ROE|稅後股東權益報酬率|資產總計)/;

// 財務術語小辭典：滑到指標名稱上顯示定義，給非財務背景的評審看得懂。
// key 會用「包含」比對指標名稱，長的排前面（NIM 要先於「利」之類的短詞）。
const GLOSSARY = [
  ["淨利息收益率", "NIM（淨利息收益率）：利息淨收益 ÷ 生息資產，衡量銀行放款賺利差的效率。"],
  ["NIM", "NIM（淨利息收益率）：利息淨收益 ÷ 生息資產，衡量銀行放款賺利差的效率。"],
  ["股東權益報酬率", "ROE：稅後淨利 ÷ 股東權益，股東每投入一元賺回多少。"],
  ["ROE", "ROE：稅後淨利 ÷ 股東權益，股東每投入一元賺回多少。"],
  ["資產報酬率", "ROA：稅後淨利 ÷ 總資產，衡量整體資產的獲利效率。"],
  ["ROA", "ROA：稅後淨利 ÷ 總資產，衡量整體資產的獲利效率。"],
  ["每股盈餘", "EPS：稅後淨利 ÷ 流通股數，每一股賺多少。"],
  ["每股稅後盈餘", "EPS：稅後淨利 ÷ 流通股數，每一股賺多少。"],
  ["資本適足率", "資本適足率：自有資本 ÷ 風險性資產，衡量銀行吸收損失的能力，法定門檻 10.5%。"],
  ["逾期放款比率", "逾放比：逾期放款 ÷ 總放款，越低代表資產品質越好。"],
  ["覆蓋率", "備抵呆帳覆蓋率：已提列的呆帳準備 ÷ 逾期放款，越高對壞帳越有保護。"],
  ["清償能力", "保險業清償能力（ICS/RBC）：衡量壽險公司償付保單的能力。"],
  ["RBC", "RBC：保險業資本適足率，衡量壽險／產險公司的財務健全度。"],
  ["CSM", "CSM（合約服務邊際）：IFRS 17 下，保單尚未實現的未來獲利。"],
  ["存放比", "存放比率：放款 ÷ 存款，衡量資金運用效率。"],
  ["利差", "利差：放款利率 − 資金成本，銀行賺的價差。"],
];
function annotateTerms(name) {
  for (const [term, tip] of GLOSSARY) {
    const i = name.indexOf(term);
    if (i >= 0) {
      const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
      return esc(name.slice(0, i)) +
        `<span class="term" data-tip="${esc(tip)}">${esc(term)}</span>` +
        esc(name.slice(i + term.length));
    }
  }
  return name.replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

// ---------- 卡片 ----------
function deltaHtml(m) {
  if (m.change === null || m.change === undefined) {
    // 累計指標跨季比較會被後端擋掉——刻意不給假數字。改用細灰註解（不是標籤），不搶數值。
    return m.cumulative ? `<span class="cum-note">累計值，跨季不可比</span>` : "";
  }
  const cls = m.change > 0 ? "up" : m.change < 0 ? "down" : "na";
  return `<span class="delta ${cls}">${m.change > 0 ? "+" : ""}${m.change}%</span>`;
}

function cardHtml(m, hero) {
  const n = num(m.value);
  const neg = !isNaN(n) && n < 0;
  return `
    <div class="name">${annotateTerms(m.metric)}</div>
    <div class="val ${neg ? "neg" : ""}">${m.value}${m.unit ? `<span class="unit">${m.unit}</span>` : ""}</div>
    <div class="meta">${deltaHtml(m)}</div>
    ${hero ? "" : '<div class="spark"></div><div class="spark-label"></div>'}`;
}

// ---------- Sparkline ----------
// 滑過卡片才抓趨勢：一次載入 60 個指標的歷史會打爆 API，而且使用者也不會全部看。
const sparkCache = new Map();

function drawSpark(el, labelEl, data) {
  const pts = data.points;
  if (pts.length < 2) {
    labelEl.textContent = "只有單一期間，無趨勢";
    return;
  }
  const vals = pts.map((p) => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = max - min || 1;
  const W = 100, H = 30;
  const xy = pts.map((p, i) => [
    (i / (pts.length - 1)) * W,
    H - ((p.value - min) / span) * (H - 6) - 3,
  ]);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  // 累計指標的線一定逐季爬升再跨年掉回原點，那是重新起算不是走勢——用灰色別讓人誤讀
  const rising = vals[vals.length - 1] >= vals[0];
  const color = data.cumulative ? "#767C87" : rising ? "#EA5E5B" : "#51A551";
  const [lx, ly] = xy[xy.length - 1];

  el.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">` +
    `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"` +
    ` vector-effect="non-scaling-stroke" stroke-linejoin="round"/>` +
    `<circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2" fill="${color}"` +
    ` vector-effect="non-scaling-stroke"/></svg>`;
  labelEl.textContent =
    `${pts[0].period} → ${pts[pts.length - 1].period}` + (data.cumulative ? "（累計值，僅供參考）" : "");
}

function attachSpark(card, company, metric) {
  const el = card.querySelector(".spark");
  const labelEl = card.querySelector(".spark-label");
  if (!el) return;
  card.addEventListener("mouseenter", async () => {
    if (el.dataset.done) return;
    el.dataset.done = "1";
    const key = `${company}|${metric}`;
    try {
      if (!sparkCache.has(key)) {
        sparkCache.set(key, await api(`/api/trend?company=${encodeURIComponent(company)}&metric=${encodeURIComponent(metric)}`));
      }
      drawSpark(el, labelEl, sparkCache.get(key));
    } catch {
      labelEl.textContent = "趨勢載入失敗";
    }
  }, { once: false });
}

// ---------- 渲染 ----------
function render(data) {
  const heroes = data.metrics.filter((m) => HERO.test(m.metric));
  const rest = data.metrics.filter((m) => !HERO.test(m.metric));

  // Hero
  const hbox = $("hero");
  hbox.innerHTML = "";
  heroes.slice(0, 4).forEach((m) => {
    const el = document.createElement("div");
    el.className = "card" + (m.cumulative ? " is-cum" : "");
    el.innerHTML = cardHtml(m, true);
    hbox.appendChild(el);
  });
  $("hero-head").classList.toggle("hidden", heroes.length === 0);

  // 其餘依財務意義分組
  const box = $("groups");
  box.innerHTML = "";
  GROUPS.forEach((g) => {
    const items = rest.filter((m) => groupOf(m.metric) === g.key);
    if (!items.length) return;
    // 整組單位一致的話，就把單位提到組標題，不用每張卡都重複
    const units = [...new Set(items.map((m) => m.unit).filter(Boolean))];
    const sec = document.createElement("div");
    sec.className = "group";
    sec.innerHTML = `
      <div class="group-head">
        <span class="dot"></span><h3>${g.key}</h3><span class="n">${items.length} 項</span>
        ${units.length === 1 ? `<span class="u">單位：${units[0]}</span>` : ""}
      </div>
      <div class="cards"></div>`;
    const grid = sec.querySelector(".cards");
    items.forEach((m) => {
      const el = document.createElement("div");
      el.className = "card" + (m.cumulative ? " is-cum" : "");
      el.innerHTML = cardHtml(m, false);
      grid.appendChild(el);
      attachSpark(el, data.company, m.metric);
    });
    box.appendChild(sec);
  });

  $("metric-count").textContent =
    `${data.metrics.length} 個　·　${data.company} ${data.period}` +
    (data.last_period ? ` vs ${data.last_period}` : "");
}

// ---------- 圖表 ----------
function renderChart(data) {
  // 只畫比率／每股。絕對金額各家單位可能不同（千元 vs 億元），同軸並排會誤導。
  const rows = data.metrics
    .filter((m) => m.comparable)
    .map((m) => ({ ...m, n: num(m.value) }))
    .filter((m) => !isNaN(m.n))
    .slice(0, 18)
    .reverse();

  if (!rows.length) {
    Plotly.purge("chart");
    $("chart").innerHTML = '<p class="loading">這個期間沒有比率／每股類指標</p>';
    return;
  }

  Plotly.newPlot("chart", [{
    type: "bar",
    orientation: "h", // 指標名稱很長，橫擺才讀得了
    x: rows.map((m) => m.n),
    y: rows.map((m) => (m.metric.length > 22 ? m.metric.slice(0, 22) + "…" : m.metric)),
    text: rows.map((m) => m.n.toLocaleString()),
    textposition: "outside",
    marker: { color: "#1E3A5F" },
    hovertemplate: "%{y}<br>%{x}<extra></extra>",
  }], {
    margin: { l: 200, r: 70, t: 8, b: 40 },
    height: Math.max(360, rows.length * 32),
    paper_bgcolor: "#FFFFFF",
    plot_bgcolor: "#FFFFFF",
    font: { family: '"PingFang TC","Microsoft JhengHei",sans-serif', size: 12, color: "#434343" },
    xaxis: { gridcolor: "#EDEDED", zerolinecolor: "#DFDFDF" },
    yaxis: { automargin: true },
    bargap: 0.4,
  }, { displayModeBar: false, responsive: true });
}

// ---------- 主流程 ----------
async function load() {
  const company = $("company").value;
  const period = $("period").value;
  const lastRaw = $("lastPeriod").value;
  const last = lastRaw && lastRaw !== "（不比較）" ? lastRaw : null;
  if (!company || !period) return;

  $("dash-error").classList.add("hidden");
  $("groups").innerHTML = '<p class="loading">載入中…</p>';

  try {
    const qs = new URLSearchParams({ company, period });
    if (last) qs.set("last_period", last);
    const data = await api(`/api/metrics?${qs}`);
    render(data);
    renderChart(data);

    const cum = data.metrics.filter((m) => m.cumulative).length;
    $("cum-note").textContent = cum
      ? `※ 其中 ${cum} 個是「年初至今累計」指標。累計值只有同一季跨年度才能比（例如去年 Q1 vs 今年 Q1）；` +
        `跨季比較沒有意義——新年度第一季必然低於前一年第四季，那是重新起算不是衰退，所以系統不給變化率。`
      : "";
  } catch (e) {
    $("groups").innerHTML = "";
    showError(`載入失敗：${e.message}`);
  }
}

// ---------- 重置 ----------
function resetDashboard() {
  $("company").selectedIndex = 0;
  onCompanyChange(); // 會把期間設回「最新一季 vs 前一季」
  $("summary-text").textContent = "按上方按鈕，讓 AI Agent 讀完本期指標後給出一句話結論。";
  $("summary-text").className = "ai-summary-body placeholder";
}

// ---------- AI 觀點總結 ----------
async function genSummary() {
  const company = $("company").value, period = $("period").value;
  const lastRaw = $("lastPeriod").value;
  const last = lastRaw && lastRaw !== "（不比較）" ? lastRaw : null;
  const btn = $("gen-summary"), out = $("summary-text");
  btn.disabled = true;
  out.className = "ai-summary-body thinking";
  out.textContent = "AI 讀取本期指標中…";
  try {
    const qs = new URLSearchParams({ company, period });
    if (last) qs.set("last_period", last);
    const d = await api(`/api/summary?${qs}`);
    out.className = "ai-summary-body";
    out.textContent = d.summary;
  } catch (e) {
    out.className = "ai-summary-body";
    out.textContent = `總結生成失敗：${e.message}`;
  } finally {
    btn.disabled = false;
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
    $("reset").onclick = resetDashboard;
    $("gen-summary").onclick = genSummary;
    onCompanyChange();
  } catch (e) {
    showError(`無法連線到 API：${e.message}　（後端有啟動嗎？）`);
  }
})();
