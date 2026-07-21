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
from vector_rag import delete_by_source, index_narrative
from graph_rag import ingest_metrics, remove_period

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_page_range(spec):
    """把 "5-9" 或 "5,7,9" 或 "5-9,14" 解析成頁碼集合（1-based）。None 代表全部。
    財報前面是四大報表、後面是附註，附註常混入其他期間／重分類的數字，
    盲目全匯會污染知識圖譜，所以要能只挑指定頁。
    """
    if not spec:
        return None
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.update(range(int(start), int(end) + 1))
        elif part:
            pages.add(int(part))
    return pages or None


def run_ingest(company, period, parsed_json_path=None, pages=None, replace=None):
    """把解析結果匯入知識庫。

    replace：匯入前先清掉這家公司這一期的舊資料。預設值是「整份匯入就清、只匯部分頁就不清」——
    節點是用「公司|指標名稱|期間」當 key，換一份簡報若指標改了名（英文版換中文版、
    「稅後淨利」變「合併稅後淨利」），不清就會變成新舊兩套並存而不是取代。
    語意段落也一樣要清，否則 Vector RAG 會同時檢索到兩個版本的敘述。

    清除只在「確定新資料有東西」之後才做——VLM 解析可能失敗或撞到額度限制而吐出空結果，
    先清再匯的話會把原本好好的資料清掉卻換不到新的。
    """
    if parsed_json_path is None:
        parsed_json_path = os.path.join(BASE_DIR, "outputs", f"parsed_{company}_{period}.json")

    if not os.path.exists(parsed_json_path):
        print(f"找不到 {parsed_json_path}，請先執行：python src/vlm_parse.py --company {company} --period {period}")
        return False

    with open(parsed_json_path, "r", encoding="utf-8") as f:
        all_pages = json.load(f)

    if replace is None:
        replace = pages is None

    if replace:
        selected = [p for i, p in enumerate(all_pages) if pages is None or (i + 1) in pages]
        has_content = any(
            p.get("narrative_text") or
            [m for m in p.get("key_metrics", []) if m.get("指標名稱") and m.get("數值")]
            for p in selected
        )
        if not has_content:
            print(f"{parsed_json_path} 解析結果是空的，為避免清掉既有資料，這次不匯入。")
            return False
        removed = remove_period(company, period)
        delete_by_source(f"{company} {period}")
        if removed:
            print(f"已清掉 {company} {period} 既有的 {removed} 筆指標與對應語意段落，準備重新匯入")

    for i, page in enumerate(all_pages):
        if pages is not None and (i + 1) not in pages:
            continue
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
    parser.add_argument("--pages", default=None,
                        help='只匯入指定頁，例如 "5-9" 或 "5,7" 或 "5-9,14"。不填=全部。'
                             '財報建議只匯入四大報表那幾頁，避免附註的其他期間數字污染圖譜')
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--replace", dest="replace", action="store_true",
                       help="匯入前先清掉這家公司這一期的舊資料（整份匯入時的預設行為）")
    group.add_argument("--append", dest="replace", action="store_false",
                       help="保留舊資料、把新的疊加上去（用 --pages 分批匯入時的預設行為）")
    parser.set_defaults(replace=None)
    args = parser.parse_args()
    run_ingest(args.company, args.period, args.json,
               pages=parse_page_range(args.pages), replace=args.replace)
