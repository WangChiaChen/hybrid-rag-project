"""EAP 平台資料驗證腳本
用途：EAP 平台的資料是透過後台網頁「手動上傳」的，我們的程式碼碰不到它的知識庫，
也沒有「列出文件」的 API。所以這支腳本改用「聊天 API」直接問平台自己，
藉此確認平台上到底有沒有某家公司的資料（例如截圖那個「找不到國泰」的狀況）。

用法：
    venv/Scripts/python.exe src/check_eap_data.py
    venv/Scripts/python.exe src/check_eap_data.py 國泰金控 中信金控 玉山金控   # 指定要驗證的公司

這支只讀不寫，不會動到平台任何資料。
"""
import sys
from eap_client import (
    EAP_BASE_URL, EAP_PROJECT_ID, EAP_API_KEY,
    get_or_create_chat, ask_question,
)

# 預設要驗證的公司（可用命令列參數覆蓋）
DEFAULT_COMPANIES = ["中信金控", "國泰金控", "玉山金控"]


def _check_config():
    missing = []
    if not EAP_BASE_URL:
        missing.append("EAP_API_BASE_URL")
    if not EAP_PROJECT_ID:
        missing.append("EAP_PROJECT_ID")
    if not EAP_API_KEY:
        missing.append("EAP_API_KEY")
    if missing:
        print("以下設定尚未填入 .env，無法連線 EAP：", "、".join(missing))
        return False
    print(f"EAP_BASE_URL   : {EAP_BASE_URL}")
    print(f"EAP_PROJECT_ID : {EAP_PROJECT_ID}")
    print("EAP_API_KEY    : 已設定")
    return True


def probe_company(chat_id, company):
    """問平台知不知道這家公司，回傳平台的原始回答字串"""
    q = (
        f"請只根據你知識庫裡實際存在的資料回答：你有沒有「{company}」的財報或法說會資料？"
        f"如果有，請列出你有的期間（例如 2026Q1）；如果完全沒有，請直接回答「沒有」。"
    )
    return ask_question(chat_id, q)


def main(companies):
    print("=" * 60)
    print("EAP 平台資料驗證")
    print("=" * 60)
    if not _check_config():
        return

    print("\n建立/取得聊天室...")
    try:
        chat_id = get_or_create_chat()
    except Exception as e:
        print("連線 EAP 失敗，錯誤內容如下：")
        print(e)
        return
    print(f"chat_id = {chat_id}\n")

    # 先問一個總覽問題
    print("-" * 60)
    print("Q：整體知識庫裡有哪些公司的資料？")
    print("-" * 60)
    try:
        overview = ask_question(
            chat_id,
            "請只根據你知識庫裡實際存在的資料，列出你目前擁有哪些公司的財報／法說會資料，"
            "以及各自涵蓋的期間。不要臆測，沒有的就不要列。",
        )
        print(overview.strip() or "（平台沒有回傳內容）")
    except Exception as e:
        print("查詢失敗：", e)

    # 再逐一驗證指定公司
    for company in companies:
        print("\n" + "-" * 60)
        print(f"Q：平台有沒有「{company}」的資料？")
        print("-" * 60)
        try:
            ans = probe_company(chat_id, company)
            print(ans.strip() or "（平台沒有回傳內容）")
        except Exception as e:
            print("查詢失敗：", e)

    print("\n" + "=" * 60)
    print("驗證完成。若某家公司平台回答「沒有」，代表該公司資料還沒上傳到 EAP 後台，")
    print("這也是聊天用 EAP 模式時比較不出來的原因，需到 EAP 後台補上傳該公司簡報。")
    print("=" * 60)


if __name__ == "__main__":
    companies = sys.argv[1:] or DEFAULT_COMPANIES
    main(companies)
