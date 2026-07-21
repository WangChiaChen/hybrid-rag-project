"""Phase 3：Graph RAG —— 財務指標知識圖譜，確保計算 100% 精準
正式版可把 networkx 換成 Neo4j，邏輯不變
資料會存成 JSON 檔，重開程式不會消失
"""
import networkx as nx
import json
import os
import re

from metric_alignment import classify_metric, is_cumulative_name, norm_metric_name

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

# 累計判定的快取。判定要掃全圖、又會逐一問手足，不快取的話單次 API 請求就是千萬次走訪。
# 圖一有異動（新增／刪除節點）就整個清空，寧可重算也不要拿舊答案。
_CUM_SELF_CACHE: dict = {}
_METRICS_BY_COMPANY: dict = {}


def _invalidate_caches():
    _CUM_SELF_CACHE.clear()
    _METRICS_BY_COMPANY.clear()


def save_graph():
    _invalidate_caches()
    os.makedirs(os.path.dirname(GRAPH_FILE), exist_ok=True)
    data = {"nodes": [{"id": n, **d} for n, d in G.nodes(data=True)]}
    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 只寫幣別、看不出量級的寫法。單獨出現時無從得知是元、千元還是百萬元。
_NO_MAGNITUDE = ("NT$", "NTD", "TWD", "新台幣", "新臺幣", "台幣", "臺幣")


def normalize_unit(unit, metric=None):
    """統一單位寫法。VLM 是照著各家報表原文抄的，同一個單位實測有 24 種寫法：
    台／臺、千／仟、百／佰 混用，還混著英文（NT$BN、TWD million）。
    不統一的話 LLM 會把同一個單位當成不同單位，存單位的用意就沒了。

    外幣（越南盾等）原樣保留——那是不同幣別，不能跟台幣混為一談。
    看不出量級的寫法（「新台幣」「NT$」）原樣保留，不猜——除非傳了 metric 進來
    且它是「每股」類：每股盈餘、每股淨值的幣別單位一定是元，沒有其他可能。
    """
    if not unit:
        return unit
    u = str(unit).strip()

    # 幣別但沒量級：只有「每股」類推得出來（每股盈餘 7.06 NT$ 就是 7.06 元）
    if u in _NO_MAGNITUDE:
        if metric and classify_metric(metric) == "per_share":
            return "元"
        return u

    # 外幣不碰
    if any(k in u for k in ("盾", "美元", "USD", "人民幣", "RMB")):
        return u

    lower = u.lower()
    if u in ("%", "百分比") or "percentage" in lower:
        return "%"

    u = u.replace("臺", "台").replace("仟", "千").replace("佰", "百").replace("拾", "十")
    if "千元" in u:
        return "千元"
    # 第一金控整份簡報用「NT$ mn / NT$MN」，跟其他家的「百萬元」是同一個量級，
    # 不收斂的話跨機構比較時會被當成兩種單位。用 \bmn\b 避免誤傷其他縮寫。
    if "百萬" in u or "million" in lower or re.search(r"\bmn\b", lower):
        return "百萬元"
    if "兆" in u:
        return "兆元"
    # 「十億」必須排在「億」前面。各家寫法有「新台幣拾億元」「十億新臺幣」，
    # 若先命中「億」會被壓成「億元」——那是 10 倍的錯，存放款規模會整批少一個量級。
    if "十億" in u or "bn" in lower or "billion" in lower:
        return "十億元"
    if "億" in u:
        return "億元"
    if u in ("元", "新台幣元"):
        return "元"
    return u


