"""FastAPI 後端：把既有的 Hybrid RAG 邏輯包成 HTTP API，給 React 前端用。

刻意不重寫任何邏輯——這層只是把 agent_router / graph_rag / vector_rag 既有的函式
轉成 endpoint。app.py（Streamlit）完全不受影響，兩個前端可以並存。

本機啟動：
    venv/Scripts/python.exe -m uvicorn api:app --reload --app-dir src
    → http://localhost:8000/docs  （自動產生的 API 文件）

部署時 FastAPI 會同時提供 API 和 React 打包後的靜態檔（見底部的 SPA 掛載），
一個服務搞定，前後端同源也就沒有 CORS 問題。
"""
import io
import json
import os
import re
import threading
import time
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_router import answer_question
from graph_rag import (
    _PERIOD_RE,
    calc_change,
    is_cumulative,
    list_companies,
    list_metrics,
    list_periods,
)
from paths import purge_old, safe_join
from metric_alignment import (
    classify_metric,
    is_cross_comparable,
    is_hero_metric,
    metric_category,
    norm_metric_name,
)
from report_generator import generate_report
from standard_metrics import STANDARD_METRICS, _pick, align_standard, key_ratios
from vector_rag import get_all_sources, query_vector_rag

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(
    title="有你蒸好 · Hybrid RAG API",
    description="財報分析：Vector RAG（語意檢索）＋ 結構化指標庫（公式計算）＋ AI Agent 路由",
    version="1.0.0",
)

# 開發時前端跑在 Vite 的 5173，後端在 8000，屬於跨來源。
# 正式部署是同源（FastAPI 直接吐 React 靜態檔），這條就用不到了。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 額度保護：對「會花掉 Gemini／EAP 額度」的端點限流 ----------
# 為什麼需要：這些端點每呼叫一次就燒一次外部 API 額度，而額度是整個服務共用一份
# （金鑰在伺服器上，不是每個使用者自帶）。公開網址一旦被連續打，額度會在幾分鐘內見底，
# 之後所有人——包括示範中的我們——都會拿到 429。
#
# 只擋這幾條，不做全站限流：/api/metrics、/api/trend 這種純讀本地 JSON 的端點
# 不花額度，前端載入儀表板一次就要打好幾條，一起限流反而會誤傷正常操作。
#
# 用記憶體的滑動視窗，不引入 redis／slowapi：單一 process 部署（見 render.yaml），
# 多開一個外部相依只為了限流不划算。代價是重啟後計數歸零、多 worker 各算各的，
# 對這個規模可以接受。
_RATE_LIMITS = {
    # 端點名稱: (視窗內允許次數, 視窗秒數)
    "chat": (20, 60),        # 問答：正常人不會一分鐘問 20 題，但示範時連問幾題要放得過
    "upload": (5, 3600),     # 上傳：一份 PDF 要 VLM 逐頁解析，最貴的一條
    "report": (10, 600),     # Word 報告（本身不呼叫 LLM，但會掃全庫組檔）
    "summary": (15, 60),     # 儀表板的「生成本期總結」，是 GET 但會呼叫 Gemini
}
_RATE_HITS: dict = {}
_RATE_LOCK = threading.Lock()


