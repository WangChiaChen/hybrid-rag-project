"""calc_change 跨期單位漂移要先換算，別直接相除。

實測：玉山「放款總額」2025Q1 存成兆元、2026Q1 存成億元；「淨利息收入」2025 是百萬元、
2026Q1 變億元。不換算就相除會差 10~100 倍，算出「-99.99%」這種假暴跌。
"""
from graph_rag import calc_change


def test_跨期金額單位漂移要換算(temp_metrics):
    co = "台北單位漂移測試金控"
    # 2兆元 = 20,000 億元；2026Q1 = 23,000 億元 → 同季 YoY +15%
    temp_metrics(co, "放款總額", {"2025Q1": "2"}, unit="兆元")
    temp_metrics(co, "放款總額", {"2026Q1": "23000"}, unit="億元")
    assert calc_change(co, "放款總額", "2026Q1", "2025Q1") == 15.0


def test_同單位照舊直接算(temp_metrics):
    co = "台北單位漂移測試金控B"
    temp_metrics(co, "淨利息收入", {"2025Q1": "100"}, unit="億元")
    temp_metrics(co, "淨利息收入", {"2026Q1": "110"}, unit="億元")
    assert calc_change(co, "淨利息收入", "2026Q1", "2025Q1") == 10.0


def test_金額對非金額不可比回None(temp_metrics):
    """一邊是金額量級、一邊不是（如「億元」對「家」）→ 不可比，寧可不給。"""
    co = "台北單位漂移測試金控C"
    temp_metrics(co, "某指標", {"2025Q1": "100"}, unit="億元")
    temp_metrics(co, "某指標", {"2026Q1": "5"}, unit="家")
    assert calc_change(co, "某指標", "2026Q1", "2025Q1") is None


def test_兩邊都非金額量級視為同單位照算(temp_metrics):
    """「家」對「個」都是量詞、同一件事，拼寫不同不該擋。"""
    co = "台北單位漂移測試金控D"
    temp_metrics(co, "分行數", {"2025Q1": "100"}, unit="家")
    temp_metrics(co, "分行數", {"2026Q1": "110"}, unit="個")
    assert calc_change(co, "分行數", "2026Q1", "2025Q1") == 10.0
