"""EAP 平台真實 API 封裝
需要在 .env 填入：
  EAP_API_BASE_URL=（平台的網址，例如 https://xxx.eap.example.com）
  EAP_API_KEY=（你的認證金鑰）

⚠️ 目前認證方式（_headers 函式）是用最常見的 Bearer Token 猜測寫的，
   還沒有實際文件確認，可能需要調整（見下方說明）。
"""
import os
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

EAP_BASE_URL = os.getenv("EAP_API_BASE_URL", "").rstrip("/")
EAP_API_KEY = os.getenv("EAP_API_KEY", "")


def _headers(extra=None):
    headers = {"Authorization": f"Bearer {EAP_API_KEY}"}  # TODO: 確認實際認證方式
    if extra:
        headers.update(extra)
    return headers


def upload_knowledge(file_path, categories="", labels=""):
    """PUT /api/v1/import/vector/knowledge
    上傳 PDF/Excel，直接在 EAP 平台的 Vector 服務裡建立知識項目。
    取代我們自己寫的 preprocess_pdf.py + vlm_parse.py + vector_rag.py 的入庫流程。

    categories / labels：逗號分隔字串，例如 "銀行,法說會"
    回傳：平台回傳的文字內容（文件標示為 text/plain）
    """
    if not EAP_BASE_URL:
        raise RuntimeError("請先在 .env 填入 EAP_API_BASE_URL")

    url = f"{EAP_BASE_URL}/api/v1/import/vector/knowledge"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, "application/pdf")}
        data = {"categories": categories, "labels": labels}
        response = requests.post(url, headers=_headers(), files=files, data=data)

    response.raise_for_status()
    return response.text


def ask_question(chat_id, question, message_id="", streaming=False):
    """POST /api/v1/chat/{chat_id}
    對指定的 chat 問問題，取代 agent_router.py 最後組答案那段。

    ⚠️ 注意：chat_id 是必填參數，代表這支 API 假設你已經先呼叫過
       「Create a new chat」建立好一個 chat_id。這支還沒有截圖給我看，
       目前程式裡先留空，需要你補上那支 API 的規格。

    streaming=True 時回傳格式是 text/event-stream（一段一段串流），
    這裡先只處理 streaming=False 的簡單版本。
    """
    if not EAP_BASE_URL:
        raise RuntimeError("請先在 .env 填入 EAP_API_BASE_URL")

    url = f"{EAP_BASE_URL}/api/v1/chat/{chat_id}"
    payload = {"q": question, "messageId": message_id, "streaming": streaming}
    response = requests.post(url, headers=_headers(), json=payload)
    response.raise_for_status()

    if streaming:
        return response.text  # TODO: 之後可改成逐段解析 SSE
    return response.json().get("response", "")


if __name__ == "__main__":
    # 快速手動測試（需要先在 .env 填好 EAP_API_BASE_URL / EAP_API_KEY）
    print("EAP_BASE_URL:", EAP_BASE_URL or "（尚未設定）")
    print("這支檔案目前只有函式定義，實際呼叫測試等 chat_id 建立方式確認後再補")
