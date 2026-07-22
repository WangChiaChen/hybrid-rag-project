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
    if (btn.dataset.tab === "sources") initSources();
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

// 預設要選哪兩期。不能直接拿陣列最後兩個：期間是字串排序，「2026Q1財報」排在
// 「2026Q1」後面，但財報期只有資產負債表那十幾個科目，法說會的 NIM、手續費都不在裡面。
// 直接當預設會讓一進頁面只剩十幾張卡片，而且兩期指標名稱不重疊、變化率全是空的。
// 所以預設用「純季度」的最後兩期；真的只有財報期才退回原本的行為。
const QUARTER_ONLY = /^\d{4}Q[1-4]$/;
function defaultPeriods(ps) {
  const qs = ps.filter((p) => QUARTER_ONLY.test(p));
  const use = qs.length ? qs : ps;
  return [use[use.length - 1], use[use.length - 2]];
}

function onCompanyChange() {
  const ps = periodsOf($("company").value);
  const [cur, prev] = defaultPeriods(ps);
  fillSelect($("period"), ps, cur);
  fillSelect($("lastPeriod"), ["（不比較）", ...ps], prev);
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

// 單位：有就顯示；推定來的加「＊」誠實揭露；真的沒有（金額類無從推定）就明講「單位未標示」，
// 免得使用者把 16,586（百萬元）和 231（億元）當成同一個級距在比。
function unitHtml(m) {
  if (m.unit) {
    return m.unit_inferred
      ? `<span class="unit inferred" title="單位為系統依指標型別推定，原始簡報未標示">${m.unit}＊</span>`
      : `<span class="unit">${m.unit}</span>`;
  }
  return `<span class="unit unknown" title="原始簡報未標示單位，無法判斷數量級，請勿直接與其他數字比大小">單位未標示</span>`;
}

function cardHtml(m, hero) {
  const n = num(m.value);
  const neg = !isNaN(n) && n < 0;
  return `
    <div class="name">${annotateTerms(m.metric)}</div>
    <div class="val ${neg ? "neg" : ""}">${m.value}${unitHtml(m)}</div>
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

// ================= 跨機構比較 =================
function initCompare() {
  const names = COMPANIES.map((c) => c.name);
  fillSelect($("cmpA"), names, names[0]);
  fillSelect($("cmpB"), names, names[1] || names[0]);
  syncComparePeriods("A");
  syncComparePeriods("B");
  ["cmpA", "cmpB"].forEach((id) => ($(id).onchange = () => { syncComparePeriods(id.endsWith("A") ? "A" : "B"); loadCompare(); }));
  ["cmpPA", "cmpPB"].forEach((id) => ($(id).onchange = loadCompare));
  loadCompare();
}

function syncComparePeriods(side) {
  const ps = periodsOf($("cmp" + side).value);
  // 同樣避開財報期：兩邊都預設財報期的話，能對齊的只剩「資產負債率」一列，
  // ROE／NIM／逾放比這些真正該比的都在法說會簡報那一期。
  fillSelect($("cmpP" + side), ps, defaultPeriods(ps)[0]);
}

async function loadCompare() {
  const a = $("cmpA").value, pa = $("cmpPA").value;
  const b = $("cmpB").value, pb = $("cmpPB").value;
  if (!a || !pa || !b || !pb) return;

  $("cmp-error").classList.add("hidden");
  $("cmp-body").innerHTML = '<p class="loading">載入中…</p>';

  try {
    const qs = new URLSearchParams({ company_a: a, period_a: pa, company_b: b, period_b: pb });
    const d = await api(`/api/compare?${qs}`);
    renderCompare(d);
  } catch (e) {
    $("cmp-body").innerHTML = "";
    $("cmp-error").textContent = `載入失敗：${e.message}`;
    $("cmp-error").classList.remove("hidden");
  }
}

// 把「千元／百萬元／億元」這類申報單位換算成統一的「億元」。
// 這讓兩家用不同單位申報的絕對金額也能對齊比較——前端不只是呈現，還在「處理」數據。
// 單位無法辨識（空的、百分比等）就回 null，該列不做換算，避免亂算誤導。
const YI = 1e8; // 1 億 = 100,000,000
// 台幣金額單位 → 元 的倍率。順序＝由大到小、複合詞在前：
// 「十億」必須排在「億」之前，否則「十億元」會被 /億/ 先吃掉、少算 10 倍。
const UNIT_TO_YUAN = [
  [/兆/, 1e12],
  [/十億/, 1e9],
  [/億/, 1e8],
  [/千萬/, 1e7],
  [/NT\$?\s*MN|\bMN\b|百萬|佰萬|Million/i, 1e6],
  [/十萬/, 1e5],
  [/萬/, 1e4],
  [/千元|仟元|千/, 1e3],
  [/^\s*元|新台幣|新臺幣|NT\$?|NTD|TWD/, 1],
];
function toYi(value, unit) {
  if (value == null || isNaN(value)) return null;
  if (!unit) return null;
  const u = String(unit);
  if (/%|％|倍|比|率|股|人|家|次|碼|個|年|成|bps|Thousand|Card/.test(u)) return null; // 不是金額
  if (/人民幣|人民|越盾|越南盾|美元|美金|USD|港幣|歐元|日圓|日元/.test(u)) return null; // 外幣不換算（沒有匯率）
  for (const [re, factor] of UNIT_TO_YUAN) {
    if (re.test(u)) return (value * factor) / YI;
  }
  return null;
}
const fmtYi = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });

function cmpTable(rows, a, b, mode) {
  // mode === "amount"：換算成億元對齊；否則直接列原值（比率／每股本來就同單位）
  const isAmount = mode === "amount";
  const cell = (txt, higher) =>
    `<td class="${higher ? "higher" : ""}"><span class="num">${txt}</span></td>`;

  const body = rows.map((r) => {
    if (isAmount) {
      const ya = toYi(r.value_a, r.unit_a), yb = toYi(r.value_b, r.unit_b);
      const canCmp = ya != null && yb != null;
      const aHi = canCmp && ya > yb, bHi = canCmp && yb > ya;
      const showA = ya != null ? fmtYi(ya) : `${r.value_a.toLocaleString()}<span class="raw-unit">${r.unit_a || "?"}</span>`;
      const showB = yb != null ? fmtYi(yb) : `${r.value_b.toLocaleString()}<span class="raw-unit">${r.unit_b || "?"}</span>`;
      // 原始申報單位標出來——這正是換算的價值所在。推定來的（本期漏標、由其他期間補上）加「＊」
      const uA = r.unit_a ? r.unit_a + (r.unit_a_inferred ? "＊" : "") : "—";
      const uB = r.unit_b ? r.unit_b + (r.unit_b_inferred ? "＊" : "") : "";
      const origin = `<td class="unit-col">${uA}${uB && r.unit_b !== r.unit_a ? ` / ${uB}` : ""}</td>`;
      return `<tr>
        <td>${annotateTerms(r.metric)}</td>
        ${cell(showA, aHi)}
        ${cell(showB, bHi)}
        ${origin}
      </tr>`;
    }
    const aHi = r.value_a > r.value_b, bHi = r.value_b > r.value_a;
    return `<tr>
      <td>${annotateTerms(r.metric)}</td>
      ${cell(r.value_a.toLocaleString(), aHi)}
      ${cell(r.value_b.toLocaleString(), bHi)}
    </tr>`;
  }).join("");

  const head = isAmount
    ? `<tr><th>指標</th><th>${a}<small>億元</small></th><th>${b}<small>億元</small></th><th>原始申報單位</th></tr>`
    : `<tr><th>指標</th><th>${a}</th><th>${b}</th></tr>`;
  // 包一層捲動容器：窄螢幕由容器橫捲，表格本身維持正常的 table 排版（欄寬才對得齊）
  return `<div class="table-scroll"><table class="cmp-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

// 標準比率對照表。每列附上「實際對應到的原始欄位」，滑鼠移上去看得到——
// 不是黑箱硬配，而是誠實揭露「A 的這欄 ≈ B 的那欄」。
function standardBlock(rows, a, b) {
  const fmt = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  const body = rows.map((r) => {
    const aHi = r.value_a > r.value_b, bHi = r.value_b > r.value_a;
    const tip = `A：${r.matched_a}　｜　B：${r.matched_b}`;
    const badge = r.derived
      ? `<span class="std-badge derived" data-tip="由「${esc(r.matched_a)}」相除計算">衍生</span>`
      : `<span class="std-badge" data-tip="${esc(tip)}">對齊</span>`;
    return `<tr>
      <td>${annotateTerms(r.metric)} ${badge}</td>
      <td class="${aHi ? "higher" : ""}"><span class="num">${fmt(r.value_a)}</span></td>
      <td class="${bHi ? "higher" : ""}"><span class="num">${fmt(r.value_b)}</span></td>
      <td class="unit-col">${r.unit || "—"}</td>
    </tr>`;
  }).join("");
  return `<div class="section-head"><h2>標準比率對照</h2>
      <span class="count">跨機構定義對齊　·　名稱不同也配得起來　·　滑到「對齊」看對應欄位</span></div>
    <div class="std-note">ROE、ROA、NIM 這類標準比率各家用詞不一（如「9M25 ROE」vs「ROE」），
      逐字比對配不起來，改用<strong>人工維護的標準定義字典</strong>對齊；<strong>資產負債率</strong>等衍生比率則由財報金額即時計算。</div>
    <div class="table-scroll"><table class="cmp-table std-table">
      <thead><tr><th>標準比率</th><th>${a}</th><th>${b}</th><th>單位</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
}

function renderCompare(d) {
  const box = $("cmp-body");
  const a = d.company_a, b = d.company_b;
  if (!d.rows.length) {
    box.innerHTML = `<p class="loading">${a}（${d.period_a}）與 ${b}（${d.period_b}）沒有名稱完全相同的指標。<br>
      換個期間試試，或用「問答與報告」讓 LLM 做語意層面的比較。</p>`;
    return;
  }

  const comparable = d.rows.filter((r) => r.comparable);
  const amounts = d.rows.filter((r) => !r.comparable);
  const standard = d.standard || [];

  let html = "";

  // 標準比率對照放最前面：這是這頁的重點——逐字配不起來的 ROE/ROA/NIM…
  // 用標準定義硬對齊，一進頁面就有一整組跨機構關鍵比率可看。
  if (standard.length) html += standardBlock(standard, a, b);

  html += `<div class="section-head"><h2>逐字比對的共同指標</h2>
    <span class="count">${d.rows.length} 個　·　${a} ${d.period_a} vs ${b} ${d.period_b}</span></div>`;

  // 可直接比較（比率／每股）→ 長條圖 + 表
  if (comparable.length) {
    html += `<div class="group"><div class="group-head"><span class="dot"></span>
      <h3>可直接比較的指標</h3><span class="n">比率／每股，單位一致</span></div>
      <div class="panel"><div id="cmp-chart"></div></div>
      <div style="margin-top:14px">${cmpTable(comparable, a, b, "ratio")}</div></div>`;
  }
  // 絕對金額 → 前端主動換算成「億元」對齊，不再只是丟警語給使用者自己算
  if (amounts.length) {
    const hasInferred = amounts.some((r) => r.unit_a_inferred || r.unit_b_inferred);
    html += `<div class="group"><div class="group-head"><span class="dot"></span>
      <h3>絕對金額指標</h3><span class="n">已統一換算成「億元」對齊</span></div>
      <div class="cmp-note">兩家原始申報單位可能不同（如百萬元 vs 十億元），系統已依各自單位換算成
      <strong>億元</strong>後對齊，較高者以粗體標示。無法辨識單位的項目維持原值並標「?」。
      ${hasInferred ? '<br><span class="infer-note">＊ 該期原始資料漏標單位，已用同機構其他期間的單位推定補上。</span>' : ""}</div>
      ${cmpTable(amounts, a, b, "amount")}</div>`;
  }
  box.innerHTML = html;

  if (comparable.length) drawCompareChart(comparable, a, b);
}

function drawCompareChart(rows, a, b) {
  const r = rows.slice(0, 16).reverse();
  const single = r.length === 1; // 單一指標時把名稱移到上方當標題，長條就左對齊、更平衡
  const label = (v) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  const maxX = Math.max(0, ...r.map((m) => Math.max(m.value_a, m.value_b)));
  // 單指標：名稱移到頂端當副標題，y 軸不放字（左邊界收窄、版面平衡）；多指標：名稱照舊放左側
  const y = r.map((m) => (single ? "" : (m.metric.length > 20 ? m.metric.slice(0, 20) + "…" : m.metric)));

  const trace = (name, key, color) => ({
    type: "bar", orientation: "h", name, y, x: r.map((m) => m[key]),
    text: r.map((m) => label(m[key])),
    textposition: "outside",                 // 數值一律擺長條外側，長條保持純色塊
    textfont: { size: 12, color: "#434343" }, // 統一深灰，不隨長條變色
    cliponaxis: false,
    marker: { color },
    hovertemplate: `${name}<br>%{y}：%{x}<extra></extra>`,
  });

  const layout = {
    barmode: "group",
    margin: { l: single ? 20 : 190, r: 58, t: single ? 52 : 34, b: 34 },
    height: single ? 172 : 44 + r.length * (r.length <= 3 ? 74 : 50),
    paper_bgcolor: "#FFF", plot_bgcolor: "#FFF",
    font: { family: '"PingFang TC","Microsoft JhengHei",sans-serif', size: 12, color: "#434343" },
    // 垂直網格線移除，只留底部坐標軸線；x 刻度（0/0.5/1…）清楚可見
    xaxis: {
      showgrid: false, zeroline: false,
      showline: true, linecolor: "#CFCFCF", linewidth: 1,
      ticks: "outside", ticklen: 4, tickcolor: "#CFCFCF", tickfont: { color: "#767C87" },
      range: [0, maxX * 1.15], fixedrange: true,
    },
    yaxis: { automargin: true, showgrid: false, fixedrange: true },
    // 圖例移到右上角，方塊色與長條完全對應
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.18, yanchor: "top" },
    bargap: 0.34, bargroupgap: 0.12,
  };
  if (single) {
    layout.title = {
      text: r[0].metric, x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
      font: { size: 14, color: "#434343" },
    };
  }

  Plotly.newPlot("cmp-chart", [trace(a, "value_a", "#1E3A5F"), trace(b, "value_b", "#6B4E9E")],
    layout, { displayModeBar: false, responsive: true });
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

// ================= 問答與報告 =================
const escapeHtml = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const chatHistory = []; // 給匯出報告用：[{question, answer}]
let chartSeq = 0;       // 每則答案的趨勢圖需要唯一 id
// 匯出報告要有個公司／期間依據——用「最後一則問答後端實際採用的」那組；
// 還沒問過就先用第一家公司的最新一期當預設。
let chatCtx = null;

const ROUTE_BADGE = {
  CALC: ["精準計算 · Graph RAG", "b-calc"],
  NARRATIVE: ["語意檢索 · Vector RAG", "b-narr"],
  BOTH: ["雙引擎 · Hybrid RAG", "b-both"],
  EAP: ["EAP 平台回答", "b-both"],
  // EAP 自己查不到，改由本地 Vector RAG 檢索、再交給 EAP 生成的答案。
  // 標成不同顏色，讓人一眼看出這題的資料是我們補的，不是平台原本就有的。
  EAP_RAG: ["EAP 平台回答 · 資料由 Vector RAG 提供", "b-narr"],
};

const SCOPE_NONE = "（不指定）";   // 「不選」選項：值為空字串，代表交給後端自動辨識

function initChat() {
  const c0 = COMPANIES[0];
  const ps0 = c0.periods || [];
  chatCtx = { company: c0.name, period: ps0[ps0.length - 1] || "", last_period: null };

  // 公司選單：第一項是「不指定」，其餘為各公司；預設停在「不指定」
  const comp = $("chatCompany");
  comp.innerHTML = "";
  [SCOPE_NONE, ...COMPANIES.map((c) => c.name)].forEach((name) => {
    const o = document.createElement("option");
    o.value = name === SCOPE_NONE ? "" : name;
    o.textContent = name;
    comp.appendChild(o);
  });
  comp.onchange = onChatCompanyChange;
  onChatCompanyChange();   // 初始化期間選單（此時公司為不指定 → 期間停用）

  $("chat-send").onclick = sendChat;
  $("chat-q").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
  $("export-report").onclick = exportReport;

  // 「改用本地知識庫」按鈕：用事件委派接（訊息是動態生成的）。
  // 兩個地方會用到——紅框的交叉驗證不一致，以及 EAP 查無資料時的退路。
  $("chat-log").addEventListener("click", (e) => {
    const btn = e.target.closest(".xc-reask");
    if (btn) askLocal(btn.dataset.company, btn.dataset.period, btn.dataset.question);
  });
}

// 關掉 EAP、鎖定該公司／期間，改用本地 Hybrid RAG 重問。
// 交叉驗證那條路傳進來的是「指標名稱」（拿系統公式驗證過的數字），
// EAP 查無資料那條路傳進來的是「使用者原本的問題」（本地的逐字稿答得出來）。
function askLocal(company, period, question) {
  const comp = $("chatCompany");
  if ([...comp.options].some((o) => o.value === company)) {
    comp.value = company;
    onChatCompanyChange();           // 重填期間選單
  }
  const per = $("chatPeriod");
  if ([...per.options].some((o) => o.value === period)) per.value = period;
  $("chatEap").checked = false;      // 走本地，不走 EAP
  $("chat-q").value = question;
  sendChat();
}

// 公司改變時，重填期間選單：沒選公司就只留「不指定」並停用；選了就列出該公司各期
function onChatCompanyChange() {
  const name = $("chatCompany").value;
  const per = $("chatPeriod");
  const ps = name ? periodsOf(name) : [];
  per.innerHTML = "";
  [SCOPE_NONE, ...ps].forEach((v) => {
    const o = document.createElement("option");
    o.value = v === SCOPE_NONE ? "" : v;
    o.textContent = v;
    per.appendChild(o);
  });
  per.disabled = !name;   // 未選公司時，期間無從選起
}

// ---------- 迷你 Markdown 渲染 ----------
// EAP／LLM 的答案常帶 **粗體**、| 表格 |、- 條列，直接秀會露出米字號跟管線符號。
// 這裡做最小可用的轉換，把它們渲染成 HTML，不引外部套件。
function mdInline(s) {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function mdTable(block) {
  const rows = block.map((r) =>
    r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim()));
  const isSep = (cells) => cells.every((c) => /^:?-{2,}:?$/.test(c) || c === "");
  const body = rows.filter((r) => !isSep(r));
  if (!body.length) return "";
  const [head, ...rest] = body;
  let h = "<table class='md-table'><thead><tr>";
  head.forEach((c) => (h += `<th>${mdInline(c)}</th>`));
  h += "</tr></thead><tbody>";
  rest.forEach((r) => {
    h += "<tr>" + r.map((c) => `<td>${mdInline(c)}</td>`).join("") + "</tr>";
  });
  return h + "</tbody></table>";
}

function renderMarkdown(src) {
  const lines = String(src).replace(/\r/g, "").split("\n");
  let html = "", i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {                     // 程式碼／指令圍欄
      const lang = line.replace(/^\s*```/, "").trim();
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      if (i < lines.length) i++;                    // 跳過收尾的 ```
      // chart 指令由後端攔截改畫成真圖了；其餘圍欄以純文字區塊呈現，別露出裸 ```
      if (lang !== "chart" && buf.join("").trim()) {
        html += `<pre class="md-pre">${escapeHtml(buf.join("\n"))}</pre>`;
      }
      continue;
    }
    const hm = line.match(/^\s*(#{1,6})\s+(.*)$/);  // 標題 #／##／### → 粗體小標，別露出米字號
    if (hm) {
      html += `<p class="md-h">${mdInline(hm[2])}</p>`;
      i++;
      continue;
    }
    if (/^\s*\|.*\|/.test(line)) {                 // 表格
      const block = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) block.push(lines[i++]);
      html += mdTable(block);
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {                // 條列
      html += "<ul>";
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        html += `<li>${mdInline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`;
        i++;
      }
      html += "</ul>";
      continue;
    }
    if (!line.trim()) { i++; continue; }           // 空行
    html += `<p>${mdInline(line)}</p>`;            // 一般段落
    i++;
  }
  return html;
}

