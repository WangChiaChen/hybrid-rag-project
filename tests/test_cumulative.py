"""累計值判定與跨期計算。

這是整個系統最容易出假數字的地方：累計值直接拿去算 QoQ，會得到「業績暴跌 71%」
這種看起來很像真的、實際上只是累計月數變少的結果。每個測試都對應一段實測過的資料。
"""
import pytest

from api import check_cumulative_comparison
from graph_rag import calc_change, is_cumulative, pins_own_period
from metric_alignment import is_cumulative_name, norm_metric_name


class Test名稱標記:
    def test_中文累計字樣(self):
        assert is_cumulative_name("累計稅後淨利")
        assert is_cumulative_name("上半年獲利")
        assert is_cumulative_name("前三季 EPS")

    def test_月數與半年度縮寫(self):
        assert is_cumulative_name("合併稅後淨利 (9M25)")   # 前三季累計
        assert is_cumulative_name("3M26每股稅後盈餘")      # 首季累計
        assert is_cumulative_name("稅後淨利 (1H25)")       # 上半年
        assert is_cumulative_name("FY24 稅後淨利")         # 全年

    def test_單季名稱不該被判成累計(self):
        assert not is_cumulative_name("手續費淨收益")
        assert not is_cumulative_name("1Q26 稅後淨利")     # 單季，不是 N 個月累計


class Test數列啟發式:
    def test_名稱看不出來時靠逐季遞增認出來(self, temp_metrics):
        """玉山簡報的 EPS 就叫「EPS」，名稱完全看不出是累計，
        但 2025 四季是 0.55 → 1.05 → 1.62 → 2.12，一路累加。"""
        c = temp_metrics("＿測試甲＿", "EPS",
                         {"2025Q1": "0.55", "2025Q2": "1.05",
                          "2025Q3": "1.62", "2025Q4": "2.12"})
        assert is_cumulative(c, "EPS")

    def test_單季數列有上有下不算累計(self, temp_metrics):
        c = temp_metrics("＿測試乙＿", "手續費淨收益",
                         {"2025Q1": "100", "2025Q2": "95", "2025Q3": "110"})
        assert not is_cumulative(c, "手續費淨收益")

    def test_比率連三季走高不算累計(self, temp_metrics):
        """中信 NIM 2025 是 1.49→1.50→1.53→1.64，連三季遞增但那是經營表現，
        不是在累加。一律當累計會把比率正常的季變化全擋掉（實測誤擋 14 筆）。"""
        c = temp_metrics("＿測試丙＿", "NIM",
                         {"2025Q1": "1.49", "2025Q2": "1.50",
                          "2025Q3": "1.53", "2025Q4": "1.64"})
        assert not is_cumulative(c, "NIM")

    def test_不足三季不下判斷(self, temp_metrics):
        c = temp_metrics("＿測試丁＿", "稅後淨利", {"2025Q1": "10", "2025Q2": "20"})
        assert not is_cumulative(c, "稅後淨利")


class Test手足繼承:
    def test_同一筆數字在別期叫法標得出累計就跟著算累計(self, temp_metrics):
        """中信的 EPS 每季名稱都不同（EPS／每股稅後盈餘／9M25每股盈餘），
        用完全同名湊數列湊不到三季。但「3M26每股稅後盈餘」是標得出來的，
        去掉期別標籤後同名的手足既然是累計，這筆就是累計。"""
        c = "＿測試戊＿"
        temp_metrics(c, "每股稅後盈餘", {"2026Q1": "1.18"})
        temp_metrics(c, "3M26每股稅後盈餘", {"2025Q4": "1.18"})
        assert norm_metric_name("3M26每股稅後盈餘") == "每股稅後盈餘"
        assert is_cumulative(c, "每股稅後盈餘")

    def test_比率不繼承(self, temp_metrics):
        """逾放比、資本適足率這些本來就該逐季比，繼承會把正常的季變化也擋掉。"""
        c = "＿測試己＿"
        temp_metrics(c, "資本適足率", {"2026Q1": "14.2"})
        temp_metrics(c, "9M25資本適足率", {"2025Q3": "13.9"})
        assert not is_cumulative(c, "資本適足率")


