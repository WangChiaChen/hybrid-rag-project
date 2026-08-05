"""Phase 4：AI Agent 路由層 —— 判斷問題走 Vector RAG 還是結構化指標庫
用 Gemini 免費版（已優化：合併呼叫次數以節省免費額度，並支援跨公司問題偵測）
TODO: 拿到 EAP 平台文件後，把這裡換成 EAP 的對話 API
"""
import os
import re
import time
import json
from google import genai
from dotenv import load_dotenv
from vector_rag import query_vector_rag
from graph_rag import (
    _UNIT_SCALE,
    calc_change,
    is_cumulative,
    list_companies,
    list_metrics,
    list_periods,
    pins_own_period,
)
from metric_alignment import is_cross_comparable, norm_metric_name
from standard_metrics import _SUBSIDIARY, _name_year, _target_year

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
    """目前選定的公司用使用者選的期間；其他被提到的公司**優先用同一期**。

    比較題幾乎都是「同一期跨公司」（使用者也常在問題裡明寫「玉山2026Q1」）。
    原本一律用其他公司「最新的期間」，會掉到 2026Q1財報——那期只有資產負債表、
    沒有 ROE 等比率，於是「中信 vs 玉山 ROE 比較」裡玉山被判成『查無 ROE』，
    但跨機構比較頁明明查得到 14.43。同名期存在就用它，不存在才退回最新期。
    """
    if c == current_company:
        return current_period
    periods = list_periods(c)
    if not periods:
        return None
    if current_period and current_period in periods:
        return current_period
    return periods[-1]


# 年報只有語意段落、被收在獨立的 doc_period（例如「2025年報」），語意檢索照期間過濾時
# 不會被搜到。問句提到年報／治理／永續這類「只有年報有」的主題時，就自動改搜年報，
# 使用者不用手動把期間鎖成「2025年報」。
_ANNUAL_HINT = re.compile(r"年報|致股東|公司治理|董事會|獨立董事|永續發展|永續報告|ESG|年度報告")


def _annual_report_period(company, question):
    """問句像在問年報時，回傳該公司的年報 doc_period；否則（或該公司沒收年報）回 None。"""
    if not company or not _ANNUAL_HINT.search(str(question)):
        return None
    from vector_rag import list_periods_from_vector
    ars = sorted(p for p in list_periods_from_vector(company) if "年報" in p)
    return ars[-1] if ars else None


def _find_metric(company, metric, period):
    """在某期間找這個指標的最佳對應。先逐字命中；沒有再用「去掉期別標籤」的正規化名比對。

    跨期命名不一致是常態：國泰當期群組稅後淨利叫「1Q26稅後淨利」、舊期卻叫「稅後淨利」。
    逐字比對會在當期落空、害系統退到舊期拿舊數字（實測問 2026Q1 卻回 2025Q4 的 107.6）。
    正規化名補救時要排除「釘死別年度」的對照值（當期資料夾裡常附去年同期，如「稅後淨利 (1Q25)」）。
    回傳指標 dict（含 value／unit／metric）或 None。
    """
    ms = list_metrics(company, period)
    exact = next((m for m in ms if m["metric"] == metric), None)
    if exact:
        return exact
    target = norm_metric_name(metric)
    ty = _target_year(period)
    cands = []
    for m in ms:
        name = str(m["metric"])
        if norm_metric_name(name) != target:
            continue
        if pins_own_period(name):                       # 名稱釘死某期別
            ny = _name_year(name)
            if ny is not None and ty is not None and ny != ty:
                continue                                # 釘死的是別的年度（去年同期對照）→ 跳過
        cands.append(m)
    cands.sort(key=lambda m: len(str(m["metric"])))     # 同正規化名取最短＝最乾淨的當期那筆
    return cands[0] if cands else None


def _value_in_period(company, metric, period):
    """某公司某期間裡這個指標的值；沒有回 None。用 _find_metric 才認得跨期命名變體。"""
    m = _find_metric(company, metric, period)
    return m["value"] if m else None


def _periods_with_metric(company, metric):
    """這個公司哪些期間有這個指標（照 list_periods 的時間順序）"""
    return [p for p in list_periods(company) if _value_in_period(company, metric, p) is not None]


# 問「各子公司／子公司別／分項」是要「分項列出」，不是要一個集團總數。
_BREAKDOWN_HINT = re.compile(r"各子公司|子公司別|各家子公司|各子公司的|子公司.{0,4}分項|分項|拆解|獲利組成")
# 從問題抓指標關鍵字（長的先比，避免「淨利」搶走「稅後淨利」）
_BREAKDOWN_KW = ["稅後淨利", "稅前淨利", "營業收入", "淨手續費收入", "手續費",
                 "淨利息收入", "淨利", "獲利", "營收", "保費", "放款", "存款", "淨值"]


