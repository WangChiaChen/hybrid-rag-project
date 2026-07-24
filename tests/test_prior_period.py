"""第六道防護：EAP 引用的是「別期」的數字。

實測 EAP 被問國泰 2026Q1 的 EPS，開頭就說「為 2.18 元」——那是 1Q25 的，
2026Q1 是 2.15。前面每一道都攔不住：本地確實有 2.18 這個數字（只是屬於去年），
所以既不算數字不符、也不算查無資料；它有回答、有數字，敘述型與無效比較都不適用。

這道防護能講出最有用的那句話：「這是 1Q25 的數字，2026Q1 是 2.15」。
"""
import pytest

from api import check_prior_period_values

CO = "台北測試金控"
P = "2026Q1"


@pytest.fixture
def 兩期並存(temp_metrics):
    """模仿金控簡報：去年同期與本期躺在同一個資料夾裡。"""
    temp_metrics(CO, "每股盈餘 (1Q25)", {P: "2.18"}, unit="元")
    temp_metrics(CO, "1Q26每股盈餘", {P: "2.15"}, unit="元")


def test_引用去年數字要抓到(兩期並存):
    ans = f"{CO} 2026Q1 的每股盈餘為 2.18 元。"
    out = check_prior_period_values(ans, CO, P)
    assert len(out) == 1
    assert "2.18" in out[0]["quoted_value"]
    assert "1Q25" in out[0]["quoted_period"]
    assert "2.15" in out[0]["current_value"], "要講得出本期正確值是多少"


def test_引用本期數字不該報(兩期並存):
    assert check_prior_period_values(f"{CO} 2026Q1 的每股盈餘為 2.15 元。", CO, P) == []


def test_數字相同但沒在講該指標就不報(兩期並存):
    """碰巧出現相同數字不算——答案要真的在講這個指標。"""
    ans = f"{CO} 的分行數為 2.18 百家。"
    assert check_prior_period_values(ans, CO, P) == []


def test_數字被包在別的數字裡不算(兩期並存):
    """2.18 不該在 12.185 裡被認出來。"""
    ans = f"{CO} 2026Q1 的每股盈餘相關比率為 12.185%。"
    assert check_prior_period_values(ans, CO, P) == []


def test_沒有本期版本可比就不報(temp_metrics):
    """只有去年那筆時，無從斷定 EAP 引錯期間——本期資料可能根本還沒收錄。"""
    temp_metrics(CO, "每股盈餘 (1Q25)", {P: "2.18"}, unit="元")
    assert check_prior_period_values(f"{CO} 每股盈餘 2.18 元。", CO, P) == []


def test_兩期同值就不吵(temp_metrics):
    """值一樣時講哪一期都不會誤導，報了只是噪音。"""
    temp_metrics(CO, "每股盈餘 (1Q25)", {P: "2.15"}, unit="元")
    temp_metrics(CO, "1Q26每股盈餘", {P: "2.15"}, unit="元")
    assert check_prior_period_values(f"{CO} 每股盈餘 2.15 元。", CO, P) == []


def test_同一指標只報一次(兩期並存):
    ans = f"{CO} 每股盈餘為 2.18 元，重申每股盈餘 2.18 元。"
    assert len(check_prior_period_values(ans, CO, P)) == 1
