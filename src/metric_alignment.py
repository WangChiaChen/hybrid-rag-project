"""跨機構指標語意對齊
不同銀行簡報用詞不一定一樣（例如「手續費淨收益」vs「淨手續費收入」），
用 embedding 語意相似度自動配對，而不是要求文字完全相同。
這支跟 vector_rag.py 用同一顆本地端 embedding 模型，完全免費、不用呼叫 API。
"""
from chromadb.utils import embedding_functions

embed_fn = embedding_functions.DefaultEmbeddingFunction()


# 判斷指標「種類」用的關鍵字。目的是分辨哪些指標可以跨公司直接比大小、
# 哪些是絕對金額（各家申報單位可能不同，例如中信用百萬元、國泰用億元，不能直接比）。
_RATIO_KEYWORDS = (
    "率", "%", "占比", "比重", "ROE", "ROA", "NIM",
    "利差", "適足", "清償能力", "覆蓋", "存放比", "成長", "年增", "季增",
)
_PER_SHARE_KEYWORDS = ("每股", "EPS")


def classify_metric(name):
    """把指標歸類成三種：
      - "ratio"     ：比率／百分比／成長率（單位無關，可直接跨公司比大小）
      - "per_share" ：每股類，單位一律是「元」（可直接跨公司比）
      - "amount"    ：絕對金額（各公司申報單位可能不同，跨公司比較前要先對齊單位）
    """
    n = str(name)
    if any(k in n for k in _PER_SHARE_KEYWORDS):
        return "per_share"
    if any(k in n for k in _RATIO_KEYWORDS):
        return "ratio"
    return "amount"


def is_cross_comparable(name):
    """這個指標是否「單位無關」、可以放心跨公司直接比大小"""
    return classify_metric(name) in ("ratio", "per_share")


def _cosine_sim(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0
    return dot / (norm_a * norm_b)


def align_metrics(names_a, names_b, threshold=0.75):
    """回傳語意配對清單：[{"a": 指標A名稱, "b": 指標B名稱, "similarity": 0.xx}]
    只保留相似度超過門檻的配對，每個指標最多配對一次。
    """
    if not names_a or not names_b:
        return []

    embeddings_a = embed_fn(names_a)
    embeddings_b = embed_fn(names_b)

    pairs = []
    used_b = set()
    for i, name_a in enumerate(names_a):
        best_j, best_score = None, 0
        for j, name_b in enumerate(names_b):
            if j in used_b:
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
