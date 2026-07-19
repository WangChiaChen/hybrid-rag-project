"""跨機構「標準比率」對照表。

問題：各金控簡報對同一個標準比率的用詞不一致——中信叫「中信金控股東權益報酬率(ROE)」、
國泰叫「國泰金控 9M25 ROE」、玉山直接叫「ROE」。逐字比對配不起來，
Chroma 的英文 embedding 對中文財務術語又沒鑑別度（見 metric_alignment.py 的說明）。

作法：用一張「人工維護的標準比率字典」把這些同義詞收斂到同一個定義。
每個標準比率給一組 include／exclude 規則，在某公司某期的指標清單裡挑出「最乾淨」的那一筆
（金控層級、非累計、非分期標籤、單位正確）。這是刻意的「高精確度優先」策略——
規則挑不到就不硬湊，寧可少一列，也不要拿子公司或累計值配錯。

另有「衍生比率」（如資產負債率）：資料裡沒有現成欄位，但兩個絕對金額相除就得到，
且各家單位一致（都用同一組報表數字），可以放心跨機構比。
"""
import re

from graph_rag import list_metrics
from metric_alignment import is_cumulative_name

# 分期／年度標籤：名稱帶這些通常是「某一期的快照」或重複揭露，挑乾淨版本時要扣分
_PERIOD_TAG = re.compile(r"\d[QH]\d{2}|\bFY\d{2}|\d+M\d{2}|20\d{2}|1[01]\d年|\(\s*\d")

# 從指標名稱裡把「年度」抽出來，用來擋掉跨年度誤配（例如把去年 9M24 的 EPS
# 拿去跟今年 9M25 比）。西元兩位數→20xx；民國三位數(11X年)→+1911。
_YEAR_PATTERNS = [
    (re.compile(r"(?:\d[QH]|\d+M|FY)(\d{2})\b"), lambda d: 2000 + int(d)),  # 9M24 / 3Q25 / 1H25 / FY24
    (re.compile(r"(20\d{2})"), lambda d: int(d)),                            # 2024
    (re.compile(r"(1[01]\d)年"), lambda d: 1911 + int(d)),                    # 民國 115年 → 2026
]


def _target_year(period):
    m = re.search(r"20\d{2}", str(period))
    return int(m.group()) if m else None


def _name_year(name):
    """名稱裡明確標到的年度；沒標到回 None（代表是泛用的當期指標）。"""
    for pat, conv in _YEAR_PATTERNS:
        m = pat.search(name)
        if m:
            return conv(m.group(1))
    return None


def _expected_tokens(period):
    """某一季「年初至今累計」慣用的標記，用來在同年多個累計視窗中挑對的那個。
    例：2025Q3 的當期 YTD 是 9M25／3Q25，就別挑到同年的 1H25 或 1Q25。"""
    m = re.match(r"(20)(\d{2})Q([1-4])", str(period))
    if not m:
        return set()
    yy, q = m.group(2), int(m.group(3))
    months = q * 3
    toks = {f"{q}Q{yy}", f"{months}M{yy}", f"Q{q}"}
    if q == 2:
        toks.add(f"1H{yy}")
    if q == 4:
        toks.add(f"FY{yy}")
    return toks

