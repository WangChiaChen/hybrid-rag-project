"""把知識圖譜匯出成 CSV，給 geminidata（EAP）的「探索關聯圖 / New Flow」建圖用。

EAP 有兩條互不相通的匯入路徑，別搞混：
  · Upload file → 只收 PDF / XLSX，餵的是聊天問答的文件檢索
  · New Flow    → 只收 CSV / JSON / 資料庫，餵的是知識圖譜  ← 這支走這條

兩種輸出格式（--format）：

precise（預設，建議用這個）
  每一筆「公司｜期間｜指標」是一個**唯一節點**，數值掛在節點屬性上。
  平台上就照這個唯一名稱建節點即可。

flat（舊版，保留備查）
  公司 / 事業體 / 指標 / 期間 各自成節點，數值掛在「申報」關係上，
  並要在精靈裡取消該關係的 Merge Duplicates。
  ——實測這個做法會**跨期間答錯**：同一個指標名稱在不同季共用同一個節點，
  平台回答時分不清問的是哪一季。所以才改成 precise，別再用 flat 建圖。

用法：
    python src/export_graph_csv.py                         # precise，全部
    python src/export_graph_csv.py --format flat           # 舊版關係式
    python src/export_graph_csv.py --periods 2026Q1 2025Q4 # 只匯出指定期間
"""
import csv
import os
import argparse

from graph_rag import G, is_cumulative, _clean_number
from metric_alignment import classify_metric, is_cross_comparable
from standard_metrics import STANDARD_METRICS, _pick

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 從指標名稱認出所屬事業體。長的名字要排前面，否則「國泰世華銀行」會先被「國泰世華」吃掉。
# 新增一家金控的資料時，這張表要一起補——漏了的話那家的指標會全部塌到母公司身上，
# 匯出的圖譜就少了子公司層級（第一金控就發生過，151 筆全歸「第一金控」）。
SUBSIDIARIES = sorted([
    "中國信託銀行", "中信銀行", "台灣人壽",
    "國泰世華銀行", "國泰世華", "國泰人壽", "國泰產險", "國泰投信", "國泰證券",
    "陸家嘴國泰人壽", "越南國泰人壽", "越南國泰產險", "大陸人壽", "越南人壽",
    "越南財產保險", "越南產險",
    "玉山銀行", "玉山證券", "玉山創投", "玉山投信",
    # 第一金控。簡報裡「第一銀行」和「一銀」混用（一銀累計淨利、一銀存放比…），兩個都要列
    "第一銀行", "一銀", "第一金證券", "第一金投信", "第一金人壽", "第一金AMC",
], key=len, reverse=True)

# 同一家子公司在不同季的簡報裡叫法會變（「一銀存放比」vs「第一銀行獲利」、
# 「國泰世華ROE」vs「國泰世華銀行 淨利息收入」）。不收斂的話匯出的圖譜會把同一家
# 拆成兩個節點，關聯圖上看起來像兩家不同的公司。一律收斂到正式名稱。
ENTITY_ALIASES = {
    "一銀": "第一銀行",
    "國泰世華": "國泰世華銀行",
    "越南人壽": "越南國泰人壽",
    "越南產險": "越南國泰產險",
    "越南財產保險": "越南國泰產險",
    "大陸人壽": "陸家嘴國泰人壽",
}

# 事業體 -> 層級標籤。舊版匯出把「子公司(世華)」「子公司(一銀)」當成獨立層級，
# 但世華就是國泰世華銀行、一銀就是第一銀行，都該歸在「子公司(銀行)」。
_LEVEL_OF = {
    "中國信託銀行": "子公司(銀行)", "中信銀行": "子公司(銀行)",
    "國泰世華銀行": "子公司(銀行)", "玉山銀行": "子公司(銀行)", "第一銀行": "子公司(銀行)",
    "台灣人壽": "子公司(人壽)", "國泰人壽": "子公司(人壽)",
    "陸家嘴國泰人壽": "子公司(人壽)", "越南國泰人壽": "子公司(人壽)",
    "第一金人壽": "子公司(人壽)",
    "國泰產險": "子公司(產險)", "越南國泰產險": "子公司(產險)",
    "國泰投信": "子公司(投信)", "玉山投信": "子公司(投信)", "第一金投信": "子公司(投信)",
    "國泰證券": "子公司(證券)", "玉山證券": "子公司(證券)", "第一金證券": "子公司(證券)",
    "玉山創投": "子公司(創投)",
    "第一金AMC": "子公司(資產管理)",
}

TYPE_LABEL = {"ratio": "比率", "per_share": "每股", "amount": "金額"}


def level_of(company, entity):
    """這筆指標屬於集團層級還是哪一類子公司。"""
    return "集團" if entity == company else _LEVEL_OF.get(entity, "子公司(其他)")


def find_entity(company, metric):
    """指標名稱有提到子公司就歸它，沒有就歸母公司本身"""
    for s in SUBSIDIARIES:
        if s in metric:
            return ENTITY_ALIASES.get(s, s)
    return company