class Test跨期計算:
    def test_一般指標正常算QoQ(self, temp_metrics):
        c = temp_metrics("＿測試庚＿", "手續費淨收益",
                         {"2026Q1": "8054", "2025Q4": "7805"})
        assert calc_change(c, "手續費淨收益", "2026Q1", "2025Q4") == 3.19

    def test_累計值跨季不給數字(self, temp_metrics):
        """2025Q4 的全年 4.08 對上 2026Q1 首季 1.18 會算出 -71%，
        那不是業績衰退，只是累計月數從 12 個月變回 3 個月。寧可不給也不要給錯的。"""
        c = temp_metrics("＿測試辛＿", "累計EPS", {"2025Q4": "4.08", "2026Q1": "1.18"})
        assert calc_change(c, "累計EPS", "2026Q1", "2025Q4") is None

    def test_累計值同一季跨年度可以比(self, temp_metrics):
        """2025Q1 vs 2026Q1 都是前三個月累計，基準一致，這種才有意義。"""
        c = temp_metrics("＿測試壬＿", "累計EPS", {"2025Q1": "1.00", "2026Q1": "1.18"})
        assert calc_change(c, "累計EPS", "2026Q1", "2025Q1") == 18.0

    def test_名稱釘死期別的快照不跨期比(self, temp_metrics):
        """國泰的「SME放款 (1Q25)」在兩份簡報裡都是 340.0——同一個數字被收錄兩次，
        比出來的 0.0% 會被讀成「本季持平」，其實根本沒有本季。"""
        c = temp_metrics("＿測試癸＿", "SME放款 (1Q25)",
                         {"2025Q1": "340.0", "2026Q1": "340.0"})
        assert pins_own_period("SME放款 (1Q25)")
        assert calc_change(c, "SME放款 (1Q25)", "2026Q1", "2025Q1") is None

    def test_缺任一期就不算(self, temp_metrics):
        c = temp_metrics("＿測試子＿", "稅後淨利", {"2026Q1": "100"})
        assert calc_change(c, "稅後淨利", "2026Q1", "2025Q4") is None

    def test_除以零不炸(self, temp_metrics):
        c = temp_metrics("＿測試丑＿", "稅後淨利", {"2025Q4": "0", "2026Q1": "100"})
        assert calc_change(c, "稅後淨利", "2026Q1", "2025Q4") is None

    def test_非數字不炸(self, temp_metrics):
        c = temp_metrics("＿測試寅＿", "備註", {"2025Q4": "N/A", "2026Q1": "無"})
        assert calc_change(c, "備註", "2026Q1", "2025Q4") is None


class Test第五道防護_無效的跨期比較:
    """數字都對，但「拿來相比」本身無效。

    實測 EAP 回答：「2026Q1 每股盈餘 1.18 元，較 2025Q4 的 4.08 元減少 2.90 元，
    減幅約 71%」——1.18 和 4.08 各自都正確、交叉驗證也過，但 4.08 是 2025 全年累計、
    1.18 是新年度首季。那個 -71% 是重新起算，不是衰退。

    前四道防護沒有一道攔得住：有回答（不觸發補強）、數字都對（交叉驗證比不出差異）、
    驗得過（不觸發無法驗證）、有數字（不是敘述型）。
    """

    CO = "台北測試金控"

    @pytest.fixture
    def 累計型指標(self, temp_metrics):
        # 刻意不在名稱裡寫「累計」——真實資料就是這樣（中信叫「每股稅後盈餘」），
        # 累計身分只能從 2025 四季逐季遞增的數列認出來。
        # 而且標準比率字典的 EPS 定義明文 exclude「累計」，名稱帶了反而配不上。
        temp_metrics(self.CO, "每股稅後盈餘",
                     {"2025Q1": "0.98", "2025Q2": "2.10", "2025Q3": "3.20",
                      "2025Q4": "4.08", "2026Q1": "1.18"}, unit="元")
        temp_metrics(self.CO, "淨利息收益率",
                     {"2025Q4": "1.64", "2026Q1": "1.68"}, unit="%")

    def test_全年累計比首季要警告(self, 累計型指標):
        ans = ("台北測試金控 2026Q1 的每股盈餘為 1.18 元，"
               "較 2025Q4 的 4.08 元減少 2.90 元，減幅約 71%。")
        out = check_cumulative_comparison(ans, self.CO, "2026Q1")
        assert len(out) == 1
        assert "2025Q4" in out[0]["periods"] and "2026Q1" in out[0]["periods"]

    def test_同一季跨年度不警告(self, 累計型指標):
        """2025Q1 vs 2026Q1 都是前三個月累計，基準一致，這種比較本來就有效。"""
        ans = "台北測試金控 2026Q1 每股盈餘 1.18 元，較 2025Q1 的 0.98 元成長 20%。"
        assert check_cumulative_comparison(ans, self.CO, "2026Q1") == []

    def test_只並列數字沒宣稱變化就不警告(self, 累計型指標):
        """把兩期數字列出來並沒有錯，錯的是宣稱「因此下滑了 71%」。"""
        ans = "台北測試金控 2026Q1 每股盈餘為 1.18 元；2025Q4 為 4.08 元。"
        assert check_cumulative_comparison(ans, self.CO, "2026Q1") == []

    def test_比率跨季比較不警告(self, 累計型指標):
        """逾放比、NIM 這些本來就該逐季比，警告了反而是誤報。"""
        ans = ("台北測試金控 2026Q1 的淨利息收益率為 1.68%，"
               "較 2025Q4 的 1.64% 上升 0.04 個百分點。")
        assert check_cumulative_comparison(ans, self.CO, "2026Q1") == []

    def test_公司名不會被當成指標名(self, 累計型指標):
        """本地有「中信金控」這種以公司為名的指標，拿它當警告標題只會讓人困惑。"""
        ans = ("台北測試金控 2026Q1 的每股盈餘為 1.18 元，較 2025Q4 的 4.08 元下滑。")
        out = check_cumulative_comparison(ans, self.CO, "2026Q1")
        assert all(w["metric"] != self.CO for w in out)

    def test_期間寫法都認得(self):
        from api import _periods_in
        got = _periods_in("2026Q1 vs 2025年第4季 vs 1Q26 vs 2025年第一季")
        assert (2026, 1) in got and (2025, 4) in got and (2025, 1) in got
