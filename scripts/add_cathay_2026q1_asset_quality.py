"""補上國泰金控 2026Q1 的當期資產品質指標（逾放比、覆蓋率）——一次性資料修正。

## 為什麼要補

國泰 2026Q1 的敘述白紙黑字寫「資產品質維持良好，逾放比為0.13%，備抵呆帳覆蓋率為1297%。」，
但解析時只把**去年同期**版本收成結構化指標（名稱帶「(1Q25)」），當期的沒進指標庫。
於是問答問「國泰 2026Q1 逾放比／覆蓋率」查無、跨機構比較頁那兩列也空白——
數字明明就在敘述裡。

## 依據

parsed_國泰金控_2026Q1.json 的敘述段：
    「資產品質維持良好，逾放比為0.13%，備抵呆帳覆蓋率為1297%。」
（此段為 2026Q1 當期資產品質說明；同頁另有標「(1Q25)」的去年同期對照值，
  數值恰好也是 0.13% / 1297%，代表資產品質年度間穩定，不是把舊值當新值。）

## 安全性

只**新增**兩個節點，不改動、不刪除既有指標；已存在同名節點就跳過（不覆蓋）。
重跑安全：同輸入得同結果。

用法（專案根目錄）：
    venv/Scripts/python.exe scripts/add_cathay_2026q1_asset_quality.py            # 只列出預計新增
    venv/Scripts/python.exe scripts/add_cathay_2026q1_asset_quality.py --apply    # 實際寫入
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

import graph_rag
from graph_rag import G, add_metric_datapoint, save_graph

COMPANY = "國泰金控"
PERIOD = "2026Q1"
APPLY = "--apply" in sys.argv

# (指標名稱, 數值, 單位, 依據)
ADD = [
    ("整體逾放比", "0.13", "%",
     "敘述『資產品質維持良好，逾放比為0.13%…』（parsed_國泰金控_2026Q1.json）"),
    ("備抵呆帳覆蓋率", "1297", "%",
     "敘述『…備抵呆帳覆蓋率為1297%。』（parsed_國泰金控_2026Q1.json）"),
]


def main():
    planned, skipped = [], []
    for metric, value, unit, why in ADD:
        node_id = f"{COMPANY}|{metric}|{PERIOD}"
        if node_id in G.nodes:
            skipped.append((metric, "圖譜裡已有同名節點，不覆蓋"))
            continue
        planned.append((metric, value, unit, why))

    print(f"{COMPANY} {PERIOD}　預計新增 {len(planned)} 筆指標"
          f"{f'，跳過 {len(skipped)} 筆' if skipped else ''}\n")
    print(f"  {'指標':16} {'數值':>8} {'單位':>4}   依據")
    print("  " + "-" * 96)
    for metric, value, unit, why in planned:
        print(f"  {metric:16} {value:>8} {unit:>4}   {why}")
    for metric, reason in skipped:
        print(f"  （跳過）{metric}：{reason}")

    if not APPLY:
        print("\n這是試跑，沒有寫入。確認無誤後加 --apply 實際執行。")
        return

    for metric, value, unit, _ in planned:
        add_metric_datapoint(COMPANY, metric, PERIOD, value, unit=unit, save=False)
    save_graph()
    print(f"\n已新增 {len(planned)} 筆到 {graph_rag.GRAPH_FILE}")
    print("只新增節點，既有指標與數值都沒有變動。")


if __name__ == "__main__":
    main()
