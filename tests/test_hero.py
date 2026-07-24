"""門面指標（首頁大字）的選擇。

這是唯一「不用問任何問題就會誤導人」的地方——使用者一打開儀表板就看到它。

實測踩過的坑：國泰 2026Q1 的首頁大字顯示「稅後淨利 32.2 十億元」，
那是 **1Q25** 的數字；真正的 2026Q1 是 31.7，被擠到下面的列表裡。
兩個原因疊在一起：
  · 當期的叫「1Q26稅後淨利」，開頭不是「稅後淨利」，名稱比對不中
  · 去年的名稱乾淨（就叫「稅後淨利」），反而中選
"""
import pytest

import api
from metric_alignment import is_hero_metric

CO = "台北測試金控"


class Test名稱判定:
    def test_帶期別標籤的當期指標也算門面(self):
        """「1Q26稅後淨利」是當期的門面數字，不能因為開頭是期別就落選。"""
        assert is_hero_metric("1Q26稅後淨利")
        assert is_hero_metric("3M26合併稅後淨利")
        assert is_hero_metric("稅後淨利")

    def test_非門面指標不受影響(self):
        assert not is_hero_metric("手續費淨收益")
        assert not is_hero_metric("逾期放款比率")


class Test別期的不可當門面:
    def test_去年同期不當門面(self, temp_metrics):
        temp_metrics(CO, "稅後淨利 (1Q25)", {"2026Q1": "32.2"}, unit="十億元")
        temp_metrics(CO, "1Q26稅後淨利", {"2026Q1": "31.7"}, unit="十億元")
        r = api.metrics(company=CO, period="2026Q1")
        heroes = [m["metric"] for m in r["metrics"] if m["hero"]]
        assert heroes == ["1Q26稅後淨利"], f"門面應該只有當期那筆，實得 {heroes}"

    def test_門面數字是當期的值(self, temp_metrics):
        temp_metrics(CO, "稅後淨利 (1Q25)", {"2026Q1": "32.2"}, unit="十億元")
        temp_metrics(CO, "1Q26稅後淨利", {"2026Q1": "31.7"}, unit="十億元")
        hero = next(m for m in api.metrics(company=CO, period="2026Q1")["metrics"] if m["hero"])
        assert hero["value"] == "31.7"


class Test同名去重:
    def test_同一個數字不出現兩張門面卡(self, temp_metrics):
        """中信同時有「每股稅後盈餘」與「3M26每股稅後盈餘」，都是 1.18。
        兩張卡放同一個數字會讓人以為是兩件事。"""
        temp_metrics(CO, "每股稅後盈餘", {"2026Q1": "1.18"}, unit="元")
        temp_metrics(CO, "3M26每股稅後盈餘", {"2026Q1": "1.18"}, unit="元")
        heroes = [m["metric"] for m in api.metrics(company=CO, period="2026Q1")["metrics"]
                  if m["hero"]]
        assert len(heroes) == 1, f"同名只該留一張，實得 {heroes}"
        assert heroes[0] == "每股稅後盈餘", "取顯示名稱較短的那個"

    def test_不同指標各自保留(self, temp_metrics):
        temp_metrics(CO, "稅後淨利", {"2026Q1": "100"}, unit="百萬元")
        temp_metrics(CO, "每股稅後盈餘", {"2026Q1": "1.18"}, unit="元")
        heroes = {m["metric"] for m in api.metrics(company=CO, period="2026Q1")["metrics"]
                  if m["hero"]}
        assert heroes == {"稅後淨利", "每股稅後盈餘"}
