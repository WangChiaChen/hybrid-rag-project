"""把 outputs/ 的法說會逐字稿轉成 PDF，供上傳到 EAP 後台。

為什麼要轉：EAP 的「Upload file」只收 PDF 與 XLSX，我們的逐字稿是 .txt，直接傳不進去。
音檔更不用說——所以流程是「錄音 →（本地 Gemini STT）→ 逐字稿 → 這支轉 PDF → 上傳 EAP」，
轉錄留在本地做，數字聽錯了看得到也改得動。

為什麼要加抬頭：逐字稿內文從頭到尾沒出現過公司名（只有檔名有），EAP 檢索時對不上
「中信金控 2025Q3」這種問法。第一頁補上公司、期間與資料性質，命中率才會正常。

用法：
    venv/Scripts/python.exe src/transcript_to_pdf.py            # 全部轉
    venv/Scripts/python.exe src/transcript_to_pdf.py --company 中信金控
"""
import argparse
import os
import re

import fitz  # pymupdf

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE_DIR, "outputs")

# PyMuPDF 內建的繁體中文字型，不必外掛字型檔就能排中文
CJK_FONT = "china-t"
PAGE_W, PAGE_H = fitz.paper_size("a4")
MARGIN = 56
LINE_H = 16
BODY_SIZE = 10.5

_QUARTER = re.compile(r"^(\d{4})Q([1-4])$")


def _period_zh(period):
    """2025Q3 -> 2025 年第三季；認不出來就原樣回傳"""
    m = _QUARTER.match(str(period))
    if not m:
        return str(period)
    return f"{m.group(1)} 年第{'一二三四'[int(m.group(2)) - 1]}季"


def _clean(text):
    """拿掉 markdown 記號。PDF 沒有粗體語意，留著 ** 只會變成雜訊字元。"""
    text = text.replace("\r", "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)      # **粗體**
    text = re.sub(r"^\s*[\*\-•]\s+", "・", text, flags=re.M)  # 項目符號統一
    return text.strip()


def build_pdf(company, period, transcript, out_path):
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = MARGIN

    def _put(para, size):
        """把一段文字放進目前頁面剩餘空間；放不下回傳 False（由呼叫端換頁重試）。
        insert_textbox 的回傳值是「剩餘高度」，負數代表塞不下。"""
        nonlocal y
        if PAGE_H - MARGIN - y < size * 2:      # 連兩行都放不下就別試了，矩形會變空的
            return False
        rect = fitz.Rect(MARGIN, y, PAGE_W - MARGIN, PAGE_H - MARGIN)
        rc = page.insert_textbox(rect, para, fontname=CJK_FONT,
                                 fontsize=size, lineheight=1.5, align=0)
        if rc < 0:
            return False
        y += (PAGE_H - MARGIN) - y - rc         # 實際佔用的高度
        return True

    def write(text, size, gap):
        """逐段寫入，寫不下就換頁。用 insert_textbox 讓它自己處理中文斷行。"""
        nonlocal y
        for para in text.split("\n"):
            if not para.strip():
                y += gap
                continue
            if _put(para, size):
                y += gap
                continue
            new_page()
            if _put(para, size):
                y += gap
                continue
            # 整頁都放不下的超長段落（法說會逐字稿不該出現，但不能讓它無限迴圈）：
            # 對半切開分頁放，寧可斷句也不要卡死或掉字。
            chunks, buf = [], para
            while buf:
                chunks.append(buf[:600])
                buf = buf[600:]
            for ch in chunks:
                if not _put(ch, size):
                    new_page()
                    _put(ch, size)
            y += gap

    title = f"{company}　{_period_zh(period)}　法說會逐字稿"
    write(title, 16, 10)
    write(f"公司：{company}　｜　期間：{period}（{_period_zh(period)}）", BODY_SIZE, 4)
    write("資料性質：法人說明會錄音，經語音轉文字後整理之逐字稿摘要。", BODY_SIZE, 4)
    write("內容包含經營團隊對業績變化的說明，以及分析師提問與回覆。", BODY_SIZE, 14)
    write(_clean(transcript), BODY_SIZE, 6)

    doc.save(out_path)
    doc.close()
    return out_path


def convert_all(company_filter=None):
    made = []
    for fn in sorted(os.listdir(OUT_DIR)):
        if not (fn.startswith("transcript_") and fn.endswith(".txt")):
            continue
        name = fn[len("transcript_"):-len(".txt")]
        if "_" not in name:
            continue
        company, period = name.rsplit("_", 1)
        if company_filter and company != company_filter:
            continue
        with open(os.path.join(OUT_DIR, fn), "r", encoding="utf-8") as f:
            transcript = f.read()
        out_path = os.path.join(OUT_DIR, f"transcript_{company}_{period}.pdf")
        build_pdf(company, period, transcript, out_path)
        made.append((company, period, out_path))
    return made


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default=None, help="只轉這一家，不填=全部")
    args = ap.parse_args()

    made = convert_all(args.company)
    if not made:
        print("outputs/ 底下沒有符合的 transcript_*.txt")
    for company, period, path in made:
        size = os.path.getsize(path) / 1024
        print(f"  {company} {period} -> {os.path.relpath(path, BASE_DIR)}（{size:.0f} KB）")
    if made:
        print("\n上傳到 EAP 後台時，建議這樣填：")
        print("  File category：法說會逐字稿")
        print("  Label        ：公司＋期間，例如「中信金控 2025Q3」")
        print("  （分類會參與檢索，Label 會顯示在聊天回答的引用來源裡）")
