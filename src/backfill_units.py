"""把 outputs/parsed_*.json 裡的單位，回填／修正到知識圖譜。

為什麼需要這支：
  1. 中信金控的節點是早期版本匯入的，當時 add_metric_datapoint 還沒有存單位，
     之後 JSON 更新了也沒重匯，導致 152 筆「原始簡報明明有標單位、圖譜卻是空的」。
     沒單位的金額在前端只能顯示「單位未標示」——EAP 答得出「百萬元」，我們卻答不出。
  2. normalize_unit() 原本把「拾億元／十億元」壓成「億元」，差整整 10 倍。
     修好函式之後，已經存進圖譜的 116 筆舊值仍是錯的，要一併校正。

刻意只動 unit 這一個欄位：不新增、不刪除、不改數值，所以重跑安全，
也不會像重新 ingest 那樣把手工整理過的節點洗掉。

用法：
    venv/Scripts/python.exe src/backfill_units.py            # 先看要改什麼（不寫入）
    venv/Scripts/python.exe src/backfill_units.py --apply    # 真的寫入
"""
import argparse
import glob
import json
import os

from graph_rag import G, normalize_unit, save_graph

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 已人工核對過、確定「圖譜對、JSON 錯」的節點，列出來免得之後有人反過來把對的改壞。
# 玉山 2026Q1 財報：本期淨利 10,067,506 千元 ＝ 100.6 億，和簡報的 10,057 百萬元對得起來，
# 所以千元是對的；JSON 那格 VLM 抄成「元」。
VERIFIED_GRAPH_WINS = {
    "玉山金控|稅前淨利|2026Q1財報",
    "玉山金控|本期淨利|2026Q1財報",
    "玉山金控|本期綜合損益總額|2026Q1財報",
    "玉山金控|母公司業主淨利歸屬|2026Q1財報",
    "玉山金控|歸屬於母公司業主之綜合損益總額 (115年1月1日至3月31日)|2026Q1財報",
}

# 看不出量級的寫法（「新台幣」「NT$」「TWD」）不回填：
# 標上去等於宣稱知道量級，其實不知道，比誠實顯示「單位未標示」更糟。
_NO_MAGNITUDE = {"新台幣", "新臺幣", "台幣", "臺幣", "NT$", "NTD", "TWD", "元"}


def _legacy_normalize(raw):
    """重現「修好之前」的 normalize_unit——它沒處理『拾』，而且『億』的判斷排在
    『十億』前面，所以會把十億元壓成億元。用它來認出哪些既有單位是我們自己寫錯的：
    只有『圖譜現值 ＝ 舊版會產出的值』才確定是這個 bug 造成的，可以放心自動校正；
    其餘的不一致是 VLM 兩次解析講法不同，機器分不出誰對，只回報給人看。
    """
    if not raw:
        return raw
    u = str(raw).strip()
    if any(k in u for k in ("盾", "美元", "USD", "人民幣", "RMB")):
        return u
    lower = u.lower()
    if u in ("%", "百分比") or "percentage" in lower:
        return "%"
    u = u.replace("臺", "台").replace("仟", "千").replace("佰", "百")
    if "千元" in u:
        return "千元"
    if "百萬" in u or "million" in lower:
        return "百萬元"
    if "bn" in lower or "billion" in lower:
        return "十億元"
    if "兆" in u:
        return "兆元"
    if "億" in u:
        return "億元"
    if u in ("元", "新台幣元"):
        return "元"
    return u


def _useful_unit(raw):
    """把 JSON 的原始單位轉成圖譜要存的寫法；無量級或空值回 None。"""
    if not raw or not str(raw).strip():
        return None
    if str(raw).strip() in _NO_MAGNITUDE and str(raw).strip() != "元":
        return None
    u = normalize_unit(raw)
    if not u or u in _NO_MAGNITUDE - {"元"}:
        return None
    return u


