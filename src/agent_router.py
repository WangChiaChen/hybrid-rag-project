"""Phase 4：AI Agent 路由層 —— 判斷問題走 Vector RAG 還是結構化指標庫
用 Gemini 免費版（已優化：合併呼叫次數以節省免費額度，並支援跨公司問題偵測）
TODO: 拿到 EAP 平台文件後，把這裡換成 EAP 的對話 API
"""
import os
import time
import json
from google import genai
from dotenv import load_dotenv
from vector_rag import query_vector_rag
from graph_rag import calc_change, list_metrics, list_companies, list_periods, is_cumulative
from metric_alignment import is_cross_comparable

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GEN_MODEL = "gemini-flash-lite-latest"

# Gemini client 改成「用到才建立」。原本在 import 時就建立，一旦部署環境沒設
# GEMINI_API_KEY 會直接拋 ValueError、讓整個服務起不來（Render 上就是 status 1）。
# 延後建立後：沒設 key 也能正常啟動，只有真的要用到 AI 時才報清楚的錯。
_client = None


def get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "尚未設定 GEMINI_API_KEY，AI 問答／總結無法使用。"
                "請在部署平台的環境變數（或本機 .env）填入你的 Gemini 金鑰。")
        _client = genai.Client(api_key=key)
    return _client


