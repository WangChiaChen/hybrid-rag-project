"""交叉驗證的層級與期別判斷。

這是整個系統對外最顯眼的防護——畫面上會跳紅框說「與本地知識庫數字不一致」。
它誤報的代價比漏報更高：使用者看到四則紅字警告正確的答案，之後連真的警告也不會信了。

實測踩過的坑：EAP 回答「中信銀行第三季稅後淨利 143 億、前三季累計 421 億」，
四個子公司數字全部被拿去跟「金控合併 249 億」比，報出 4 則不一致——
而本地明明就有「中信銀行第三季稅後淨利 = 143 億元」，EAP 一個字都沒錯。

用假公司測，不依賴知識庫實際內容（真實資料會隨著重新匯入而變）。
"""
import pytest

from api import cross_check_metrics

COMPANY = "台北測試金控"
PERIOD = "2025Q3"


@pytest.fixture
def 集團與子公司資料(temp_metrics):
    """一組有層級、也有單季／累計之分的資料，模仿真實簡報的結構。"""
    temp_metrics(COMPANY, "台北測試金控第三季稅後淨利", {PERIOD: "249"}, unit="億元")
    temp_metrics(COMPANY, "台北測試銀行第三季稅後淨利", {PERIOD: "143"}, unit="億元")
    temp_metrics(COMPANY, "台北測試銀行前三季稅後淨利", {PERIOD: "421"}, unit="億元")
    # 同一家子公司還有一筆沒標期別、單位也不同的（真實資料就長這樣）
    temp_metrics(COMPANY, "台北測試銀行稅後淨利", {PERIOD: "42057"}, unit="百萬元")
    return COMPANY


def 表格(單季值, 累計值=None):
    cum = f" {累計值} |" if 累計值 else ""
    head = "| 子公司 | 2025Q3單季稅後淨利 |" + (" 前三季累計稅後淨利 |" if 累計值 else "")
    sep = "|---|---|" + ("---|" if 累計值 else "")
    return f"{head}\n{sep}\n| 台北測試銀行 | {單季值} |{cum}\n"


class Test子公司不可跟集團比:
    def test_子公司數字正確就不該報(self, 集團與子公司資料):
        """本地有一模一樣的子公司數字，報出不一致就是誤報。"""
        gaps, checked = cross_check_metrics(表格("143億元", "421億元"), COMPANY, PERIOD)
        assert checked >= 2, "應該真的比對到子公司的數字，而不是略過不比"
        assert gaps == [], f"不該有任何不一致，卻報了：{gaps}"

    def test_子公司數字錯誤仍要抓到(self, 集團與子公司資料):
        """只求不誤報而把功能弄鈍，比誤報更糟。"""
        gaps, _ = cross_check_metrics(表格("243億元"), COMPANY, PERIOD)
        assert len(gaps) == 1
        assert "台北測試銀行" in gaps[0]["company"]
        assert "143" in gaps[0]["local_value"], "要跟子公司的 143 億比，不是集團的 249 億"

    def test_警告要標子公司而不是金控(self, 集團與子公司資料):
        """原本四則警告全寫「中信金控 稅後淨利」，實際講的是兩家子公司——
        標錯對象會讓使用者去查錯的數字。"""
        gaps, _ = cross_check_metrics(表格("243億元"), COMPANY, PERIOD)
        assert gaps[0]["company"] != COMPANY

    def test_本地沒有該子公司的數字就跳過(self, temp_metrics):
        """找不到對應的子公司數字時，寧可不比，也不要退回集團層級硬比。"""
        temp_metrics(COMPANY, "台北測試金控第三季稅後淨利", {PERIOD: "249"}, unit="億元")
        gaps, checked = cross_check_metrics(表格("143億元"), COMPANY, PERIOD)
        assert gaps == []
        assert checked == 0, "本地沒有子公司的數字，不該拿集團的來充數"


