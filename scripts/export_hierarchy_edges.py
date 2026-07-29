"""產生「金控 → 子公司 → 稅後淨利」的層級關聯 CSV，供 EAP 畫關聯圖用。

## 為什麼另外產這一份

主圖譜（eap_graph_precise.csv）是精準版：1,255 個獨立節點、0 關聯——正確，但畫出來
是散點沒有線。要 Demo「關聯」需要邊。這份就是那些邊：一條清楚的層級鏈
    金控 ──擁有──> 子公司 ──揭露──> 稅後淨利
畫出來是漂亮的 hub-and-spoke，而且直接支撐「金控 vs 銀行不可比」的防呆論點。

## 只挑「當期正確值」

國泰的 2026Q1 資料夾混了去年同期（國泰人壽 1Q25 = 18.4）。用 api._pins_other_period
把釘死在別期的排除掉，只取真正的本期數字——不然關聯圖上會掛去年的數字。

## 匯入方式

EAP New Flow 另建一個 flow，來源選這份 CSV，Model 設兩個關係：
  金控（來源節點）──擁有──> 子公司（目標節點）
  子公司（來源節點）──揭露稅後淨利──> （稅後淨利當子公司節點的屬性即可）
不影響主圖譜的 1,255 個指標節點——這份只加維度節點與邊，是疊加的。

用法：
    venv/Scripts/python.exe scripts/export_hierarchy_edges.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

import api
from graph_rag import list_companies, list_metrics
from metric_alignment import norm_metric_name

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERIOD = "2026Q1"
OUT = os.path.join(BASE, "eap_hierarchy_edges.csv")


def main():
    edges = []
    for company in list_companies():
        # 這家在本期有哪些子公司（從 事業體＋層級 認出來），取每家的稅後淨利
        subs = {}
        for m in list_metrics(company, PERIOD):
            name = m["metric"]
            if "稅後淨利" not in name:
                continue
            if api._pins_other_period(name, PERIOD):     # 排除釘死在別期的（去年同期）
                continue
            ent = api._entity_in(name)                   # 認出子公司名
            if not ent or ent == company:                # 沒有子公司名＝集團層級，跳過
                continue
            val = api._to_float_safe(m["value"])
            unit = m.get("unit")
            if val is None or not unit:
                continue
            # 同一子公司可能有多筆，取名稱最短（最乾淨）的那筆
            key = norm_metric_name(name)
            if ent not in subs or len(name) < subs[ent][2]:
                subs[ent] = (val, unit, len(name), name)

        # 換算成「億元」統一顯示（畫圖標籤才不會百萬/億/十億混雜）。
        # 只在 Demo 標籤用，原始值與單位仍保留在旁邊欄位，誠實可查。
        to_yi = {"百萬元": 0.01, "億元": 1.0, "十億元": 10.0, "千元": 0.00001}
        for ent, (val, unit, _, src) in sorted(subs.items()):
            yi = round(val * to_yi[unit], 1) if unit in to_yi else None
            edges.append({
                "金控": company,
                "關係1": "擁有",
                "子公司": ent,
                "關係2": "揭露稅後淨利",
                "稅後淨利_億元": yi if yi is not None else "",
                "稅後淨利_原始": val,
                "原始單位": unit,
                "期間": PERIOD,
                "來源指標": src,
            })

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["金控", "關係1", "子公司", "關係2",
                                          "稅後淨利_億元", "稅後淨利_原始", "原始單位",
                                          "期間", "來源指標"])
        w.writeheader()
        w.writerows(edges)

    print(f"已產生 {OUT}")
    print(f"共 {len(edges)} 條「金控→子公司」關聯（{PERIOD}）：\n")
    cur = None
    for e in edges:
        if e["金控"] != cur:
            print(f"  {e['金控']}"); cur = e["金控"]
        print(f"    └─擁有─> {e['子公司']:10} 稅後淨利 {e['稅後淨利_億元']} 億元"
              f"（原始 {e['稅後淨利_原始']} {e['原始單位']}）")


if __name__ == "__main__":
    main()
