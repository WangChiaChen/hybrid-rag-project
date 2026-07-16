"""把 vlm_parse.py 解析出來的真實簡報資料，灌進 Vector RAG 和 Graph RAG
執行順序：先跑過 vlm_parse.py（會產生對應的 parsed_*.json），再跑這支

用法：
  python src/ingest.py                                          # 用預設值
  python src/ingest.py --company 玉山金控 --period 2026Q1        # 匯入第二組資料
  python src/ingest.py --company 中信金控 --period 2025Q4        # 匯入上一季資料
"""
import json
import os
import argparse
from vector_rag import index_narrative
from graph_rag import ingest_metrics

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_ingest(company, period, parsed_json_path=None):
    if parsed_json_path is None:
        parsed_json_path = os.path.join(BASE_DIR, "outputs", f"parsed_{company}_{period}.json")

    if not os.path.exists(parsed_json_path):
        print(f"找不到 {parsed_json_path}，請先執行：python src/vlm_parse.py --company {company} --period {period}")
        return False

    with open(parsed_json_path, "r", encoding="utf-8") as f:
        pages = json.load(f)

    for i, page in enumerate(pages):
        narrative = page.get("narrative_text")
        if narrative:
            index_narrative(
                doc_id=f"{company}_{period}_p{i}",
                text=narrative,
                metadata={"source": f"{company} {period}", "page": i + 1}
            )
            print(f"第 {i+1} 頁敘述已寫入 Vector RAG")

        key_metrics = page.get("key_metrics", [])
        valid_metrics = [m for m in key_metrics if m.get("指標名稱") and m.get("數值")]
        if valid_metrics:
            ingest_metrics(company, period, valid_metrics)
            print(f"第 {i+1} 頁指標已寫入 Graph RAG：{[m.get('指標名稱') for m in valid_metrics]}")

    print(f"\n{company} {period} 的資料已全部匯入完成！")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", default="中信金控")
    parser.add_argument("--period", default="2026Q1")
    parser.add_argument("--json", default=None, help="自訂 JSON 路徑（通常不用填，會自動對應）")
    args = parser.parse_args()
    run_ingest(args.company, args.period, args.json)