class Test單季與累計不可混比:
    def test_累計值被標成單季要抓到(self, 集團與子公司資料):
        """421 是前三季累計，標成單季就是錯的（單季是 143）。
        這裡容易假通過：本地還有一筆沒標期別的「台北測試銀行稅後淨利 42,057 百萬元」
        換算後正好約等於 421 億，挑到它就會因為數字對得上而放行。"""
        gaps, _ = cross_check_metrics(表格("421億元"), COMPANY, PERIOD)
        assert len(gaps) == 1
        assert "143" in gaps[0]["local_value"]

    def test_累計欄要跟累計值比(self, 集團與子公司資料):
        gaps, _ = cross_check_metrics(表格("143億元", "999億元"), COMPANY, PERIOD)
        assert len(gaps) == 1
        assert "421" in gaps[0]["local_value"], "累計欄應該跟前三季的 421 億比"


class Test單位寫法:
    """讀不到單位就整張表都比不了，畫面上還會顯示「無法驗證」——
    最該展示防護力的跨公司比較題反而什麼都沒做。以下寫法都是 EAP 實際吐過的。"""

    @pytest.fixture
    def 金控資料(self, temp_metrics):
        temp_metrics(COMPANY, "台北測試金控合併稅後淨利", {"2026Q1": "23104"}, unit="百萬元")

    def test_括號裡夾雜其他字(self, 金控資料):
        """「（億元新台幣）」——原本要求括號裡剛好只有單位，這種就漏掉。"""
        ans = (f"| 公司 | 2026Q1 合併稅後淨利（億元新台幣） |\n|---|---|\n"
               f"| {COMPANY} | 131.04 |\n")
        gaps, checked = cross_check_metrics(ans, COMPANY, "2026Q1")
        assert checked == 1, "應該讀得出單位並完成比對"
        assert len(gaps) == 1, "131.04 億 ≠ 23,104 百萬，該報出來"

    def test_單位寫在表格上方那一行(self, 金控資料):
        """「…如下（單位：億元）：」——單位根本不在表頭裡。"""
        ans = (f"{COMPANY} 2026Q1 的獲利如下（單位：億元）：\n"
               f"| 公司 | 合併稅後淨利 |\n|---|---|\n| {COMPANY} | 131.04 |\n")
        gaps, checked = cross_check_metrics(ans, COMPANY, "2026Q1")
        assert checked == 1
        assert len(gaps) == 1

    def test_數字正確時不誤報(self, 金控資料):
        ans = (f"| 公司 | 2026Q1 合併稅後淨利（億元新台幣） |\n|---|---|\n"
               f"| {COMPANY} | 231.04 |\n")
        gaps, checked = cross_check_metrics(ans, COMPANY, "2026Q1")
        assert checked == 1 and gaps == []

    def test_不可裸抓元字(self):
        """在整段文字裡裸找「元」會誤中「元大證券」「還原」這類詞。"""
        from api import _unit_hint
        assert _unit_hint("元大證券的表現") is None
        assert _unit_hint("（元）") == "元"


class Test名稱釘死別期的不可當基準:
    """同一份簡報常附去年同期當對照，解析時連標籤一起被收進當期資料夾：
    國泰 2026Q1 底下就有「國泰世華銀行 1Q25 稅後淨利 = 12.2 十億元」。

    實測 EAP 被問 2026Q1 時，回的正是這批 1Q25 的數字。若拿它當比對基準，
    我們會回報「一致」——等於幫錯誤背書，比不驗證還糟。
    """

    @pytest.fixture
    def 只有去年同期的資料(self, temp_metrics):
        temp_metrics(COMPANY, "台北測試銀行 1Q25 稅後淨利", {"2026Q1": "12.2"}, unit="十億元")

    def test_不拿去年同期的數字背書(self, 只有去年同期的資料):
        ans = ("各子公司稅後淨利如下（單位：十億元）：\n"
               "| 子公司 | 稅後淨利 |\n|---|---|\n| 台北測試銀行 | 12.2 |\n")
        gaps, checked = cross_check_metrics(ans, COMPANY, "2026Q1")
        assert checked == 0, "名稱釘死 1Q25，不該被當成 2026Q1 的比對基準"
        assert gaps == []

    def test_釘死本期的仍可用(self, temp_metrics):
        """「3M26」釘的就是本期，這種要留著用。"""
        temp_metrics(COMPANY, "台北測試銀行 3M26 稅後淨利", {"2026Q1": "8762"}, unit="百萬元")
        ans = ("各子公司稅後淨利如下（單位：億元）：\n"
               "| 子公司 | 稅後淨利 |\n|---|---|\n| 台北測試銀行 | 99.9 |\n")
        gaps, checked = cross_check_metrics(ans, COMPANY, "2026Q1")
        assert checked == 1, "3M26 就是本期，應該拿來比"
        assert len(gaps) == 1, "99.9 億 ≠ 8,762 百萬"


