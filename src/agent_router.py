"""Phase 4：AI Agent 路由層 —— 判斷問題走 Vector RAG 還是 Graph RAG
用 Gemini 免費版（已優化：合併呼叫次數以節省免費額度，並支援跨公司問題偵測）
TODO: 拿到 EAP 平台文件後，把這裡換成 EAP 的對話 API
"""
import os
import time
import json
from google import genai
from dotenv import load_dotenv
from vector_rag import query_vector_rag
from graph_rag import calc_change, list_metrics, list_companies, list_periods

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


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
    response = call_with_retry(lambda: client.models.generate_content(
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


def _pick_period_for_company(c, current_company, current_period):
    """目前選定的公司用使用者選的期間；其他被提到的公司則用它最新的期間"""
    if c == current_company:
        return current_period
    periods = list_periods(c)
    return periods[-1] if periods else None


def answer_question(question, company, this_period, last_period=None):
    """回傳結構化結果。CALC 類問題直接用公式結果組答案，不額外呼叫 LLM，節省額度。
    如果問題提到其他公司，會自動變成跨公司比較模式。
    """
    companies_in_scope = detect_mentioned_companies(question, company)
    is_cross_company = len(companies_in_scope) > 1

    route, metric_used = None, None
    if not is_cross_company:
        available = [m["metric"] for m in list_metrics(company, this_period)]
        route, metric_used = route_and_pick_metric(question, available)
    else:
        # 跨公司問題：用問題本身＋所有相關公司的指標清單一起判斷
        combined_available = []
        for c in companies_in_scope:
            p = _pick_period_for_company(c, company, this_period)
            if p:
                combined_available.extend([m["metric"] for m in list_metrics(c, p)])
        route, _ = route_and_pick_metric(question, list(dict.fromkeys(combined_available)))

    calc_result = None
    context_parts = []
    sources = []

    if route in ("CALC", "BOTH"):
        if is_cross_company:
            calc_lines = []
            for c in companies_in_scope:
                p = _pick_period_for_company(c, company, this_period)
                if not p:
                    continue
                available_c = [m["metric"] for m in list_metrics(c, p)]
                _, metric_c = route_and_pick_metric(question, available_c)
                if metric_c:
                    value_c = next(
                        (m["value"] for m in list_metrics(c, p) if m["metric"] == metric_c), None
                    )
                    if value_c is not None:
                        calc_lines.append(f"{c}（{p}）{metric_c}：{value_c}")
            if calc_lines:
                context_parts.append("[跨公司精確數據]\n" + "\n".join(calc_lines))
                calc_result = {"metric": "、".join(companies_in_scope) + " 比較", "value": "；".join(calc_lines), "change": None}
        else:
            if metric_used:
                current_value = next(
                    (m["value"] for m in list_metrics(company, this_period) if m["metric"] == metric_used),
                    None
                )
                change = None
                if last_period:
                    change = calc_change(company, metric_used, this_period, last_period)
                if current_value is not None:
                    calc_result = {"metric": metric_used, "value": current_value, "change": change}

            if route == "CALC" and calc_result:
                change_text = f"，較 {last_period} 變化 {calc_result['change']}%" if calc_result.get("change") is not None else ""
                answer_text = f"{company} {this_period} 的{calc_result['metric']}為 {calc_result['value']}{change_text}。"
                return {"answer": answer_text, "route": route, "calc_result": calc_result, "sources": []}

            if calc_result:
                change_text = f"，較 {last_period} 變化 {calc_result['change']}%" if calc_result.get("change") is not None else ""
                context_parts.append(f"[精確計算結果] {calc_result['metric']}：{calc_result['value']}{change_text}")

    if route in ("NARRATIVE", "BOTH"):
        if is_cross_company:
            vec_results = query_vector_rag(question, top_k=10, company=companies_in_scope, period=None)
        else:
            vec_results = query_vector_rag(question, top_k=8, company=company, period=this_period)

        docs = vec_results.get("documents", [[]])[0]
        metas = vec_results.get("metadatas", [[]])[0]
        if docs:
            context_parts.append(f"[相關法說會敘述] {' '.join(docs)}")
            sources.extend(metas)

    if not context_parts:
        context_parts.append("（目前知識庫中沒有相關資料，請先執行資料匯入）")

    scope_note = f"（本次比較對象：{'、'.join(companies_in_scope)}）\n" if is_cross_company else ""
    final_prompt = f"""根據以下真實資料回答問題，不要編造數字：
{scope_note}{chr(10).join(context_parts)}

問題：{question}"""

    response = call_with_retry(lambda: client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=final_prompt,
    ))

    return {
        "answer": response.text,
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
