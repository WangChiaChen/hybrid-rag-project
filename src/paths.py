"""檔名與路徑的安全處理。

為什麼需要：公司名稱與期間是使用者從網頁填的，卻會被拿去組資料夾與檔名
（pages/{公司}_{期間}/、outputs/parsed_{公司}_{期間}.json）。
原本只有 /api/upload 自己的暫存檔清掉了斜線，傳給背景工作的仍是原始值，
所以填「../../../tmp/x」就能把 PNG 與 JSON 寫到專案目錄外——實測可重現。

清理集中在這裡，各處呼叫同一支，才不會又漏掉某條路徑。
"""
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 路徑分隔符、上層參照、Windows 保留字元，以及控制字元
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_component(name, fallback="unnamed"):
    """把使用者輸入轉成「只能當單一層檔名」的字串。

    保留中文（公司名稱要看得懂），只拿掉會改變路徑意義的字元。
    """
    s = _UNSAFE.sub("_", str(name or "")).strip()
    s = s.replace("..", "_")          # 擋掉上層參照
    s = s.strip(". ")                 # Windows 不允許檔名結尾是點或空白
    s = s[:80]                        # 避免超長檔名撐爆檔案系統上限
    return s or fallback


def inside(path, root):
    """path 是否確實落在 root 底下（解析過 .. 與符號連結之後）。"""
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    return path_real == root_real or path_real.startswith(root_real + os.sep)


def safe_join(root, *parts):
    """組路徑並確認沒有跳出 root；跳出就拋錯而不是默默寫到別的地方。"""
    path = os.path.join(root, *[safe_component(p) for p in parts])
    if not inside(path, root):
        raise ValueError(f"路徑不合法：{path}")
    return path


def purge_old(directory, max_age_hours=24):
    """刪掉 directory 底下超過指定時數沒被動過的檔案／資料夾，回傳刪除數量。

    上傳的暫存檔（uploads_temp/）與 PDF 轉出的圖片（pages/）現在沒有人清，
    長期跑下去會把磁碟塞滿。這支只刪「舊的」，不會動到正在處理中的工作。
    """
    import shutil

    if not os.path.isdir(directory):
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            removed += 1
        except OSError:
            pass      # 檔案正被使用中就跳過，下次再清
    return removed
