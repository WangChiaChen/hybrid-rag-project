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
