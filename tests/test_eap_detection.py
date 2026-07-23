"""EAP「查無資料」偵測。

這個判斷是四道防護的觸發開關：判 True 才會啟動 Vector RAG 補強與本地退路按鈕，
判 False 才會去做「講得頭頭是道但驗不了」的標示。兩個方向都錯不得——
漏判會讓補強不觸發，誤判會把正常答案當成查無資料。

EAP 的措辭實測會變（見 EAP平台提示詞設定.md），所以這裡測的是「各種說法都要認得」，
不是比對某一句固定字串。
"""
import pytest

from api import _eap_found_nothing


@pytest.mark.parametrize("answer", [
    "查無此項資料",
    "查詢不到相關資訊",
    "資料中查不到員工人數",
    "找不到這筆資料",
    "沒有找到相關內容",
    "無法取得該期間的數字",
    "無法提供這項資訊",
    "未能查詢到相關資料",
    "未查詢到此指標",
    "目前尚無這項資料",
    "此項目未收錄於資料中",
    "無相關資料",
    "無相關資訊",
    # 中間夾著公司＋期間＋文件類型，窗口要放得夠寬
    "資料中並沒有中信金控 2025 年第三季法說會的逐字稿或問答內容。",
])
def test_中文各種說法都認得(answer):
    assert _eap_found_nothing(answer)


@pytest.mark.parametrize("answer", [
    # 平台在檢索之前的「專案相關性」關卡，回的是英文，不受後台措辭設定控制
    "Unable to answer question not relevant to this project and its data",
    "Unable to find any relevant information",
    "Cannot answer this question",
    "No relevant data found in the project",
])
def test_英文拒答也認得(answer):
    """這道關卡的措辭是平台寫死的英文，中文骨架一句都對不上。
    漏掉的話 Vector RAG 補強不會觸發——而被它擋下時平台根本沒去檢索，
    正是本地最該接手的時機。"""
    assert _eap_found_nothing(answer)


def test_空回答視為查無():
    assert _eap_found_nothing("")
    assert _eap_found_nothing("   ")
    assert _eap_found_nothing(None)


def test_短道歉兜底():
    """措辭骨架仍可能漏掉沒見過的講法，但查不到的回覆有兩個穩定特徵：
    帶道歉語氣、而且很短（真的查到時它會長篇大論還附表格）。"""
    assert _eap_found_nothing("抱歉，我無法協助這個問題。")
    assert _eap_found_nothing("Unfortunately I don't have that.")


@pytest.mark.parametrize("answer", [
    "中信金控 2026 年第一季的每股盈餘為 1.18 元，較去年同期的 0.98 元成長 20.4%。",
    "玉山金控的 NIM（Net Interest Margin，淨利息收益率）為 1.45%，較上季微幅上升。",
])
def test_正常作答不可被誤判(answer):
    assert not _eap_found_nothing(answer)


def test_有表格就代表撈到東西了():
    """即使句子裡出現「部分查不到」，只要附了資料表就不算整題落空，
    不該跳出補強提示——那會讓使用者以為系統沒答出來。"""
    answer = (
        "| 公司 | 稅後淨利 | 單位 |\n"
        "|---|---|---|\n"
        "| 中信金控 | 23,100 | 百萬元 |\n\n"
        "其中基金手續費的部分查不到單獨揭露。"
    )
    assert not _eap_found_nothing(answer)


def test_長篇道歉不算查無():
    """道歉兜底只在答案很短時生效，否則「很抱歉，以下說明…」開頭的正常長答案會被誤殺。"""
    long_answer = "很抱歉造成困擾，以下說明中信金控的獲利結構。" + "細節如下。" * 40
    assert len(long_answer) > 120
    assert not _eap_found_nothing(long_answer)
