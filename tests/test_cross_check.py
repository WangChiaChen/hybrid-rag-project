"""交叉驗證的層級與期別判斷。

這是整個系統對外最顯眼的防護——畫面上會跳紅框說「與本地知識庫數字不一致」。
它誤報的代價比漏報更高：使用者看到四則紅字警告正確的答案，之後連真的警告也不會信了。

實測踩過的坑：EAP 回答「中信銀行第三季稅後淨利 143 億、前三季累計 421 億」，
四個子公司數字全部被拿去跟「金控合併 249 億」比，報出 4 則不一致——
而本地明明就有「中信銀行第三季稅後淨利 = 143 億元」，EAP 一個字都沒錯。

用假公司測，不依賴知識庫實際內容（真實資料會隨著重新匯入而變）。
"""
import pytest

from api import (
    _cross_check_prose_entity,
    _pick_local_metric,
    cross_check_eap,
    cross_check_metrics,
)

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


class Test母公司單獨不可跟集團合併混比:
    """母公司單獨（金單一／單體）是第三種實體——集團合併 ＝ 各子公司 ＋ 母公司單獨。
    母公司單獨常是小額或虧損。實測 EAP 把合併數（231 億）貼錯標籤成「中信金單一」，
    而中信金單一 1Q26 其實是虧損 43 億；拿合併數去比集團合併剛好對上、等於幫錯標籤背書。
    要認出「金單一」才比得到本地正確的那筆。

    地雷：「單一季」是「單季」的意思，跟母公司單一無關，絕不能誤中。
    """

    COMPANY = "台北測試金控"

    @pytest.fixture
    def 母公司單獨與合併(self, temp_metrics):
        temp_metrics(self.COMPANY, "台北測試金單一 1Q26 稅後淨利", {"2026Q1": "-4,313"}, unit="百萬元")
        temp_metrics(self.COMPANY, "3M26合併稅後淨利", {"2026Q1": "23,104"}, unit="百萬元")
        return self.COMPANY

    def test_把合併數冒充母公司單獨要抓到(self, 母公司單獨與合併):
        # EAP 拿合併的 231.04 億貼上「金單一」標籤；本地金單一是虧損 4,313 百萬
        ans = "| 期間 | 台北測試金單一稅後淨利（億元） |\n|---|---|\n| 2026Q1 | 231.04 |"
        gaps, _ = cross_check_metrics(ans, self.COMPANY, "2026Q1")
        assert len(gaps) == 1
        assert "金單一" in gaps[0]["company"]
        assert "4,313" in str(gaps[0]["local_value"])

    def test_母公司單獨數字正確就不誤報(self, 母公司單獨與合併):
        ans = "| 期間 | 台北測試金單一稅後淨利（億元） |\n|---|---|\n| 2026Q1 | -43.13 |"
        gaps, checked = cross_check_metrics(ans, self.COMPANY, "2026Q1")
        assert checked == 1 and gaps == []

    def test_單一季不可被當母公司單獨(self, 母公司單獨與合併):
        """「單一季稅後淨利 231 億」＝單季合併，正確；不能拿去比母公司單一的 -43 億。"""
        ans = "台北測試金控2026年第一季單一季稅後淨利為新台幣231億元。"
        gaps, _ = cross_check_metrics(ans, self.COMPANY, "2026Q1")
        assert gaps == [], f"單一季是單季合併，不該誤報：{gaps}"

    def test_集團稅後淨利挑合併不挑母公司單獨(self, 母公司單獨與合併):
        pick = _pick_local_metric(self.COMPANY, "2026Q1", "稅後淨利")
        assert pick is not None and "合併" in pick["name"], \
            "集團層級的稅後淨利要挑合併數，不能挑到母公司單一"


class Test開場白不可污染公司判斷:
    """EAP 每則回答都以固定開場白起頭，裡面列出所有可查詢的公司。

    純文字比對是靠「公司名出現的位置」把答案切段的。開場白把四家公司名塞在最前面，
    最後一家會把整段答案本文吞進它的段落——於是不管實際問哪家，指標都被算到最後那家
    頭上、跟它的本地數字比。實測：問國泰世華 ROE 16.9%，卻跳出「第一金控 16.9 vs 11.16」
    的假紅框。開場白必須在比對前剝掉。
    """

    開場白 = ("您好，我是財報分析助理，可查詢台北甲金控、台北乙金控、台北丙金控、台北丁金控"
            "的財報與法說會內容。所有數字均附出處；查無資料時會明確告知，不會臆測。\n\n")

    def test_不把答案算到開場白最後一家頭上(self, temp_metrics):
        # 開場白最後一家（丁）本地 ROE 11.16；答案其實在講甲、ROE 16.9。
        temp_metrics("台北丁金控", "台北丁金控股東權益報酬率", {"2026Q1": "11.16"}, unit="%")
        temp_metrics("台北甲金控", "台北甲金控股東權益報酬率", {"2026Q1": "16.9"}, unit="%")
        ans = self.開場白 + "台北甲金控2026年第一季的股東權益報酬率（ROE）為16.9%。"
        gaps, _ = cross_check_eap(ans, "2026Q1")
        assert all(g["company"] != "台北丁金控" for g in gaps), \
            f"答案講的是甲，不該報成開場白最後一家的丁：{gaps}"
        assert gaps == [], f"甲的 16.9 本地就是 16.9，不該有任何不一致：{gaps}"

    def test_開場白不遮蔽真正的不一致(self, temp_metrics):
        # 甲本地 ROE 12.0，EAP 答 16.9——真的差很多，剝掉開場白後要抓得到。
        temp_metrics("台北甲金控", "台北甲金控股東權益報酬率", {"2026Q1": "12.0"}, unit="%")
        ans = self.開場白 + "台北甲金控2026年第一季的股東權益報酬率（ROE）為16.9%。"
        gaps, checked = cross_check_eap(ans, "2026Q1")
        assert checked == 1
        assert len(gaps) == 1 and gaps[0]["company"] == "台北甲金控"


