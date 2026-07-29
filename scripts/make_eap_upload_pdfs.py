"""把三家年報整理成「EAP 上傳版」PDF——乾淨、只含前段敘述、平台讀得懂。

## 為什麼不直接傳原始年報

  中信 183 頁、玉山 140 頁：夾帶 200 頁四大報表與附註（表格），
        對「怎麼解釋下滑」這類敘述問題是雜訊，還讓檔案變大、檢索被稀釋。
  國泰 82 頁：前段敘述（致股東報告書）用自訂 CID 字型，
        EAP 自己抽文字也會抽到亂碼「㕜岲ꆄ䱾」——等於上傳平台讀不懂的檔。

## 一律產「純文字 PDF」

  EAP 對上傳檔做的是文字檢索，圖表對它沒有幫助；而切原始頁會把高解析圖表一起帶進去，
  中信切 44 頁就 59MB，平台可能直接拒收。所以三份都產純文字版（幾十 KB）：
    中信、玉山：文字層乾淨，直接抽敘述頁的文字。
    國泰　　　：原始字型是亂碼，改用本地已 VLM 還原好的乾淨中文（索引進 Vector RAG 那批）。

兩種產出都只含敘述、跟本地 Vector RAG 的內容一致——上傳後 EAP 與本地會查到同一批
資料，交叉驗證才對得齊。

輸出到 outputs/，檔名 annual_upload_公司2025.pdf。實際上傳仍須你在 EAP 後台手動做
（平台沒有上傳 API）。

用法：
    venv/Scripts/python.exe scripts/make_eap_upload_pdfs.py
"""
import os
import sys

import fitz

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

from transcript_to_pdf import CJK_FONT, PAGE_W, PAGE_H, MARGIN, BODY_SIZE, _clean

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "outputs")

# 沿用 ingest_annual_reports.py 定過的敘述頁判準，兩支要一致
SRC = {
    "中信金控": {"path": r"C:\Users\jenny wang\Downloads\中信金2025.pdf", "mode": "extract"},
    "玉山金控": {"path": r"C:\Users\jenny wang\Downloads\玉山金2025.pdf", "mode": "extract"},
    "國泰金控": {"path": r"C:\Users\jenny wang\Downloads\國泰金2025.pdf", "mode": "vlm"},
}
SCAN = 45


def _cjk(t):
    return sum(1 for c in t if "一" <= c <= "鿿") / max(len(t), 1)


def _digit(t):
    return sum(1 for c in t if c.isdigit()) / max(len(t), 1)


def _is_narrative(t):
    return _cjk(t) > 0.35 and _digit(t) < 0.25 and len(t.strip()) > 400


def rows_from_extract(path):
    """中信／玉山：直接抽敘述頁的乾淨文字，回傳 [(頁碼, 文字)]。"""
    src = fitz.open(path)
    rows = [(i + 1, src[i].get_text()) for i in range(min(SCAN, len(src)))
            if _is_narrative(src[i].get_text())]
    src.close()
    return rows


def rows_from_vector(company):
    """國泰：從 Vector RAG 已還原的乾淨中文段落，回傳 [(頁碼, 文字)]。"""
    from vector_rag import collection
    got = collection.get()
    rows = [(m.get("page", 0), d)
            for d, m in zip(got.get("documents", []) or [], got.get("metadatas", []) or [])
            if m.get("source") == f"{company} 2025年報"]
    rows.sort(key=lambda t: t[0])
    return rows


def text_pdf(company, rows, out_path):
    """把 [(頁碼, 文字)] 排成一份純文字 PDF（用內建繁中字型，檔案小、平台讀得懂）。"""
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN

    def put(text, size, gap):
        nonlocal page, y
        for para in text.split("\n"):
            if not para.strip():
                y += gap; continue
            if PAGE_H - MARGIN - y < size * 3:
                page = doc.new_page(width=PAGE_W, height=PAGE_H); y = MARGIN
            rect = fitz.Rect(MARGIN, y, PAGE_W - MARGIN, PAGE_H - MARGIN)
            rc = page.insert_textbox(rect, para, fontname=CJK_FONT,
                                     fontsize=size, lineheight=1.5, align=0)
            if rc < 0:
                page = doc.new_page(width=PAGE_W, height=PAGE_H); y = MARGIN
                rect = fitz.Rect(MARGIN, y, PAGE_W - MARGIN, PAGE_H - MARGIN)
                page.insert_textbox(rect, para, fontname=CJK_FONT,
                                    fontsize=size, lineheight=1.5, align=0)
            y = (PAGE_H - MARGIN) - max(rc, 0) if rc >= 0 else MARGIN
            y += gap

    put(f"{company}　2025 年報（敘述摘錄）", 16, 10)
    put(f"公司：{company}　｜　期間：2025年報", BODY_SIZE, 4)
    put("內容：致股東報告書、營運概況、分部門獲利、風險與財務說明。", BODY_SIZE, 4)
    put("（本檔為原始年報前段敘述章節之文字摘錄，供語意檢索使用；"
        "完整財務報表請見原始年報。）", BODY_SIZE, 14)
    last = None
    for pg, text in rows:
        if pg != last:
            put(f"── 原始年報第 {pg} 頁 ──", BODY_SIZE + 1, 6)
            last = pg
        put(_clean(text), BODY_SIZE, 6)
    doc.save(out_path)
    n = len(doc)
    doc.close()
    return n, [p for p, _ in rows]


def main():
    print("產生 EAP 上傳版 PDF（純文字、只含前段敘述）：\n")
    for company, cfg in SRC.items():
        if cfg["mode"] == "extract" and not os.path.exists(cfg["path"]):
            print(f"  找不到 {cfg['path']}，跳過"); continue
        slug = "".join(c for c in company if c.isalnum())
        out_path = os.path.join(OUT, f"annual_upload_{slug}2025.pdf")
        if cfg["mode"] == "extract":
            rows = rows_from_extract(cfg["path"])
            tag = f"抽出 {len(rows)} 頁敘述文字"
        else:
            rows = rows_from_vector(company)
            tag = f"{len(rows)} 段 VLM 還原文字（原始字型亂碼）"
        n, _ = text_pdf(company, rows, out_path)
        kb = os.path.getsize(out_path) // 1024
        print(f"  ✓ {company}：{tag} → {n} 頁 PDF（{kb} KB）")
        print(f"     {out_path}")
    print("\n完成。請在 EAP 後台『Upload file』手動上傳這三份——平台沒有上傳 API，這步只能手動。")


if __name__ == "__main__":
    main()