# 直接挑名稱最短、最乾淨的那筆標準比率。
# score 越高越好：單位正確 > 命中偏好詞（金控層級）> 名稱短 > 不帶分期標籤 > 非累計
STANDARD_METRICS = [
    {
        "key": "ROE",
        "label": "股東權益報酬率 (ROE)",
        "type": "ratio",
        "unit": "%",
        "include": [r"ROE", r"股東權益報酬率"],
        # 子公司（銀行／人壽／投信／產險／證券／世華／創投）與成長率都不是「集團 ROE」
        "exclude": [r"成長", r"年增", r"銀行", r"人壽", r"投信", r"產險", r"證券", r"世華", r"創投"],
        "prefer": [r"金控"],
    },
    {
        "key": "ROA",
        "label": "資產報酬率 (ROA)",
        "type": "ratio",
        "unit": "%",
        "include": [r"ROA", r"資產報酬率"],
        "exclude": [r"成長", r"年增", r"銀行", r"人壽", r"投信", r"產險", r"證券", r"世華", r"創投"],
        "prefer": [r"金控"],
    },
    {
        "key": "NIM",
        "label": "淨利息收益率 (NIM)",
        "type": "ratio",
        "unit": "%",
        "include": [r"NIM", r"淨利息收益率", r"淨利差", r"Net interest margin"],
        # 含 SWAP／擬制／Pro-forma 是調整後版本，不是原始 NIM；季度快照另計
        "exclude": [r"SWAP", r"擬制", r"Pro-?forma", r"Avg", r"生息"],
        "prefer": [],
    },
    {
        "key": "CAR",
        "label": "資本適足率",
        "type": "ratio",
        "unit": "%",
        "include": [r"資本適足率"],
        # 只要集團／金控層級，排除銀行子公司與第一/二類資本、總計等細項
        "exclude": [r"銀行", r"第一類", r"第二類", r"人壽", r"產險", r"總計"],
        "prefer": [r"金控"],
    },
    {
        "key": "NPL",
        "label": "逾期放款比率",
        "type": "ratio",
        "unit": "%",
        "include": [r"逾期放款比率", r"逾放比", r"NPL Ratio"],
        "exclude": [r"房貸", r"覆蓋"],  # 房貸逾放比是單一產品；覆蓋率是另一個指標
        "prefer": [r"整體"],
    },
    {
        "key": "COVERAGE",
        "label": "備抵呆帳覆蓋率",
        "type": "ratio",
        "unit": "%",
        "include": [r"備抵呆帳覆蓋率", r"覆蓋率", r"Coverage ratio"],
        "exclude": [],
        "prefer": [r"備抵呆帳"],
    },
    {
        "key": "EPS",
        "label": "每股盈餘 (EPS)",
        "type": "per_share",
        "unit": "元",
        "include": [r"基本每股盈餘", r"每股盈餘", r"每股稅後盈餘", r"\bEPS\b"],
        # 淨值/股利/股本是「每股」但不是盈餘；稀釋、調整後、累計都不是「基本單季」EPS
        "exclude": [r"淨值", r"股利", r"股本", r"稀釋", r"調整後", r"累計"],
        "prefer": [r"基本每股盈餘"],
    },
]

# 衍生比率：資料無現成欄位，用兩個絕對金額相除。分子/分母走「逐字精準」比對，
# 且相除後單位相消，跨機構直接可比。
DERIVED_METRICS = [
    {
        "key": "DEBT_ASSET",
        "label": "資產負債率",
        "type": "ratio",
        "unit": "%",
        "numerator": "負債總計",
        "denominator": "資產總計",
        "scale": 100.0,  # 轉成百分比
    },
]


def _to_float(value):
    """把指標值轉成 float；多期字串（'14,319 (2Q25); 27,763'）或非數字回 None。"""
    s = str(value).strip()
    if ";" in s or "(" in s:  # 多期或帶註記的絕對金額——標準比率不該長這樣
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _score(name, unit, spec, expected):
    """名稱越乾淨、越像集團層級、視窗越吻合當期，分數越高。"""
    score = 0.0
    if unit and spec["unit"] in str(unit):
        score += 100
    if any(re.search(p, name) for p in spec.get("prefer", [])):
        score += 40
    if expected and any(tok in name for tok in expected):
        score += 30  # 命中當期 YTD 標記（如 2025Q3 的 9M25），優先於同年其他累計視窗
    if _PERIOD_TAG.search(name):
        score -= 25
    if is_cumulative_name(name):
        score -= 30
    score -= len(name) * 0.5  # 同分時挑短名字（通常最泛用）
    return score


def _pick(metrics, spec, period=None):
    """在一家公司某期的指標清單裡，挑出最符合這個標準比率定義的一筆。

    擋掉跨年度誤配：名稱明確標到的年度若跟所選期間的年度不同，直接跳過
    （例如所選是 2025Q3，就不拿名稱裡寫 9M24／2024 的去年數字來比）。
    """
    target_year = _target_year(period)
    expected = _expected_tokens(period)
    best, best_score = None, None
    for m in metrics:
        name = str(m["metric"])
        if not any(re.search(p, name) for p in spec["include"]):
            continue
        if any(re.search(p, name) for p in spec.get("exclude", [])):
            continue
        ny = _name_year(name)
        if ny is not None and target_year is not None and ny != target_year:
            continue  # 跨年度，跳過
        val = _to_float(m.get("value"))
        if val is None:
            continue
        s = _score(name, m.get("unit"), spec, expected)
        if best_score is None or s > best_score:
            best, best_score = {"name": name, "value": val, "unit": m.get("unit")}, s
    return best