class Test指名子公司的純文字比率:
    """EAP 常寫「國泰世華銀行…ROE 為 16.9%。資料來源：國泰金控…」——主詞是子公司、
    金控名只在結尾出處行。本地把子公司比率以「國泰世華ROE=16.9」收在金控底下，
    這條路徑要用子公司名去對，才驗得到（金控層級那條路對不到，會顯示「無法驗證」）。

    安全性關鍵：只拿子公司去比「同一家子公司」的本地數字，絕不退回集團層級。
    """

    @pytest.fixture
    def 子公司比率(self, temp_metrics):
        # 本地以「子公司名＋指標」的形式收在金控底下（真實資料就長這樣）
        temp_metrics(COMPANY, "台北富華ROE", {"2026Q1": "16.9"}, unit="%")
        temp_metrics(COMPANY, "台北人壽ROE", {"2026Q1": "12.3"}, unit="%")
        return COMPANY

    def test_子公司比率正確就驗得到且不誤報(self, 子公司比率):
        ans = "台北富華銀行於2026年第一季的股東權益報酬率（ROE）為16.9%。資料來源：台北測試金控2026Q1財報。"
        gaps, checked = _cross_check_prose_entity(ans, COMPANY, "2026Q1")
        assert checked == 1, "本地有台北富華ROE，應該真的比對到"
        assert gaps == [], f"16.9 對 16.9，不該報不一致：{gaps}"

    def test_子公司比率錯誤要抓到並標對子公司(self, 子公司比率):
        ans = "台北富華銀行2026Q1股東權益報酬率（ROE）為20.0%。"
        gaps, checked = _cross_check_prose_entity(ans, COMPANY, "2026Q1")
        assert checked == 1
        assert len(gaps) == 1
        assert gaps[0]["company"] == "台北富華銀行", "要標成講的那家子公司"
        assert "16.9" in str(gaps[0]["local_value"])

    def test_不同子公司各比各的不互相誤配(self, 子公司比率):
        # 講人壽就該比人壽（12.3），不能拿到世華的 16.9
        ans = "台北人壽2026Q1股東權益報酬率（ROE）為12.3%。"
        gaps, checked = _cross_check_prose_entity(ans, COMPANY, "2026Q1")
        assert checked == 1 and gaps == [], f"人壽 12.3 對 12.3 不該報：{gaps}"

    def test_沒指名子公司就不啟動(self, 子公司比率):
        ans = "台北測試金控2026Q1股東權益報酬率為9.9%。"
        gaps, checked = _cross_check_prose_entity(ans, COMPANY, "2026Q1")
        assert checked == 0 and gaps == [], "沒點名子公司，這條路徑不該比"


class Test子公司簡稱與全名:
    """同一家子公司，簡報有時寫全名、有時省略業別。

    實測：本地是「國泰世華稅後淨利」，EAP 寫「國泰世華銀行」——只比對全名會整個
    對不上，於是 EAP 拿去年同期的數字回答本季時沒被抓到。
    """

    @pytest.fixture
    def 省略業別的本地資料(self, temp_metrics):
        # 本地用簡稱（沒有「銀行」兩字），EAP 會寫全名
        temp_metrics(COMPANY, "台北富華稅後淨利", {"2026Q1": "13.2"}, unit="十億元")

    def 表(self, 實體, 值):
        return (f"各子公司稅後淨利如下（單位：十億元）：\n"
                f"| 子公司 | 稅後淨利 |\n|---|---|\n| {實體} | {值} |\n")

    def test_EAP寫全名也對得上本地簡稱(self, 省略業別的本地資料):
        gaps, checked = cross_check_metrics(
            self.表("台北富華銀行", "12.2"), COMPANY, "2026Q1")
        assert checked == 1, "「台北富華銀行」應該對得上本地的「台北富華稅後淨利」"
        assert len(gaps) == 1 and "13.2" in gaps[0]["local_value"]

    def test_數字正確時不誤報(self, 省略業別的本地資料):
        gaps, checked = cross_check_metrics(
            self.表("台北富華銀行", "13.2"), COMPANY, "2026Q1")
        assert checked == 1 and gaps == []

    def test_簡稱太短就不套用(self, temp_metrics):
        """「國泰人壽」去掉業別剩「國泰」，會把國泰世華、國泰產險全部誤配成同一家。
        所以只有去掉業別後仍 ≥4 字才採用簡稱。"""
        temp_metrics(COMPANY, "台北富華稅後淨利", {"2026Q1": "13.2"}, unit="十億元")
        # 「台北人壽」去掉「人壽」剩「台北」（2 字），不該拿去配「台北富華稅後淨利」
        gaps, checked = cross_check_metrics(
            self.表("台北人壽", "99.9"), COMPANY, "2026Q1")
        assert checked == 0, "簡稱過短就不該套用，否則不同子公司會互相誤配"
        assert gaps == []
