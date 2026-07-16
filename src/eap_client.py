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