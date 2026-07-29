"""把「結構化指標庫」整理成 EAP 上傳版 PDF——每指標一行、標明層級，平台讀得懂。

## 為什麼要這份

EAP 對上傳檔做的是**純文字檢索**，但季度**法說會簡報**是圖表導向的：同一張圖上
「集團合併 231 億」和「母公司單獨 中信金單一 ﹣43 億」靠視覺位置分開，抽成文字後
那個區隔就沒了，EAP 於是把合併數貼錯標籤成單一（實測 2026Q1 就這樣答錯）。

這份檔把**指標庫**（已解析、已對帳、已補單位的結構化數字）攤成純文字：
    一指標一行，並標明「集團合併／母公司單獨／子公司」層級。
EAP 拿到沒有圖表歧義的文字，就不容易再把不同層級的數字混在一起。

跟 make_eap_upload_pdfs.py 是同一套思路（年報→純文字上傳版），這支負責季度指標。

## 產出

outputs/eap_upload_{公司}_結構化指標.pdf，一家一份、涵蓋該公司所有期間。
數字全部來自本地指標庫，跟 Vector RAG 一致；上傳後 EAP 與本地會對得齊。

**上傳仍須你在 EAP 後台『Upload file』手動做**——平台沒有上傳 API。

用法（專案根目錄）：
    venv/Scripts/python.exe scripts/make_eap_metric_upload_pdfs.py
"""
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

from graph_rag import list_companies, list_metrics, list_periods
from standard_metrics import _SUBSIDIARY
from transcript_to_pdf import BODY_SIZE, CJK_FONT, MARGIN, PAGE_H, PAGE_W

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")

# 三種層級的判定樣態（與 api.py 的交叉驗證保持一致）
_PARENT = re.compile(r"[一-鿿]{1,4}金單一(?!季)|單體")          # 母公司單獨（金單一/單體）
_SUBS = re.compile(r"[一-鿿]{2,6}?(?:" + "|".join(
    p for p in _SUBSIDIARY if not p.startswith("(")) + r")")   # 子公司
_ALIASES = {"中國信託銀行": "中信銀行", "一銀": "第一銀行", "國泰世華": "國泰世華銀行"}
_RATIO_NAME = re.compile(r"ROE|ROA|NIM|報酬率|逾放|覆蓋率|適足率|利差|成長|年增|季增|佔比|占比|比率|率$")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def _level(name):
    """這筆是哪個層級：母公司單獨／子公司：X／集團合併／（空＝集團層級預設）。"""
    if _PARENT.search(name):
        return "母公司單獨"
    for alias, canon in _ALIASES.items():
        if alias in name:
            return f"子公司：{canon}"
    m = _SUBS.search(name)
    if m:
        return f"子公司：{m.group(0)}"
    if "合併" in name:
        return "集團合併"
    return ""


def _fmt(m):
    """數值＋單位；沒單位但名稱明顯是比率就補『%』，其餘原樣（多期字串等原封保留）。"""
    v = str(m["value"]).strip()
    unit = m.get("unit")
    if unit:
        return f"{v} {unit}"
    if _RATIO_NAME.search(str(m["metric"])) and _NUMERIC.match(v.replace(",", "")):
        return f"{v}%"
    return v


def _lines(company):
    """回傳這家公司整份文件的段落清單 [(size, text, gap)]。"""
    out = [(16, f"{company}　各期財務指標摘要（結構化）", 10),
           (BODY_SIZE, f"公司：{company}", 4),
           (BODY_SIZE,
            "說明：本檔為指標庫解析原始簡報所得的結構化摘要，一指標一行，並標明"
            "「集團合併／母公司單獨／子公司」層級，供平台精準檢索、避免把不同層級的數字混淆。",
            12)]
    for period in list_periods(company):
        ms = list_metrics(company, period)
        if not ms:
            continue
        out.append((BODY_SIZE + 1, f"── {company} {period}（共 {len(ms)} 筆）──", 6))
        for m in ms:
            name = str(m["metric"])
            lvl = _level(name)
            tag = f"（{lvl}）" if lvl else ""
            out.append((BODY_SIZE,
                        f"{company} {period} 的「{name}」為 {_fmt(m)}{tag}。", 3))
    return out


def _pdf(company, paras, out_path):
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    y = MARGIN
    for size, text, gap in paras:
        if PAGE_H - MARGIN - y < size * 3:
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN
        rect = fitz.Rect(MARGIN, y, PAGE_W - MARGIN, PAGE_H - MARGIN)
        rc = page.insert_textbox(rect, text, fontname=CJK_FONT,
                                 fontsize=size, lineheight=1.4, align=0)
        if rc < 0:                      # 這頁塞不下，換頁重放
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            y = MARGIN
            rect = fitz.Rect(MARGIN, y, PAGE_W - MARGIN, PAGE_H - MARGIN)
            rc = page.insert_textbox(rect, text, fontname=CJK_FONT,
                                     fontsize=size, lineheight=1.4, align=0)
        y = (PAGE_H - MARGIN) - max(rc, 0) + gap
    n = len(doc)
    doc.save(out_path)
    doc.close()
    return n


def main():
    os.makedirs(OUT, exist_ok=True)
    print("產生 EAP 上傳版『結構化指標』PDF（純文字、一指標一行、標明層級）：\n")
    total = 0
    for company in list_companies():
        paras = _lines(company)
        rows = sum(1 for s, t, g in paras if t.startswith(company + " ") and "的「" in t)
        out_path = os.path.join(OUT, f"eap_upload_{company}_結構化指標.pdf")
        n = _pdf(company, paras, out_path)
        kb = os.path.getsize(out_path) // 1024
        total += rows
        print(f"  ✓ {company}：{rows} 筆指標 → {n} 頁 PDF（{kb} KB）")
        print(f"     {out_path}")
    print(f"\n完成，共 {total} 筆指標。請在 EAP 後台『Upload file』手動上傳這幾份"
          f"（平台沒有上傳 API，這步只能手動）。")


if __name__ == "__main__":
    main()
