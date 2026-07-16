"""Phase 1.2：用 VLM 解析簡報圖片，取代傳統 OCR
用 Gemini 免費版（gemini-flash-lite-latest 支援看圖，額度免費）
TODO: 拿到 EAP 平台文件後，把這裡換成 EAP 的 VLM endpoint

用法：
  python src/vlm_parse.py                                        # 用預設值（中信金控 2026Q1 sample.pdf）
  python src/vlm_parse.py --pdf other.pdf --company 玉山金控 --period 2026Q1   # 處理第二組資料（跨機構比較用）
  python src/vlm_parse.py --company 中信金控 --period 2025Q4       # 處理同公司的上一季（QoQ 比較用）
"""
from google import genai
from google.genai import types
import json
import os
import time
import argparse
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(BASE_DIR, ".env"))
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def call_with_retry(fn, max_retries=4, base_wait=5):
    """遇到 503（伺服器忙線）或 429 每分鐘額度時自動等待後重試；
    429 每日額度用完則直接拋出，重試沒有意義"""
    last_error = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_error = e
            error_msg = str(e)
            if "503" in error_msg or "UNAVAILABLE" in error_msg:
                wait_time = base_wait * (attempt + 1)
                print(f"  伺服器忙線，{wait_time} 秒後重試（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait_time)
            elif ("429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg) and "PerMinute" in error_msg:
                wait_time = 65
                print(f"  已達每分鐘請求上限，{wait_time} 秒後重試（第 {attempt + 1}/{max_retries} 次）...")
                time.sleep(wait_time)
            elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                # 每日額度用完，重試沒有用，直接拋出
                raise
            else:
                raise
    print(f"\n重試 {max_retries} 次後仍失敗，真正的錯誤訊息如下：\n{last_error}\n")
    raise RuntimeError(f"重試多次仍失敗：{last_error}")


PROMPT = """你是財務簡報解析專家。請看這張投資人簡報圖片，只回傳以下格式的 JSON，不要有其他文字或 markdown 符號：
{
  "page_type": "圖表/文字/表格/封面",
  "title": "這頁的標題",
  "key_metrics": [{"指標名稱": "", "數值": "", "單位": "", "QoQ": "", "YoY": ""}],
  "narrative_text": "非數字類的文字重點摘要，沒有就填空字串"
}"""


def parse_slide_image(image_path):
    with open(image_path, "rb") as f:
        img_bytes = f.read()

    response = call_with_retry(lambda: client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            PROMPT,
        ],
    ))
    raw = response.text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"page_type": "unknown", "title": "", "key_metrics": [], "narrative_text": raw}


def run_vlm_parse(pdf_path, company, period, max_pages=10, progress_callback=None, pace_seconds=4.5):
    """可被 app.py 直接呼叫的整合函式：PDF -> 圖片 -> VLM 解析 -> 存成 JSON
    progress_callback(current, total) 可選，讓呼叫端更新進度條
    pace_seconds：每次呼叫 API 之間主動間隔的秒數，用來避免撞到「每分鐘請求上限」
                  （免費版通常是每分鐘 15 次，換算至少要間隔 4 秒，這裡預設抓 4.5 秒留一點餘裕）
    回傳 (解析結果 list, 存檔路徑)
    """
    from preprocess_pdf import pdf_to_images

    output_dir = os.path.join(BASE_DIR, "pages", f"{company}_{period}")
    pages = pdf_to_images(pdf_path, output_dir=output_dir)
    pages_to_process = pages[:max_pages]

    results = []
    for i, p in enumerate(pages_to_process):
        result = parse_slide_image(p)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(pages_to_process))
        if pace_seconds and i < len(pages_to_process) - 1:
            time.sleep(pace_seconds)

    out_dir = os.path.join(BASE_DIR, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"parsed_{company}_{period}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return results, out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=os.path.join(BASE_DIR, "sample.pdf"), help="簡報 PDF 檔案路徑")
    parser.add_argument("--company", default="中信金控", help="公司名稱")
    parser.add_argument("--period", default="2026Q1", help="期間，例如 2026Q1")
    parser.add_argument("--max_pages", type=int, default=10, help="最多處理幾頁")
    args = parser.parse_args()

    pdf_path = args.pdf if os.path.isabs(args.pdf) else os.path.join(BASE_DIR, args.pdf)

    if os.path.exists(pdf_path):
        results, out_path = run_vlm_parse(
            pdf_path, args.company, args.period, max_pages=args.max_pages,
            progress_callback=lambda cur, total: print(f"解析進度 {cur}/{total} ...")
        )
        for r in results:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        print(f"\n已儲存解析結果至 {out_path}")
    else:
        print(f"找不到 {pdf_path}，請確認檔案路徑")