def _subsidiary_breakdown(question, company, period):
    """「國泰各子公司稅後淨利」這種分項題：列出各子公司的該指標，而不是回一個集團總數。

    原本路由把它當單一指標算，回的是金控整體、還常因命名不一致退到舊期（實測回 2025Q4
    的 107.6）。這裡直接從指標庫撈「指名子公司且屬當期」的分項，組成確定性答案、不走 LLM。
    回傳答案字串或 None（不是分項題、或本地沒有分項就回 None，讓它走原本流程）。
    """
    if not company or not _BREAKDOWN_HINT.search(question):
        return None
    kw = next((k for k in _BREAKDOWN_KW if k in question), None)
    if not kw:
        return None
    ty = _target_year(period)
    ent_re = re.compile(r"[一-鿿]{2,6}?(?:" + "|".join(
        s for s in _SUBSIDIARY if not s.startswith("(")) + r")")
    by_entity = {}
    for m in list_metrics(company, period):
        name = str(m["metric"])
        if kw not in name or not any(s in name for s in _SUBSIDIARY):
            continue
        if re.search(r"成長|年增|季增|佔比|占比", name):   # 成長率／佔比不是金額本身
            continue
        if pins_own_period(name):
            ny = _name_year(name)
            if ny is not None and ty is not None and ny != ty:
                continue                                  # 去年同期對照，跳過
        if m.get("value") is None:
            continue
        em = ent_re.search(name)
        entity = em.group(0) if em else name
        # 同一家子公司可能有多個變體（中信銀行稅後淨利 16,586百萬 vs 第一季稅後淨利 166億，
        # 其實同一筆）——每家只留名稱最短、最乾淨的那筆。
        if entity not in by_entity or len(name) < len(by_entity[entity][0]):
            by_entity[entity] = (name, m["value"], m.get("unit") or "")
    if not by_entity:
        return None
    # 各子公司單位常不一（中信銀行百萬、台灣人壽億），並排不好讀——統一換算成億元。
    rows = sorted(by_entity.items(), key=lambda kv: len(kv[1][0]))
    parts = []
    for entity, (name, value, unit) in rows:
        yi = _to_yi(value, unit)
        parts.append(f"{entity} {_fmt_yi(yi)}億元" if yi is not None else f"{entity} {value}{unit}")
    body = "；".join(parts)
    return f"{company} {period} 各子公司{kw}（單位：億元）：{body}。（源自本地指標庫，已統一換算）"


def _to_yi(value, unit):
    """把金額換算成「億元」；認不出單位或非數字回 None。"""
    try:
        v = float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
    scale = _UNIT_SCALE.get(unit)
    return v * scale / 1e8 if scale else None


def _fmt_yi(v):
    """億元數字的顯示：取一位小數，整數就不留小數點（132.0→132、165.86→165.9）。"""
    return f"{v:.1f}".rstrip("0").rstrip(".")


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
        # 分項題（各子公司…）優先：直接列出各子公司的該指標，別當單一集團數字算
        breakdown = _subsidiary_breakdown(question, company, this_period)
        if breakdown:
            _say("偵測到子公司分項題，彙整各子公司指標中…")
            return {"answer": breakdown, "prompt": None, "route": "CALC",
                    "calc_result": None, "sources": []}

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
            hit = _find_metric(company, metric_used, calc_period)
            current_value = hit["value"] if hit else None
            unit = (hit.get("unit") or "") if hit else ""
            change = calc_change(company, metric_used, calc_period, prev_period) if prev_period else None
            if current_value is not None:
                calc_result = {"metric": metric_used, "value": current_value, "unit": unit,
                               "change": change, "period": calc_period}

        if route == "CALC" and calc_result:
            change_text = f"，較 {prev_period} 變化 {calc_result['change']}%" if calc_result.get("change") is not None else ""
            # 帶上單位——漏了單位「107.6」會被讀成 107.6 億還是 107.6 十億分不清
            answer_text = f"{company} {calc_result['period']} 的{calc_result['metric']}為 {calc_result['value']}{calc_result.get('unit', '')}{change_text}。"
            if calc_result["period"] != this_period:
                answer_text += f"（你選的 {this_period} 沒有這個指標，改用最近有資料的 {calc_result['period']}）"
            # 純計算題不必走 LLM——答案就是公式結果本身
            return {"answer": answer_text, "prompt": None, "route": route,
                    "calc_result": calc_result, "sources": []}

        if calc_result:
            change_text = f"，較 {prev_period} 變化 {calc_result['change']}%" if calc_result.get("change") is not None else ""
            period_note = "" if calc_result["period"] == this_period else f"（期間 {calc_result['period']}）"
            context_parts.append(f"[精確計算結果]{period_note} {calc_result['metric']}：{calc_result['value']}{calc_result.get('unit', '')}{change_text}")

        # NARRATIVE/BOTH 要檢索；CALC 但連跨期都找不到指標時，也退回語意檢索，不要直接放棄
        if route in ("NARRATIVE", "BOTH") or (route == "CALC" and not calc_result):
            _say("檢索法說會內容中…")
            # 問句像在問年報時，自動改搜年報那個 doc_period——不然照當季過濾會撈到季報、
            # 年報整個搜不到（實測問「玉山 2025年報怎麼看台灣經濟」鎖在 2026Q1 就答不出來）。
            vec_period = _annual_report_period(company, question) or this_period
            vec_results = query_vector_rag(question, top_k=8, company=company, period=vec_period)
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
