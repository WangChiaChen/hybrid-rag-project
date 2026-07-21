"""Phase 2：Vector RAG —— 負責語意檢索（經理人怎麼解釋數字）
TODO: 拿到 EAP 平台文件後，可把 embedding_function 換成 EAP 的 embedding API
"""
import chromadb
from chromadb.utils import embedding_functions
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

client = chromadb.PersistentClient(path=os.path.join(BASE_DIR, "vector_db"))
embed_fn = embedding_functions.DefaultEmbeddingFunction()

collection = client.get_or_create_collection(
    name="earnings_call_narratives",
    embedding_function=embed_fn
)


def index_narrative(doc_id, text, metadata):
    """metadata 範例: {"source": "中信金控 2026Q1 法說會", "page": 5}"""
    if not text:
        return
    collection.upsert(documents=[text], metadatas=[metadata], ids=[doc_id])


def delete_by_source(source):
    """刪掉某個來源的所有段落（重新索引前先清掉舊的，避免殘留／重複）。"""
    try:
        collection.delete(where={"source": source})
    except Exception:
        pass


import re as _re


def _keywords(q):
    """把問題拆成中文 2-gram ＋ 英數詞，用來做「字面關鍵字」比對。"""
    q = str(q)
    zh = _re.findall(r"[一-鿿]", q)
    grams = {zh[i] + zh[i + 1] for i in range(len(zh) - 1)}
    grams |= {w.lower() for w in _re.findall(r"[A-Za-z0-9]{2,}", q)}
    # 去掉太泛用、幾乎每段都有的詞，避免它們把排序帶偏
    stop = {"金控", "公司", "請問", "關於", "以及", "表現", "情況", "如何", "什麼", "多少"}
    return {g for g in grams if g not in stop}


def _kw_score(doc, grams):
    d = doc.lower()
    return sum(1 for g in grams if (g in doc) or (g in d))


def query_vector_rag(question, top_k=5, company=None, period=None):
    """語意檢索（中文混合式）。

    Chroma 預設 embedding 是英文模型，對中文問題排序不可靠，而且原本只抓全庫前 50 筆
    再依公司過濾——排在後面的正確段落會被整個丟掉。改成：先抓「該公司該期」的全部候選，
    再用「字面關鍵字命中數」重新排序（中文字面比對比英文 embedding 可靠），
    embedding 名次只當同分時的次要依據。這樣問「發債計畫」就會撈到講發債那一段。
    """
    if company:
        companies = [company] if isinstance(company, str) else list(company)
        total = max(collection.count(), 1)
        raw = collection.query(query_texts=[question], n_results=total)  # 全撈，別讓正確段落在過濾前就被截掉
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]

        filtered = []
        for rank, (d, m) in enumerate(zip(docs, metas)):
            source = m.get("source", "")
            if any(source.startswith(c) for c in companies) and (period is None or period in source):
                filtered.append((rank, d, m))

        grams = _keywords(question)
        # 排序鍵：關鍵字命中數（高優先）→ embedding 名次（次要，越前面越好）
        filtered.sort(key=lambda t: (-_kw_score(t[1], grams), t[0]))
        picked = filtered[:top_k]
        return {
            "documents": [[d for _, d, _ in picked]],
            "metadatas": [[m for _, _, m in picked]],
        }

    return collection.query(query_texts=[question], n_results=top_k)


def get_all_sources():
    """回傳所有已索引文件的來源清單，用於資料來源總覽畫面"""
    try:
        all_docs = collection.get()
        return all_docs.get("metadatas", []) or []
    except Exception:
        return []


def list_periods_from_vector(company):
    """從語意資料的 metadata 反推這家公司有哪些期間，
    用於補足「只上傳錄音、沒有 PDF 數字指標」的期間也能被選到
    """
    periods = set()
    for m in get_all_sources():
        source = m.get("source", "")
        if source.startswith(company):
            rest = source[len(company):].strip()
            period = rest.split(" ")[0] if rest else None
            if period:
                periods.add(period)
    return periods


if __name__ == "__main__":
    # 這支自我測試寫的是「正式」的知識庫（同一個 vector_db），測試段落留著會被真的檢索到，
    # 而且內容是編的——曾經就有一筆「季減 4.8%」的假敘述躺在庫裡。跑完一定要刪掉。
    try:
        index_narrative(
            "test_1",
            "手續費淨收益因財富管理業務放緩而季減 4.8%，主要受市場波動影響客戶投資意願下降。",
            {"source": "測試資料", "page": 1}
        )
        result = query_vector_rag("為什麼手續費收入下滑？")
        print(result["documents"])
    finally:
        collection.delete(ids=["test_1"])
        print("（已清掉測試段落，知識庫維持乾淨）")