def _standard_label_map(periods=None):
    """(公司, 期間, 指標原始名稱) -> 標準指標標籤。

    同一個標準比率各家用詞不同（中信「中信金控股東權益報酬率(ROE)」、玉山「ROE」），
    這裡把 standard_metrics 實際挑中的那一筆標記出來，平台上就能靠這欄跨公司對齊。
    只有被挑中的那筆會有值，其餘留空——沿用「高精確度優先，寧可少配也不要配錯」的原則。
    """
    from graph_rag import list_companies, list_periods, list_metrics

    out = {}
    for c in list_companies():
        for p in list_periods(c):
            if periods and p not in periods:
                continue
            ms = list_metrics(c, p)
            for spec in STANDARD_METRICS:
                pick = _pick(ms, spec, p)
                if pick:
                    out[(c, p, pick["name"])] = spec["label"]
    return out


def build_precise_rows(periods=None):
    """每一筆「公司｜期間｜指標」一個唯一節點，數值掛節點屬性。

    為什麼要唯一節點：若只用指標名稱當節點，同一個「手續費淨收益」在五個季度會共用
    一個節點，平台回答「2025Q4 是多少」時分不出要哪一季，實測就是這樣答錯的。
    把公司與期間編進節點名稱，每一格數字就有自己的身分。
    """
    std = _standard_label_map(periods)
    rows = []
    for _, d in G.nodes(data=True):
        if periods and d["period"] not in periods:
            continue
        company, metric, period = d["company"], d["metric"], d["period"]
        unit = d.get("unit") or ""
        kind = classify_metric(metric, unit)
        entity = find_entity(company, metric)
        try:
            value = _clean_number(d["value"])
        except (ValueError, TypeError):
            value = ""   # 多期字串等非純數值，保留原始值那一欄就好
        rows.append({
            "公司": company,
            "期間": period,
            "標準指標": std.get((company, period, metric), ""),
            "指標原始名稱": metric,
            "類型": TYPE_LABEL.get(kind, "金額"),
            "層級": level_of(company, entity),
            "事業體": entity,
            "累計別": "累計" if is_cumulative(company, metric) else "單季/當期",
            "可跨公司比較": "是" if is_cross_comparable(metric, unit) else "否(需對齊單位)",
            "數值": value,
            "單位": unit,
            "數值原始": d["value"],
            "年增": d.get("yoy") or "",
            "指標節點名稱(唯一)": f"{company}｜{period}｜{metric}",
        })
    rows.sort(key=lambda r: (r["公司"], r["期間"], r["層級"], r["指標原始名稱"]))
    return rows


PRECISE_FIELDS = ["公司", "期間", "標準指標", "指標原始名稱", "類型", "層級", "事業體",
                  "累計別", "可跨公司比較", "數值", "單位", "數值原始", "年增",
                  "指標節點名稱(唯一)"]


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


def export(out_path, periods=None, fmt="precise"):
    rows = build_precise_rows(periods) if fmt == "precise" else build_rows(periods)
    fields = PRECISE_FIELDS if fmt == "precise" else FIELDS
    # utf-8-sig：平台和 Excel 才不會把中文讀成亂碼
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["precise", "flat"], default="precise",
                        help="precise＝每指標唯一節點（建議）；flat＝舊版關係式，會跨期間答錯")
    parser.add_argument("--out", default=None, help="不填會依格式自動命名")
    parser.add_argument("--periods", nargs="*", default=None, help="只匯出這些期間，不填=全部")
    args = parser.parse_args()

    out = args.out or os.path.join(
        BASE_DIR, "eap_graph_precise.csv" if args.format == "precise" else "graph_import.csv")
    rows = export(out, args.periods, args.format)
    print(f"已匯出 {len(rows)} 列（{args.format}）-> {os.path.relpath(out, BASE_DIR)}")

    def summarize(key):
        seen = {}
        for r in rows:
            seen[r[key]] = seen.get(r[key], 0) + 1
        return dict(sorted(seen.items()))

    print("  公司:", summarize("公司"))
    print("  期間:", summarize("期間"))
    if args.format == "precise":
        print("  層級:", summarize("層級"))
        print("  累計別:", summarize("累計別"))
        print("  可跨公司比較:", summarize("可跨公司比較"))
        print(f"  已對齊標準指標的列: {sum(1 for r in rows if r['標準指標'])}")
        print(f"  節點名稱是否唯一: "
              f"{'是' if len({r['指標節點名稱(唯一)'] for r in rows}) == len(rows) else '否（有重複！）'}")
    else:
        print("  指標類型:", summarize("指標類型"))
        print("  累計:", summarize("累計"))
    print(f"  事業體: {len(set(r['事業體'] for r in rows))} 個")
    print(f"  有單位的列: {sum(1 for r in rows if r['單位'])} / {len(rows)}")
