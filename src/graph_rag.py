"""Phase 3：Graph RAG —— 財務指標知識圖譜，確保計算 100% 精準
正式版可把 networkx 換成 Neo4j，邏輯不變
資料會存成 JSON 檔，重開程式不會消失
"""
import networkx as nx
import json
import os
import re

from metric_alignment import is_cumulative_name

GRAPH_FILE = os.path.join(os.path.dirname(__file__), "..", "vector_db", "graph_data.json")


def _load_graph():
    g = nx.DiGraph()
    if os.path.exists(GRAPH_FILE):
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for node in data.get("nodes", []):
            node_id = node["id"]
            attrs = {k: v for k, v in node.items() if k != "id"}
            g.add_node(node_id, **attrs)
    return g


G = _load_graph()


def save_graph():
    os.makedirs(os.path.dirname(GRAPH_FILE), exist_ok=True)
    data = {"nodes": [{"id": n, **d} for n, d in G.nodes(data=True)]}
    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_unit(unit):
    """統一單位寫法。VLM 是照著各家報表原文抄的，同一個單位實測有 24 種寫法：
    台／臺、千／仟、百／佰 混用，還混著英文（NT$BN、TWD million）。
    不統一的話 LLM 會把同一個單位當成不同單位，存單位的用意就沒了。

    外幣（越南盾等）原樣保留——那是不同幣別，不能跟台幣混為一談。
    看不出量級的寫法（「新台幣」「NT$」）也原樣保留，不猜。
    """
    if not unit:
        return unit
    u = str(unit).strip()

    # 外幣不碰
    if any(k in u for k in ("盾", "美元", "USD", "人民幣", "RMB")):
        return u

    lower = u.lower()
    if u in ("%", "百分比") or "percentage" in lower:
        return "%"

    u = u.replace("臺", "台").replace("仟", "千").replace("佰", "百")
    if "千元" in u:
        return "千元"
    if "百萬" in u or "million" in lower:
        return "百萬元"
    if "bn" in lower or "billion" in lower:
        return "十億元"
    if "兆" in u:
        return "兆元"
    if "億" in u:
        return "億元"
    if u in ("元", "新台幣元"):
        return "元"
    return u


def add_metric_datapoint(company, metric, period, value, unit=None, yoy=None):
    node_id = f"{company}|{metric}|{period}"
    attrs = {"company": company, "metric": metric, "period": period, "value": value}
    # 單位是跨公司比較的關鍵（同樣是「稅後淨利」，財報用千元、簡報用億元，不能直接比大小）
    unit = normalize_unit(unit)
    if unit:
        attrs["unit"] = unit
    if yoy:
        attrs["yoy"] = yoy
    G.add_node(node_id, **attrs)
    save_graph()
    return node_id


def remove_period(company, period):
    """刪掉某公司某期間的全部指標節點，回傳刪除筆數。

    重新匯入同一期間前要先清乾淨——節點是用「公司|指標名稱|期間」當 key，
    如果換了一份簡報而指標名稱跟著變（例如英文版換成中文版），
    直接重匯會變成新舊兩套並存，而不是取代。
    """
    targets = [
        n for n, d in G.nodes(data=True)
        if d["company"] == company and d["period"] == period
    ]
    G.remove_nodes_from(targets)
    save_graph()
    return len(targets)


def ingest_metrics(company, period, key_metrics):
    """key_metrics 是 vlm_parse.py 解析出來的 list，例如：
    [{"指標名稱": "手續費淨收益", "數值": "8054", "單位": "千元", "YoY": "12%"}]
    """
    for m in key_metrics:
        name = m.get("指標名稱")
        value = m.get("數值")
        if name and value:
            add_metric_datapoint(
                company, name, period, value,
                unit=m.get("單位") or None,
                yoy=m.get("YoY") or None,
            )


def _clean_number(v):
    return float(str(v).replace(",", "").replace("%", ""))


_PERIOD_RE = re.compile(r"^(\d{4})Q([1-4])$")


def _parse_period(period):
    """把 "2026Q1" 拆成 (2026, 1)。認不出來就回 None（例如 "2026Q1財報"）"""
    m = _PERIOD_RE.match(str(period))
    return (int(m.group(1)), int(m.group(2))) if m else None


def is_cumulative(company, metric):
    """判斷指標是不是「年初至今累計值」。法說會簡報的獲利／EPS 多半是累計而非單季，
    直接拿去算 QoQ 會得到假的成長率（累計月數變多而已，不是業績成長）。

    兩種訊號：
      1. 名稱有標記——「累計」「上半年」「9M25」「1H25」等
      2. 數值在同一年內逐季遞增——玉山簡報的 EPS 就叫「EPS」，名稱完全看不出來，
         但 2025 四季是 0.55 → 1.05 → 1.62 → 2.12，一路累加，只能從數列認出來
    """
    if is_cumulative_name(metric):
        return True

    # 依年份收集同一指標的各季數值
    by_year = {}
    for _, d in G.nodes(data=True):
        if d["company"] != company or d["metric"] != metric:
            continue
        parsed = _parse_period(d["period"])
        if not parsed:
            continue
        year, q = parsed
        try:
            by_year.setdefault(year, {})[q] = _clean_number(d["value"])
        except ValueError:
            continue

    # 同一年內至少三季、且逐季嚴格遞增 -> 累計
    for quarters in by_year.values():
        if len(quarters) < 3:
            continue
        vals = [quarters[q] for q in sorted(quarters)]
        if all(b > a for a, b in zip(vals, vals[1:])):
            return True
    return False


def calc_change(company, metric, this_period, last_period):
    """公式計算核心 —— 直接算，不讓 LLM 猜。

    累計型指標只有「同一季跨年度」能比（例如 2025Q1 vs 2026Q1，都是前三個月累計）。
    跨季比較（2025Q1 的 3 個月 vs 2025Q3 的 9 個月，或 2025Q4 的 12 個月 vs 2026Q1 的
    3 個月）算出來的百分比沒有意義，寧可不給數字也不要給錯的。
    """
    n1 = f"{company}|{metric}|{this_period}"
    n2 = f"{company}|{metric}|{last_period}"
    if n1 not in G.nodes or n2 not in G.nodes:
        return None

    p1, p2 = _parse_period(this_period), _parse_period(last_period)
    if p1 and p2 and p1[1] != p2[1] and is_cumulative(company, metric):
        return None

    try:
        v1 = _clean_number(G.nodes[n1]["value"])
        v2 = _clean_number(G.nodes[n2]["value"])
        if v2 == 0:
            return None
        return round((v1 - v2) / v2 * 100, 2)
    except ValueError:
        return None


def list_companies():
    return sorted(set(d["company"] for _, d in G.nodes(data=True)))


def list_periods(company):
    return sorted(set(d["period"] for _, d in G.nodes(data=True) if d["company"] == company))


def list_metrics(company, period):
    return [
        {"metric": d["metric"], "value": d["value"], "unit": d.get("unit"), "yoy": d.get("yoy")}
        for _, d in G.nodes(data=True)
        if d["company"] == company and d["period"] == period
    ]


if __name__ == "__main__":
    # 快速自我測試
    add_metric_datapoint("中信金控", "手續費淨收益", "2026Q1", "8054")
    add_metric_datapoint("中信金控", "手續費淨收益", "2025Q4", "7805")
    change = calc_change("中信金控", "手續費淨收益", "2026Q1", "2025Q4")
    print(f"QoQ 變化：{change}%")
    print("目前所有公司：", list_companies())
