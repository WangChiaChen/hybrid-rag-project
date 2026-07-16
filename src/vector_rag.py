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


def query_vector_rag(question, top_k=5, company=None, period=None):
    """語意檢索。company 可以是單一公司名稱字串，也可以是公司名稱的清單（跨公司比較用）。
    會先撈比較多候選結果，再過濾掉不符合條件的內容。
    """
    if company:
        companies = [company] if isinstance(company, str) else list(company)
        total = max(collection.count(), 1)
        fetch_n = min(total, 50)
        raw = collection.query(query_texts=[question], n_results=fetch_n)
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]

        filtered = []
        for d, m in zip(docs, metas):
            source = m.get("source", "")
            if any(source.startswith(c) for c in companies) and (period is None or period in source):
                filtered.append((d, m))

        filtered = filtered[:top_k]
        return {
            "documents": [[d for d, _ in filtered]],
            "metadatas": [[m for _, m in filtered]],
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
    index_narrative(
        "test_1",
        "手續費淨收益因財富管理業務放緩而季減 4.8%，主要受市場波動影響客戶投資意願下降。",
        {"source": "測試資料", "page": 1}
    )
    result = query_vector_rag("為什麼手續費收入下滑？")
    print(result["documents"])
