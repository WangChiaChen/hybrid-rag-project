"""跨公司比較題的期間挑選。

實測踩過：問「中信2026Q1和玉山2026Q1股東權益報酬率(ROE)比較」，問答頁答
「資料中未提供玉山金控的 ROE」，但跨機構比較頁明明查得到 14.43。原因是被比較的
公司一律用「最新的期間」＝2026Q1財報，那期只有資產負債表、沒有 ROE，於是被判成查無。
主要公司選了哪期，其他公司優先用同一期（比較幾乎都是同期跨公司）。

用假公司測，不依賴知識庫實際內容。
"""
from agent_router import _pick_period_for_company


def test_選定公司用使用者選的期(temp_metrics):
    assert _pick_period_for_company("A金控", "A金控", "2025Q3") == "2025Q3"


def test_其他公司優先用同一期而非最新財報期(temp_metrics):
    """被比較的公司有 2026Q1（含 ROE）也有較新的 2026Q1財報（只有資產負債表）。
    主要公司選 2026Q1 時，這家也該用 2026Q1，不能掉到只有資產負債表的財報期。"""
    co = "台北比較測試金控"
    temp_metrics(co, "股東權益報酬率(ROE)", {"2026Q1": "14.43"}, unit="%")
    temp_metrics(co, "資產總計", {"2026Q1財報": "999"}, unit="百萬元")
    assert _pick_period_for_company(co, "主要金控", "2026Q1") == "2026Q1"


def test_同名期不存在才退回最新期(temp_metrics):
    """主要公司選 2026Q1，但這家根本沒有 2026Q1 → 退回它最新的期。"""
    co = "台北比較測試金控B"
    temp_metrics(co, "股東權益報酬率(ROE)", {"2025Q4": "10.0"}, unit="%")
    temp_metrics(co, "資產總計", {"2026Q1財報": "999"}, unit="百萬元")
    assert _pick_period_for_company(co, "主要金控", "2026Q1") == "2026Q1財報"


def test_沒指定期間就退回最新期(temp_metrics):
    co = "台北比較測試金控C"
    temp_metrics(co, "股東權益報酬率(ROE)", {"2026Q1": "12.0"}, unit="%")
    assert _pick_period_for_company(co, "主要金控", "") == "2026Q1"