function appendMsg(cls, html) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = html;
  const log = $("chat-log");
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function sendChat() {
  const q = $("chat-q").value.trim();
  if (!q) return;

  $("chat-q").value = "";
  $("chat-error").classList.add("hidden");
  // 第一次提問就把提示語清掉
  const hint = $("chat-log").querySelector(".chat-hint");
  if (hint) hint.remove();

  // 鎖定範圍：選了就帶給後端；沒選（空字串）就不帶，維持後端自動辨識
  const lockCompany = $("chatCompany").value;
  const lockPeriod = $("chatPeriod").value;
  const scopeTag = lockCompany
    ? `<span class="scope-chip">🔒 ${escapeHtml(lockCompany)}${lockPeriod ? " · " + escapeHtml(lockPeriod) : ""}</span>`
    : "";

  appendMsg("me", scopeTag + escapeHtml(q));
  const thinking = appendMsg("bot thinking", "AI 查詢中…");

  try {
    // 有鎖定範圍就帶公司／期間；否則交給後端從問題文字自動辨識
    const body = { question: q, use_eap: $("chatEap").checked };
    if (lockCompany) body.company = lockCompany;
    if (lockPeriod) body.period = lockPeriod;
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const d = await r.json();
    thinking.remove();
    const fig = renderAnswer(d);
    // 連同圖（趨勢折線或 EAP 長條）一起存，匯出報告時轉成 PNG 嵌進去
    chatHistory.push({ question: q, answer: d.answer, fig: fig || null });
    // 記住後端實際採用的公司／期間，給匯出報告用
    if (d.company) chatCtx = { company: d.company, period: d.period || chatCtx.period, last_period: d.last_period || null };
  } catch (e) {
    thinking.remove();
    $("chat-error").textContent = `查詢失敗：${e.message}`;
    $("chat-error").classList.remove("hidden");
  }
}

