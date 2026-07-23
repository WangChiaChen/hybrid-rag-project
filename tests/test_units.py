"""單位正規化。

為什麼值得測：VLM 是照各家報表原文抄的，同一個單位實測有 24 種寫法。這裡每一條
規則都對應一個踩過的坑，而且錯了不會報錯——只會讓數字差一個量級，還照樣顯示出來。
"""
import pytest

from graph_rag import normalize_unit


@pytest.mark.parametrize("raw, expected", [
    ("百萬元", "百萬元"),
    ("新臺幣百萬元", "百萬元"),
    ("TWD million", "百萬元"),
    ("NT$ mn", "百萬元"),      # 第一金控整份簡報都用這個寫法
    ("NT$MN", "百萬元"),
    ("仟元", "千元"),
    ("新台幣千元", "千元"),
    ("億元", "億元"),
    ("兆元", "兆元"),
    ("元", "元"),
    ("%", "%"),
    ("百分比", "%"),
    ("percentage", "%"),
])
def test_常見寫法收斂到同一個單位(raw, expected):
    assert normalize_unit(raw) == expected


@pytest.mark.parametrize("raw", ["十億元", "新台幣拾億元", "十億新臺幣", "NT$ bn", "billion"])
def test_十億不可被壓成億(raw):
    """「十億」必須排在「億」前面比對。若先命中「億」，存放款規模會整批少一個量級——
    這是 10 倍的錯，而且畫面上看起來完全正常。"""
    assert normalize_unit(raw) == "十億元"


@pytest.mark.parametrize("raw", ["越南盾", "百萬美元", "USD", "人民幣", "RMB"])
def test_外幣原樣保留(raw):
    """外幣是不同幣別，不能跟台幣混為一談，也不該被收斂成台幣單位。"""
    assert normalize_unit(raw) == raw


@pytest.mark.parametrize("raw", ["NT$", "NTD", "TWD", "新台幣", "臺幣"])
def test_看不出量級的寫法不亂猜(raw):
    """只寫幣別、沒寫量級時無從得知是元、千元還是百萬元，寧可原樣保留也不要猜。"""
    assert normalize_unit(raw) == raw


def test_每股類例外可以推定為元():
    """唯一能推定的情況：每股盈餘 7.06 NT$ 的幣別單位一定是元，沒有其他可能。"""
    assert normalize_unit("NT$", metric="每股盈餘") == "元"
    assert normalize_unit("NT$", metric="每股淨值") == "元"
    # 非每股類就不推定
    assert normalize_unit("NT$", metric="稅後淨利") == "NT$"


def test_空值不炸():
    assert normalize_unit(None) is None
    assert normalize_unit("") == ""