def collect(json_dir=None):
    """掃過所有 parsed JSON，回傳 (要補的, 要改的) 兩份清單。"""
    json_dir = json_dir or os.path.join(BASE_DIR, "outputs")
    fill, fix, conflicts = [], [], []
    for path in sorted(glob.glob(os.path.join(json_dir, "parsed_*.json"))):
        base = os.path.basename(path)[len("parsed_"):-len(".json")]
        if "_" not in base:
            continue
        company, period = base.rsplit("_", 1)
        with open(path, "r", encoding="utf-8") as f:
            pages = json.load(f)

        for page in pages:
            for m in page.get("key_metrics", []):
                name, value = m.get("指標名稱"), m.get("數值")
                if not name or not value:
                    continue
                want = _useful_unit(m.get("單位"))
                if not want:
                    continue
                node_id = f"{company}|{name}|{period}"
                if node_id not in G.nodes:
                    continue  # JSON 有、圖譜沒有：那是節點層級的問題，這支不處理
                # 同一份簡報常在不同頁重複用同一個指標名稱、單位卻不同
                # （玉山「稅後淨利」有一頁 8,792 百萬元、另一頁 87.9 億元）。
                # 節點是用名稱當 key，只能存一筆——所以要數值也對得上，
                # 才能確定講的是同一個數字，否則就是拿別頁的單位去蓋，會蓋錯。
                if str(G.nodes[node_id].get("value")).strip() != str(value).strip():
                    continue
                cur = G.nodes[node_id].get("unit")
                if not cur:
                    fill.append((node_id, want))
                elif cur == want:
                    pass
                elif cur == _legacy_normalize(m.get("單位")):
                    fix.append((node_id, cur, want))      # 確定是舊版 normalize 的鍋
                else:
                    conflicts.append((node_id, cur, want))  # 兩份解析講法不同，交給人判斷
    return fill, fix, conflicts


def collect_renormalize():
    """把既有節點的單位重跑一次 normalize_unit。

    normalize_unit 修過幾次（十億元、NT$ mn、每股類的 NT$→元），但改的是「之後匯入」的路徑，
    早就存進圖譜的舊值不會自己更新。這支負責把存量補上，重跑安全（同樣的輸入得同樣的輸出）。
    """
    out = []
    for node_id, d in G.nodes(data=True):
        cur = d.get("unit")
        if not cur:
            continue
        want = normalize_unit(cur, d.get("metric"))
        if want and want != cur:
            out.append((node_id, cur, want))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的寫入圖譜；不加只列出預計變更")
    args = ap.parse_args()

    renorm = collect_renormalize()
    print(f"既有單位重新正規化：{len(renorm)} 筆")
    for node_id, cur, want in renorm[:20]:
        print(f"   ~ {node_id}  {cur}  ->  {want}")
    if len(renorm) > 20:
        print(f"   …（其餘 {len(renorm) - 20} 筆）")
    if args.apply and renorm:
        for node_id, _cur, want in renorm:
            G.nodes[node_id]["unit"] = want
        save_graph()
    print()

    fill, fix, conflicts = collect()

    print(f"缺單位、可從原始 JSON 補上：{len(fill)} 筆")
    for node_id, want in fill[:20]:
        print(f"   + {node_id}  ->  {want}")
    if len(fill) > 20:
        print(f"   …（其餘 {len(fill) - 20} 筆）")

    print(f"\n舊版 normalize_unit 換算錯、可安全校正：{len(fix)} 筆")
    for node_id, cur, want in fix[:20]:
        print(f"   ~ {node_id}  {cur}  ->  {want}")
    if len(fix) > 20:
        print(f"   …（其餘 {len(fix) - 20} 筆）")

    if conflicts:
        print(f"\n兩份解析講法不同、不自動更動：{len(conflicts)} 筆")
        for node_id, cur, want in conflicts:
            note = "（已核對，圖譜正確）" if node_id in VERIFIED_GRAPH_WINS else "（請人工確認）"
            print(f"   ? {node_id}  圖譜={cur}  JSON={want} {note}")

    if not args.apply:
        print("\n（預覽模式，未寫入。確認無誤後加 --apply 實際執行）")
        return

    for node_id, want in fill:
        G.nodes[node_id]["unit"] = want
    for node_id, _cur, want in fix:
        G.nodes[node_id]["unit"] = want
    save_graph()
    print(f"\n已寫入 {os.path.relpath(os.path.join(BASE_DIR, 'vector_db', 'graph_data.json'), BASE_DIR)}"
          f"：補 {len(fill)} 筆、校正 {len(fix)} 筆。")


if __name__ == "__main__":
    main()
