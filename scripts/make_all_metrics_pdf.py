"""把「結構化指標庫」全部公司、全部期間合併成**一份** PDF，供 EAP 後台『Upload file』上傳。

跟 make_eap_metric_upload_pdfs.py 是同一套思路（一指標一行、標明層級），差別只在
那支是一家公司一份，這支是全部公司合併成一份，方便只上傳一個檔案。

## 為什麼要多印「衍生比率」

原始指標只有「負債總計」「資產總計」這種絕對金額，沒有「資產負債率」這個現成欄位——
它是拿兩個絕對金額相除算出來的（跟 standard_metrics.py 用的是同一套公式）。
實測過：PDF 裡只放兩個絕對金額、不把算好的比率明講出來，EAP 遇到「資產負債率」這種問題
會自己去翻更長的官方財報硬猜，猜錯還配一個真實但不相關的頁碼當引用（見
簡報與文件/EAP平台提示詞設定.md 記錄的幻覺案例）。這裡把衍生比率直接寫成一行文字，
EAP 讀了就有現成答案，不必自己算。

用法（專案根目錄）：
    venv/Scripts/python.exe scripts/make_all_metrics_pdf.py
"""
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.stdout.reconfigure(encoding="utf-8")

from graph_rag import list_companies, list_metrics, list_periods
from standard_metrics import DERIVED_METRICS, _SUBSIDIARY, _derive
from transcript_to_pdf import BODY_SIZE, CJK_FONT, MARGIN, PAGE_H, PAGE_W

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
OUT_PATH = os.path.join(OUT, "eap_upload_全部公司_結構化指標.pdf")

# 三種層級的判定樣態（與 make_eap_metric_upload_pdfs.py / api.py 的交叉驗證保持一致）
_PARENT = re.compile(r"[一-鿿]{1,4}金單一(?!季)|單體")
_SUBS = re.compile(r"[一-鿿]{2,6}?(?:" + "|".join(
    p for p in _SUBSIDIARY if not p.startswith("(")) + r")")
_ALIASES = {"中國信託銀行": "中信銀行", "一銀": "第一銀行", "國泰世華": "國泰世華銀行"}
_RATIO_NAME = re.compile(r"ROE|ROA|NIM|報酬率|逾放|覆蓋率|適足率|利差|成長|年增|季增|佔比|占比|比率|率$")
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def _level(name):
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
    v = str(m["value"]).strip()
    unit = m.get("unit")
    if unit:
        return f"{v} {unit}"
    if _RATIO_NAME.search(str(m["metric"])) and _NUMERIC.match(v.replace(",", "")):
        return f"{v}%"
    return v


def _lines():
    """回傳全部公司合併後的段落清單 [(size, text, gap)]。"""
    out = [(16, "財報分析　全部公司各期財務指標摘要（結構化）", 10),
           (BODY_SIZE,
            "說明：本檔為指標庫解析原始簡報所得的結構化摘要，一指標一行，並標明"
            "「集團合併／母公司單獨／子公司」層級，供平台精準檢索、避免把不同層級的數字混淆。"
            "「資產負債率」等衍生比率是拿兩個絕對金額即時相除算好直接列出，"
            "不需要平台自己從其他文件推算。",
            12)]
    total = 0
    for company in list_companies():
        out.append((14, f"■ {company}", 8))
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
                total += 1

            by_name = {mm["metric"]: mm for mm in ms}
            for spec in DERIVED_METRICS:
                v = _derive(by_name, spec)
                if v is None:
                    continue
                out.append((BODY_SIZE,
                            f"{company} {period} 的「{spec['label']}」為 {round(v, 2)}"
                            f"{spec['unit']}（衍生計算：{spec['numerator']} ÷ {spec['denominator']}，"
                            f"集團合併）。", 3))
                total += 1
    return out, total


def _pdf(paras, out_path):
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
        if rc < 0:
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
    print("產生 EAP 上傳版『全部公司結構化指標』合併 PDF：\n")
    paras, total = _lines()
    n = _pdf(paras, OUT_PATH)
    kb = os.path.getsize(OUT_PATH) // 1024
    print(f"  ✓ 共 {total} 筆（含衍生比率）→ {n} 頁 PDF（{kb} KB）")
    print(f"     {OUT_PATH}")
    print("\n請在 EAP 後台『Upload file』手動上傳這份（平台沒有上傳 API，這步只能手動）。")


if __name__ == "__main__":
    main()
