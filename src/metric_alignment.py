"""跨機構指標語意對齊
不同銀行簡報用詞不一定一樣（例如「手續費淨收益」vs「淨手續費收入」），
用 embedding 語意相似度自動配對，而不是要求文字完全相同。
這支跟 vector_rag.py 用同一顆本地端 embedding 模型，完全免費、不用呼叫 API。
"""
import re
from chromadb.utils import embedding_functions

embed_fn = embedding_functions.DefaultEmbeddingFunction()


# 判斷指標「種類」用的關鍵字。目的是分辨哪些指標可以跨公司直接比大小、
# 哪些是絕對金額（各家申報單位可能不同，例如中信用百萬元、國泰用億元，不能直接比）。
_RATIO_KEYWORDS = (
    "率", "%", "占比", "比重", "ROE", "ROA", "NIM",
    "利差", "適足", "清償能力", "覆蓋", "存放比", "成長", "年增", "季增",
    # 「獲利組成 - 銀行 60 / 人壽 28 / 其他 11」這種占比，加總約 100，是百分比不是金額
    "組成",
)
_PER_SHARE_KEYWORDS = ("每股", "EPS")


# 「年初至今累計」的名稱標記。法說會簡報的獲利／EPS 常常是累計值而非單季值，
# 例如中信「合併稅後淨利 (9M25)」是前三季累計、國泰「稅後淨利 (1H25)」是上半年累計。
_CUMULATIVE_KEYWORDS = ("累計", "累積", "上半年", "前三季", "全年", "年初至今", "YTD")
# 6M25 / 9M25 / 3M26（N個月累計）、1H25（上半年）、FY24（全年）
_CUMULATIVE_PATTERNS = (
    re.compile(r"\d+M\d{2}"),
    re.compile(r"\b1H\d{2}"),
    re.compile(r"\bFY\d{2}"),
)


def is_cumulative_name(name):
    """光看指標名稱能不能判斷它是「累計值」。
    注意：名稱看不出來不代表不是累計（例如玉山簡報直接叫「EPS」，其實是年初至今累計），
    那種要靠數列判斷，見 graph_rag.is_cumulative()。
    """
    n = str(name)
    if any(k in n for k in _CUMULATIVE_KEYWORDS):
        return True
    return any(p.search(n) for p in _CUMULATIVE_PATTERNS)


# 指標名稱裡的期別標籤（3M26、1Q26、FY24、2025年…）。
# 放在這裡是因為 graph_rag（判累計）和 api（補單位）都要用，擺在任一邊都會造成循環 import。
_NAME_PERIOD_TAG = re.compile(r"\(?\s*(?:\d[QH]\d{2}|\d+M\d{2}|FY\d{2}|20\d{2}年?|1[01]\d年)\s*\)?")


def norm_metric_name(name):
    """把「2025年合併稅後淨利」「3M26合併稅後淨利」正規化成同一個「合併稅後淨利」。

    注意只拿掉期別標籤，不動「第一季」這種字——中信「中信銀行稅後淨利(百萬元)」與
    「中信銀行第一季稅後淨利(億元)」是同一筆金額的不同單位，合併會套錯單位。
    """
    s = _NAME_PERIOD_TAG.sub("", str(name)).strip(" -－")
    # 只剝掉「拿掉期別後才落單」的括號，成對的括號要留著——
    # 「國泰人壽稅後淨利(含FVOCI)」若被剝成「…(含FVOCI」，跨期就對不上同一個指標。
    if s.count("(") != s.count(")") or s.count("（") != s.count("）"):
        s = s.strip("()（）")
    return s.strip(" -－")


# 單位若是這些，指標鐵定是比率——比名稱可靠得多。
# 例如國泰簡報的「國泰世華銀行獲利：17」單位是「百分比」（其實是 +17% 年成長），
# 名稱裡沒有「率」或「%」，光看名字會誤判成絕對金額，然後被拿去跟別家的獲利金額比。
_RATIO_UNITS = ("百分比", "%", "bps", "個百分點", "percentage")


def classify_metric(name, unit=None):
    """把指標歸類成三種：
      - "ratio"     ：比率／百分比／成長率（單位無關，可直接跨公司比大小）
      - "per_share" ：每股類，單位一律是「元」（可直接跨公司比）
      - "amount"    ：絕對金額（各公司申報單位可能不同，跨公司比較前要先對齊單位）

    有單位就以單位為準，沒有才退回看名稱。
    """
    if unit:
        u = str(unit).lower()
        if any(k in u for k in _RATIO_UNITS):
            return "ratio"

    n = str(name)
    if any(k in n for k in _PER_SHARE_KEYWORDS):
        return "per_share"
    if any(k in n for k in _RATIO_KEYWORDS):
        return "ratio"
    return "amount"


