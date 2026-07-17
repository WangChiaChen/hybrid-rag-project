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
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
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

    rows = []
    for name in sorted(set(ma) & set(mb)):
        try:
            va = float(str(ma[name]["value"]).replace(",", ""))
            vb = float(str(mb[name]["value"]).replace(",", ""))
        except ValueError:
            continue
        unit = ma[name].get("unit")
        kind = classify_metric(name, unit)
        rows.append({
            "metric": name,
            "value_a": va,
            "value_b": vb,
            "unit": unit,
            "type": TYPE_LABEL.get(kind, "金額"),
            # 只有比率／每股能直接比大小；絕對金額各家單位可能不同
            "comparable": kind in ("ratio", "per_share"),
        })

    return {
        "company_a": company_a, "period_a": period_a,
        "company_b": company_b, "period_b": period_b,
        "rows": rows,
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


class ChatRequest(BaseModel):
    question: str
    company: str
    period: str
    last_period: Optional[str] = None
    use_eap: bool = False


@app.post("/api/chat")
def chat(req: ChatRequest):
    if req.use_eap:
        # EAP 的檢索器對多公司混合查詢會漏抓，ask_smart 會自動拆解成逐家查詢
        from eap_client import ask_smart, get_or_create_chat
        try:
            chat_id = get_or_create_chat()
            answer = ask_smart(chat_id, req.question, list_companies())
        except Exception as e:
            raise HTTPException(502, f"EAP 平台連線失敗：{e}")
        return {"answer": answer, "route": "EAP", "calc_result": None, "sources": []}

    result = answer_question(
        req.question,
        company=req.company,
        this_period=req.period,
        last_period=req.last_period,
    )
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


class ReportRequest(BaseModel):
    company: str
    period: str
    last_period: Optional[str] = None
    narrative: str = ""


@app.post("/api/report")
def report(req: ReportRequest):
    """產生 Word 報告。寫到記憶體不落地——部署後使用者碰不到伺服器磁碟。"""
    summary = []
    for m in list_metrics(req.company, req.period):
        change = calc_change(req.company, m["metric"], req.period, req.last_period) if req.last_period else None
        summary.append({
            "name": m["metric"],
            "value": m["value"],
            "change": change if change is not None else "",
        })

    buf = io.BytesIO()
    generate_report(
        company=req.company,
        period=req.period,
        metrics_summary=summary,
        narrative_summary=req.narrative or "（尚無對話紀錄）",
        output=buf,
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