def key_ratios(company, period):
    """單一機構某期的「標準關鍵比率」（只回 % 類，畫圖才會單位一致）。

    用標準比率字典挑出乾淨的那一筆，去掉重複與子公司變體
    （例如同時有「稅後資產報酬率(3M26)」「(2025)」時只留最合適的一個）。
    刻意排除 EPS——它單位是「元」，跟一堆百分比放同一軸會誤導。
    """
    ms = list_metrics(company, period)
    out = []
    for spec in STANDARD_METRICS:
        if spec["type"] != "ratio":  # 只要比率（%）；每股類（元）另計
            continue
        pick = _pick(ms, spec, period)
        if pick:
            out.append({"name": spec["label"], "value": round(pick["value"], 2), "unit": "%"})
    by = {m["metric"]: m for m in ms}
    for spec in DERIVED_METRICS:  # 衍生比率（資產負債率）也是 %
        v = _derive(by, spec)
        if v is not None:
            out.append({"name": spec["label"], "value": round(v, 2), "unit": "%"})
    return out


def align_standard(company_a, period_a, company_b, period_b):
    """回傳兩機構在標準比率定義下對齊的清單。

    每一列附上「兩邊各自實際命中的原始指標名稱」（matched_a / matched_b），
    讓前端能誠實揭露「這是用哪兩個欄位對齊的」——不是黑箱硬配。
    """
    ma = list_metrics(company_a, period_a)
    mb = list_metrics(company_b, period_b)
    rows = []

    for spec in STANDARD_METRICS:
        pa, pb = _pick(ma, spec, period_a), _pick(mb, spec, period_b)
        if not pa or not pb:
            continue
        rows.append({
            "key": spec["key"],
            "metric": spec["label"],
            "type": spec["type"],
            "unit": spec["unit"],
            "value_a": round(pa["value"], 4),
            "value_b": round(pb["value"], 4),
            "matched_a": pa["name"],
            "matched_b": pb["name"],
            "derived": False,
        })

    # 衍生比率
    da = {m["metric"]: m for m in ma}
    db = {m["metric"]: m for m in mb}
    for spec in DERIVED_METRICS:
        va = _derive(da, spec)
        vb = _derive(db, spec)
        if va is None or vb is None:
            continue
        rows.append({
            "key": spec["key"],
            "metric": spec["label"],
            "type": spec["type"],
            "unit": spec["unit"],
            "value_a": round(va, 4),
            "value_b": round(vb, 4),
            "matched_a": f'{spec["numerator"]} ÷ {spec["denominator"]}',
            "matched_b": f'{spec["numerator"]} ÷ {spec["denominator"]}',
            "derived": True,
        })

    return rows


def _derive(by_name, spec):
    num = by_name.get(spec["numerator"])
    den = by_name.get(spec["denominator"])
    if not num or not den:
        return None
    n, d = _to_float(num.get("value")), _to_float(den.get("value"))
    if n is None or d is None or d == 0:
        return None
    return n / d * spec.get("scale", 1.0)


if __name__ == "__main__":
    import io
    import sys
    from graph_rag import list_companies, list_periods

    out = io.open("scratch_standard_test.txt", "w", encoding="utf-8")
    companies = list_companies()
    # 每家挑最後一個「像季度」的期間來看挑到什麼
    for c in companies:
        ps = list_periods(c)
        p = ps[-1]
        out.write(f"===== {c} | {p} =====\n")
        ms = list_metrics(c, p)
        for spec in STANDARD_METRICS:
            pick = _pick(ms, spec, p)
            out.write(f'  {spec["label"]:20s} -> {pick["name"] + "  = " + str(pick["value"]) + (pick["unit"] or "") if pick else "（無）"}\n')
        for spec in DERIVED_METRICS:
            by = {m["metric"]: m for m in ms}
            v = _derive(by, spec)
            out.write(f'  {spec["label"]:20s} -> {round(v,2) if v is not None else "（無）"}%\n')
    # 實際兩兩對齊
    out.write("\n\n########## 兩兩對齊結果 ##########\n")
    for i in range(len(companies)):
        for j in range(i + 1, len(companies)):
            a, b = companies[i], companies[j]
            pa, pb = list_periods(a)[-1], list_periods(b)[-1]
            out.write(f"\n--- {a} {pa}  vs  {b} {pb} ---\n")
            for r in align_standard(a, pa, b, pb):
                tag = "衍生" if r["derived"] else "字典"
                out.write(f'  [{tag}] {r["metric"]}: {r["value_a"]} vs {r["value_b"]} {r["unit"]}\n')
                out.write(f'         A命中「{r["matched_a"]}」 / B命中「{r["matched_b"]}」\n')
    out.close()
    print("done")