function renderAnswer(d) {
  const [label, cls] = ROUTE_BADGE[d.route] || ["", ""];
  let html = label ? `<span class="chat-badge ${cls}">${label}</span>` : "";
  html += `<div class="bot-text">${renderMarkdown(d.answer)}</div>`;

  if (d.calc_result) {
    const cr = d.calc_result;
    const chg = cr.change != null && cr.change !== ""
      ? `　·　較上期 <strong>${cr.change > 0 ? "+" : ""}${cr.change}%</strong>` : "";
    html += `<div class="calc-verify">✓ 公式驗證：${escapeHtml(cr.metric)} = <strong>${escapeHtml(String(cr.value))}</strong>${chg}</div>`;
  }

  // 交叉驗證：EAP 的數字若和本地知識庫差太多，標紅提醒（不斷言誰對，只提示對不上）
  if (d.cross_check && d.cross_check.length) {
    const attr = (s) => escapeHtml(String(s)).replace(/"/g, "&quot;");
    const rows = d.cross_check.map((g) =>
      `<li><strong>${escapeHtml(g.company)}</strong> ${escapeHtml(g.metric)}（${escapeHtml(g.period)}）：
        EAP <b class="xc-eap">${escapeHtml(String(g.eap_value))}</b>
        vs 本地知識庫 <b class="xc-local">${escapeHtml(String(g.local_value))}</b>
        <button class="xc-reask" data-company="${attr(g.company)}" data-period="${attr(g.period)}" data-question="${attr(g.metric)}">↻ 改用本地知識庫重問</button></li>`
    ).join("");
    html += `<div class="cross-warn">
      <div class="cross-warn-head">⚠ 與本地知識庫數字不一致，請留意</div>
      <ul>${rows}</ul>
      <div class="cross-warn-foot">本地數字由 Graph RAG 直接讀簡報解析而得；EAP 為外部平台回答。兩者對不上時，建議以本地知識庫為準或人工複核。</div>
    </div>`;
  }

  // 這題 EAP 原本查不到，是我們補了本地資料它才答得出來——講清楚，不要讓人以為平台本來就有。
  if (d.route === "EAP_RAG") {
    html += `<div class="local-hint">
      <div class="local-hint-head">EAP 平台原本查無此資料</div>
      <p class="local-hint-src">上方答案由 EAP 生成，但依據的是<strong>本系統提供的法說會逐字稿</strong>（來源見下方）。</p>
      <div class="local-hint-foot">EAP 收錄的是後台上傳的簡報；逐字稿由本系統將錄音經 STT 轉寫後索引，只存在本地。</div>
    </div>`;
  }

  // EAP 查無資料，但本地知識庫有料 —— 給一條退路。
  // 兩邊的知識庫是各自獨立的：EAP 只有後台上傳的簡報，法說會逐字稿是我們自己 STT 轉的，
  // 所以「EAP 查不到」多半代表資料不在它那邊，而不是問題問得不好。
  if (d.local_fallback) {
    const f = d.local_fallback;
    const attr = (s) => escapeHtml(String(s)).replace(/"/g, "&quot;");
    html += `<div class="local-hint">
      <div class="local-hint-head">EAP 平台沒有這筆資料，但本地知識庫有</div>
      <p class="local-hint-src">找到相關內容：<strong>${escapeHtml(f.source)}</strong></p>
      <button class="xc-reask" data-company="${attr(f.company || "")}"
        data-period="${attr(f.period || "")}" data-question="${attr(f.question)}">↻ 改用本地知識庫回答</button>
      <div class="local-hint-foot">EAP 收錄的是後台上傳的簡報；法說會逐字稿由本系統將錄音經 STT 轉寫後索引，只存在本地。</div>
    </div>`;
  }

  // 決定要不要畫圖：Gemini 給趨勢（折線）；EAP 指令若點名指標→各期直條圖，否則→關鍵比率橫條圖
  let fig = null, chartId = null;
  if (d.chart && d.chart.points && d.chart.points.length >= 2) {
    fig = trendFigure(d.chart);
  } else if (d.chart_bar) {
    if (d.chart_bar.kind === "series" && d.chart_bar.points && d.chart_bar.points.length) {
      fig = seriesFigure(d.chart_bar);
    } else if (d.chart_bar.items && d.chart_bar.items.length) {
      fig = barFigure(d.chart_bar);
    }
  }
  if (fig) {
    chartId = `fig-${++chartSeq}`;
    html += `<div class="bot-chart" id="${chartId}"></div>`;
  }

  if (d.sources && d.sources.length) {
    const seen = new Set();
    const tags = d.sources.map((s) => {
      const key = `${s.source || ""}|${s.page || ""}`;
      if (seen.has(key)) return "";
      seen.add(key);
      const pg = s.page ? `　第 ${s.page} 頁` : "";
      return `<span class="src-tag">📄 ${escapeHtml(s.source || "來源")}${pg}</span>`;
    }).join("");
    html += `<div class="src-row">${tags}</div>`;
  }

  appendMsg("bot", html);
  if (fig) Plotly.newPlot(chartId, fig.data, fig.layout, { displayModeBar: false, responsive: true });
  return fig;   // 交給 sendChat 存起來，匯出報告時轉成 PNG
}

// 答案牽涉的指標，把歷史數列畫成折線圖（就是使用者要的「圖」）。
// 抽成 figure 物件，畫面上用 Plotly.newPlot、匯出報告用 Plotly.toImage 都吃這一份。
function trendFigure(chart) {
  const pts = chart.points;
  const rising = pts[pts.length - 1].value >= pts[0].value;
  // 累計指標的線會逐季爬升再跨年掉回起點，那是重新起算不是趨勢——用灰色別讓人誤讀
  const color = chart.cumulative ? "#767C87" : rising ? "#EA5E5B" : "#51A551";
  return {
    data: [{
      type: "scatter", mode: "lines+markers",
      x: pts.map((p) => p.period), y: pts.map((p) => p.value),
      line: { color, width: 2, shape: "spline", smoothing: 0.4 },
      marker: { color, size: 6 },
      hovertemplate: "%{x}：%{y:,}<extra></extra>",
    }],
    layout: {
      title: {
        text: `${chart.metric}　歷史趨勢${chart.cumulative ? "（累計值，僅供參考）" : ""}`,
        x: 0, xanchor: "left", font: { size: 12, color: "#434343" },
      },
      margin: { l: 52, r: 20, t: 32, b: 30 },
      height: 220,
      paper_bgcolor: "#FFF", plot_bgcolor: "#FFF",
      font: { family: '"PingFang TC","Microsoft JhengHei",sans-serif', size: 11, color: "#434343" },
      xaxis: { showgrid: false, showline: true, linecolor: "#CFCFCF", ticks: "outside", tickcolor: "#CFCFCF" },
      yaxis: { gridcolor: "#EDEDED", zeroline: false },
    },
  };
}

// EAP 指令點名某個指標時，畫「該指標各期」的直條圖。
// 對齊 EAP 平台那張圖：主指標長條 ＋ 期間對期間成長率折線（第二軸）＋ 圖例。
function seriesFigure(cs) {
  const pts = cs.points;
  const yTitle = cs.unit ? `${cs.metric}（${cs.unit}）` : (cs.metric || "數值");
  // 成長率由數值自己算（EAP 圖上的那條橘線也是它自己算的，不在資料表裡）
  const growth = pts.map((p, i) => {
    if (i === 0) return null;
    const prev = pts[i - 1].value;
    return prev ? +(((p.value - prev) / Math.abs(prev)) * 100).toFixed(1) : null;
  });
  const hasGrowth = growth.some((g) => g !== null);
  // 名稱要對：季度資料是 QoQ（季對季）、年度（FYxx）才是年成長率
  const isFY = pts.some((p) => /FY/i.test(p.period));
  const isQ = pts.some((p) => /Q[1-4]/i.test(p.period));
  const growthName = isFY ? "年成長率 (%)" : isQ ? "QoQ 變化 (%)" : "期間變化 (%)";

  const data = [{
    type: "bar", name: cs.metric || "數值",
    x: pts.map((p) => p.period), y: pts.map((p) => p.value),
    text: pts.map((p) => p.value.toLocaleString()),
    textposition: "outside", textfont: { size: 11, color: "#434343" }, cliponaxis: false,
    marker: { color: "#2E6CB5" },
    hovertemplate: "%{x}：%{y:,}<extra></extra>",
  }];
  if (hasGrowth) data.push({
    type: "scatter", mode: "lines+markers", name: growthName,
    x: pts.map((p) => p.period), y: growth, yaxis: "y2", connectgaps: true,
    line: { color: "#E8833A", width: 2 }, marker: { color: "#E8833A", size: 6 },
    hovertemplate: "成長率：%{y}%<extra></extra>",
  });

  const layout = {
    title: { text: cs.title || `${cs.metric}　各期`, x: 0.5, xanchor: "center", font: { size: 13, color: "#434343" } },
    margin: { l: 72, r: hasGrowth ? 60 : 24, t: 44, b: 40 },
    height: 320,
    paper_bgcolor: "#FFF", plot_bgcolor: "#FFF",
    font: { family: '"PingFang TC","Microsoft JhengHei",sans-serif', size: 11, color: "#434343" },
    xaxis: { showgrid: false, showline: true, linecolor: "#CFCFCF", ticks: "outside", tickcolor: "#CFCFCF" },
    yaxis: { title: { text: `<b>${yTitle}</b>`, font: { size: 12, color: "#434343" } }, gridcolor: "#EDEDED", zeroline: false, rangemode: "tozero" },
    showlegend: hasGrowth,
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.16, yanchor: "top" },
  };
  if (hasGrowth) {
    layout.yaxis2 = { title: { text: growthName, font: { size: 11, color: "#767C87" } }, overlaying: "y", side: "right", showgrid: false, zeroline: false };
  }
  return { data, layout };
}

// EAP 只給「畫長條圖」的指令，數據由後端用 Graph RAG 補上；這裡把它畫成橫向長條圖
function barFigure(cb) {
  const items = cb.items.slice(0, 10).reverse();
  return {
    data: [{
      type: "bar", orientation: "h",
      x: items.map((i) => i.value),
      y: items.map((i) => (i.name.length > 18 ? i.name.slice(0, 18) + "…" : i.name)),
      text: items.map((i) => i.value.toLocaleString() + (i.unit ? ` ${i.unit}` : "")),
      textposition: "auto", insidetextfont: { color: "#fff" }, textfont: { size: 11, color: "#434343" },
      cliponaxis: false,
      marker: { color: "#1E3A5F" },
      hovertemplate: "%{y}：%{x}<extra></extra>",
    }],
    layout: {
      title: { text: cb.title, x: 0, xanchor: "left", font: { size: 12, color: "#434343" } },
      margin: { l: 150, r: 48, t: 32, b: 30 },
      height: Math.max(180, 44 + items.length * 30),
      paper_bgcolor: "#FFF", plot_bgcolor: "#FFF",
      font: { family: '"PingFang TC","Microsoft JhengHei",sans-serif', size: 11, color: "#434343" },
      xaxis: { showgrid: false, showline: true, linecolor: "#CFCFCF", zeroline: false, ticks: "outside", tickcolor: "#CFCFCF" },
      yaxis: { automargin: true },
    },
  };
}

async function exportReport() {
  const company = chatCtx.company, period = chatCtx.period, last = chatCtx.last_period;
  const btn = $("export-report");
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = "產生中…";
  $("chat-error").classList.add("hidden");
  try {
    // 每一題若有趨勢圖，就用 Plotly 匯出成 PNG，一起塞進報告
    const qa = [];
    for (const h of chatHistory) {
      const item = { question: h.question, answer: h.answer };
      if (h.fig) {
        try {
          item.image = await Plotly.toImage(h.fig, { format: "png", width: 760, height: 320, scale: 2 });
        } catch { /* 圖失敗就純文字，不擋整份報告 */ }
      }
      qa.push(item);
    }
    const r = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company, period, last_period: last, qa }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${company}_${period}_財務分析報告.docx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    $("chat-error").textContent = `匯出失敗：${e.message}`;
    $("chat-error").classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

// ================= 資料來源總覽 =================
let sourcesLoaded = false;

async function initSources() {
  if (sourcesLoaded) return;   // 切到這頁才載入，且只載一次
  sourcesLoaded = true;
  try {
    const d = await api("/api/sources");
    renderSourceStats(d);
    renderSourceDetail(d);
  } catch (e) {
    $("src-error").textContent = `載入失敗：${e.message}`;
    $("src-error").classList.remove("hidden");
    sourcesLoaded = false;
  }
  loadTranscripts();
}

function statCard(label, value, sub) {
  return `<div class="src-stat">
    <div class="src-stat-val">${value.toLocaleString()}</div>
    <div class="src-stat-label">${label}</div>
    ${sub ? `<div class="src-stat-sub">${sub}</div>` : ""}</div>`;
}

function renderSourceStats(d) {
  const companies = [...new Set(d.rows.map((r) => r.company))];
  const totalMetrics = d.rows.reduce((s, r) => s + r.metrics, 0);
  $("src-stats").innerHTML =
    statCard("收錄公司", companies.length, "家") +
    statCard("收錄期間", d.rows.length, "組（公司 × 期間）") +
    statCard("結構化指標", totalMetrics, "由 Graph RAG 精準計算") +
    statCard("語意段落", d.total_narratives, "供 Vector RAG 檢索");
}

function renderSourceDetail(d) {
  const companies = [...new Set(d.rows.map((r) => r.company))];
  const box = $("src-detail");
  box.innerHTML = "";
  companies.forEach((c) => {
    const rows = d.rows.filter((r) => r.company === c);
    const cMetrics = rows.reduce((s, r) => s + r.metrics, 0);
    const cNarr = rows.reduce((s, r) => s + r.narratives, 0);
    const body = rows.map((r) => `<tr>
      <td>${r.period}</td>
      <td class="num-cell">${r.metrics}</td>
      <td class="num-cell">${r.narratives}</td>
      <td><span class="src-bar"><span style="width:${r.metrics ? Math.max(4, (r.metrics / 80) * 100) : 0}%"></span></span></td>
    </tr>`).join("");
    const sec = document.createElement("div");
    sec.className = "group";
    sec.innerHTML = `
      <div class="group-head"><span class="dot"></span><h3>${c}</h3>
        <span class="n">${rows.length} 期　·　指標 ${cMetrics}　·　語意段落 ${cNarr}</span></div>
      <div class="table-scroll"><table class="cmp-table src-table">
        <thead><tr><th>期間</th><th>指標數</th><th>語意段落</th><th>指標量</th></tr></thead>
        <tbody>${body}</tbody></table></div>`;
    box.appendChild(sec);
  });
}

async function loadTranscripts() {
  const box = $("src-transcripts");
  try {
    const d = await api("/api/transcripts");
    if (!d.transcripts.length) {
      box.innerHTML = `<p class="note">目前還沒有已存的逐字稿。在 Streamlit 版上傳法說會錄音後，轉出的逐字稿會出現在這裡。</p>`;
      return;
    }
    const opts = d.transcripts
      .map((t) => `<option value="${t.file}">${t.company}　${t.period}</option>`).join("");
    box.innerHTML = `
      <div class="controls" style="margin-top:0">
        <div class="field">
          <label for="transcriptSel">選擇逐字稿</label>
          <select id="transcriptSel">${opts}</select>
        </div>
      </div>
      <div id="transcript-view" class="transcript-view loading">載入中…</div>`;
    $("transcriptSel").onchange = () => showTranscript($("transcriptSel").value);
    showTranscript(d.transcripts[0].file);
  } catch (e) {
    box.innerHTML = `<p class="note">逐字稿載入失敗：${e.message}</p>`;
  }
}

async function showTranscript(file) {
  const view = $("transcript-view");
  view.className = "transcript-view loading";
  view.textContent = "載入中…";
  try {
    const d = await api(`/api/transcript?file=${encodeURIComponent(file)}`);
    view.className = "transcript-view";
    // 逐字稿本身帶 Markdown（**粗體**、* 條列）——渲染掉，別讓米字號露出來
    view.innerHTML = d.content ? renderMarkdown(d.content) : "（此逐字稿是空的）";
  } catch (e) {
    view.className = "transcript-view";
    view.textContent = `載入失敗：${e.message}`;
  }
}

// ---------- 上傳新資料 ----------
function initUpload() {
  $("up-pages").oninput = () => ($("up-pages-val").textContent = $("up-pages").value);
  $("up-submit").onclick = startUpload;
}

function showUpStatus(status, msg, progress) {
  $("up-status").classList.remove("hidden");
  $("up-msg").textContent = msg;
  $("up-msg").className = status === "error" ? "up-err" : status === "done" ? "up-ok" : "up-run";
  if (progress != null) $("up-bar").style.width = `${Math.round(progress * 100)}%`;
}

async function startUpload() {
  const company = $("up-company").value.trim();
  const period = $("up-period").value.trim();
  const file = $("up-file").files[0];
  if (!company || !period || !file) {
    showUpStatus("error", "請先填公司名稱、期間，並選擇 PDF 或錄音檔");
    return;
  }
  const fd = new FormData();
  fd.append("company", company);
  fd.append("period", period);
  fd.append("max_pages", $("up-pages").value);
  fd.append("file", file);

  const btn = $("up-submit");
  btn.disabled = true;
  btn.textContent = "上傳中…";
  showUpStatus("running", "上傳檔案中…", 0.04);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const d = await r.json();
    pollUpload(d.job_id);
  } catch (e) {
    showUpStatus("error", `上傳失敗：${e.message}`);
    btn.disabled = false;
    btn.textContent = "開始解析並匯入";
  }
}

function pollUpload(jobId) {
  const btn = $("up-submit");
  const iv = setInterval(async () => {
    try {
      const j = await api(`/api/upload_status?job_id=${jobId}`);
      showUpStatus(j.status, j.message, j.progress);
      if (j.status === "done" || j.status === "error") {
        clearInterval(iv);
        btn.disabled = false;
        btn.textContent = "開始解析並匯入";
        if (j.status === "done") {
          // 匯入成功 → 重新整理來源總覽，並更新公司清單讓其他分頁也看得到新資料
          sourcesLoaded = false;
          initSources();
          try { COMPANIES = await api("/api/companies"); } catch { /* ignore */ }
        }
      }
    } catch (e) {
      clearInterval(iv);
      showUpStatus("error", `狀態查詢失敗：${e.message}`);
      btn.disabled = false;
      btn.textContent = "開始解析並匯入";
    }
  }, 1500);
}

// ---------- 冷啟動提示 ----------
// Render 免費方案的服務閒置會休眠，第一個請求要等 50 秒以上才醒得過來。
// 沒有提示的話畫面就是一片空白，看的人只會以為壞了——現場展示時這一下就毀了。
// 作法：先打一個輕量的 /api/health，超過 2.5 秒還沒回應就跳出橫幅安撫，回來了再收掉。
function showWaking(msg) {
  let el = document.getElementById("waking");
  if (!el) {
    el = document.createElement("div");
    el.id = "waking";
    el.className = "waking";
    document.body.appendChild(el);
  }
  el.innerHTML = msg;
  el.classList.remove("hidden");
}

function hideWaking() {
  document.getElementById("waking")?.classList.add("hidden");
}

async function waitForBackend() {
  const slow = setTimeout(() => showWaking(
    '<span class="waking-dot"></span>服務喚醒中，約需 30–60 秒…' +
    '<small>雲端免費方案閒置後會休眠，第一次連線需要等它啟動，之後就很快了</small>'
  ), 2500);
  try {
    await api("/api/health");
  } finally {
    clearTimeout(slow);
    hideWaking();
  }
}

// ---------- 啟動 ----------
(async () => {
  try {
    initUpload();
    await waitForBackend();
    COMPANIES = await api("/api/companies");
    if (!COMPANIES.length) return showError("知識庫是空的");
    fillSelect($("company"), COMPANIES.map((c) => c.name));
    $("company").onchange = onCompanyChange;
    $("period").onchange = load;
    $("lastPeriod").onchange = load;
    $("reset").onclick = resetDashboard;
    $("gen-summary").onclick = genSummary;
    onCompanyChange();
    initCompare();
    initChat();
  } catch (e) {
    hideWaking();
    showError(`無法連線到 API：${e.message}　（後端有啟動嗎？）`);
  }
})();
