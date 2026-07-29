"""把三家金控 2025 年報的「前段敘述章節」索引進 Vector RAG，供敘述型問答檢索。

年報前段（致股東報告書／營運概況／分部門／五年摘要）是敘述文字，回答的是
「公司怎麼解釋下滑」「哪個事業是獲利主力」這類問題——走語意檢索，不是指標庫。
後段兩百頁是四大報表與附註（表格），語意檢索命中率低，刻意不收。

## 兩條處理路徑（實測決定）

  中信、玉山：文字層乾淨（中文佔比 64-66%），PyMuPDF 直接抽字，免費又快。
  國泰　　　：字型是自訂 CID 編碼，抽出來是亂碼，只能走 VLM 看渲染圖。

兩條路徑最後都餵進同一個索引函式，來源標為「X 2025年報」、附頁碼。

## 安全性

- period 用「2025年報」，跟現有季度資料（2025Q1-Q4／2026Q1）分開，
  不會污染跨期比較、門面數字或交叉驗證（那些只在同一 period 內作用）。
- 只寫語意段落（index_narrative），不碰指標庫（graph_data.json）。
- 重跑先 delete_by_source 清掉舊的同來源段落，不會重複累積。

用法：
    venv/Scripts/python.exe scripts/ingest_annual_reports.py --company 中信金控   # 單一家
    venv/Scripts/python.exe scripts/ingest_annual_reports.py --text-only          # 只跑文字乾淨的兩家
    venv/Scripts/python.exe scripts/ingest_annual_reports.py --all                # 三家都跑（國泰走 VLM）
"""
import argparse
import os
import sys

import fitz

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

from stt_parse import _chunk_transcript
from vector_rag import index_narrative, delete_by_source

PERIOD = "2025年報"

DOCS = {
    "中信金控": {"path": r"C:\Users\jenny wang\Downloads\中信金2025.pdf", "mode": "text",
                 "max_scan": 45},
    "玉山金控": {"path": r"C:\Users\jenny wang\Downloads\玉山金2025.pdf", "mode": "text",
                 "max_scan": 45},
    "國泰金控": {"path": r"C:\Users\jenny wang\Downloads\國泰金2025.pdf", "mode": "vlm",
                 "max_scan": 34},   # 前段致股東報告書＋營運概況，跳過封面
}
# 只收前段敘述章節（致股東報告書／營運概況／分部門）。實測過掃全份會把後面兩百頁的
# 財務報表附註（會計政策 boilerplate）與董事薪酬明細一起收進來——那些是中文散文、
# 過得了「敘述頁」判準，但對業務問答是雜訊，會把知識庫從 846 撐到 2047 段。
# 財務數字用季度指標庫；ESG／永續要另立乾淨的來源，不從附註夾帶。


def _cjk(t):
    return sum(1 for c in t if "一" <= c <= "鿿") / max(len(t), 1)


def _digit(t):
    return sum(1 for c in t if c.isdigit()) / max(len(t), 1)


def _is_narrative(text):
    """敘述頁：中文夠多、數字不多、有一定篇幅。自動跳過封面、目錄、純表格。"""
    return _cjk(text) > 0.35 and _digit(text) < 0.25 and len(text.strip()) > 400


def _index_page(company, page_no, text, dry):
    """把一頁文字切段後索引。回傳這頁產生幾段。"""
    chunks = _chunk_transcript(text)
    if not dry:
        for j, ch in enumerate(chunks):
            index_narrative(
                doc_id=f"{company}_年報2025_p{page_no}_{j}",
                text=ch,
                metadata={"source": f"{company} {PERIOD}", "page": page_no},
            )
    return len(chunks)


def ingest_text(company, cfg, dry):
    """文字層乾淨的年報：直接抽字。"""
    d = fitz.open(cfg["path"])
    scan_to = min(cfg["max_scan"], len(d)) if cfg["max_scan"] else len(d)
    pages, total = [], 0
    for i in range(scan_to):
        t = d[i].get_text()
        if _is_narrative(t):
            n = _index_page(company, i + 1, t, dry)
            pages.append(i + 1)
            total += n
    d.close()
    span = f"{pages[0]}-{pages[-1]}" if pages else "無"
    print(f"  [文字] {company}：敘述頁 {len(pages)} 頁（p{span}）→ {total} 段")
    return total


def ingest_vlm(company, cfg, dry):
    """字型亂碼的年報：渲染成圖，VLM 讀出敘述摘要。"""
    from preprocess_pdf import pdf_to_images
    from vlm_parse import call_with_retry, get_client
    from google.genai import types
    import time

    PROMPT = ("這是一份金控公司年報的其中一頁。請用繁體中文摘要這一頁的重點文字內容"
              "（例如致股東報告書、營運概況、分部門獲利、風險與財務說明），"
              "只回傳摘要本文、不要加標題或 markdown 符號；"
              "若這頁主要是表格數字或無實質敘述，回傳空字串。")

    slug = "".join(c for c in company if c.isalnum())
    out_dir = os.path.join(os.path.dirname(cfg["path"]), f"_pages_{slug}")
    imgs = pdf_to_images(cfg["path"], output_dir=out_dir, max_pages=cfg["max_scan"])
    imgs = imgs[1:]   # 跳過封面第 1 頁

    pages, total = [], 0
    for i, img in enumerate(imgs):
        page_no = i + 2   # imgs[0] 是第 2 頁
        with open(img, "rb") as f:
            data = f.read()
        try:
            resp = call_with_retry(lambda: get_client().models.generate_content(
                model="gemini-flash-lite-latest",
                contents=[types.Part.from_bytes(data=data, mime_type="image/png"), PROMPT],
            ))
            text = (resp.text or "").strip()
        except Exception as e:
            print(f"     p{page_no} VLM 失敗：{e}")
            text = ""
        if len(text) > 80:      # 有實質敘述才收
            n = _index_page(company, page_no, text, dry)
            pages.append(page_no)
            total += n
        if i < len(imgs) - 1:
            time.sleep(4.5)     # 免費額度每分鐘 15 次，間隔避免撞上限
    print(f"  [VLM ] {company}：敘述頁 {len(pages)} 頁 → {total} 段")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="只跑指定公司")
    ap.add_argument("--text-only", action="store_true", help="只跑文字乾淨的兩家（免費）")
    ap.add_argument("--all", action="store_true", help="三家都跑（國泰走 VLM，會花額度）")
    ap.add_argument("--dry", action="store_true", help="只掃描列出，不寫入")
    args = ap.parse_args()

    if args.company:
        targets = [args.company]
    elif args.text_only:
        targets = [c for c, v in DOCS.items() if v["mode"] == "text"]
    elif args.all:
        targets = list(DOCS)
    else:
        print("請指定 --company <名稱> 或 --text-only 或 --all")
        return

    print(f"目標：{targets}　period={PERIOD}　{'（試掃，不寫入）' if args.dry else ''}\n")
    grand = 0
    for c in targets:
        cfg = DOCS[c]
        if not os.path.exists(cfg["path"]):
            print(f"  找不到 {cfg['path']}，跳過"); continue
        if not args.dry:
            delete_by_source(f"{c} {PERIOD}")   # 重跑先清舊的
        grand += (ingest_text if cfg["mode"] == "text" else ingest_vlm)(c, cfg, args.dry)
    print(f"\n合計 {grand} 段語意段落{'（未寫入）' if args.dry else '已寫入 Vector RAG'}。")


if __name__ == "__main__":
    main()