def add_metric_datapoint(company, metric, period, value, unit=None, yoy=None):
    node_id = f"{company}|{metric}|{period}"
    attrs = {"company": company, "metric": metric, "period": period, "value": value}
    # 單位是跨公司比較的關鍵（同樣是「稅後淨利」，財報用千元、簡報用億元，不能直接比大小）
    unit = normalize_unit(unit, metric)
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

    三種訊號，前兩種看自己、第三種看「同一筆數字在別期的叫法」：
      1. 名稱有標記——「累計」「上半年」「9M25」「1H25」等
      2. 數值在同一年內逐季遞增——玉山簡報的 EPS 就叫「EPS」，名稱完全看不出來，
         但 2025 四季是 0.55 → 1.05 → 1.62 → 2.12，一路累加，只能從數列認出來
      3. 同一指標各期叫法不一致時，向手足繼承。中信的 EPS 每季名稱都不同
         （EPS／每股稅後盈餘／9M25 每股盈餘），用「完全同名」湊數列在 2025 年只湊得到
         兩個點，不到訊號 2 的三季門檻，於是 2025Q4 的全年 4.08 對上 2026Q1 首季 1.18
         被算成 -71%。但同一筆 1.18 叫「3M26每股稅後盈餘」時是標得出來的——
         去掉期別標籤後同名的手足既然是累計，這筆就是累計。

         只對「非比率」繼承：逾放比、資本適足率這些本來就該逐季比，
         繼承會把它們正常的季變化也擋掉。實測這條規則擋下 10 筆假變化率、誤擋 0 筆。
    """
    if _is_cumulative_self(company, metric):
        return True
    if classify_metric(metric) == "ratio":
        return False
    target = norm_metric_name(metric)
    for sibling in _sibling_metrics(company, metric):
        if norm_metric_name(sibling) == target and _is_cumulative_self(company, sibling):
            return True
    return False


def _sibling_metrics(company, metric):
    """同一家公司底下、除了自己以外的所有指標名稱（去重）。"""
    if company not in _METRICS_BY_COMPANY:
        acc = {}
        for _, d in G.nodes(data=True):
            acc.setdefault(d["company"], set()).add(d["metric"])
        _METRICS_BY_COMPANY.update(acc)
    return _METRICS_BY_COMPANY.get(company, set()) - {metric}


def _is_cumulative_self(company, metric):
    """只看這個指標自己的名稱與數列，不向手足繼承——避免 is_cumulative 互相遞迴。

    有快取：手足繼承會對同一家公司的每個指標各問一次，沒快取的話一次
    /api/metrics（約 70 個指標）要掃出上千萬次節點走訪。圖一有變動就整個清掉。
    """
    key = (company, metric)
    if key in _CUM_SELF_CACHE:
        return _CUM_SELF_CACHE[key]
    _CUM_SELF_CACHE[key] = result = _compute_cumulative_self(company, metric)
    return result


def _compute_cumulative_self(company, metric):
    if is_cumulative_name(metric):
        return True

    # 數列啟發式（連三季遞增）只對「金額／每股」有效。比率連三季走高是很正常的經營表現，
    # 不代表在累加：中信 NIM 2025 是 1.49→1.50→1.53→1.64，逾放覆蓋率、存放比也一樣，
    # 一律當成累計會把它們正常的季變化全擋掉（實測誤擋 14 筆）。
    # 名稱明講是累計的比率（「9M25 ROE」）已在上面攔下，不受這條影響。
    if classify_metric(metric) == "ratio":
        return False

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


def pins_own_period(metric):
    """指標名稱有沒有把期別寫死（「SME放款 (1Q25)」「玉山金控資本適足率 (2025)」）。

    這種指標的數字屬於「名稱裡的那一期」，不屬於它被歸檔的那一期——同一份簡報常會
    附上去年同期或前幾年的數字當對照，解析時就連標籤一起被收進當期資料夾。
    """
    return norm_metric_name(metric) != str(metric)


def calc_change(company, metric, this_period, last_period):
    """公式計算核心 —— 直接算，不讓 LLM 猜。

    累計型指標只有「同一季跨年度」能比（例如 2025Q1 vs 2026Q1，都是前三個月累計）。
    跨季比較（2025Q1 的 3 個月 vs 2025Q3 的 9 個月，或 2025Q4 的 12 個月 vs 2026Q1 的
    3 個月）算出來的百分比沒有意義，寧可不給數字也不要給錯的。

    名稱釘死期別的快照型指標也不比。國泰的「SME放款 (1Q25)」在 2025Q1 和 2026Q1
    兩份簡報裡都是 340.0——那是同一個數字被收錄兩次，比出來的 0.0% 會被讀成
    「本季持平」，其實根本沒有本季。玉山更糟：2026Q1 簡報把當季資本適足率標成
    「(2025)」，跨期一比就生出 +1.07% 的假變化。
    """
    n1 = f"{company}|{metric}|{this_period}"
    n2 = f"{company}|{metric}|{last_period}"
    if n1 not in G.nodes or n2 not in G.nodes:
        return None

    if this_period != last_period and pins_own_period(metric):
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
    # 快速自我測試。用假公司名而不是「中信金控」——這支寫的是正式圖譜（會存檔），
    # 原本會把中信真正的手續費淨收益 17,977／13,779 蓋成 8054／7805 再存回去。
    # 跑完務必刪乾淨。
    DEMO = "＿測試公司＿"
    try:
        add_metric_datapoint(DEMO, "手續費淨收益", "2026Q1", "8054")
        add_metric_datapoint(DEMO, "手續費淨收益", "2025Q4", "7805")
        change = calc_change(DEMO, "手續費淨收益", "2026Q1", "2025Q4")
        print(f"QoQ 變化：{change}%（預期 3.19%）")
    finally:
        for p in ("2026Q1", "2025Q4"):
            remove_period(DEMO, p)
        print("（已清掉測試節點）")
    print("目前所有公司：", list_companies())
