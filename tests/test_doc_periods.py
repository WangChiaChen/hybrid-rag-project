"""年報等「只有敘述、沒有指標」的期間，不能混進儀表板的期間清單。

年報只索引語意段落、沒抽指標。若讓它出現在 /api/companies 的 periods 裡，
儀表板選到它會 404（list_metrics 是空的）。所以獨立成 doc_periods，只有問答頁吃。
"""
import api
from vector_rag import index_narrative, delete_by_source


def test_年報期間不進指標期間清單(temp_metrics):
    co = "台北年報測試金控"
    temp_metrics(co, "稅後淨利", {"2026Q1": "100"}, unit="百萬元")
    try:
        index_narrative(f"{co}_yr_1",
                        "致股東報告書：本年度獲利成長，財富管理業務表現亮眼。",
                        {"source": f"{co} 2025年報", "page": 4})
        row = next(c for c in api.companies() if c["name"] == co)
        assert "2025年報" not in row["periods"], "年報不該進儀表板期間（會 404）"
        assert "2025年報" in row["doc_periods"], "年報應該出現在問答頁可鎖的 doc_periods"
        # 指標期間仍在
        assert "2026Q1" in row["periods"]
    finally:
        delete_by_source(f"{co} 2025年報")


def test_法說會錄音標籤不算期間(temp_metrics):
    """「X 法說會錄音」是段落來源標籤，不是期間，不該冒充成可鎖期間。"""
    co = "台北錄音測試金控"
    temp_metrics(co, "稅後淨利", {"2026Q1": "100"}, unit="百萬元")
    try:
        index_narrative(f"{co}_rec_1", "經理人說明發債計畫。",
                        {"source": f"{co} 法說會錄音", "page": 1})
        row = next(c for c in api.companies() if c["name"] == co)
        assert not any("法說會錄音" in p for p in row["doc_periods"])
    finally:
        delete_by_source(f"{co} 法說會錄音")


def test_年報只有語意段落也要出現在資料來源總覽(temp_metrics):
    """年報沒抽指標、只有語意段落，原本被資料來源總覽整個漏掉——但上方統計卻把它算進去，
    兩邊對不上。年報這種 doc_period 也要出現在明細（指標 0、語意段落 N）。"""
    co = "台北來源測試金控"
    temp_metrics(co, "稅後淨利", {"2026Q1": "100"}, unit="百萬元")
    try:
        index_narrative(f"{co}_yr_src_1", "致股東報告書：本年度營運穩健。",
                        {"source": f"{co} 2025年報", "page": 4})
        rows = [r for r in api.sources()["rows"] if r["company"] == co]
        yr = next((r for r in rows if r["period"] == "2025年報"), None)
        assert yr is not None, "年報（只有語意段落）也該出現在資料來源總覽"
        assert yr["metrics"] == 0 and yr["narratives"] >= 1
        assert any(r["period"] == "2026Q1" and r["metrics"] >= 1 for r in rows)
    finally:
        delete_by_source(f"{co} 2025年報")


def test_問到年報自動搜年報不用手動鎖(temp_metrics):
    """年報收在獨立 doc_period，照當季過濾就搜不到。問句提到年報／治理／永續時，
    自動改搜年報，使用者不必手動把期間鎖成「2025年報」。"""
    from agent_router import _annual_report_period
    co = "台北語意路由金控"   # 名稱不含「年報」，免得公司名本身觸發偵測
    temp_metrics(co, "稅後淨利", {"2026Q1": "100"}, unit="百萬元")
    try:
        index_narrative(f"{co}_ar_1", "致股東報告書：本年度營運穩健、公司治理健全。",
                        {"source": f"{co} 2025年報", "page": 1})
        assert _annual_report_period(co, f"{co} 2025年報重點？") == "2025年報"
        assert _annual_report_period(co, f"{co}的公司治理如何？") == "2025年報"   # 年報獨有主題也算
        assert _annual_report_period(co, f"{co} 2026Q1 ROE？") is None          # 季度題不受影響
    finally:
        delete_by_source(f"{co} 2025年報")


def test_沒收年報的公司問年報不亂鎖(temp_metrics):
    """該公司根本沒收年報時，就算問到「年報」也回 None，走原本流程、不硬鎖到空的期間。"""
    from agent_router import _annual_report_period
    co = "台北未收金控"   # 名稱不含「年報」
    temp_metrics(co, "稅後淨利", {"2026Q1": "100"}, unit="百萬元")
    assert _annual_report_period(co, f"{co} 年報重點？") is None
