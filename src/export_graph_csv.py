"""把知識圖譜匯出成 CSV，給 geminidata 的「探索關聯圖 (Data Explorer)」建圖用。

geminidata 的建圖精靈只吃結構化來源（CSV / JSON / 資料庫），不吃 PDF。
所以流程是：非結構化 PDF -> VLM 抽取 -> 這支匯出 CSV -> 平台建知識圖譜。

在平台的 New Flow 精靈裡建議這樣對應：
  節點：公司 / 事業體 / 指標 / 期間
  關係：公司 --持有--> 事業體 --申報--> 指標 --於期間--> 期間
  「申報」這條關係取消 Merge Duplicates，並掛上 數值／單位／期間 當屬性，
  才能保留每一季各自的數字。

用法：
    python src/export_graph_csv.py                    # 匯出全部
    python src/export_graph_csv.py --out my.csv       # 自訂檔名
    python src/export_graph_csv.py --periods 2026Q1 2025Q4   # 只匯出指定期間
"""
import csv
import os
import argparse

from graph_rag import G, is_cumulative
from metric_alignment import classify_metric

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 從指標名稱認出所屬事業體。長的名字要排前面，否則「國泰世華銀行」會先被「國泰世華」吃掉。
SUBSIDIARIES = sorted([
    "中國信託銀行", "中信銀行", "台灣人壽",
    "國泰世華銀行", "國泰世華", "國泰人壽", "國泰產險", "國泰投信", "國泰證券",
    "陸家嘴國泰人壽", "越南國泰人壽", "越南國泰產險", "大陸人壽", "越南人壽",
    "越南財產保險", "越南產險",
    "玉山銀行", "玉山證券", "玉山創投", "玉山投信",
], key=len, reverse=True)

TYPE_LABEL = {"ratio": "比率", "per_share": "每股", "amount": "金額"}


def find_entity(company, metric):
    """指標名稱有提到子公司就歸它，沒有就歸母公司本身"""
    for s in SUBSIDIARIES:
        if s in metric:
            return s
    return company


def build_rows(periods=None):
    rows = []
    for _, d in G.nodes(data=True):
        if periods and d["period"] not in periods:
            continue
        metric = d["metric"]
        company = d["company"]
        rows.append({
            "公司": company,
            "事業體": find_entity(company, metric),
            "指標": metric,
            "指標類型": TYPE_LABEL.get(classify_metric(metric, d.get("unit")), "金額"),
            "單位": d.get("unit") or "",
            "期間": d["period"],
            "數值": d["value"],
            "累計": "是" if is_cumulative(company, metric) else "否",
            "年增": d.get("yoy") or "",
        })
    # 排序讓 CSV 好讀，也讓每次匯出結果穩定
    rows.sort(key=lambda r: (r["公司"], r["期間"], r["事業體"], r["指標"]))
    return rows


FIELDS = ["公司", "事業體", "指標", "指標類型", "單位", "期間", "數值", "累計", "年增"]


def export(out_path, periods=None):
    rows = build_rows(periods)
    # utf-8-sig：平台和 Excel 才不會把中文讀成亂碼
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(BASE_DIR, "graph_import.csv"))
    parser.add_argument("--periods", nargs="*", default=None, help="只匯出這些期間，不填=全部")
    args = parser.parse_args()

    rows = export(args.out, args.periods)
    print(f"已匯出 {len(rows)} 列 -> {args.out}")

    def summarize(key):
        seen = {}
        for r in rows:
            seen[r[key]] = seen.get(r[key], 0) + 1
        return dict(sorted(seen.items()))

    print("  公司:", summarize("公司"))
    print("  期間:", summarize("期間"))
    print("  指標類型:", summarize("指標類型"))
    print("  累計:", summarize("累計"))
    print(f"  事業體: {len(set(r['事業體'] for r in rows))} 個")
    print(f"  有單位的列: {sum(1 for r in rows if r['單位'])} / {len(rows)}")
