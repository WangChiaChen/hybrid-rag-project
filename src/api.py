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
import os
import re
import threading
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
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
from metric_alignment import classify_metric
from report_generator import generate_report
from standard_metrics import align_standard, key_ratios
from vector_rag import get_all_sources

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(
    title="有你蒸好 · Hybrid RAG API",
    description="財報分析：Vector RAG（語意檢索）＋ Graph RAG（精準計算）＋ AI Agent 路由",
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

TYPE_LABEL = {"ratio": "比率", "per_share": "每股", "amount": "金額"}


def _metric_payload(company: str, m: dict) -> dict:
    """統一的指標輸出格式。unit/cumulative 一定要帶——前端要靠它們決定能不能比大小。"""
    return {
        "metric": m["metric"],
        "value": m["value"],
        "unit": m.get("unit"),
        "yoy": m.get("yoy"),
        "type": TYPE_LABEL.get(classify_metric(m["metric"], m.get("unit")), "金額"),
        "comparable": classify_metric(m["metric"], m.get("unit")) in ("ratio", "per_share"),
        "cumulative": is_cumulative(company, m["metric"]),
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

    out = []
    for m in ms:
        item = _metric_payload(company, m)
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
    company: str = Query(...),
    period: str = Query(...),
    last_period: Optional[str] = Query(None),
):
    """AI 觀點總結：用一句話講出這期的重點。呼應 Hybrid RAG + Agent 的定位——
    不是只堆數字，而是讓 AI 讀完數字給結論。
    只根據下方提供的真實數字，累計指標明確標記避免它拿去比錯。
    """
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
    return ps[-1] if ps else None


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
    圖就能直接用 EAP 自己的數字畫，和答案文字一致（不會又換成我們的 Graph RAG 數字）。
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
    偵測到就用我們自己的 Graph RAG 數據把圖畫出來，並把裸指令從文字裡拿掉。
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
    #    和上方表格／文字一致（不會又被換成我們 Graph RAG 的數字，導致對不上）
    tbl = _parse_series_table(answer)
    if tbl:
        return cleaned, {"kind": "series", "title": title or f"{c} 各期{metric or tbl['label']}",
                         "metric": metric or tbl["label"] or "數值", "unit": "", "points": tbl["points"]}

    # 1) 沒有資料表，但指令點名了某個具體指標 → 用我們 Graph RAG 的該指標各期
    if metric:
        t = trend(company=c, metric=metric)
        pts = t.get("points", [])
        if pts:
            cleaned += f"\n\n（下圖為系統依 Graph RAG 數據繪製：{c} 各期{metric}）"
            return cleaned, {"kind": "series", "title": title or f"{c} 各期{metric}",
                             "metric": metric, "unit": _metric_unit(c, metric), "points": pts}

    # 2) 否則畫「標準關鍵比率」（單位一致 %）
    p = _infer_period(directive, c) if re.search(r"Q[1-4]|第.季|20\d{2}", directive) else period
    items = key_ratios(c, p)
    if not items:
        return cleaned, None
    cleaned += "\n\n（下圖為系統依 Graph RAG 數據繪製的標準關鍵比率，單位皆為 %）"
    return cleaned, {"kind": "bars", "title": title or f"{c} {p} 主要績效指標", "items": items}


@app.post("/api/chat")
def chat(req: ChatRequest):
    company = req.company or _infer_company(req.question)
    period = req.period or _infer_period(req.question, company)

    if req.use_eap:
        # EAP 的檢索器對多公司混合查詢會漏抓，ask_smart 會自動拆解成逐家查詢
        from eap_client import ask_smart, get_or_create_chat
        q = req.question
        # 問到圖表時，EAP 只回「畫圖指令」卻常漏掉數字。明確要它先用表格列出各期數值，
        # 我們才能用「EAP 自己的數據」把圖畫出來，讓圖和它的答案一致。
        if re.search(r"圖|chart|長條|直條|趨勢|走勢|各期|視覺化|bar", q, re.I):
            q += "\n\n（若要呈現圖表，請務必先用 markdown 表格完整列出各期的數值，再附上圖表。）"
        try:
            chat_id = get_or_create_chat()
            answer = ask_smart(chat_id, q, list_companies())
        except Exception as e:
            raise HTTPException(502, f"EAP 平台連線失敗：{e}")
        # 優先用 EAP 答案裡的資料表畫圖；沒有才退回我們的 Graph RAG
        answer, bar = _eap_chart_from_directive(answer, company, period)
        resp = {"answer": answer, "route": "EAP", "calc_result": None, "sources": [],
                "company": company, "period": period, "last_period": req.last_period}
        if bar:
            resp["chart_bar"] = bar
        return resp

    result = answer_question(
        req.question,
        company=company,
        this_period=period,
        last_period=req.last_period,
    )
    # 若答案牽涉某個指標，附上它的歷史趨勢，讓前端把「圖」也畫出來，而不是只有文字
    cr = result.get("calc_result")
    if cr and cr.get("metric"):
        t = trend(company=company, metric=cr["metric"])
        if len(t.get("points", [])) >= 2:
            result["chart"] = t
    # 回傳後端實際採用的公司／期間，讓前端知道匯出報告要用哪一組
    result["company"] = company
    result["period"] = period
    result["last_period"] = req.last_period
    return result


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


@app.post("/api/upload")
def upload(
    company: str = Form(...),
    period: str = Form(...),
    max_pages: int = Form(15),
    file: UploadFile = File(...),
):
    """上傳 PDF（走 VLM 解析）或錄音（走 STT 轉錄），存檔後背景解析＋匯入，回傳 job_id。"""
    if not company.strip() or not period.strip():
        raise HTTPException(400, "請填公司名稱與期間")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(400, "只接受 PDF 或音檔（mp3/wav/m4a/mp4/aac/ogg）")
    kind = "pdf" if ext == ".pdf" else "audio"

    temp_dir = os.path.join(BASE_DIR, "uploads_temp")
    os.makedirs(temp_dir, exist_ok=True)
    safe = f"{company}_{period}".replace("/", "_").replace("\\", "_")
    saved_path = os.path.join(temp_dir, f"{safe}{ext}")
    with open(saved_path, "wb") as out:
        out.write(file.file.read())

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
def report(req: ReportRequest):
    """產生 Word 報告。寫到記憶體不落地——部署後使用者碰不到伺服器磁碟。"""
    summary = []
    for m in list_metrics(req.company, req.period):
        change = calc_change(req.company, m["metric"], req.period, req.last_period) if req.last_period else None
        summary.append({
            "name": m["metric"],
            "value": m["value"],
            "unit": m.get("unit") or "",
            "change": change if change is not None else "",
            "cumulative": is_cumulative(req.company, m["metric"]),
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
