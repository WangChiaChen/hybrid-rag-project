"""跨公司比較題的期間挑選。

實測踩過：問「中信2026Q1和玉山2026Q1股東權益報酬率(ROE)比較」，問答頁答
「資料中未提供玉山金控的 ROE」，但跨機構比較頁明明查得到 14.43。原因是被比較的
公司一律用「最新的期間」＝2026Q1財報，那期只有資產負債表、沒有 ROE，於是被判成查無。
主要公司選了哪期，其他公司優先用同一期（比較幾乎都是同期跨公司）。

用假公司測，不依賴知識庫實際內容。
"""
from agent_router import (
    _find_metric,
    _pick_period_for_company,
    _subsidiary_breakdown,
    _value_in_period,
)


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


class Test當期命名變體不退舊期:
    """跨期命名不一致：當期群組稅後淨利叫「1Q26稅後淨利」、舊期叫「稅後淨利」。
    逐字比對會在當期落空、退到舊期拿舊數字（實測問 2026Q1 卻回 2025Q4 的 107.6）。
    改用正規化名補救，且排除當期資料夾裡釘死去年同期的對照值。
    """

    def test_當期帶前綴的變體找得到不退舊期(self, temp_metrics):
        co = "台北命名測試金控"
        temp_metrics(co, "1Q26稅後淨利", {"2026Q1": "31.7"}, unit="十億元")
        temp_metrics(co, "稅後淨利", {"2025Q4": "107.6"}, unit="十億元")
        assert _value_in_period(co, "稅後淨利", "2026Q1") == "31.7"
        m = _find_metric(co, "稅後淨利", "2026Q1")
        assert m["metric"] == "1Q26稅後淨利" and m["unit"] == "十億元"

    def test_當期的去年同期對照不被誤選(self, temp_metrics):
        co = "台北命名測試金控B"
        temp_metrics(co, "1Q26稅後淨利", {"2026Q1": "31.7"}, unit="十億元")
        temp_metrics(co, "稅後淨利 (1Q25)", {"2026Q1": "32.2"}, unit="十億元")
        m = _find_metric(co, "稅後淨利", "2026Q1")
        assert m["metric"] == "1Q26稅後淨利", f"該選當期不選去年對照，got {m}"


class Test各子公司分項:
    """「國泰各子公司稅後淨利」是要分項列出，不是回一個集團總數。"""

    def test_列出各子公司分項且排除集團與去年(self, temp_metrics):
        co = "台北分項測試金控"
        temp_metrics(co, "台北富華銀行稅後淨利", {"2026Q1": "166"}, unit="億元")
        temp_metrics(co, "台北人壽稅後淨利", {"2026Q1": "78"}, unit="億元")
        temp_metrics(co, "稅後淨利", {"2026Q1": "244"}, unit="億元")            # 集團，不該列
        temp_metrics(co, "台北富華銀行 1Q25 稅後淨利", {"2026Q1": "150"}, unit="億元")  # 去年對照
        ans = _subsidiary_breakdown(f"{co}各子公司稅後淨利", co, "2026Q1")
        assert ans and "台北富華銀行" in ans and "台北人壽" in ans
        assert "166" in ans and "78" in ans
        assert "150" not in ans, "去年同期對照不該混進分項"

    def test_同一子公司多變體去重(self, temp_metrics):
        co = "台北分項測試金控B"
        temp_metrics(co, "台北富華銀行稅後淨利", {"2026Q1": "16586"}, unit="百萬元")
        temp_metrics(co, "台北富華銀行第一季稅後淨利", {"2026Q1": "166"}, unit="億元")
        ans = _subsidiary_breakdown("各子公司稅後淨利", co, "2026Q1")
        assert ans.count("台北富華銀行") == 1, f"同一家只該出現一次：{ans}"

    def test_非分項題回None(self, temp_metrics):
        co = "台北分項測試金控C"
        temp_metrics(co, "台北富華銀行稅後淨利", {"2026Q1": "166"}, unit="億元")
        assert _subsidiary_breakdown("台北富華銀行稅後淨利", co, "2026Q1") is None
