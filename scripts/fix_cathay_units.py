"""補上國泰金控 2026Q1 缺失的單位（一次性資料修正，含推定依據）。

## 為什麼要補

沒有單位的指標，交叉驗證一律跳過（`_pick_local_for_entity` 要求 unit）。
實測就吃過虧：問「國泰金控 2026Q1 各子公司稅後淨利」，EAP 回的是 **1Q25** 的數字
（12.2／18.4／1.1…十億元），本地雖然有 1Q26 的正確值（國泰世華 13.2），
卻因為那批數字沒有單位而驗不了，系統只能顯示「無法驗證」。

## 這些單位怎麼來的

**沒有對照原始 PDF**（`parsed_國泰金控_2026Q1.json` 沒有記頁碼），
全部靠內部一致性推定。每一筆的依據都寫在下面的 `EVIDENCE` 裡，可以覆核。
三類依據，強度由高到低：

  1. 算術恆等式  —— 帳面淨值 817.0 ＋ 稅後CSM 439.7 ＝ 調整後淨值總計 1256.7（完全相等）
  2. 同頁 1Q25 對照 —— 同一指標的去年同期值就在同一份資料裡，且有單位
  3. 跨期同名一致 —— 同一個指標名在其他季別都用同一個單位

特別注意國泰人壽那三筆是「億元」而不是「十億元」：
若為十億元，壽險淨值會變成 12,100 億元 ＝ 集團帳面淨值 8,170 億元的 148%，不可能。
另有稅率佐證：集團稅後CSM 4,397 億 ÷ 壽險稅前CSM 5,324 億 ＝ 0.826，符合營所稅約 20%。
IFRS 17 下壽險帳面淨值本來就被壓低、CSM 另計，所以才要另外揭露「調整後淨值」。

## 安全性

只改 `unit` 欄位——不新增、不刪除、不改數值。已經有單位的一律跳過（不覆蓋）。
重跑安全：同樣的輸入得同樣的結果。

用法（專案根目錄）：
    venv/Scripts/python.exe scripts/fix_cathay_units.py            # 只列出預計變更
    venv/Scripts/python.exe scripts/fix_cathay_units.py --apply    # 實際寫入
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

import graph_rag
from graph_rag import G, normalize_unit, save_graph

COMPANY = "國泰金控"
PERIOD = "2026Q1"
APPLY = "--apply" in sys.argv

# (指標名稱, 要補的單位, 依據)
EVIDENCE = [
    # ---- 算術恆等式：817.0 + 439.7 = 1256.7 ----
    ("1Q26 帳面淨值", "十億元", "同頁 1Q25 對照『帳面淨值 = 883.6 十億元』"),
    ("1Q26 稅後CSM", "十億元", "817.0 ＋ 439.7 ＝ 1256.7，與帳面淨值同單位"),
    ("1Q26 調整後淨值總計", "十億元", "同上恆等式"),

    # ---- 同頁 1Q25 對照 ----
    ("1Q26稅後淨利", "十億元", "同頁 1Q25 對照『稅後淨利 = 32.2 十億元』"),
    ("國泰世華稅後淨利", "十億元",
     "同頁『國泰世華銀行 1Q25 稅後淨利 = 12.2 十億元』；2025Q2 JSON 記 NT$BN"),
    ("國泰人壽稅後淨利(含FVOCI)", "十億元",
     "同頁『國泰人壽 1Q25 稅後淨利 = 18.4 十億元』"),

    # ---- 比值反推 ----
    ("1Q26調整後獲利", "十億元",
     "48.6÷31.7 = 1.5331 ≈ 調整後EPS 3.31÷2.15 = 1.5395（差 0.42%），與稅後淨利同單位"),
    ("國泰投信全委資產管理規模", "兆元",
     "同期『國泰投信資產管理規模 = 2.2 兆元』，全委佔 59%；若為億元僅 1.3 億，不可能"),

    # ---- 跨期同名一致 ----
    ("大陸人壽總保費", "億人民幣", "2025Q2 = 41.3、2025Q4 = 106，跨期同名皆億人民幣"),
    ("越南財產保險簽單保費", "億越盾", "2025Q2 = 3,054、2025Q4 = 5,533，跨期同名皆億越盾"),

    # ---- 每股類：同頁對照 ----
    ("1Q26每股盈餘", "元", "同頁『每股盈餘 = 2.18 元』"),
    ("1Q26調整後每股盈餘", "元", "同頁『每股盈餘 = 2.18 元』"),
    ("1Q26 每股淨值", "元", "同頁『普通股每股淨值 = 52.8 元』"),
    ("1Q26 調整後每股淨值", "元", "同頁『普通股每股淨值 = 52.8 元』"),

    # ---- 國泰人壽：億元而非十億元（見檔頭說明）----
    ("國泰人壽CSM餘額", "億元",
     "集團稅後CSM 4,397 億 ÷ 5,324 億 = 0.826，符合營所稅約 20%；十億元會差 10 倍"),
    ("國泰人壽新契約CSM", "億元", "與 CSM 餘額同組"),
    ("國泰人壽淨值", "億元",
     "十億元 → 12,100 億元 = 集團帳面淨值 8,170 億元的 148%，不可能"),

    # ---- 不是缺單位，是被當成金額的比率 ----
    ("國泰人壽調整後淨值比", "%", "同頁『國泰人壽淨值比 = 8.5 %』；名稱只有裸的「比」，分類器沒認出來"),
    ("國泰人壽負債利息成本", "%", "2.11 這個量級在壽險語境是利率，不是金額"),
]


def main():
    planned, skipped = [], []
    for metric, unit, why in EVIDENCE:
        node_id = f"{COMPANY}|{metric}|{PERIOD}"
        if node_id not in G.nodes:
            skipped.append((metric, "圖譜裡沒有這個節點"))
            continue
        current = G.nodes[node_id].get("unit")
        if current:
            skipped.append((metric, f"已經有單位（{current}），不覆蓋"))
            continue
        planned.append((node_id, metric, G.nodes[node_id].get("value"),
                        normalize_unit(unit, metric), why))

    print(f"{COMPANY} {PERIOD}　預計補上 {len(planned)} 筆單位"
          f"{f'，跳過 {len(skipped)} 筆' if skipped else ''}\n")
    print(f"  {'指標':30} {'數值':>10}  {'補上':>8}   依據")
    print("  " + "-" * 104)
    for _, metric, value, unit, why in planned:
        print(f"  {metric[:30]:30} {str(value):>10}  {unit:>8}   {why}")
    for metric, reason in skipped:
        print(f"  （跳過）{metric}：{reason}")

    if not APPLY:
        print(f"\n這是試跑，沒有寫入。確認無誤後加 --apply 實際執行。")
        return

    for node_id, _, _, unit, _ in planned:
        G.nodes[node_id]["unit"] = unit
    save_graph()
    print(f"\n已寫入 {len(planned)} 筆到 {graph_rag.GRAPH_FILE}")
    print("只改了 unit 欄位，數值與節點數量都沒有變動。")


if __name__ == "__main__":
    main()
