"""把國泰 2026Q1 裡「其實是去年同期」的指標補上期別標籤（一次性資料修正）。

## 問題

國泰的簡報把去年同期放在同一頁對照，但 VLM 解析出來的命名跟一般慣例相反：
**當期的被標了「1Q26」，去年的反而沒標**。於是那批沒標的被當成當期資料：

    2026Q1 資料夾
      稅後淨利      = 32.2 十億元   ← 其實是 1Q25
      1Q26稅後淨利  = 31.7 十億元   ← 這才是 2026Q1

後果有兩個，第二個更嚴重：
  · 交叉驗證拿去年的數字當基準，EAP 用去年數字回答時反而「驗過沒問題」
  · 儀表板的門面數字（hero）挑到名稱乾淨的那筆，於是首頁大字顯示的是**去年的** 32.2

## 判定依據

只改「兩個條件同時成立」的：
  1. 同一份資料裡已經有標了 1Q26 的當期版本（代表這筆不可能也是當期）
  2. 這筆的值與 2025Q1 資料夾裡同一指標的值完全相同（代表它就是去年那筆）

刻意不改只符合單一條件的（例如「手續費淨收益 10.2」值對得上去年，但同期沒有 1Q26
版本——改了會讓當期完全沒有手續費數字）。那幾筆要人工比對簡報才能決定。

## 安全性

只改指標名稱，value／unit／yoy 原樣搬過去，節點總數不變。
目標名稱已存在就跳過，不覆蓋。重跑安全。

用法（專案根目錄）：
    venv/Scripts/python.exe scripts/fix_cathay_prior_year_labels.py
    venv/Scripts/python.exe scripts/fix_cathay_prior_year_labels.py --apply
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

import graph_rag
from graph_rag import G, save_graph

COMPANY = "國泰金控"
PERIOD = "2026Q1"
APPLY = "--apply" in sys.argv

# (現名, 新名, 同期的當期版本, 去年同期的佐證)
RENAMES = [
    ("稅後淨利", "稅後淨利 (1Q25)",
     "1Q26稅後淨利 = 31.7 十億元", "2025Q1 的『稅後淨利 (1Q25)』= 32.2 十億元"),
    ("每股盈餘", "每股盈餘 (1Q25)",
     "1Q26每股盈餘 = 2.15 元", "2025Q1 的『每股盈餘 (1Q25)』= 2.18 元"),
    ("帳面淨值", "帳面淨值 (1Q25)",
     "1Q26 帳面淨值 = 817.0 十億元", "2025Q1 的『帳面淨值』= 883.6 十億元"),
]


def main():
    planned, skipped = [], []
    for old, new, sibling, evidence in RENAMES:
        old_id = f"{COMPANY}|{old}|{PERIOD}"
        new_id = f"{COMPANY}|{new}|{PERIOD}"
        if old_id not in G.nodes:
            skipped.append((old, "圖譜裡沒有這個節點"))
            continue
        if new_id in G.nodes:
            skipped.append((old, f"目標名稱已存在（{new}），不覆蓋"))
            continue
        planned.append((old_id, new_id, old, new, dict(G.nodes[old_id]), sibling, evidence))

    print(f"{COMPANY} {PERIOD}　預計改名 {len(planned)} 筆"
          f"{f'，跳過 {len(skipped)} 筆' if skipped else ''}\n")
    for _, _, old, new, attrs, sibling, evidence in planned:
        print(f"  {old} → {new}")
        print(f"      值：{attrs.get('value')} {attrs.get('unit') or ''}（不變）")
        print(f"      同期已有當期版本：{sibling}")
        print(f"      去年同期佐證：　　{evidence}")
    for name, reason in skipped:
        print(f"  （跳過）{name}：{reason}")

    if not APPLY:
        print("\n這是試跑，沒有寫入。確認無誤後加 --apply 實際執行。")
        return

    for old_id, new_id, _, new, attrs, _, _ in planned:
        attrs["metric"] = new
        G.add_node(new_id, **attrs)
        G.remove_node(old_id)
    save_graph()
    print(f"\n已改名 {len(planned)} 筆。數值與單位原樣保留，節點總數不變。")


if __name__ == "__main__":
    main()
