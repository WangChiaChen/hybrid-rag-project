"""驗證 EAP 平台端的提示詞設定是否生效（見 EAP平台提示詞設定.md）。

每題開新聊天室：實測過對話記憶會讓同一題在舊聊天室答得出來、新聊天室卻查不到，
不隔離就分不清是提示詞的效果還是記憶的殘留。

只送唯讀提問，不會動到平台資料。

用法（在專案根目錄）：
    venv/Scripts/python.exe scripts/verify_eap_prompts.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")
from eap_client import create_chat, ask_question
import api

TESTS = [
    ("A. 前提驗證（Generate Response 第1步）",
     "為什麼中信金控 2026Q1 的基金手續費收入下滑？",
     "應指出前提不成立——實際是成長（1Q25 6,312 → 1Q26 8,054 百萬元），"
     "而不是順著問題編出下滑原因"),

    ("B. 嚴禁臆測（Setting for when unable to answer）",
     "中信金控 2026Q1 的員工人數是多少？",
     "資料裡沒有這項，應明確說查無並指出缺哪一塊，不得用「通常」「可能」填補"),

    ("C. 科目區分（Specific Context A）",
     "中信金控 2026Q1 的基金手續費金額是多少？",
     "基金是財管手續費的子項、財報不單獨揭露，"
     "不該拿母集合（財管手續費 8,054）的數字代答"),

    ("D. 術語對映（Semantic Adjustment）",
     "中信金控 2026Q1 的淨手續費收入是多少？",
     "「淨手續費收入」是中信寫成「手續費淨收益」的同義詞，"
     "若對映生效應答得出 17,977 百萬元"),

    ("E. 單位與累計規則（Specific Context B）",
     "中信金控和玉山金控 2026Q1 的稅後淨利誰比較高？",
     "兩家單位可能不同，應連單位一起講、或改用比率比較，不得直接比原始數字"),

    ("F. 正常題（不該被過度限制）",
     "中信金控 2026Q1 的每股盈餘是多少？",
     "資料裡有，應正常答出 1.18 元"),

    ("G. 英文相關性關卡（不受後台措辭設定控制）",
     "請問今天台北的天氣如何？",
     "平台會在檢索之前先擋下來，且回的是英文 "
     "「Unable to answer question not relevant to this project and its data」；"
     "重點是下方「我方偵測：查無資料」必須為 True，否則 Vector RAG 補強不會觸發"),
]

for label, q, expect in TESTS:
    print("=" * 78)
    print(f"【{label}】")
    print(f"問：{q}")
    print(f"期望：{expect}")
    print("-" * 78)
    try:
        ans = ask_question(create_chat(), q).strip()
        print(ans[:600] or "（無回應）")
        print(f"\n  → 我方偵測：查無資料={api._eap_found_nothing(ans)}")
    except Exception as e:
        print("查詢失敗：", e)
    print()
