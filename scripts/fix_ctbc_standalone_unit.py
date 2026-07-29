"""補上「中信金單一 1Q26 稅後淨利」缺失的單位（一次性資料修正，含依據）。

## 為什麼要補

沒有單位的指標，交叉驗證一律跳過（`_pick_local_for_entity` 要求 unit）。
實測踩過：EAP 把中信金控**合併**稅後淨利（231.04 億）貼錯標籤成「中信金單一」，
但「中信金單一」其實是**母公司單獨**、1Q26 為**虧損 43 億**。本地明明有這筆，
卻因為它 unit=None 而驗不了，於是那個張冠李戴的答案沒被攔下來。

## 這個單位怎麼來的

`outputs/parsed_中信金控_2026Q1.json` 的敘述段白紙黑字寫：
    「…另中信金單一虧損 4,313 佰萬元。」
佰萬元＝百萬元。本地存的數值正是 -4,313，單位就是百萬元。

## 安全性

只改 `unit` 欄位——不新增、不刪除、不改數值。已有單位就跳過（不覆蓋）。
重跑安全：同輸入得同結果。

用法（專案根目錄）：
    venv/Scripts/python.exe scripts/fix_ctbc_standalone_unit.py            # 只列出預計變更
    venv/Scripts/python.exe scripts/fix_ctbc_standalone_unit.py --apply    # 實際寫入
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

import graph_rag
from graph_rag import G, normalize_unit, save_graph

COMPANY = "中信金控"
PERIOD = "2026Q1"
APPLY = "--apply" in sys.argv

# (指標名稱, 要補的單位, 依據)
EVIDENCE = [
    ("中信金單一 1Q26 稅後淨利", "百萬元",
     "parsed_中信金控_2026Q1.json 敘述段：『另中信金單一虧損 4,313 佰萬元』（佰萬＝百萬）"),
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
        print("\n這是試跑，沒有寫入。確認無誤後加 --apply 實際執行。")
        return

    for node_id, _, _, unit, _ in planned:
        G.nodes[node_id]["unit"] = unit
    save_graph()
    print(f"\n已寫入 {len(planned)} 筆到 {graph_rag.GRAPH_FILE}")
    print("只改了 unit 欄位，數值與節點數量都沒有變動。")


if __name__ == "__main__":
    main()