def call_with_retry(fn, max_retries=4, base_wait=5):
    """遇到 503（伺服器忙線）或 429 每分鐘額度時自動等待後重試；
    429 每日額度用完則直接拋出，重試沒有意義"""
    last_error = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            error_msg = str(e)
            if "503" in error_msg or "UNAVAILABLE" in error_msg:
                wait_time = base_wait * (attempt + 1)
                print(f"  伺服器忙線，{wait_time} 秒後重試（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait_time)
            elif ("429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg) and "PerMinute" in error_msg:
                wait_time = 65
                print(f"  已達每分鐘請求上限，{wait_time} 秒後重試（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait_time)
            elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                raise
            else:
                raise
    print(f"\n重試 {max_retries} 次後仍失敗，真正的錯誤訊息如下：")
    print(f"{last_error}\n")
    raise RuntimeError(f"重試多次仍失敗：{last_error}")


ROUTE_AND_METRIC_PROMPT = """你是財務問答系統的判斷模組。根據使用者問題，判斷以下兩件事，只回傳 JSON，不要有其他文字：

1. route：這個問題屬於哪一類
   - "CALC"：只需要精確數字/計算（如「QoQ 是多少」「數值是多少」「誰比較高」）
   - "NARRATIVE"：只需要語意解釋（如「為什麼下滑」「經理人怎麼說」）
   - "BOTH"：兩者都要

2. metric：如果 route 是 CALC 或 BOTH，從下面的指標清單中選出最相關的一個，原封不動照抄名稱；如果都不相關或 route 是 NARRATIVE，填 null

可用指標清單：{available_metrics}
使用者問題：{question}

回傳格式範例：{{"route": "CALC", "metric": "手續費淨收益"}}
"""


def route_and_pick_metric(question, available_metrics):
    prompt = ROUTE_AND_METRIC_PROMPT.format(
        available_metrics=available_metrics,
        question=question
    )
    response = call_with_retry(lambda: get_client().models.generate_content(
        model="gemini-flash-lite-latest",
        contents=prompt,
    ))
    raw = response.text.strip().replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(raw)
        route = parsed.get("route", "NARRATIVE")
        metric = parsed.get("metric")
        if metric not in available_metrics:
            metric = None
        return route, metric
    except json.JSONDecodeError:
        return "NARRATIVE", None


def _short_name(company):
    """把「玉山金控」這種正式名稱去掉常見後綴，變成「玉山」這種簡稱，方便比對使用者口語問法"""
    for suffix in ("金融控股", "金控", "控股", "銀行", "集團", "證券", "人壽"):
        if company.endswith(suffix):
            short = company[: -len(suffix)]
            if short:
                return short
    return company


def detect_mentioned_companies(question, current_company):
    """偵測問題裡有沒有提到「目前選定公司以外」的其他公司名稱（支援簡稱），
    有的話就當作跨公司問題來處理。回傳的清單第一個一定是目前選定的公司。
    """
    all_companies = list_companies()
    mentioned = []
    for c in all_companies:
        if c == current_company:
            continue
        short = _short_name(c)
        if c in question or (short and short in question):
            mentioned.append(c)
    return [current_company] + mentioned


def _fmt_metric(m, company=None):
    """把指標排成給 LLM 看的文字。有單位就一定標出來——單位是跨公司比較能不能比的關鍵。
    累計型指標也要標，否則 LLM 會把「Q4 累計 2.12 → 隔年 Q1 的 0.62」當成暴跌。
    """
    text = f"{m['metric']}：{m['value']}"
    if m.get("unit"):
        text += f" {m['unit']}"
    if m.get("yoy"):
        text += f"（年增 {m['yoy']}）"
    if company and is_cumulative(company, m["metric"]):
        text += "［年初至今累計］"
    return text


def _pick_period_for_company(c, current_company, current_period):
    """目前選定的公司用使用者選的期間；其他被提到的公司則用它最新的期間"""
    if c == current_company:
        return current_period
    periods = list_periods(c)
    return periods[-1] if periods else None


def _value_in_period(company, metric, period):
    """某公司某期間裡這個指標的值；沒有回 None"""
    return next((m["value"] for m in list_metrics(company, period) if m["metric"] == metric), None)


def _periods_with_metric(company, metric):
    """這個公司哪些期間有這個指標（照 list_periods 的時間順序）"""
    return [p for p in list_periods(company) if _value_in_period(company, metric, p) is not None]


def answer_question(question, company, this_period, last_period=None):
    """回傳結構化結果（一次給完，不串流）。

    實作上分成兩段：prepare_answer 蒐證並組出 prompt，這裡再做生成。
    拆開是為了讓 /api/chat/stream 能在同一套邏輯上逐字串流，不必維護第二份檢索程式碼。
    """
    prepared = prepare_answer(question, company, this_period, last_period)
    if prepared["answer"] is not None:      # CALC 捷徑：公式算得出來就不呼叫 LLM
        return {k: prepared[k] for k in ("answer", "route", "calc_result", "sources")}

    response = call_with_retry(lambda: get_client().models.generate_content(
        model=GEN_MODEL,
        contents=prepared["prompt"],
    ))
    return {
        "answer": response.text,
        "route": prepared["route"],
        "calc_result": prepared["calc_result"],
        "sources": prepared["sources"],
    }


def generate_stream(prompt):
    """把 prompt 送去生成，逐塊 yield 文字。給 SSE 串流用。

    只對「建立串流」這個動作重試；一旦開始吐字就不重試了——重試會讓使用者
    看到答案從頭再寫一次。
    """
    stream = call_with_retry(lambda: get_client().models.generate_content_stream(
        model=GEN_MODEL,
        contents=prompt,
    ))
    for chunk in stream:
        text = getattr(chunk, "text", None)
        if text:
            yield text


def prepare_answer(question, company, this_period, last_period=None, progress=None):
    """蒐集證據並組出要送給 LLM 的 prompt。

    回傳 dict：
      answer      —— 有值代表不需要 LLM（CALC 捷徑），直接就是最終答案
      prompt      —— 有值代表要送去生成
      route / calc_result / sources —— 前端要用的中繼資料

    progress：可選的回呼，用來在串流模式下即時回報「正在做什麼」，
    讓使用者在等待生成前就知道系統沒有卡住。

    單一公司的 CALC 類問題直接用公式結果組答案，不額外呼叫 LLM，節省額度。
    如果問題提到其他公司，會自動切換成跨公司比較模式：不強迫鎖定單一指標，
    而是把相關公司完整的指標資料交給 LLM 統整回答，避免問題太籠統時硬選錯指標。
    """
    def _say(text):
        if progress:
            progress(text)

    companies_in_scope = detect_mentioned_companies(question, company)
    is_cross_company = len(companies_in_scope) > 1

    calc_result = None
    context_parts = []
    sources = []

    if is_cross_company:
        _say(f"偵測到跨公司問題（{'、'.join(companies_in_scope)}），彙整各家指標中…")
        comparable_lines = []  # 比率／每股類：單位無關，可直接比大小
        amount_lines = []      # 絕對金額：各家申報單位可能不同，比較前要留意單位
        for c in companies_in_scope:
            p = _pick_period_for_company(c, company, this_period)
            if not p:
                continue
            metrics_c = list_metrics(c, p)
            if not metrics_c:
                continue
            comparable = [m for m in metrics_c if is_cross_comparable(m["metric"], m.get("unit"))]
            amounts = [m for m in metrics_c if not is_cross_comparable(m["metric"], m.get("unit"))]
            if comparable:
                text = "；".join(_fmt_metric(m, c) for m in comparable)
                comparable_lines.append(f"{c}（{p}）：{text}")
            if amounts:
                text = "；".join(_fmt_metric(m, c) for m in amounts)
                amount_lines.append(f"{c}（{p}）：{text}")

        if comparable_lines:
            context_parts.append(
                "[可直接跨公司比較的指標｜比率／每股／成長率，單位一致，請直接比大小]\n"
                + "\n".join(comparable_lines)
            )
        if amount_lines:
            context_parts.append(
                "[絕對金額指標｜注意：各公司申報單位可能不同（例如一家用百萬元、另一家用億元），"
                "禁止直接比較原始數字大小；若要比較必須先換算成相同單位，無法確定單位時請明講而不要臆測]\n"
                + "\n".join(amount_lines)
            )

        vec_results = query_vector_rag(question, top_k=10, company=companies_in_scope, period=None)
        docs = vec_results.get("documents", [[]])[0]
        metas = vec_results.get("metadatas", [[]])[0]
        if docs:
            context_parts.append(f"[相關法說會敘述] {' '.join(docs)}")
            sources.extend(metas)

        route = "BOTH"

    else:
        _say("AI Agent 判斷該用精準計算還是語意檢索…")
        available = [m["metric"] for m in list_metrics(company, this_period)]
        route, metric_used = route_and_pick_metric(question, available)

        # 這期沒挑到指標時，改用「全公司所有期間」的指標清單再挑一次。
        # 使用者常會選到只有資產負債表的「財報」期，那裡沒有法說會簡報才有的
        # 手續費淨收益、NIM 之類指標——不該因此就答不出來。
        if route in ("CALC", "BOTH") and not metric_used:
            available_all = sorted({m["metric"] for p in list_periods(company) for m in list_metrics(company, p)})
            _, metric_used = route_and_pick_metric(question, available_all)

        # 決定要用哪一期算：這期有就用這期；沒有就退到「最近有這個指標」的期間，
        # 變化率則對它前一個有資料的期間比。
        calc_period, prev_period = None, last_period
        if route in ("CALC", "BOTH") and metric_used:
            if _value_in_period(company, metric_used, this_period) is not None:
                calc_period = this_period
            else:
                ps = _periods_with_metric(company, metric_used)
                if ps:
                    calc_period = ps[-1]
                    prev_period = ps[-2] if len(ps) >= 2 else None

        if calc_period:
            current_value = _value_in_period(company, metric_used, calc_period)
            change = calc_change(company, metric_used, calc_period, prev_period) if prev_period else None
            if current_value is not None:
                calc_result = {"metric": metric_used, "value": current_value,
                               "change": change, "period": calc_period}

        if route == "CALC" and calc_result:
            change_text = f"，較 {prev_period} 變化 {calc_result['change']}%" if calc_result.get("change") is not None else ""
            answer_text = f"{company} {calc_result['period']} 的{calc_result['metric']}為 {calc_result['value']}{change_text}。"
            if calc_result["period"] != this_period:
                answer_text += f"（你選的 {this_period} 沒有這個指標，改用最近有資料的 {calc_result['period']}）"
            # 純計算題不必走 LLM——答案就是公式結果本身
            return {"answer": answer_text, "prompt": None, "route": route,
                    "calc_result": calc_result, "sources": []}

        if calc_result:
            change_text = f"，較 {prev_period} 變化 {calc_result['change']}%" if calc_result.get("change") is not None else ""
            period_note = "" if calc_result["period"] == this_period else f"（期間 {calc_result['period']}）"
            context_parts.append(f"[精確計算結果]{period_note} {calc_result['metric']}：{calc_result['value']}{change_text}")

        # NARRATIVE/BOTH 要檢索；CALC 但連跨期都找不到指標時，也退回語意檢索，不要直接放棄
        if route in ("NARRATIVE", "BOTH") or (route == "CALC" and not calc_result):
            _say("檢索法說會內容中…")
            vec_results = query_vector_rag(question, top_k=8, company=company, period=this_period)
            docs = vec_results.get("documents", [[]])[0]
            metas = vec_results.get("metadatas", [[]])[0]
            if not docs:
                # 這期沒有語意段落（例如財報期），放寬到不鎖期間、全公司再檢索一次
                vec_results = query_vector_rag(question, top_k=8, company=company, period=None)
                docs = vec_results.get("documents", [[]])[0]
                metas = vec_results.get("metadatas", [[]])[0]
            if docs:
                context_parts.append(f"[相關法說會敘述] {' '.join(docs)}")
                sources.extend(metas)

    if not context_parts:
        context_parts.append("（目前知識庫中沒有相關資料，請先執行資料匯入）")

    if is_cross_company:
        scope_note = (
            f"（本次比較對象：{'、'.join(companies_in_scope)}）\n"
            "比較守則：優先使用「可直接跨公司比較的指標」區塊（比率／每股／成長率）做高下判斷；"
            "「絕對金額」區塊各公司單位可能不同，不得直接比原始數字大小；"
            "標示［年初至今累計］的指標是從年初累加到當季，只有同一季跨年度才能比"
            "（例如去年Q1 vs 今年Q1）；跨季比較沒有意義，尤其新年度第一季的數字必然低於"
            "前一年第四季，那是重新起算而不是衰退，不要解讀成暴跌；"
            "只根據下方提供的數據回答，缺哪一家的資料就如實說明，不要臆測。\n"
        )
    else:
        scope_note = ""
    final_prompt = f"""根據以下真實資料回答問題，不要編造數字：
{scope_note}{chr(10).join(context_parts)}

問題：{question}"""

    _say("整理答案中…")
    return {
        "answer": None,          # 交給呼叫端決定要一次生成還是串流
        "prompt": final_prompt,
        "route": route,
        "calc_result": calc_result,
        "sources": sources,
    }


if __name__ == "__main__":
    result = answer_question(
        "手續費淨收益為什麼變化？變化多少？",
        company="中信金控",
        this_period="2026Q1",
        last_period="2025Q4",
    )
    print(result)