def _client_key(request) -> str:
    """限流的識別鍵。部署在 Render 這類反向代理後面時，request.client.host 會是代理的 IP
    （所有人看起來都同一個），要改看 X-Forwarded-For 的第一段才是真正的來源。
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request, bucket: str):
    """滑動視窗限流。超過就丟 429，並附上 Retry-After 讓前端知道要等多久。"""
    limit, window = _RATE_LIMITS[bucket]
    now = time.time()
    key = (bucket, _client_key(request))
    with _RATE_LOCK:
        hits = [t for t in _RATE_HITS.get(key, []) if now - t < window]
        if len(hits) >= limit:
            retry = int(window - (now - hits[0])) + 1
            _RATE_HITS[key] = hits
            raise HTTPException(
                429,
                f"請求太頻繁，請在 {retry} 秒後再試"
                f"（{bucket} 每 {window} 秒最多 {limit} 次）",
                headers={"Retry-After": str(retry)},
            )
        hits.append(now)
        _RATE_HITS[key] = hits
        # 順手清掉沒人在用的鍵，否則長時間運行下這個 dict 只會單向長大
        if len(_RATE_HITS) > 1000:
            for k in [k for k, v in _RATE_HITS.items() if not v or now - v[-1] > 3600]:
                _RATE_HITS.pop(k, None)


TYPE_LABEL = {"ratio": "比率", "per_share": "每股", "amount": "金額"}


# 正規化搬到 metric_alignment（graph_rag 判累計時也要用，放這裡會循環 import）
_norm_metric_name = norm_metric_name


def _unit_map_norm(company: str) -> dict:
    """和 _unit_map 一樣，但用「去掉期別標籤的名稱」當鍵，補得更廣一點。"""
    from collections import Counter
    acc: dict = {}
    for p in list_periods(company):
        for m in list_metrics(company, p):
            u = m.get("unit")
            if u:
                acc.setdefault(_norm_metric_name(m["metric"]), Counter())[u] += 1
    return {k: c.most_common(1)[0][0] for k, c in acc.items()}


def _prefix_unit(norm: str, umap_norm: dict) -> Optional[str]:
    """同一筆數字在不同期的名稱可能多了描述性後綴：中信 2025Q4 叫「中信金單一 4Q25」、
    2026Q1 卻叫「中信金單一 1Q26 稅後淨利」。去掉期別後仍差一截，等值比對配不起來，
    所以再退一步用「前綴」找：拿最長的、且本身夠具體（≥4 字）的前綴來補。

    防呆：只有「同一種指標型別」才採用。否則「稅後淨利」（金額）會被「稅後淨利成長」
    （%）配上，補出來的單位是錯的——那比留空更糟。
    """
    if len(norm) < 5:
        return None
    kind = classify_metric(norm)
    best = None
    for key, unit in umap_norm.items():
        if len(key) >= 4 and key != norm and norm.startswith(key) and classify_metric(key) == kind:
            if best is None or len(key) > len(best[0]):
                best = (key, unit)
    return best[1] if best else None


def _fill_unit(name: str, raw_unit, umap: dict, umap_norm: Optional[dict] = None):
    """補單位。來源簡報常漏標（中信有 37% 的指標沒單位），沒單位的數字無從解讀。
    順序：本期有標就用 → 同公司其他期間標過的 → 依指標型別推定（比率→%、每股→元）。
    金額類「不推定」——百萬元和億元差 100 倍，猜錯比留白更糟，寧可誠實標示未知。
    回傳 (單位, 是否為推定)。
    """
    if raw_unit and str(raw_unit).strip() not in ("", "None"):
        return raw_unit, False
    u = umap.get(name)
    if u:
        return u, True
    norm = _norm_metric_name(name)
    if umap_norm:
        u = umap_norm.get(norm)
        if u:
            return u, True
        u = _prefix_unit(norm, umap_norm)
        if u:
            return u, True
    kind = classify_metric(name)
    if kind == "ratio":
        return "%", True
    if kind == "per_share":
        return "元", True
    return None, False


# 換算成「元」的倍率，用來做同期不同單位的金額比對
_UNIT_SCALE = {"千元": 1e3, "百萬元": 1e6, "NT$MN": 1e6, "NT$ mn": 1e6,
               "億元": 1e8, "十億元": 1e9, "兆元": 1e12}


def _anchor_units(ms: list, umap: dict, umap_norm: Optional[dict] = None) -> dict:
    """用「同一筆金額在同期以不同單位重複揭露」反推缺漏的單位。

    例：中信同一期同時有「中信銀行稅後淨利 16,586（百萬元）」與
    「中信銀行第一季稅後淨利 166（沒單位）」——166 億元 ＝ 16,586 百萬元，
    金額對得起來就能確定後者是「億元」。這是資料自我校驗，比用數值大小硬猜可靠
    （實測百萬元 282~127,587 與億元 1~5,607 範圍大幅重疊，量級根本猜不準）。
    配不到錨點就不猜，維持「單位未標示」。
    """
    anchors = []
    for m in ms:
        u, _ = _fill_unit(m["metric"], m.get("unit"), umap, umap_norm)
        s = _UNIT_SCALE.get(str(u).strip()) if u else None
        v = _cell_to_float(m.get("value"))
        if s and v:
            anchors.append(abs(v) * s)
    out: dict = {}
    if not anchors:
        return out
    for m in ms:
        u, _ = _fill_unit(m["metric"], m.get("unit"), umap, umap_norm)
        if u or classify_metric(m["metric"]) != "amount":
            continue
        v = _cell_to_float(m.get("value"))
        if not v:
            continue
        for cand in ("億元", "百萬元", "十億元", "千元"):
            real = abs(v) * _UNIT_SCALE[cand]
            if any(a and abs(real - a) / a < 0.01 for a in anchors):
                out[m["metric"]] = cand
                break
    return out


def _metric_payload(company: str, m: dict, umap: Optional[dict] = None,
                    anchor_units: Optional[dict] = None,
                    umap_norm: Optional[dict] = None) -> dict:
    """統一的指標輸出格式。unit/cumulative 一定要帶——前端要靠它們決定能不能比大小。"""
    unit, inferred = _fill_unit(m["metric"], m.get("unit"), umap or {}, umap_norm)
    if not unit and anchor_units:
        anchored = anchor_units.get(m["metric"])
        if anchored:
            unit, inferred = anchored, True
    kind = classify_metric(m["metric"], unit)
    return {
        "metric": m["metric"],
        "value": m["value"],
        "unit": unit,
        # 推定來的單位要標記，前端會加上「＊」誠實揭露，不假裝是原始揭露值
        "unit_inferred": inferred,
        "yoy": m.get("yoy"),
        "type": TYPE_LABEL.get(kind, "金額"),
        "comparable": kind in ("ratio", "per_share"),
        "cumulative": is_cumulative(company, m["metric"]),
        # 分組與門面判斷也由後端給，前端不必自己用 regex 猜。
        # 這樣改分類規則只要動後端，不用重新部署前端。
        "category": metric_category(m["metric"]),
        "hero": is_hero_metric(m["metric"]),
    }


@app.get("/api/health")
def health():
    companies = list_companies()
    return {
        "ok": True,
        "companies": len(companies),
        "metrics": sum(len(list_metrics(c, p)) for c in companies for p in list_periods(c)),
    }


@app.get("/api/companies")
def companies():
    """所有公司，各自附上有資料的期間"""
    return [{"name": c, "periods": list_periods(c)} for c in list_companies()]


@app.get("/api/metrics")
def metrics(
    company: str = Query(...),
    period: str = Query(...),
    last_period: Optional[str] = Query(None, description="給定就一併回傳對上期的變化率"),
):
    ms = list_metrics(company, period)
    if not ms:
        raise HTTPException(404, f"{company} {period} 沒有資料")

    umap = _unit_map(company)                        # 同公司其他期間標過的單位
    umap_norm = _unit_map_norm(company)              # 同指標不同期別標籤（2025年X / 3M26X）共用單位
    anchors = _anchor_units(ms, umap, umap_norm)     # 同期「同金額不同單位」互相校驗，反推剩下的
    out = []
    for m in ms:
        item = _metric_payload(company, m, umap, anchors, umap_norm)
        # calc_change 自己會擋掉累計指標的跨季比較，回 None 而不是假數字
        item["change"] = calc_change(company, m["metric"], period, last_period) if last_period else None
        out.append(item)
    return {"company": company, "period": period, "last_period": last_period, "metrics": out}


def _unit_map(company: str) -> dict:
    """掃過某公司所有期間，替每個指標找出最常出現的申報單位。

    來源資料有時同一個指標在某幾期漏標單位（例如中信「手續費淨收益」2025Q1~Q3
    標了百萬元、2025Q4/2026Q1 卻是空的）。跨機構換算時若單位是空的就無從對齊，
    用同公司其他期間的單位補回來，比直接放棄合理。
    """
    from collections import Counter
    acc: dict = {}
    for p in list_periods(company):
        for m in list_metrics(company, p):
            u = m.get("unit")
            if u:
                acc.setdefault(m["metric"], Counter())[u] += 1
    return {k: c.most_common(1)[0][0] for k, c in acc.items()}


@app.get("/api/compare")
def compare(
    company_a: str = Query(...),
    period_a: str = Query(...),
    company_b: str = Query(...),
    period_b: str = Query(...),
):
    """跨機構比較。只配對名稱完全相同的指標——語意配對已停用（見 app.py 的說明）。"""
    ma = {m["metric"]: m for m in list_metrics(company_a, period_a)}
    mb = {m["metric"]: m for m in list_metrics(company_b, period_b)}
    umap_a = _unit_map(company_a)
    umap_b = _unit_map(company_b)

    rows = []
    for name in sorted(set(ma) & set(mb)):
        try:
            va = float(str(ma[name]["value"]).replace(",", ""))
            vb = float(str(mb[name]["value"]).replace(",", ""))
        except ValueError:
            continue
        # 本期沒標單位就用同公司其他期間推定的補上，並標記是推定來的
        raw_a, raw_b = ma[name].get("unit"), mb[name].get("unit")
        unit_a = raw_a or umap_a.get(name)
        unit_b = raw_b or umap_b.get(name)
        kind = classify_metric(name, unit_a)
        rows.append({
            "metric": name,
            "value_a": va,
            "value_b": vb,
            # 兩家各自的申報單位分開帶——前端要據此把絕對金額換算到同一單位再比
            "unit_a": unit_a,
            "unit_b": unit_b,
            "unit_a_inferred": raw_a is None and unit_a is not None,
            "unit_b_inferred": raw_b is None and unit_b is not None,
            "unit": unit_a,  # 相容舊欄位
            "type": TYPE_LABEL.get(kind, "金額"),
            # 只有比率／每股能直接比大小；絕對金額各家單位可能不同
            "comparable": kind in ("ratio", "per_share"),
        })

    return {
        "company_a": company_a, "period_a": period_a,
        "company_b": company_b, "period_b": period_b,
        "rows": rows,
        # 標準比率對照：逐字配不起來的 ROE/ROA/NIM… 用人工維護的標準定義字典對齊，
        # 每列附上兩邊實際命中的原始欄位名稱，誠實揭露是怎麼配的（見 standard_metrics.py）
        "standard": align_standard(company_a, period_a, company_b, period_b),
    }


@app.get("/api/trend")
def trend(company: str = Query(...), metric: str = Query(...)):
    """單一指標橫跨所有期間的數列，給卡片上的 sparkline 用。

    只回真正的季度（2025Q1…2026Q1），把 "2026Q1財報" 這種非季度標籤排除——
    那是另一個資料來源，混進趨勢線會讓走勢看起來斷掉。
    """
    points = []
    for p in list_periods(company):
        if not _PERIOD_RE.match(p):
            continue
        hit = next((m for m in list_metrics(company, p) if m["metric"] == metric), None)
        if not hit:
            continue
        try:
            v = float(str(hit["value"]).replace(",", "").replace("(", "-").replace(")", ""))
        except ValueError:
            continue
        points.append({"period": p, "value": v})

    return {
        "company": company,
        "metric": metric,
        "points": points,
        # 累計指標的線一定逐季往上、跨年掉回原點，那是重新起算不是趨勢
        "cumulative": is_cumulative(company, metric),
    }


@app.get("/api/summary")
def summary(
    request: Request,
    company: str = Query(...),
    period: str = Query(...),
    last_period: Optional[str] = Query(None),
):
    """AI 觀點總結：用一句話講出這期的重點。呼應 Hybrid RAG + Agent 的定位——
    不是只堆數字，而是讓 AI 讀完數字給結論。
    只根據下方提供的真實數字，累計指標明確標記避免它拿去比錯。
    """
    # 這條是 GET、看起來像其他讀資料的端點，但它會真的呼叫 Gemini，所以要限流。
    # 儀表板上就是一顆按鈕，連點就是連續燒額度。
    _rate_limit(request, "summary")
    from agent_router import get_client, call_with_retry

    ms = list_metrics(company, period)
    if not ms:
        raise HTTPException(404, f"{company} {period} 沒有資料")

    # 送給 LLM 前先篩過：比率／每股 + 有算出變化率的，最多 24 個，避免 prompt 太長也省額度
    picked = []
    for m in ms:
        kind = classify_metric(m["metric"], m.get("unit"))
        change = calc_change(company, m["metric"], period, last_period) if last_period else None
        if kind in ("ratio", "per_share") or change is not None:
            picked.append((m, kind, change))
    picked = picked[:24] or [(m, classify_metric(m["metric"], m.get("unit")), None) for m in ms[:24]]

    lines = []
    for m, kind, change in picked:
        s = f"{m['metric']}：{m['value']}{m.get('unit') or ''}"
        if change is not None:
            s += f"（較{last_period} {'+' if change > 0 else ''}{change}%）"
        if is_cumulative(company, m["metric"]):
            s += "［年初至今累計，勿跨季比較］"
        lines.append(s)

    prompt = (
        f"你是財務分析助理。以下是{company} {period}的真實財務數據，"
        f"請用繁體中文寫「一到兩句」的重點總結，直接給結論、點出亮點與需留意處，"
        f"只能引用下方數字、不要虛構、不要條列、不要開場白：\n\n" + "\n".join(lines)
    )
    try:
        resp = call_with_retry(lambda: get_client().models.generate_content(
            model="gemini-flash-lite-latest", contents=prompt))
        return {"summary": resp.text.strip(), "company": company, "period": period}
    except Exception as e:
        raise HTTPException(502, f"AI 總結失敗：{e}")


class ChatRequest(BaseModel):
    question: str
    # 公司／期間改成選填：前端不再有選單，後端從問題文字自動辨識
    company: Optional[str] = None
    period: Optional[str] = None
    last_period: Optional[str] = None
    use_eap: bool = False


_QUARTER_MAP = {"一": "1", "二": "2", "三": "3", "四": "4"}


def _infer_company(question: str) -> Optional[str]:
    """從問題文字裡認出是哪一家公司（支援「玉山」這種簡稱）；認不出就用第一家。"""
    from agent_router import _short_name
    for c in list_companies():
        if c in question or (_short_name(c) and _short_name(c) in question):
            return c
    cs = list_companies()
    return cs[0] if cs else None


def _infer_period(question: str, company: Optional[str]) -> Optional[str]:
    """從問題文字解析「西元年＋季」（支援 2025Q4、2025年第四季）；解析不到就用該公司最新一期。"""
    ps = list_periods(company) if company else []
    ym = re.search(r"20\d{2}", question)
    year = ym.group() if ym else None
    if not year:
        rm = re.search(r"1[01]\d年", question)  # 民國年
        if rm:
            year = str(1911 + int(rm.group()[:-1]))
    qm = re.search(r"Q\s*([1-4])", question, re.I) or re.search(r"第\s*([一二三四1-4])\s*季", question)
    q = _QUARTER_MAP.get(qm.group(1), qm.group(1)) if qm else None
    if year and q:
        for cand in (f"{year}Q{q}", f"{year}Q{q}財報"):
            if cand in ps:
                return cand
    return _default_period(ps)


def _default_period(ps: list) -> Optional[str]:
    """沒指定期間時該用哪一期。

    不能直接取 ps[-1]：期間是字串排序，「2026Q1財報」排在「2026Q1」後面，
    但財報期只有資產負債表那十幾個科目，法說會簡報的 NIM、手續費淨收益都不在裡面。
    直接拿它當預設，會讓儀表板一開啟只剩十幾張卡片、問答也查不到多數指標。
    所以優先挑「純季度」那一期；真的只有財報期才退回去用它。
    """
    if not ps:
        return None
    quarters = [p for p in ps if _PERIOD_RE.match(p)]
    return quarters[-1] if quarters else ps[-1]


_CHART_FENCE = re.compile(r"```chart\s*(.*?)```", re.S)


def _metric_in_directive(directive: str, company: str):
    """指令標題裡若點名了某個已知指標（如「各期手續費淨收益」），把它找出來。
    排除「公司名本身也是一個指標」這種雜訊（中信金控、中信…），挑最長的具體指標名。"""
    names = {m["metric"] for p in list_periods(company) for m in list_metrics(company, p)}
    hits = [n for n in names if n and len(n) >= 3 and n in directive
            and n != company and not company.startswith(n)]
    return max(hits, key=len) if hits else None


def _metric_unit(company: str, metric: str):
    for p in reversed(list_periods(company)):
        u = next((m.get("unit") for m in list_metrics(company, p) if m["metric"] == metric), None)
        if u:
            return u
    return ""


_PERIOD_CELL = re.compile(r"20\d{2}\s*Q[1-4]|Q[1-4]|FY\d{2}|\d[HQ]\d{2}")


def _parse_series_table(text: str):
    """EAP 的答案通常自己附了一張「期間｜數值」資料表。把它解析出來，
    圖就能直接用 EAP 自己的數字畫，和答案文字一致（不會又換成我們的指標庫 數字）。
    回傳 {points:[{period,value}], label} 或 None。
    """
    def to_num(s):
        mm = re.search(r"-?[\d,]+\.?\d*", str(s))
        if not mm:
            return None
        try:
            return float(mm.group().replace(",", ""))
        except ValueError:
            return None

    rows = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):  # 分隔列
                continue
            rows.append(cells)
    if len(rows) < 2:
        return None

    header, body = rows[0], rows[1:]
    ncol = max(len(r) for r in rows)
    col = lambda i: [r[i] for r in body if i < len(r)]

    pcol = next((i for i in range(ncol)
                 if col(i) and sum(bool(_PERIOD_CELL.search(v)) for v in col(i)) >= max(1, len(col(i)) // 2)),
                None)
    if pcol is None:
        return None
    vcol = next((i for i in range(ncol) if i != pcol and col(i)
                 and sum(to_num(v) is not None for v in col(i)) >= max(1, len(col(i)) // 2)),
                None)
    if vcol is None:
        return None

    points = []
    for r in body:
        if pcol < len(r) and vcol < len(r):
            pm = _PERIOD_CELL.search(r[pcol])
            n = to_num(r[vcol])
            if pm and n is not None:
                points.append({"period": pm.group().replace(" ", ""), "value": n})
    if len(points) < 2:
        return None
    return {"points": points, "label": header[vcol] if vcol < len(header) else ""}


def _eap_chart_from_directive(answer: str, company: str, period: str):
    """EAP 只吐「畫圖指令」（```chart / barchart: 標題）而沒有數據。
    偵測到就用我們自己的 指標庫數據把圖畫出來，並把裸指令從文字裡拿掉。
    回傳（清理過的答案文字, 圖表 payload 或 None）。
    """
    m = _CHART_FENCE.search(answer)
    if not m:
        return answer, None
    directive = m.group(1)
    c = _infer_company(directive) or company
    cleaned = _CHART_FENCE.sub("", answer).strip()
    dm = re.search(r"barchart:\s*(.+)", directive)
    title = dm.group(1).strip() if dm else ""
    metric = _metric_in_directive(directive, c)

    # 0) EAP 答案通常自己附了一張「期間｜數值」資料表 → 圖直接用 EAP 的數字畫，
    #    和上方表格／文字一致（不會又被換成我們指標庫 的數字，導致對不上）
    tbl = _parse_series_table(answer)
    if tbl:
        return cleaned, {"kind": "series", "title": title or f"{c} 各期{metric or tbl['label']}",
                         "metric": metric or tbl["label"] or "數值", "unit": "", "points": tbl["points"]}

    # 1) 沒有資料表，但指令點名了某個具體指標 → 用我們指標庫 的該指標各期。
    #    只對「比率／每股」畫趨勢：絕對金額同名常不同義（年度值 vs 單季值，單位還混用百萬/億/十億），
    #    跨期畫會畫出假的暴跌。金額類就不畫這條線，改走下方的標準關鍵比率圖。
    if metric and is_cross_comparable(metric):
        t = trend(company=c, metric=metric)
        pts = t.get("points", [])
        if pts:
            cleaned += f"\n\n（下圖為系統依 指標庫數據繪製：{c} 各期{metric}）"
            return cleaned, {"kind": "series", "title": title or f"{c} 各期{metric}",
                             "metric": metric, "unit": _metric_unit(c, metric), "points": pts}

    # 點名的是「絕對金額」指標 → 跨期畫不可靠、不畫；也別改畫不相干的比率圖（標題會對不上內容），
    # 就不附圖，讓答案只留 EAP 表格，乾淨不誤導。
    if metric and not is_cross_comparable(metric):
        return cleaned, None

    # 2) 沒點名具體指標 → 畫「標準關鍵比率」（單位一致 %）
    p = _infer_period(directive, c) if re.search(r"Q[1-4]|第.季|20\d{2}", directive) else period
    items = key_ratios(c, p)
    if not items:
        return cleaned, None
    cleaned += "\n\n（下圖為系統依 指標庫數據繪製的標準關鍵比率，單位皆為 %）"
    # 這條路一律畫「標準關鍵比率」，所以標題要照實寫，不要沿用 EAP 指令的標題
    # （EAP 常給「各期手續費淨收益」這種標題，掛在比率圖上會標題文不對圖）
    return cleaned, {"kind": "bars", "title": f"{c} {p} 主要關鍵比率", "items": items}


# ---------- EAP 答案交叉驗證 ----------
# EAP 是外部平台，它的數字我們無法保證正確。這裡把 EAP 答案裡的標準指標
# （EPS／ROE／ROA…）跟我們自己「本地結構化指標庫」挑出的乾淨數字比對，
# 差太多就標出來，提醒使用者「這筆對不上，請留意」——做的是交叉驗證，不是斷言誰對。

def _cell_to_float(cell):
    m = re.search(r"-?[\d,]+\.?\d*", str(cell))
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def _resolve_company(cell):
    """把表格裡的公司欄位對回知識庫的正式公司名（支援「玉山」這種簡稱）。"""
    from agent_router import _short_name
    s = str(cell).strip()
    if not s:
        return None
    for c in list_companies():
        if c == s:
            return c
    for c in list_companies():
        if c in s or (_short_name(c) and _short_name(c) in s):
            return c
    return None


def _match_period(company, period):
    """把「2026Q1」對到該公司實際存在的期間資料夾（可能是 2026Q1 或 2026Q1財報）。"""
    if not period:
        return None
    ps = list_periods(company)
    if period in ps:
        return period
    if f"{period}財報" in ps:
        return f"{period}財報"
    for p in ps:
        if p.startswith(period):
            return p
    return None


def _spec_for_text(text):
    """看一段文字（通常是表頭）像哪個標準指標。"""
    t = str(text)
    for spec in STANDARD_METRICS:
        if any(re.search(p, t) for p in spec["include"]):
            return spec
    return None


def _significant_gap(eap_val, local_val):
    """EAP 值與本地值是否差到值得提醒：相對差 >5% 且絕對差夠大（避免抓四捨五入）。
    門檻設在 5%：像「抓錯季度」這種差 ~9% 的錯也要抓得到，但仍會放過純四捨五入。"""
    diff = abs(eap_val - local_val)
    if local_val == 0:
        return diff > 0.01
    rel = diff / abs(local_val)
    return rel > 0.05 and diff > 0.02


def _num_near_metric(segment, spec):
    """在一段文字裡，抓「指標名稱附近」的數值（比率取 %、每股取 元）。
    優先取指標關鍵字之後最近的那個數字，避免抓到年增率等旁邊的數字。"""
    mpos = -1
    for p in spec["include"]:
        m = re.search(p, segment, re.I)
        if m:
            mpos = m.start()
            break
    pat = r"(\d+\.?\d*)\s*%" if spec["unit"] == "%" else r"(\d+\.?\d*)\s*元"
    cands = [(m.start(), float(m.group(1))) for m in re.finditer(pat, segment)]
    if not cands and spec["unit"] != "%":  # 每股類有時沒帶「元」，退而取「為 X」
        cands = [(m.start(), float(m.group(1))) for m in re.finditer(r"為\s*\**\s*(\d+\.?\d*)", segment)]
    if not cands:
        return None
    if mpos >= 0:
        after = [c for c in cands if c[0] >= mpos]
        if after:
            return after[0][1]
    return cands[0][1]


def _cross_check_prose(answer, fallback_period):
    """純文字（非表格）答案的交叉驗證：把文中每家公司講到的標準指標數字，跟本地知識庫比。
    以「公司名出現的位置」把文字切段，各段內找指標與數值配對——EAP 常一句話講一家，切段夠用。

    回傳 (不一致清單, 實際比對過幾筆)。要回報比對數，是因為「0 筆不一致」有兩種意思：
    比對過而且都吻合，或者根本沒東西可比。後者代表這個答案我們驗不了，得誠實講。
    """
    text = str(answer)
    # 純文字用「完整公司名」比對就好——用簡稱會誤撞（如「第一」金控 vs「第一季」）。
    # EAP 的答案幾乎都寫全名，全名對不到頂多漏報，總比誤報一家不相干的公司好。
    positions = [(text.find(c), c) for c in list_companies() if text.find(c) >= 0]
    positions.sort()
    if not positions:
        return [], 0

    out, checked = [], 0
    for i, (pos, c) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        segment = text[pos:end]
        spec = _spec_for_text(segment)
        if not spec:
            continue
        pm = re.search(r"20\d{2}\s*Q[1-4]", segment)
        period = pm.group().replace(" ", "") if pm else fallback_period
        period = _match_period(c, period)
        if not period:
            continue
        eap_val = _num_near_metric(segment, spec)
        if eap_val is None:
            continue
        pick = _pick(list_metrics(c, period), spec, period)
        if not pick:
            continue
        local_val = round(pick["value"], 2)
        checked += 1
        if _significant_gap(eap_val, local_val):
            out.append({
                "company": c,
                "metric": spec["label"],
                "period": period,
                "eap_value": eap_val,
                "local_value": local_val,
                "local_source": pick["name"],
            })
    return out, checked


# 數字後面緊跟的單位。EAP 常自己換算單位而且會算錯，所以比對前一定要看它「宣稱」的單位，
# 不能假設它跟本地一樣。
_NUM_WITH_UNIT = re.compile(r"(-?[\d,]+(?:\.\d+)?)\s*(兆元|十億元|億元|百萬元|千元|元)?")


def _to_base_amount(value, unit):
    """把金額換算成「元」，用來比較不同單位的數字。認不出單位就回 None。"""
    if unit is None:
        return None
    u = str(unit).strip()
    if u == "元":
        return value
    scale = _UNIT_SCALE.get(u)
    return value * scale if scale else None


def _pick_local_metric(company, period, label):
    """在本地找出「叫這個名字」的指標，優先集團層級、排除子公司。

    重用 standard_metrics 那套挑選邏輯：問「稅後淨利」時，中信有
    「3M26合併稅後淨利」「中信銀行第一季稅後淨利」「台灣人壽第一季稅後淨利」等多筆，
    直接抓第一個會比到子公司的數字，反而製造假警報。
    """
    from standard_metrics import _GROUP_LEVEL, _SUBSIDIARY, _pick

    ms = list_metrics(company, period)
    if not ms:
        return None
    # 只留「金額類」候選。問「稅後淨利」時，中信有「其他子公司整體稅後淨利年成長 111%」
    # 這種比率型指標也含關鍵字，挑到它就會拿 111% 去跟 EAP 的億元數字比，必然誤報。
    ms = [m for m in ms if classify_metric(m["metric"], m.get("unit")) == "amount"]
    if not ms:
        return None
    spec = {"include": [re.escape(label)],
            # 成長／年增這類衍生指標不是金額本身
            "exclude": _SUBSIDIARY + [r"成長", r"年增", r"季增", r"佔比", r"占比"],
            "prefer": _GROUP_LEVEL, "unit": ""}
    return _pick(ms, spec, period)


# 子公司名稱在本地指標裡的寫法不一定跟 EAP 一樣（EAP 寫「中國信託銀行」，
# 本地是「中信銀行」；「一銀」對「第一銀行」）。收斂到本地用的寫法才對得上。
_ENTITY_ALIASES = {
    "中國信託銀行": "中信銀行",
    "一銀": "第一銀行",
    "國泰世華": "國泰世華銀行",
}


def _entity_in(text):
    """這段文字指名了哪個子公司？沒有就回 None（代表講的是集團層級）。

    為什麼要分：EAP 說「中信銀行第三季稅後淨利 143 億」時，那是**子公司**的數字。
    若照舊拿金控合併的 249 億去比，必然報出不一致——但 EAP 其實完全正確，
    本地就有「中信銀行第三季稅後淨利 = 143 億元」這筆。實測一則回答就這樣製造 4 則假警報。
    """
    from standard_metrics import _SUBSIDIARY

    s = str(text)
    for alias, canon in _ENTITY_ALIASES.items():
        if alias in s:
            return canon
    # 「中信銀行」「台灣人壽」這種「前綴＋業別」的寫法，抓出完整的實體名
    m = re.search(r"([一-鿿]{2,6}?(?:" + "|".join(
        p for p in _SUBSIDIARY if not p.startswith("(")) + r"))", s)
    return m.group(1) if m else None


# 「累計」與「單季」是兩個不同的數字，混比等於製造假警報：中信銀行 2025Q3
# 單季 143 億、前三季累計 421 億，兩筆都在本地，拿錯一筆就報不一致。
_CUMULATIVE_HINT = re.compile(r"前[一二三四兩]?季|累計|累積|上半年|年初至今|YTD|\d+M\d{2}|1H\d{2}")
_SINGLE_HINT = re.compile(r"單季|第[一二三四]季|[1-4]Q\d{2}|當季")


def _is_cumulative_text(text):
    """從欄位標題／上下文判斷這個數字是累計還是單季；判斷不出來回 None。"""
    s = str(text)
    if _CUMULATIVE_HINT.search(s):
        return True
    if _SINGLE_HINT.search(s):
        return False
    return None


# 單位的寫法比想像中雜。實測 EAP 出現過：
#   「2026Q1 合併稅後淨利（億元新台幣）」  ← 括號裡不只單位
#   「（單位：十億元）」寫在表格上方那一行，根本不在表頭
# 原本要求「括號裡剛好只有單位」，這三種全部漏掉，於是整張表一筆都沒驗到，
# 畫面上還顯示「無法驗證」——最該展示防護力的跨公司比較題反而什麼都沒做。
_UNIT_TOKEN = r"(兆元|十億元|億元|百萬元|千元)"
_UNIT_IN_PAREN = re.compile(r"[（(][^（()）]{0,10}?" + _UNIT_TOKEN + r"[^（()）]{0,6}?[）)]")
_UNIT_AFTER_LABEL = re.compile(r"單位\s*[:：]?\s*" + _UNIT_TOKEN)
_BARE_YUAN = re.compile(r"[（(]\s*元\s*[）)]")


def _unit_hint(*texts):
    """從表頭或表格前的敘述裡找出金額單位；找不到回 None。

    刻意不在整段文字裡裸找「元」——「元大證券」「還原」都會誤中，
    只有寫成「（元）」這種明確形式才認。
    """
    for t in texts:
        s = str(t or "")
        for pat in (_UNIT_AFTER_LABEL, _UNIT_IN_PAREN):
            m = pat.search(s)
            if m:
                return m.group(1)
        if _BARE_YUAN.search(s):
            return "元"
    return None


def _pins_other_period(name, period):
    """這個指標的名稱是不是釘死在「別的期間」。

    同一份簡報常附去年同期當對照，解析時連標籤一起被收進當期資料夾：
    國泰 2026Q1 底下就有「國泰世華銀行 1Q25 稅後淨利 = 12.2 十億元」。
    拿它當比對基準會出大事——EAP 若把 1Q25 的數字當成 2026Q1 回答（實測就發生了），
    我們反而會回報「一致」，等於幫錯誤背書。寧可不比。
    """
    from graph_rag import pins_own_period
    from standard_metrics import _expected_tokens, _name_year, _target_year

    if not pins_own_period(name):
        return False
    ny, ty = _name_year(name), _target_year(period)
    if ny is not None and ty is not None and ny != ty:
        return True
    return not any(tok in str(name) for tok in _expected_tokens(period))


def _pick_local_for_entity(company, period, entity, label, cumulative=None):
    """找「某個子公司」的某個指標。找不到就回 None——**不退回集團層級**。

    退回集團層級正是假警報的來源：EAP 明明在講子公司，卻被拿去跟金控合併數比。
    寧可不比（少報一筆），也不要報一則會讓使用者不信任正確答案的假警報。
    """
    ms = [m for m in list_metrics(company, period)
          if classify_metric(m["metric"], m.get("unit")) == "amount"]
    # 同一家子公司，簡報有時寫全名、有時省略業別：本地是「國泰世華稅後淨利」，
    # EAP 寫「國泰世華銀行」。只比對全名會整個對不上——實測 EAP 拿 1Q25 的
    # 國泰世華數字回答 2026Q1，就是因為這個而沒被抓到。
    # 只有去掉業別後仍夠具體（≥4 字）才採用簡稱：「國泰人壽」去成「國泰」會把
    # 國泰世華、國泰產險全部誤配成同一家。
    forms = {entity}
    for suffix in ("銀行", "人壽", "產險", "證券", "投信", "創投", "投顧"):
        if entity.endswith(suffix):
            core = entity[: -len(suffix)]
            # 兩道門檻缺一不可：
            #   ≥4 字     ——「國泰人壽」去成「國泰」會把國泰世華、國泰產險全配成同一家
            #   不在母公司名稱裡 ——「中信銀行」去成「中信」正好是「中信金控」的一部分，
            #                     會讓子公司的數字配到金控合併數，繞回一開始那個假警報
            if len(core) >= 4 and core not in company:
                forms.add(core)
            break

    cands = []
    for m in ms:
        name = str(m["metric"])
        if label not in name or not any(f in name for f in forms):
            continue
        if re.search(r"成長|年增|季增|佔比|占比", name):
            continue
        if _pins_other_period(name, period):
            continue
        val = _to_float_safe(m.get("value"))
        if val is None or not m.get("unit"):
            continue
        cands.append((_is_cumulative_text(name), len(name),
                      {"name": name, "value": val, "unit": m["unit"]}))
    if not cands:
        return None

    if cumulative is not None:
        # 名稱明講單季／累計的優先，名稱看不出來的只當備胎。
        # 中信銀行同時有「第三季稅後淨利 143 億」（單季）、「前三季稅後淨利 421 億」（累計）
        # 和沒標期別的「稅後淨利 42,057 百萬元」。挑最短名稱會挑到沒標的那筆，
        # 於是 EAP 把累計值標成單季時，反而因為數字對得上而放行——防護等於失效。
        explicit = [c for c in cands if c[0] is cumulative]
        ambiguous = [c for c in cands if c[0] is None]
        cands = explicit or ambiguous or []
        if not cands:
            return None

    cands.sort(key=lambda t: t[1])      # 同一類裡取名稱最短＝最乾淨的那筆
    return cands[0][2]


def _to_float_safe(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def cross_check_metrics(answer, company, period, unmatched=None):
    """比對 EAP 答案中「任何本地也有的指標」數字，不限於七個標準比率。

    為什麼要擴大：實測 EAP 回答「中信稅後淨利 166 億、玉山 87.6 億」，
    而本地是 236.08 億與 100.68 億——它自己換算單位而且算錯了。
    但「稅後淨利」不在 STANDARD_METRICS 的七個標準比率裡，原本的交叉驗證完全比不到，
    這種錯就這樣送到使用者面前。

    只比對「本地明確找得到對應指標」的項目，找不到就跳過——
    沿用專案一貫的高精確度優先：寧可少報，也不要對不相干的數字發假警報。
    回傳 (不一致清單, 實際比對過幾筆)。

    unmatched：傳一個 list 進來，就會把「認得出是哪個指標、但本地沒有對應數字」
    的項目記進去。這是為了讓畫面分得出「驗過沒問題」和「根本沒驗」——
    實測 EAP 答台灣人壽前三季 177 億，本地沒收錄這筆，於是安靜跳過，
    畫面上跟「四筆全部驗過」看起來一模一樣，等於默認了那個沒驗過的數字。

    用選填參數而不是改回傳值的元組長度：這支有多個呼叫端與測試，
    多回一個元素會把它們全部打掉，而多數呼叫端並不需要這份清單。
    """
    text = str(answer)
    # 候選標籤：本地這一期所有指標的名稱，取「去掉期別標籤」後的形式來比對，
    # 因為 EAP 通常寫「稅後淨利」而本地叫「3M26合併稅後淨利」
    labels = set()
    for m in list_metrics(company, period):
        norm = norm_metric_name(m["metric"])
        if len(norm) >= 3:
            labels.add(norm)
    labels |= {"稅後淨利", "營業收入", "營業費用", "總資產"}   # EAP 少照抄本地的完整名稱
    labels = sorted(labels, key=len, reverse=True)   # 長的先比，避免「淨利」搶走「稅後淨利」

    rows = _markdown_rows(text)
    out, checked = [], 0

    def compare(comp, label, eap_val, eap_unit, entity=None, cumulative=None):
        """把一組 (公司,指標,數值,單位) 跟本地比。回傳是否真的比到了。

        entity：EAP 這筆講的是哪個子公司（None = 集團層級）。
        指名子公司時只跟該子公司的數字比，本地沒有就跳過，絕不退回集團層級——
        那會把「EAP 講中信銀行 143 億」拿去跟「金控合併 249 億」比，報出假的不一致。
        """
        nonlocal checked
        if entity:
            pick = _pick_local_for_entity(comp, period, entity, label, cumulative)
            display_comp = entity
        else:
            pick = _pick_local_metric(comp, period, label)
            display_comp = comp

        def skip():
            """記下「認得出是哪個指標，但驗不了」的項目，好讓畫面誠實揭露。"""
            if unmatched is not None:
                key = (display_comp, label, str(eap_val))
                if key not in {(u["company"], u["metric"], u["eap_value_raw"])
                               for u in unmatched}:
                    unmatched.append({
                        "company": display_comp, "metric": label, "period": period,
                        "eap_value": f"{eap_val:,.2f}".rstrip("0").rstrip(".") + (eap_unit or ""),
                        "eap_value_raw": str(eap_val),
                    })
            return False

        if not pick or not pick.get("unit"):
            return skip()
        # 兩邊都換算成「元」才有可比性——EAP 宣稱的單位常跟本地不同，而且它常換算錯
        eap_base = _to_base_amount(eap_val, eap_unit)
        local_base = _to_base_amount(pick["value"], pick["unit"])
        if eap_base is None or local_base is None:
            return skip()
        checked += 1
        if _significant_gap(eap_base, local_base):
            fmt = lambda v, u: f"{v:,.2f}".rstrip("0").rstrip(".") + (u or "")
            out.append({
                "company": display_comp, "metric": label, "period": period,
                "eap_value": fmt(eap_val, eap_unit),
                "local_value": fmt(pick["value"], pick["unit"]),
                "local_source": pick["name"],
            })
        return True

    if len(rows) >= 2:
        # 表格：單位常寫在表頭（「稅後淨利（億元）」），不在數字旁邊；公司則寫在各列
        header, body = rows[0], rows[1:]
        cols = {}
        # 單位常寫在表格「上方那一行」而不是表頭（「…如下（單位：十億元）：」），
        # 只看表頭會整張表都判不出單位，於是一筆都比不了。
        pre_text = text.split("\n|")[0][-120:] if "\n|" in text else ""
        table_unit = _unit_hint(pre_text)
        for i, h in enumerate(header):
            label = next((l for l in labels if l in h), None)
            if label:
                # 單季／累計寫在欄位標題（「2025Q3單季稅後淨利」「前三季累計稅後淨利」），
                # 不分開的話兩欄會比到同一筆本地數字，其中一欄必然報錯
                cols[i] = (label, _unit_hint(h) or table_unit, _is_cumulative_text(h))
        for r in body:
            comp = next((rc for rc in (_resolve_company(c) for c in r) if rc), None) or company
            # 列首通常是實體名稱（「中信銀行」「台灣人壽」）。_resolve_company 會把
            # 「中信銀行」解析成「中信金控」（簡稱「中信」是它的子字串），
            # 所以不能只靠它判斷層級，要另外抓子公司名。
            entity = next((e for e in (_entity_in(c) for c in r) if e), None)
            for i, cell in enumerate(r):
                if i not in cols:
                    continue
                label, unit, cum = cols[i]
                m = _NUM_WITH_UNIT.search(cell)
                if not m:
                    continue
                try:
                    val = float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                compare(comp, label, val, m.group(2) or unit, entity=entity, cumulative=cum)
    else:
        # 純文字：單位可能緊跟數字，也可能寫在指標名稱後的括號裡
        seen = set()
        for label in labels:
            idx = text.find(label)
            if idx < 0 or label in seen:
                continue
            tail = text[idx + len(label): idx + len(label) + 12]
            m = _NUM_WITH_UNIT.search(text, idx + len(label))
            if not m:
                continue
            try:
                val = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            # 往前看一小段：「**中國信託銀行**：2025年第三季稅後淨利為143億元」——
            # 主詞在指標名之前，只看指標名本身會誤以為講的是集團。
            lead = text[max(0, idx - 40): idx]
            if compare(company, label, val, m.group(2) or _unit_hint(tail),
                       entity=_entity_in(lead),
                       cumulative=_is_cumulative_text(lead + label + tail)):
                seen.add(label)
    return out, checked


def _markdown_rows(text):
    """把 markdown 表格的資料列抽出來（跳過 |---| 分隔列）。"""
    rows = []
    for ln in str(text).split("\n"):
        s = ln.strip()
        if s.startswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            rows.append(cells)
    return rows


def cross_check_eap(answer, fallback_period):
    """比對 EAP 答案中的標準指標數字與本地知識庫。
    回傳 (不一致清單, 實際比對過幾筆)。
    有表格就比表格（較可靠）；沒表格則退回逐句解析純文字答案。"""
    rows = []
    for ln in str(answer).split("\n"):
        s = ln.strip()
        if s.startswith("|") and s.count("|") >= 2:
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):  # 分隔列
                continue
            rows.append(cells)
    if len(rows) < 2:
        return _cross_check_prose(answer, fallback_period)

    header, body = rows[0], rows[1:]
    # 哪幾欄是「標準指標的數值欄」——看表頭認得出指標的才算
    col_spec = {i: _spec_for_text(header[i]) for i in range(len(header))}
    if not any(col_spec.values()):
        return [], 0

    out, checked = [], 0
    for r in body:
        company = next((rc for rc in (_resolve_company(c) for c in r) if rc), None)
        if not company:
            continue
        period = fallback_period
        for c in r:
            pm = re.search(r"20\d{2}\s*Q[1-4]", c)
            if pm:
                period = pm.group().replace(" ", "")
                break
        period = _match_period(company, period)
        if not period:
            continue
        ms = list_metrics(company, period)
        for i, cell in enumerate(r):
            spec = col_spec.get(i)
            if not spec:
                continue
            eap_val = _cell_to_float(cell)
            if eap_val is None:
                continue
            pick = _pick(ms, spec, period)
            if not pick:
                continue
            local_val = round(pick["value"], 2)
            checked += 1
            if _significant_gap(eap_val, local_val):
                out.append({
                    "company": company,
                    "metric": spec["label"],
                    "period": period,
                    "eap_value": eap_val,
                    "local_value": local_val,
                    "local_source": pick["name"],
                })
    return out, checked


def _metric_aliases(text):
    """問題若點到某個標準指標，回傳它的同義詞（含英文縮寫）。
    各家用詞不一（國泰「淨利差」、玉山「淨利息收益率」都是 NIM），問 EAP 時把別名一併附上，
    EAP 的檢索器才不會因為用詞不同而回「查不到」。同義詞來源就是本地的標準比率字典。"""
    aliases = set()
    for spec in STANDARD_METRICS:
        if any(re.search(p, text, re.I) for p in spec["include"]):
            for p in spec["include"]:
                clean = p.replace(r"\b", "").strip()
                if clean and re.fullmatch(r"[\w一-鿿() ]+", clean):
                    aliases.add(clean)
    return sorted(aliases)


# EAP 回「查不到」時的常見說法。實測它的措辭會變（「查詢不到」「無法取得」「未能查詢到」），
# 所以比對關鍵動詞而不是整句。
# EAP 同一種情況每次的說法都不一樣，實測就見過「查詢不到」「查無」「無法取得」
# 「未能查詢到」「未查詢到」「並沒有…內容」，而且「資料／資訊」也會互換。
# 逐句列舉追不完，所以拆成「動詞骨架」寫，把可省略的字設成選配。
_EAP_NO_DATA = re.compile(
    r"查無|查詢不到|查不到|找不到|沒有找到|"
    r"無法(取得|提供|查詢|回答|找到)|"
    r"未(能)?(查詢|查得|查到|取得|找到|收錄)|"
    r"尚無|未收錄|無相關(資料|資訊|內容)|"
    # 「並沒有中信金控 2025 年第三季法說會的逐字稿或問答內容」——中間夾的公司＋期間＋
    # 文件類型可以很長，窗口要放寬，但仍限制在同一句內（不跨標點）才算。
    r"(沒有|並無)[^。；\n]{0,40}(資料|資訊|內容|紀錄|逐字稿)"
)

# 平台還有一道「專案相關性」關卡，擋在檢索之前、而且回的是英文：
#     Unable to answer question not relevant to this project and its data
# 它不受後台「Setting for when unable to answer」控制（那裡設的是中文），所以中文骨架
# 一句都對不上，補強與退路按鈕都不會觸發。實測問一句與專案無關的問題就會遇到。
# 這種情況正好是 Vector RAG 最該接手的時機——平台根本沒去檢索，本地卻可能有逐字稿。
_EAP_NO_DATA_EN = re.compile(
    r"unable to (answer|find|provide|retrieve|locate)|"
    r"(cannot|can not|can't|could not|couldn't) (answer|find|provide|locate|retrieve)|"
    r"not relevant to this project|"
    r"no (relevant )?(data|information|records?|results?|content)\b[^.\n]{0,40}"
    r"(found|available|in (the )?(project|knowledge|database))|"
    r"(is|are) not (available|included|covered) in",
    re.I,
)

# 兜底用。上面的骨架仍可能漏掉沒見過的講法，但「查不到」的回覆有兩個穩定特徵：
# 帶道歉語氣、而且很短（真的查到資料時它會長篇大論還附表格）。
_EAP_APOLOGY = re.compile(r"抱歉|unfortunately", re.I)
_EAP_SHORT_ANSWER = 120


def _eap_found_nothing(answer) -> bool:
    """EAP 這次是不是根本沒撈到資料。

    有 markdown 表格就代表它撈到東西了——那種情況即使句子裡出現「部分查不到」，
    也不算整題落空，不需要跳出退路提示。

    None 要在 str() 之前擋掉：平台回 {"result": null} 時（ask_question 的非串流分支
    會原樣把 None 傳下來），str(None) 是 "None"——非空字串、又不命中任何說法，
    於是被當成「有回答」，畫面上就會顯示 None 這四個字當作答案。
    """
    if answer is None:
        return True
    text = str(answer).strip()
    if not text:
        return True
    if text.count("|") >= 4:
        return False
    if _EAP_NO_DATA.search(text) or _EAP_NO_DATA_EN.search(text):
        return True
    return bool(_EAP_APOLOGY.search(text)) and len(text) <= _EAP_SHORT_ANSWER


# 判定「本地真的答得出來」的字面命中門檻。
# query_vector_rag 一定會回 top-1，不管相不相關——問第一金控「參股泰山保險」它照樣給你一段
# 完全無關的簡報敘述。按了才發現本地也答不出來，比不給按鈕更糟，所以要求字面上真的對得上。
#
# 用「命中比例」而不是命中數量：實測答得出來的題目命中 0.7~1.0，答不出來的只有 0.13~0.21，
# 分得很開；而比例不受問題長短影響，短問題不會因為 2-gram 本來就少而被誤判成沒料。
# 另設絕對下限，避免極短問題（兩三個字）湊巧全中就過關。
_LOCAL_HIT_RATIO = 0.4
_LOCAL_HIT_MIN = 3


def _src_label(meta):
    page = meta.get("page")
    return f"{meta.get('source', '本地知識庫')}{f'　{page}' if page else ''}"


def _local_context(question, company, period, top_k=3):
    """本地知識庫對這個問題有沒有料；有的話連段落本文一起回傳。

    只用本地的向量檢索（Chroma ＋ 本地 embedding，不呼叫任何外部 API、不花額度），
    所以可以在每次 EAP 落空時順手問一下。

    回傳 {"text": 供 LLM 閱讀的段落, "sources": [meta…], "label": 最相關來源} 或 None。
    """
    from vector_rag import _keywords, _kw_score

    grams = _keywords(question)
    if not grams:
        return None

    for scope in (period, None):   # 先看這一期，再放寬到全公司（逐字稿常掛在別的期間）
        res = query_vector_rag(question, top_k=top_k, company=company, period=scope)
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        if not docs or not metas:
            continue
        hits = _kw_score(docs[0], grams)   # 只用最相關那一段判斷夠不夠格
        if hits < _LOCAL_HIT_MIN or hits / len(grams) < _LOCAL_HIT_RATIO:
            continue
        return {
            "text": "\n".join(f"[{_src_label(m)}] {d}" for d, m in zip(docs, metas)),
            "sources": list(metas),
            "label": _src_label(metas[0]),
        }
    return None


def _augment_with_local(question, ctx):
    """把本地 Vector RAG 檢索到的段落交給 EAP，讓它據此作答。

    檢索與生成本來就是兩件事：這裡 Vector RAG 當檢索器、EAP 當生成器，
    等於用我們自己的資料補上 EAP 知識庫的缺口，而不是繞過平台自己另做一套。

    **一定要開新的聊天室**。EAP 的聊天有對話記憶，而 get_or_create_chat() 是沿用同一間；
    若把逐字稿注入共用聊天室，之後所有提問都可能從這段記憶作答，畫面上卻標著
    「EAP 平台回答」——看起來像平台本來就有這筆資料，其實是我們幾題前塞進去的。
    實測確認過：同一題在舊聊天室答得出來、在新聊天室回「查不到」。
    開新的一間，這次的答案就確定只來自我們提供的段落。

    刻意寫死「只根據下列內容」：先前實測過，EAP 在沒有範圍限制時會拿一堆不相干的指標
    硬湊出結論（問參股案，它回台灣人壽 RBC、國泰產險市佔率）。寧可它說資料不足，
    也不要它自由發揮——真的不足時，外層還有完全走本地的退路。

    回傳補強後的答案；補了還是答不出來就回 None。
    """
    from eap_client import ask_question, create_chat

    q = (
        "以下是我方知識庫的原始內容（來自法說會錄音的語音轉文字結果，皆為真實資料）。\n"
        "請「只根據下列內容」回答問題：不要引用其他資料、不要臆測；"
        "若下列內容不足以回答，請直接說明資料不足。\n\n"
        f"{ctx['text']}\n\n問題：{question}"
    )
    aug = ask_question(create_chat(), q)
    return None if _eap_found_nothing(aug) else str(aug).strip()


# 答案裡提到的期間。各種寫法都要認：2026Q1／2026年第1季／2026年第一季／1Q26／3M26。
_PERIOD_MENTIONS = (
    re.compile(r"(20\d{2})\s*年?\s*Q([1-4])"),
    re.compile(r"(20\d{2})\s*年\s*第\s*([1-4一二三四])\s*季"),
    re.compile(r"([1-4])Q(\d{2})\b"),
)
_ZH_Q = {"一": 1, "二": 2, "三": 3, "四": 4}

# 「相比／減少／下滑／減幅 71%」這類「宣稱了變化」的措辭。
# 只有真的在做跨期比較才警告——單純把兩期數字列出來並沒有錯。
_CHANGE_CLAIM = re.compile(
    r"減少|增加|下滑|下降|衰退|成長|減幅|增幅|降至|升至|相比|較[^。；\n]{0,12}(?:元|%|億|萬)"
)


def _periods_in(text):
    """答案裡提到了哪些期間，回傳 {(年, 季)}。"""
    out = set()
    s = str(text)
    for i, pat in enumerate(_PERIOD_MENTIONS):
        for m in pat.finditer(s):
            a, b = m.group(1), m.group(2)
            if i == 2:                      # 1Q26 這種是「季在前、年在後」
                out.add((2000 + int(b), int(a)))
            else:
                out.add((int(a), _ZH_Q.get(b, b if not str(b).isdigit() else int(b))))
    return {(y, q) for y, q in out if isinstance(q, int)}


def check_cumulative_comparison(answer, company, period):
    """第五道防護：數字都對，但「拿來相比」本身無效。

    實測 EAP 回答：「2026Q1 每股盈餘 1.18 元，較 2025Q4 的 4.08 元減少 2.90 元，
    減幅約 71%」——1.18 和 4.08 各自都正確，本地也驗得過，但 4.08 是 2025 全年累計、
    1.18 是新年度首季。相減得到的 -71% 沒有意義，那是重新起算不是衰退。

    前四道防護沒有一道攔得住：有回答（不觸發補強）、數字都對（交叉驗證比不出差異）、
    驗得過（不觸發無法驗證）、有數字（不是敘述型）。而本地其實早就知道——
    calc_change() 對同一組期間直接回 None，明文拒絕給這個數字。

    只在「累計型指標 ＋ 跨不同季 ＋ 答案宣稱了變化」三者同時成立時才警告：
    同一季跨年度（2025Q1 vs 2026Q1）本來就可以比，單純並列兩期數字也沒有錯。
    """
    from graph_rag import is_cumulative

    text = str(answer)
    if not _CHANGE_CLAIM.search(text):
        return []

    quarters = {q for _, q in _periods_in(text)}
    if len(quarters) < 2:          # 沒有跨到不同季，就沒有這個問題
        return []

    ms = list_metrics(company, period)
    periods_txt = sorted(f"{y}Q{q}" for y, q in _periods_in(text))
    out, seen = [], set()

    def add(label, local_name):
        if label in seen:
            return
        seen.add(label)
        out.append({"metric": label, "company": company,
                    "periods": periods_txt, "local_source": local_name})

    # 先用標準比率的同義詞表。EAP 寫「每股盈餘」而本地叫「每股稅後盈餘」，
    # 純字面比對會整個漏掉——而 EPS 正是最常被拿來錯誤跨季比較的指標。
    for spec in STANDARD_METRICS:
        if spec["type"] == "ratio":
            continue          # 比率本來就該逐季比，不是累計型
        if not any(re.search(p, text, re.I) for p in spec["include"]):
            continue
        pick = _pick(ms, spec, period)
        if pick and is_cumulative(company, pick["name"]):
            add(spec["label"], pick["name"])

    # 再掃本地指標名稱本身（涵蓋標準表沒收錄的，例如「合併稅後淨利」）
    for m in ms:
        name = m["metric"]
        label = norm_metric_name(name)
        # 「中信金控」這種本身就是公司／子公司名的指標名不是指標，拿來當警告標題只會讓人困惑
        if len(label) < 3 or label not in text:
            continue
        if label in list_companies() or _entity_in(label) == label:
            continue
        if is_cumulative(company, name):
            add(label, name)
    return out


def _build_eap_question(req, company, period):
    """把使用者的問題加工成要送給 EAP 的提問，回傳 (提問, 原問題, focus)。

    抽出來是為了讓 /api/chat 與 /api/chat/stream 共用同一套加工規則——
    範圍限制、同義詞增補、圖表指示這幾條都直接影響答案品質，不能兩邊各寫一份。
    """
    from eap_client import _short_name
    original = req.question
    # 使用者若「鎖定」了公司／期間（下拉選單明確選了，非系統推測），把它明講給 EAP，
    # 否則像「eps」這種問題 EAP 收不到公司脈絡，會自己亂挑公司答（甚至跳過你要問的那家）。
    # 只在明確鎖定時注入，系統推測的就不硬塞，以免用猜錯的公司誤導 EAP。
    scope = " ".join(x for x in (req.company, req.period) if x)
    # 判斷是不是「比較題」：問題已點名鎖定以外的其他公司，或帶比較字眼。
    # 比較題不能用「只針對X、不要提及其他公司」——那會跟比較意圖打架，導致 EAP 回「查不到」。
    others = [c for c in list_companies() if c != req.company
              and (c in original or (_short_name(c) and _short_name(c) in original))]
    is_compare = bool(others) or bool(re.search(r"比較|相比|對比|對照|vs|誰|哪一?家|哪個", original, re.I))
    if scope and is_compare:
        q = f"請以「{scope}」為主要對象回答，可與問題中提到的其他公司比較：{original}"
    elif scope:
        q = f"請只針對「{scope}」回答，不要提及其他公司：{original}"
    else:
        q = original
    # 同義詞增補：各家指標用詞不一（國泰「淨利差」vs 玉山「淨利息收益率」都是 NIM），
    # 把別名一併告訴 EAP，避免它因用詞不同而回「查不到」。
    aliases = _metric_aliases(original)
    alias_hint = f"（此指標的常見別名：{'、'.join(aliases)}，任一名稱的數據皆可採用）" if aliases else ""
    if alias_hint:
        q += alias_hint
    focus = (original + alias_hint) if is_compare else None
    # 問到圖表時，EAP 只回「畫圖指令」卻常漏掉數字。明確要它先用表格列出各期數值，
    # 我們才能用「EAP 自己的數據」把圖畫出來，讓圖和它的答案一致。
    if re.search(r"圖|chart|長條|直條|趨勢|走勢|各期|視覺化|bar", q, re.I):
        q += "\n\n（若要呈現圖表，請務必先用 markdown 表格完整列出各期的數值，再附上圖表。）"
    return q, original, focus


def _finalize_eap(answer, original, company, period, last_period):
    """EAP 回答後的共同收尾：畫圖、交叉驗證、查無資料時的本地補強。
    /api/chat 與 /api/chat/stream 共用，避免兩條路徑的加值行為不一致。
    """
    # 優先用 EAP 答案裡的資料表畫圖；沒有才退回我們的指標庫
    answer, bar = _eap_chart_from_directive(answer, company, period)
    resp = {"answer": answer, "route": "EAP", "calc_result": None, "sources": [],
            "company": company, "period": period, "last_period": last_period}
    if bar:
        resp["chart_bar"] = bar
    # 交叉驗證：EAP 的數字跟本地知識庫差太多就標出來提醒。
    # 兩層——標準比率（跨公司對齊過的定義）＋ 任何本地也有的指標（抓單位換算錯誤那類）。
    checked = 0
    try:
        unmatched = []
        gaps, checked = cross_check_eap(answer, period)
        more, more_checked = cross_check_metrics(answer, company, period, unmatched=unmatched)
        checked += more_checked
        # 同一個指標可能兩層都抓到，用 (公司,指標) 去重，標準比率那層優先
        seen = {(g["company"], g["metric"]) for g in gaps}
        gaps += [g for g in more if (g["company"], g["metric"]) not in seen]
        if gaps:
            resp["cross_check"] = gaps
        # 部分驗證：有些數字驗過了、有些本地根本沒有對應資料。
        # 不揭露的話，畫面上「四筆全驗過」和「三筆驗過、一筆沒驗」長得一模一樣，
        # 等於默認了那個沒驗過的數字（實測：EAP 答台灣人壽前三季 177 億，本地沒收錄）。
        # 只在「真的驗過幾筆」時才顯示——一筆都沒驗到的情況由 unverified 那段負責。
        flagged = {(g["company"], g["metric"]) for g in gaps}
        rest = [u for u in unmatched if (u["company"], u["metric"]) not in flagged]
        if checked > 0 and rest:
            resp["partial_check"] = {"verified": checked, "unmatched": rest}
    except Exception:
        pass  # 交叉驗證只是加值提醒，出錯不能影響主回答
    # EAP 撈不到時，改用「本地檢索 ＋ EAP 生成」再試一次（見 _augment_with_local）。
    # 兩邊的知識庫是各自獨立的：EAP 只有你在它後台上傳的簡報，法說會逐字稿是我們自己
    # STT 轉的、只存在本地。所以「EAP 查不到」很常見的原因是資料不在它那邊，不是問題不好。
    try:
        if _eap_found_nothing(answer):
            ctx = _local_context(original, company, period)
            if ctx:
                aug = _augment_with_local(original, ctx)
                if aug:
                    resp["answer"] = aug
                    resp["route"] = "EAP_RAG"
                    resp["sources"] = ctx["sources"]
                else:
                    # 連補了資料都答不出來 → 留一條完全走本地的退路
                    resp["local_fallback"] = {
                        "question": original, "company": company,
                        "period": period, "source": ctx["label"],
                    }
    except Exception:
        pass  # 補強只是加值，出錯不能影響 EAP 的主回答

    # 第五道防護：數字都對，但「拿來相比」本身無效（累計值跨季比較）。
    # 放在交叉驗證之後、無法驗證之前：它跟數字對不對無關，兩者可能同時成立。
    try:
        cum = check_cumulative_comparison(answer, company, period)
        if cum:
            resp["cumulative_warning"] = cum
    except Exception:
        pass  # 加值提醒，出錯不能影響主回答

    # 第四道防護：EAP 講得頭頭是道，但我們一條都驗不了。
    # 實測遇過——問「為什麼基金手續費下滑」，EAP 列出三條理由（市場波動、銷售動能趨緩、
    # 資產配置轉移），看起來很專業，但本地全庫沒有任何一段提到原因，那三條是生成的。
    # 前三道防護都攔不住這種：它「有回答」所以不觸發補強，通篇沒數字所以交叉驗證無從比對。
    # 這裡不斷言 EAP 錯（它的知識庫可能真有我們沒有的資料），只誠實說「我們驗不了」，
    # 跟「單位未標示」「累計值跨季不可比」是同一個原則。
    try:
        if resp["route"] == "EAP" and not _eap_found_nothing(resp["answer"]):
            if checked == 0 and _local_context(original, company, period) is None:
                resp["unverified"] = {
                    "reason": "沒有可對應的數據或法說會內容",
                    "company": company,
                    "period": period,
                }
    except Exception:
        pass
    return resp


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    _rate_limit(request, "chat")
    company = req.company or _infer_company(req.question)
    period = req.period or _infer_period(req.question, company)

    if req.use_eap:
        # EAP 的檢索器對多公司混合查詢會漏抓，ask_smart 會自動拆解成逐家查詢
        from eap_client import ask_smart, get_or_create_chat
        q, original, focus = _build_eap_question(req, company, period)
        try:
            chat_id = get_or_create_chat()
            # 比較題把使用者真正問的指標（含同義詞）帶進去逐家查，避免被寫死的績效清單漏掉（如 NIM）
            answer = ask_smart(chat_id, q, list_companies(), focus=focus)
        except Exception as e:
            raise HTTPException(502, f"EAP 平台連線失敗：{e}")
        return _finalize_eap(answer, original, company, period, req.last_period)

    result = answer_question(
        req.question,
        company=company,
        this_period=period,
        last_period=req.last_period,
    )
    # 若答案牽涉某個指標，附上它的歷史趨勢，讓前端把「圖」也畫出來，而不是只有文字。
    # 一樣只對「比率／每股」畫趨勢，絕對金額基準/單位不一致，跨期畫會誤導。
    cr = result.get("calc_result")
    if cr and cr.get("metric") and is_cross_comparable(cr["metric"]):
        t = trend(company=company, metric=cr["metric"])
        if len(t.get("points", [])) >= 2:
            result["chart"] = t
    # 回傳後端實際採用的公司／期間，讓前端知道匯出報告要用哪一組
    result["company"] = company
    result["period"] = period
    result["last_period"] = req.last_period
    return result


def _local_finalize(result, company, period, last_period):
    """本地路徑的收尾：附上趨勢圖與實際採用的公司／期間。"""
    cr = result.get("calc_result")
    if cr and cr.get("metric") and is_cross_comparable(cr["metric"]):
        t = trend(company=company, metric=cr["metric"])
        if len(t.get("points", [])) >= 2:
            result["chart"] = t
    result["company"] = company
    result["period"] = period
    result["last_period"] = last_period
    return result


def _sse(event_type, **fields):
    """組一則 SSE 事件。前端靠 type 決定要更新進度、續寫文字，還是收尾。"""
    return "data: " + json.dumps({"type": event_type, **fields}, ensure_ascii=False) + "\n\n"


def _with_heartbeat(events, interval=10):
    """把事件流丟到背景執行緒跑，靜默超過 interval 秒就送一個 SSE 註解當心跳。

    為什麼需要：EAP 一題實測會有 15 秒以上完全不吐任何事件（它的 SSE 不是逐字送的）。
    本機直連沒事，但雲端前面有反向代理（Render／Nginx／Cloudflare），
    連線靜默太久會被判定逾時而直接切斷，使用者就看到查詢無故失敗。
    「: 」開頭的行是 SSE 的註解，前端會忽略，但足以讓連線維持活著。
    """
    import queue

    q, box = queue.Queue(), {}
    done = object()

    def worker():
        try:
            for item in events:
                q.put(item)
        except Exception as exc:
            box["error"] = exc
        finally:
            q.put(done)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while True:
        try:
            item = q.get(timeout=interval)
        except queue.Empty:
            yield ": keep-alive\n\n"
            continue
        if item is done:
            break
        yield item
    t.join()
    if "error" in box:
        # 已經開始串流才出錯，沒辦法改 HTTP 狀態碼，只能用事件告訴前端
        yield _sse("error", text=str(box["error"]))


def _with_live_progress(fn, **kwargs):
    """在背景執行緒跑 fn，即時 yield 它回報的進度（已包成 SSE 事件）；
    用 `yield from` 取得 fn 的回傳值。

    fn 是同步函式（prepare_answer），若在主執行緒直接跑完再排空進度，
    所有進度會在結束的那一瞬間才一起送出——使用者盯著「查詢中…」好幾秒，
    然後三行進度一閃而過，等於沒做。丟到執行緒跑、主線邊等邊送才是真的即時。
    """
    import queue
    box, q = {}, queue.Queue()

    def worker():
        try:
            box["value"] = fn(progress=q.put, **kwargs)
        except Exception as exc:
            box["error"] = exc
        finally:
            q.put(None)          # 結束訊號

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while True:
        item = q.get()
        if item is None:
            break
        yield _sse("status", text=item)
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest, request: Request):
    """逐字串流版的問答。

    為什麼要做：EAP 一題要 10~20 秒，本地生成也要好幾秒，原本這段時間畫面只有
    「AI 查詢中…」，使用者無從判斷是在跑還是當掉。改成串流後，檢索階段先回報進度，
    生成階段逐字吐出來，等待的體感差很多。

    事件格式（SSE）：
      status —— 進度描述，如「檢索法說會內容中…」
      delta  —— 新增的文字片段，前端往答案後面接
      done   —— 收尾，帶上圖表／來源／交叉驗證等中繼資料
      error  —— 出錯訊息

    刻意不用 EventSource：它只支援 GET，問題會被塞進網址。這裡用 POST ＋
    fetch 的 ReadableStream 讀，前端一樣簡單。
    """
    # 限流要在建立 StreamingResponse「之前」擋掉：一旦開始串流，HTTP 狀態碼已經送出去
    # 是 200，這時才在產生器裡丟 HTTPException 只會變成一則 error 事件，拿不到 429。
    _rate_limit(request, "chat")
    company = req.company or _infer_company(req.question)
    period = req.period or _infer_period(req.question, company)

    def events():
        try:
            if req.use_eap:
                from eap_client import ask_smart_stream, get_or_create_chat
                q, original, focus = _build_eap_question(req, company, period)
                yield _sse("status", text="連線 EAP 平台…")

                sent = ""
                for kind, value in ask_smart_stream(chat_id=get_or_create_chat(), question=q,
                                                    known_companies=list_companies(), focus=focus):
                    if kind == "status":
                        yield _sse("status", text=value)
                        continue
                    # EAP 送的是累積快照，算出增量才能讓前端用「續寫」的方式呈現
                    if value.startswith(sent):
                        delta, sent = value[len(sent):], value
                    else:
                        delta, sent = value, value        # 內容被改寫過就整段重送
                        yield _sse("reset")
                    if delta:
                        yield _sse("delta", text=delta)

                yield _sse("status", text="整理圖表與交叉驗證…")
                resp = _finalize_eap(sent, original, company, period, req.last_period)
            else:
                from agent_router import generate_stream, prepare_answer
                prepared = yield from _with_live_progress(
                    prepare_answer, question=req.question, company=company,
                    this_period=period, last_period=req.last_period)

                if prepared["answer"] is not None:
                    # CALC 捷徑：答案是公式算出來的，沒有可串流的生成過程
                    yield _sse("delta", text=prepared["answer"])
                    answer = prepared["answer"]
                else:
                    parts = []
                    for chunk in generate_stream(prepared["prompt"]):
                        parts.append(chunk)
                        yield _sse("delta", text=chunk)
                    answer = "".join(parts)

                resp = _local_finalize(
                    {"answer": answer, "route": prepared["route"],
                     "calc_result": prepared["calc_result"], "sources": prepared["sources"]},
                    company, period, req.last_period)

            # 收尾的答案可能跟串流出來的不同（EAP 補強會整段換掉），一併送回讓前端覆寫
            yield _sse("done", payload=resp)
        except Exception as e:
            yield _sse("error", text=str(e))

    # 包一層心跳：EAP 那段實測會靜默 15 秒以上，雲端代理可能因此切斷連線
    return StreamingResponse(_with_heartbeat(events()), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # 擋掉反向代理的緩衝，否則串流會被整段憋到最後才吐
    })


@app.get("/api/sources")
def sources():
    """資料來源總覽：各公司各期間收錄了多少指標與語意段落"""
    counts = {}
    for m in get_all_sources():
        src = m.get("source", "未知")
        counts[src] = counts.get(src, 0) + 1

    rows = []
    for c in list_companies():
        for p in list_periods(c):
            rows.append({
                "company": c,
                "period": p,
                "metrics": len(list_metrics(c, p)),
                "narratives": counts.get(f"{c} {p}", 0) + counts.get(f"{c} {p} 法說會錄音", 0),
            })
    return {"rows": rows, "total_narratives": sum(counts.values())}


@app.get("/api/transcripts")
def transcripts():
    """列出 outputs/ 底下所有法說會逐字稿檔（錄音轉文字的成果）"""
    out_dir = os.path.join(BASE_DIR, "outputs")
    items = []
    if os.path.isdir(out_dir):
        for f in sorted(os.listdir(out_dir)):
            if f.startswith("transcript_") and f.endswith(".txt"):
                name = f[len("transcript_"):-len(".txt")]
                parts = name.rsplit("_", 1)
                items.append({
                    "file": f,
                    "company": parts[0] if len(parts) == 2 else name,
                    "period": parts[1] if len(parts) == 2 else "",
                })
    return {"transcripts": items}


@app.get("/api/transcript")
def transcript_content(file: str = Query(...)):
    """回傳單一逐字稿內容。檔名白名單＋擋目錄穿越，只允許 outputs/transcript_*.txt。"""
    if not (file.startswith("transcript_") and file.endswith(".txt")) \
            or "/" in file or "\\" in file or ".." in file:
        raise HTTPException(400, "檔名不合法")
    path = os.path.join(BASE_DIR, "outputs", file)
    if not os.path.isfile(path):
        raise HTTPException(404, "找不到逐字稿")
    with open(path, "r", encoding="utf-8") as fh:
        return {"file": file, "content": fh.read()}


# ---------- 上傳新資料：解析 + 匯入（背景執行 + 輪詢進度）----------
# VLM 逐頁解析、STT 轉錄都很花時間（分鐘級），同步請求會卡住又沒進度。
# 改成背景執行緒跑，前端拿 job_id 輪詢進度。
_UPLOAD_EXTS = {".pdf", ".mp3", ".wav", ".m4a", ".mp4", ".aac", ".ogg"}

# 上傳大小上限。原本是 file.file.read() 一次把整個檔案讀進記憶體、且沒有任何上限——
# 公開部署時傳一個幾 GB 的檔就能把服務的記憶體吃光（Render 免費方案只有 512MB）。
# 分段讀＋累計計數，超過就中止並刪掉半個檔，不等整份收完才判斷。
# 音檔給得比 PDF 寬：一場法說會錄音一小時起跳，PDF 簡報則很少超過 30MB。
_MAX_UPLOAD_BYTES = {"pdf": 50 * 1024 * 1024, "audio": 200 * 1024 * 1024}
_UPLOAD_CHUNK = 1024 * 1024

_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _set_job(job_id, **kw):
    with _JOBS_LOCK:
        _JOBS.setdefault(job_id, {}).update(kw)


def _run_upload_job(job_id, kind, saved_path, company, period, max_pages):
    try:
        if kind == "pdf":
            from vlm_parse import run_vlm_parse
            from ingest import run_ingest
            _set_job(job_id, status="running", progress=0.03, message="開始用 VLM 解析簡報…")

            def cb(cur, total):
                _set_job(job_id, progress=0.05 + 0.82 * (cur / total),
                         message=f"VLM 解析第 {cur}/{total} 頁（刻意放慢避免超過免費額度）…")

            _results, json_path = run_vlm_parse(
                saved_path, company, period, max_pages=max_pages, progress_callback=cb)
            _set_job(job_id, progress=0.9, message="解析完成，匯入知識庫中…")
            ok = run_ingest(company, period, parsed_json_path=json_path)
            _set_job(job_id, status="done" if ok else "error", progress=1.0,
                     message=(f"完成！{company} {period} 已匯入" if ok
                              else "解析完成，但匯入時發生問題，請看終端機訊息"))
        else:
            from stt_parse import run_stt_and_ingest
            _set_job(job_id, status="running", progress=0.15,
                     message="正在聽錄音、轉成逐字稿（可能需要幾分鐘）…")
            run_stt_and_ingest(saved_path, company, period)
            _set_job(job_id, status="done", progress=1.0,
                     message=f"完成！{company} {period} 的錄音已轉逐字稿並匯入")
    except Exception as e:
        msg = str(e)
        hint = "（常見原因：免費 API 額度用完，訊息會含 RESOURCE_EXHAUSTED 或 429）" \
            if ("RESOURCE_EXHAUSTED" in msg or "429" in msg) else ""
        _set_job(job_id, status="error", progress=1.0, message=f"處理失敗：{msg}{hint}")
    finally:
        # 原始上傳檔（PDF／音檔）已經解析完，沒有留著的必要——雲端磁碟很小
        try:
            if os.path.exists(saved_path):
                os.remove(saved_path)
        except OSError:
            pass


@app.post("/api/upload")
def upload(
    request: Request,
    company: str = Form(...),
    period: str = Form(...),
    max_pages: int = Form(15),
    file: UploadFile = File(...),
):
    """上傳 PDF（走 VLM 解析）或錄音（走 STT 轉錄），存檔後背景解析＋匯入，回傳 job_id。"""
    _rate_limit(request, "upload")
    if not company.strip() or not period.strip():
        raise HTTPException(400, "請填公司名稱與期間")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(400, "只接受 PDF 或音檔（mp3/wav/m4a/mp4/aac/ogg）")
    kind = "pdf" if ext == ".pdf" else "audio"

    temp_dir = os.path.join(BASE_DIR, "uploads_temp")
    os.makedirs(temp_dir, exist_ok=True)
    # 順手清掉一天前的殘留：暫存檔與 PDF 轉出的圖片原本沒人清，長期會塞爆磁碟
    purge_old(temp_dir)
    purge_old(os.path.join(BASE_DIR, "pages"))
    saved_path = safe_join(temp_dir, f"{company}_{period}{ext}")
    limit = _MAX_UPLOAD_BYTES[kind]
    written = 0
    try:
        with open(saved_path, "wb") as out:
            while chunk := file.file.read(_UPLOAD_CHUNK):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        413, f"檔案超過上限（{kind} 最大 {limit // (1024 * 1024)}MB）")
                out.write(chunk)
    except Exception:
        # 半個檔留在磁碟上，之後的解析會拿到壞檔；超限或寫入失敗都要清掉
        if os.path.exists(saved_path):
            os.unlink(saved_path)
        raise
    if written == 0:
        os.unlink(saved_path)
        raise HTTPException(400, "檔案是空的")

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="queued", progress=0.0, message="已排入處理…", kind=kind)
    threading.Thread(
        target=_run_upload_job,
        args=(job_id, kind, saved_path, company.strip(), period.strip(), int(max_pages)),
        daemon=True,
    ).start()
    return {"job_id": job_id, "kind": kind}


@app.get("/api/upload_status")
def upload_status(job_id: str = Query(...)):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "找不到這個工作")
    return job


class QAPair(BaseModel):
    question: str
    answer: str
    image: Optional[str] = None  # 該題趨勢圖的 base64 PNG（前端 Plotly 匯出），有就嵌進報告


class ReportRequest(BaseModel):
    company: str
    period: str
    last_period: Optional[str] = None
    narrative: str = ""
    qa: Optional[list[QAPair]] = None  # 問答紀錄，有的話報告會逐題排版


@app.post("/api/report")
def report(req: ReportRequest, request: Request):
    """產生 Word 報告。寫到記憶體不落地——部署後使用者碰不到伺服器磁碟。"""
    _rate_limit(request, "report")
    ms = list_metrics(req.company, req.period)
    # 單位要走跟 /api/metrics 同一套推定（同公司其他期間 → 同期錨點 → 指標型別），
    # 否則畫面上顯示「-4,313 百萬元＊」的指標，匯出的報告卻是空白單位——
    # 報告是拿去給人看的成果，不該比畫面少資訊。推定來的一樣標「＊」誠實揭露。
    umap = _unit_map(req.company)
    umap_norm = _unit_map_norm(req.company)
    anchors = _anchor_units(ms, umap, umap_norm)

    summary = []
    for m in ms:
        change = calc_change(req.company, m["metric"], req.period, req.last_period) if req.last_period else None
        item = _metric_payload(req.company, m, umap, anchors, umap_norm)
        unit = item["unit"] or ""
        if unit and item["unit_inferred"]:
            unit += "＊"
        summary.append({
            "name": m["metric"],
            "value": m["value"],
            "unit": unit,
            "change": change if change is not None else "",
            "cumulative": item["cumulative"],
        })

    buf = io.BytesIO()
    generate_report(
        company=req.company,
        period=req.period,
        metrics_summary=summary,
        narrative_summary=req.narrative or "（尚無對話紀錄）",
        output=buf,
        last_period=req.last_period,
        qa_pairs=[{"question": q.question, "answer": q.answer, "image": q.image} for q in req.qa] if req.qa else None,
    )
    buf.seek(0)
    # HTTP header 只能放 latin-1，中文檔名要照 RFC 5987 做 percent-encoding，
    # 否則 starlette 在 .encode("latin-1") 直接炸掉。
    filename = f"{req.company}_{req.period}_財務分析報告.docx"
    quoted = quote(filename)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


# 前端是手寫的 HTML/CSS/JS，沒有打包步驟，所以直接掛 web/ 而不是 web/dist。
# 同源提供，前端 fetch("/api/...") 就不會有 CORS 問題。
# 這行必須放在所有 /api 路由之後——StaticFiles 掛在 "/" 會吃掉所有沒被前面接走的路徑。
_WEB = os.path.join(BASE_DIR, "web")
if os.path.isdir(_WEB):
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