class Test集團層級照舊:
    def test_集團數字錯誤照樣報(self, 集團與子公司資料):
        gaps, _ = cross_check_metrics(
            f"{COMPANY} 2025 年第三季稅後淨利為 149 億元。", COMPANY, PERIOD)
        assert len(gaps) == 1
        assert "249" in gaps[0]["local_value"]

    def test_集團數字正確不報(self, 集團與子公司資料):
        gaps, checked = cross_check_metrics(
            f"{COMPANY} 2025 年第三季稅後淨利為 249 億元。", COMPANY, PERIOD)
        assert checked >= 1
        assert gaps == []

    def test_單位換算錯誤仍抓得到(self, temp_metrics):
        """這是這個功能存在的理由：EAP 把 10,057 百萬元換算成「10.057 億元」
        （應為 100.57 億），差了 10 倍。"""
        temp_metrics(COMPANY, "台北測試金控 3M26 稅後淨利總計", {"2026Q1": "10057"}, unit="百萬元")
        gaps, _ = cross_check_metrics(
            f"{COMPANY} 稅後淨利為 10.057 億元。", COMPANY, "2026Q1")
        assert len(gaps) == 1


class Test部分驗證的揭露:
    """「驗過沒問題」和「根本沒驗」在畫面上必須分得出來。

    實測 EAP 答台灣人壽前三季 177 億，本地沒收錄這筆，於是安靜跳過——
    畫面上跟「四筆全部驗過」長得一模一樣，等於默認了那個沒驗過的數字。
    """

    def test_驗不了的項目會被記錄(self, temp_metrics):
        temp_metrics(COMPANY, "台北測試銀行第三季稅後淨利", {PERIOD: "143"}, unit="億元")
        ans = ("| 子公司 | 2025Q3單季稅後淨利 |\n|---|---|\n"
               "| 台北測試銀行 | 143億元 |\n| 台北測試人壽 | 105億元 |\n")
        unmatched = []
        gaps, checked = cross_check_metrics(ans, COMPANY, PERIOD, unmatched=unmatched)
        assert gaps == [] and checked == 1, "銀行那筆對得上，應該驗過且無不一致"
        assert len(unmatched) == 1, "人壽那筆本地沒有，應該被記下來而不是安靜跳過"
        assert "人壽" in unmatched[0]["company"]
        assert "105" in unmatched[0]["eap_value"]

    def test_全部都驗得到就沒有未驗項目(self, temp_metrics):
        temp_metrics(COMPANY, "台北測試銀行第三季稅後淨利", {PERIOD: "143"}, unit="億元")
        ans = ("| 子公司 | 2025Q3單季稅後淨利 |\n|---|---|\n| 台北測試銀行 | 143億元 |\n")
        unmatched = []
        _, checked = cross_check_metrics(ans, COMPANY, PERIOD, unmatched=unmatched)
        assert checked == 1 and unmatched == []

    def test_不傳清單也不會壞(self, temp_metrics):
        """unmatched 是選填的——多數呼叫端不需要這份清單。"""
        temp_metrics(COMPANY, "台北測試銀行第三季稅後淨利", {PERIOD: "143"}, unit="億元")
        ans = ("| 子公司 | 2025Q3單季稅後淨利 |\n|---|---|\n| 台北測試人壽 | 105億元 |\n")
        gaps, checked = cross_check_metrics(ans, COMPANY, PERIOD)
        assert gaps == [] and checked == 0

    def test_已經報成不一致的不重複列為未驗(self, temp_metrics):
        """同一筆不該同時出現在紅框和「驗不了」清單裡。"""
        temp_metrics(COMPANY, "台北測試銀行第三季稅後淨利", {PERIOD: "143"}, unit="億元")
        ans = ("| 子公司 | 2025Q3單季稅後淨利 |\n|---|---|\n| 台北測試銀行 | 243億元 |\n")
        unmatched = []
        gaps, _ = cross_check_metrics(ans, COMPANY, PERIOD, unmatched=unmatched)
        assert len(gaps) == 1
        assert unmatched == []
