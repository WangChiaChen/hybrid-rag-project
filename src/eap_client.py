"""EAP 平台真實 API 封裝
依據官方提供的 Sample Code 撰寫（已確認可用的真實串接方式）

比賽範圍只需要用到「聊天 API」：資料匯入是透過 EAP 後台網頁介面手動上傳，
不是透過我們自己的程式呼叫 API 上傳，所以這支只負責「建立聊天室、問問題、拿答案」。

需要在 .env 填入：
  EAP_API_BASE_URL=https://cloud.geminidata.com/api/portal/api10
  EAP_PROJECT_ID=（你的專案 ID，在專案網址列可以找到，例如 .../portal/project/69ec1d70e2d327002b0dfbb5）
  EAP_API_KEY=（你的專案 Token，在 EAP 後台「管理專案」→「通證管理」→「新增通證」取得）
"""
import os
import json
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

EAP_BASE_URL = os.getenv("EAP_API_BASE_URL", "https://cloud.geminidata.com/api/portal/api10").rstrip("/")
EAP_PROJECT_ID = os.getenv("EAP_PROJECT_ID", "")
EAP_API_KEY = os.getenv("EAP_API_KEY", "")


def _headers():
    return {
        "Authorization": f"Bearer {EAP_API_KEY}",
        "x-application-tenant": EAP_PROJECT_ID,
    }


def list_chats():
    """GET /assistant/chat/list —— 取得目前專案下所有聊天室清單"""
    response = requests.get(f"{EAP_BASE_URL}/assistant/chat/list", headers=_headers())
    response.raise_for_status()
    return response.json().get("data", [])


def create_chat():
    """POST /assistant/chat/create —— 建立一個新的聊天室，回傳 chat_id"""
    response = requests.post(f"{EAP_BASE_URL}/assistant/chat/create", headers=_headers(), json={})
    response.raise_for_status()
    return response.json().get("data", {}).get("insertedId")


def get_or_create_chat():
    """方便使用：專案下已經有聊天室就沿用最新的一個，沒有就建立新的，
    這樣同一次展示連續問問題時可以保留在同一個對話脈絡裡。
    """
    chats = list_chats()
    if chats:
        return chats[-1].get("_id")
    return create_chat()


def ask_question(chat_id, question, streaming=True):
    """POST /assistant/chat/{chat_id} —— 送出問題，取得回答
    平台建議用 streaming=True，避免問題複雜時逾時（504）
    """
    data = {"q": question, "streaming": streaming}
    response = requests.post(
        f"{EAP_BASE_URL}/assistant/chat/{chat_id}",
        headers=_headers(), json=data, stream=streaming,
    )
    response.raise_for_status()

    if not streaming:
        return response.json().get("result", "")

    final_result = ""
    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode("utf-8")
        if decoded.startswith("data: "):
            json_str = decoded.replace("data: ", "").strip()
            if json_str:
                try:
                    parsed = json.loads(json_str)
                    if "result" in parsed:
                        final_result = parsed["result"]
                except json.JSONDecodeError:
                    pass
    return final_result


def _short_name(company):
    """把「國泰金控」去掉後綴變「國泰」，方便比對使用者口語問法"""
    for suffix in ("金融控股", "金控", "控股", "銀行", "集團", "證券", "人壽"):
        if company.endswith(suffix):
            short = company[: -len(suffix)]
            if short:
                return short
    return company


def detect_companies_in_question(question, known_companies):
    """從問題裡找出提到的（已知）公司，支援簡稱。回傳命中清單。"""
    hits = []
    for c in known_companies:
        short = _short_name(c)
        if c in question or (short and short in question):
            hits.append(c)
    return hits


def ask_smart(chat_id, question, known_companies, streaming=True):
    """對 EAP 平台問問題，但針對「跨公司比較」做查詢拆解。

    實測發現：EAP 平台的檢索遇到「中信和國泰比較」這種混合查詢時會撈不到資料、
    誤答「查不到」；但逐家單獨問績效則完全正常。因此當問題同時提到 2 家以上公司時，
    改成先逐家各問一次（會成功），再把撈到的數據內嵌進最後一次提問讓平台直接比較，
    避免平台的檢索器成為瓶頸。單一公司問題則維持原本行為。
    """
    companies = detect_companies_in_question(question, known_companies)
    if len(companies) < 2:
        return ask_question(chat_id, question, streaming)

    facts = []
    for c in companies:
        sub_q = (
            f"請只查詢並回答「{c}」最近一季的績效重點"
            f"（稅後淨利、每股盈餘EPS、股東權益報酬率ROE、主要成長率等），只回答這一家。"
        )
        ans = ask_question(chat_id, sub_q, streaming)
        facts.append(f"【{c}】\n{ans.strip()}")

    combined = "\n\n".join(facts)
    synth_q = (
        "以下是各公司已經查到的績效數據（皆為真實資料），請直接根據這些數據進行比較，"
        "不要再說查不到資料。注意：不同公司若金額單位不一致（例如百萬元 vs 億元），"
        "不得直接比較原始數字大小，應優先用比率／每股／成長率等單位一致的指標比較。\n\n"
        f"{combined}\n\n原始問題：{question}"
    )
    return ask_question(chat_id, synth_q, streaming)


if __name__ == "__main__":
    print("EAP_BASE_URL:", EAP_BASE_URL)
    print("EAP_PROJECT_ID:", EAP_PROJECT_ID or "（尚未設定，請在 .env 填入 EAP_PROJECT_ID）")
    print("EAP_API_KEY:", "已設定" if EAP_API_KEY else "（尚未設定，請在 .env 填入 EAP_API_KEY）")

    if EAP_PROJECT_ID and EAP_API_KEY:
        print("\n嘗試建立/取得聊天室並問一個測試問題...")
        try:
            chat_id = get_or_create_chat()
            print("chat_id =", chat_id)
            answer = ask_question(chat_id, "中信金控2026年第一季的獲利表現如何？")
            print("平台回應：", answer)
        except Exception as e:
            print("失敗，錯誤內容如下（把這段貼給 Claude 看）：")
            print(e)