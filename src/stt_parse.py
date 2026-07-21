"""Phase 2.2：法說會錄音轉文字（STT）
用 Gemini 免費版直接聽懂音檔內容，不用另外接語音辨識服務
TODO: 拿到 EAP 平台文件後，如果平台有 STT API，可以換掉這裡

用法：
  python src/stt_parse.py --audio call.mp3 --company 中信金控 --period 2026Q1
"""
from google import genai
from google.genai import types
import os
import time
import argparse
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# 跟 agent_router 一樣「用到才建立」——在 import 時就建立的話，部署環境沒設
# GEMINI_API_KEY 會直接拋 ValueError，上傳錄音會掛在看不懂的錯誤上。
_client = None


def get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "尚未設定 GEMINI_API_KEY，無法把錄音轉成逐字稿。"
                "請在部署平台的環境變數（或本機 .env）填入你的 Gemini 金鑰。")
        _client = genai.Client(api_key=key)
    return _client


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
                raise
            else:
                raise
    print(f"\n重試 {max_retries} 次後仍失敗，真正的錯誤訊息如下：\n{last_error}\n")
    raise RuntimeError(f"重試多次仍失敗：{last_error}")


STT_PROMPT = """你是財務法說會逐字稿助手。請聽這段法說會錄音，整理成逐字稿摘要，並標註大致的說話人角色
（例如：主持人、財務長、分析師提問）。

輸出格式範例：
[財務長] 本季手續費淨收益因財富管理業務放緩而季減...
[分析師提問] 請問財富管理業務放緩的原因是否與市場波動有關？
[財務長] 是的，主要受...

請專注在財務數字的解釋、經營團隊對業績變化原因的說明，以及分析師的提問與回答，
不需要逐字翻譯每一句話，重點摘要即可。
"""


def _guess_mime_type(path):
    ext = path.lower().rsplit(".", 1)[-1]
    return {
        "mp3": "audio/mp3", "wav": "audio/wav", "m4a": "audio/mp4",
        "mp4": "audio/mp4", "aac": "audio/aac", "ogg": "audio/ogg",
    }.get(ext, "audio/mp3")


def transcribe_audio(audio_path):
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    mime_type = _guess_mime_type(audio_path)

    response = call_with_retry(lambda: get_client().models.generate_content(
        model="gemini-flash-lite-latest",
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            STT_PROMPT,
        ],
    ))
    return response.text


import re


def _chunk_transcript(text, target=260, hard_max=600):
    """把逐字稿切成多個「小段」再索引。

    原本整份逐字稿存成一段——它的向量是所有主題（獲利、發債、聯名卡…）平均，
    語意檢索時相似度被稀釋、撈不到特定主題。切成小段後，問「發債計畫」就能命中
    講發債那一段。以說話人 [角色]／段落為界，太短的合併、太長的再依句子切開。
    """
    text = str(text).replace("\r", "")
    blocks, cur = [], ""
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            if cur:
                blocks.append(cur); cur = ""
            continue
        new = bool(re.match(r"^(\*\*)?\[|^[\*\-•#]", s))  # 說話人標籤／項目符號／標題 → 換新段
        if new and cur:
            blocks.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip() if cur else s
    if cur:
        blocks.append(cur)

    def _clean(b):
        b = b.replace("**", "")
        b = re.sub(r"^\s*[\*\-•#]+\s*", "", b)
        return b.strip()

    blocks = [c for c in (_clean(b) for b in blocks) if len(c) >= 6]

    chunks, buf = [], ""
    for b in blocks:
        if len(b) > hard_max:  # 單段太長 → 依句尾標點再切
            for part in re.split(r"(?<=[。！？!?])", b):
                part = part.strip()
                if not part:
                    continue
                if buf and len(buf) + len(part) > target:
                    chunks.append(buf); buf = ""
                buf = (buf + part).strip()
            continue
        if buf and len(buf) + len(b) > target:
            chunks.append(buf); buf = ""
        buf = (buf + "  " + b).strip() if buf else b
    if buf:
        chunks.append(buf)
    return chunks


def index_transcript(company, period, transcript):
    """把一份逐字稿切段後索引進 Vector RAG（重傳會先清掉舊的，避免重複）。"""
    from vector_rag import index_narrative, delete_by_source

    source = f"{company} {period} 法說會錄音"
    delete_by_source(source)
    chunks = _chunk_transcript(transcript)
    for i, ch in enumerate(chunks, 1):
        index_narrative(
            doc_id=f"{company}_{period}_audio_{i}",
            text=ch,
            metadata={"source": source, "page": f"錄音 #{i}"},
        )
    return len(chunks)


def run_stt_and_ingest(audio_path, company, period):
    """整合函式：錄音 -> 逐字稿 -> 切段寫入 Vector RAG，可被 app.py 呼叫"""
    transcript = transcribe_audio(audio_path)

    out_dir = os.path.join(BASE_DIR, "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"transcript_{company}_{period}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(transcript)

    index_transcript(company, period, transcript)
    return transcript, out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="錄音檔路徑（mp3/wav/m4a 等）")
    parser.add_argument("--company", default="中信金控")
    parser.add_argument("--period", default="2026Q1")
    args = parser.parse_args()

    audio_path = args.audio if os.path.isabs(args.audio) else os.path.join(BASE_DIR, args.audio)
    if os.path.exists(audio_path):
        transcript, out_path = run_stt_and_ingest(audio_path, args.company, args.period)
        print(transcript)
        print(f"\n逐字稿已存至 {out_path}")
        print("已寫入 Vector RAG 語意索引")
    else:
        print(f"找不到 {audio_path}")