def is_cross_comparable(name, unit=None):
    """這個指標是否「單位無關」、可以放心跨公司直接比大小"""
    return classify_metric(name, unit) in ("ratio", "per_share")


# 依財務意義分組。60 幾個指標平鋪很難讀，照這個順序分區才能照邏輯瀏覽。
# 順序即重要性，第一個命中的就是它的組別。
#
# 放在後端而不是前端：分組規則是「財務判斷」，跟 classify_metric、is_cumulative 同一類，
# 應該和它們待在一起。前端只負責顯示後端算好的結果，改規則不必重新部署前端。
_GROUPS = (
    ("獲利能力", re.compile(r"淨利|獲利|盈餘|EPS|收益|營收|報酬率|ROE|ROA|股利|配發")),
    ("資本結構", re.compile(r"資本適足|權益|淨值|槓桿|清償|CSM|RBC")),
    ("資產品質", re.compile(r"逾期|呆帳|覆蓋|減損|信用")),
    ("業務規模", re.compile(r"放款|存款|資產|保費|手續費|財富管理|信用卡|規模|市占|市佔")),
    ("現金流量", re.compile(r"現金流|現金及約當")),
)

# 金控層級的門面數字，在畫面上要比其他項目搶眼
_HERO = re.compile(r"^(合併稅後淨利|本期淨利|稅後淨利|基本每股盈餘|每股稅後盈餘|EPS|ROE|"
                   r"稅後股東權益報酬率|資產總計)")


def metric_category(name):
    """這個指標屬於哪一個財務意義分組；都不符合就歸「其他」。"""
    n = str(name)
    for label, pattern in _GROUPS:
        if pattern.search(n):
            return label
    return "其他"


def is_hero_metric(name):
    """是不是該放大呈現的門面指標。"""
    return bool(_HERO.match(str(name)))


def _cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0
    return dot / (norm_a * norm_b)


def align_metrics(names_a, names_b, threshold=0.75, units_a=None, units_b=None):
    """回傳語意配對清單：[{"a": 指標A名稱, "b": 指標B名稱, "similarity": 0.xx}]
    只保留相似度超過門檻的配對，每個指標最多配對一次。

    只有「同一種指標類型」才配得起來。embedding 只看字面相似度，分不出
    「中信銀行資本適足率」（比率）跟「國泰投信稅後淨利」（金額）根本是兩回事，
    照字面配就會配出這種東西。傳 units 進來讓 classify_metric 判得更準
    （單位比名稱可靠）。

    型別限制是在配對迴圈裡做的，不是配完才篩：這樣某個指標的最佳匹配若是
    不同類型，它還能退而求其次找到同類型裡最像的，而不是整個被丟掉。
    """
    if not names_a or not names_b:
        return []

    units_a = units_a or {}
    units_b = units_b or {}
    types_a = [classify_metric(n, units_a.get(n)) for n in names_a]
    types_b = [classify_metric(n, units_b.get(n)) for n in names_b]

    embeddings_a = embed_fn(names_a)
    embeddings_b = embed_fn(names_b)

    pairs = []
    used_b = set()
    for i, name_a in enumerate(names_a):
        best_j, best_score = None, 0
        for j, name_b in enumerate(names_b):
            if j in used_b or types_a[i] != types_b[j]:
                continue
            score = _cosine_sim(embeddings_a[i], embeddings_b[j])
            if score > best_score:
                best_score, best_j = score, j
        if best_j is not None and best_score >= threshold:
            pairs.append({"a": name_a, "b": names_b[best_j], "similarity": round(best_score, 3)})
            used_b.add(best_j)

    return sorted(pairs, key=lambda p: -p["similarity"])


if __name__ == "__main__":
    # 快速自我測試
    names_a = ["手續費淨收益", "稅後淨利", "每股盈餘"]
    names_b = ["淨手續費收入", "稅後純益", "EPS"]
    result = align_metrics(names_a, names_b, threshold=0.5)
    for p in result:
        print(f"{p['a']}  ≈  {p['b']}　（相似度 {p['similarity']}）")
