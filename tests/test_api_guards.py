"""公開部署的兩道防護：限流與上傳大小上限。

刻意不透過真的打 /api/chat 來測限流——那會真的呼叫 Gemini，把測試變成燒額度的行為
（而限流存在的理由正是額度）。改成直接測 _rate_limit 這個函式本身。
上傳則走 TestClient，但只測「被擋下」的路徑：通過的話會啟動背景解析執行緒，一樣要花錢。
"""
import io
import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import api


def fake_request(ip="1.2.3.4", forwarded=None):
    """最小的假 request：_rate_limit 只用到 headers 與 client.host。"""
    headers = {"x-forwarded-for": forwarded} if forwarded else {}
    return types.SimpleNamespace(
        headers=headers,
        client=types.SimpleNamespace(host=ip),
    )


@pytest.fixture(autouse=True)
def 清空限流計數():
    """限流狀態是模組層級的全域 dict，不清的話測試之間會互相影響。"""
    api._RATE_HITS.clear()
    yield
    api._RATE_HITS.clear()


class Test限流:
    def test_額度內放行(self):
        limit, _ = api._RATE_LIMITS["chat"]
        req = fake_request()
        for _ in range(limit):
            api._rate_limit(req, "chat")   # 不該丟例外

    def test_超過就擋下並回429(self):
        limit, _ = api._RATE_LIMITS["chat"]
        req = fake_request()
        for _ in range(limit):
            api._rate_limit(req, "chat")
        with pytest.raises(HTTPException) as e:
            api._rate_limit(req, "chat")
        assert e.value.status_code == 429
        # 要告訴前端還要等多久，否則使用者只能盲目重試
        assert "Retry-After" in e.value.headers

    def test_不同來源各自計算(self):
        """一個人打爆額度不該連坐其他使用者。"""
        limit, _ = api._RATE_LIMITS["chat"]
        for _ in range(limit):
            api._rate_limit(fake_request(ip="1.1.1.1"), "chat")
        api._rate_limit(fake_request(ip="2.2.2.2"), "chat")   # 另一個 IP 仍可通過

    def test_不同用途各自計算(self):
        """問答打滿不該連帶擋掉產報告。"""
        limit, _ = api._RATE_LIMITS["chat"]
        req = fake_request()
        for _ in range(limit):
            api._rate_limit(req, "chat")
        api._rate_limit(req, "report")

    def test_反向代理後面要看真正的來源IP(self):
        """部署在 Render 這類代理後面時，request.client.host 是代理的 IP，
        所有使用者看起來會是同一個人——限流就變成全站共用一份額度。"""
        limit, _ = api._RATE_LIMITS["chat"]
        proxy = "10.0.0.1"
        for _ in range(limit):
            api._rate_limit(fake_request(ip=proxy, forwarded="203.0.113.5"), "chat")
        # 同一台代理、但真實來源不同 → 應該放行
        api._rate_limit(fake_request(ip=proxy, forwarded="203.0.113.99"), "chat")
        # 同一個真實來源 → 應該擋下
        with pytest.raises(HTTPException) as e:
            api._rate_limit(fake_request(ip=proxy, forwarded="203.0.113.5"), "chat")
        assert e.value.status_code == 429

    def test_取第一段而不是整串(self):
        """X-Forwarded-For 經過多層代理會是「原始IP, 代理1, 代理2」。"""
        assert api._client_key(fake_request(forwarded="203.0.113.5, 10.0.0.1")) == "203.0.113.5"


class Test限流涵蓋範圍:
    """限流漏掉某條端點是「加了防護卻沒真的擋住」的典型漏洞，而且看不出來——
    /api/summary 就是 GET、長得跟其他讀資料的端點一樣，實際上每按一次就呼叫一次 Gemini。
    這裡不靠人工記得，直接掃 api.py 的原始碼：只要端點裡出現會呼叫外部模型的函式，
    就必須也出現 _rate_limit。
    """

    LLM_MARKERS = {
        "generate_content", "generate_content_stream",
        "ask_question", "ask_smart", "ask_smart_stream",
        "answer_question", "_augment_with_local", "_finalize_eap",
        # 上傳本身不呼叫模型，但它排的背景工作會跑 VLM 逐頁解析／STT 轉錄，
        # 是所有端點裡最貴的一條。列進來，限流被拿掉時一樣會被抓到。
        "_run_upload_job",
    }

    def endpoints(self):
        import ast
        import os

        src = open(os.path.join(api.BASE_DIR, "src", "api.py"), encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_endpoint = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "app"
                and d.func.attr in ("get", "post", "delete")
                for d in node.decorator_list
            )
            if is_endpoint:
                names = {
                    n.attr if isinstance(n, ast.Attribute) else n.id
                    for n in ast.walk(node)
                    if isinstance(n, (ast.Name, ast.Attribute))
                }
                yield node.name, names

    def test_會呼叫模型的端點都要限流(self):
        漏掉的 = [
            name for name, names in self.endpoints()
            if (names & self.LLM_MARKERS) and "_rate_limit" not in names
        ]
        assert not 漏掉的, f"這些端點會呼叫外部模型卻沒限流：{漏掉的}"

    def test_用到的每個限流桶都有設定(self):
        """打錯 bucket 名稱會直接 KeyError 讓端點 500，而且只有真的被打到才會發現。"""
        import ast
        import os

        src = open(os.path.join(api.BASE_DIR, "src", "api.py"), encoding="utf-8").read()
        used = {
            node.args[1].value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_rate_limit"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        }
        assert used, "找不到任何 _rate_limit 呼叫，測試本身可能失效了"
        assert used <= set(api._RATE_LIMITS), f"用了沒設定的桶：{used - set(api._RATE_LIMITS)}"


class Test上傳防護:
    @pytest.fixture
    def client(self):
        return TestClient(api.app)

    def test_擋掉不支援的副檔名(self, client):
        r = client.post(
            "/api/upload",
            data={"company": "測試", "period": "2026Q1"},
            files={"file": ("evil.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_超過大小上限回413(self, client, monkeypatch):
        """把上限暫時調小來測，不用真的傳 50MB 進來。"""
        monkeypatch.setitem(api._MAX_UPLOAD_BYTES, "pdf", 1024)
        r = client.post(
            "/api/upload",
            data={"company": "測試", "period": "2026Q1"},
            files={"file": ("big.pdf", io.BytesIO(b"x" * 5000), "application/pdf")},
        )
        assert r.status_code == 413

    def test_超過上限不留下半個檔(self, client, monkeypatch, tmp_path):
        """分段寫入時超限，已經落地的部分要刪掉——否則之後的解析會拿到壞檔。"""
        import os
        monkeypatch.setitem(api._MAX_UPLOAD_BYTES, "pdf", 1024)
        temp_dir = os.path.join(api.BASE_DIR, "uploads_temp")
        client.post(
            "/api/upload",
            data={"company": "＿殘檔測試＿", "period": "2026Q1"},
            files={"file": ("big.pdf", io.BytesIO(b"x" * 5000), "application/pdf")},
        )
        leftover = os.path.join(temp_dir, "＿殘檔測試＿_2026Q1.pdf")
        assert not os.path.exists(leftover)

    def test_空檔案被擋下(self, client):
        r = client.post(
            "/api/upload",
            data={"company": "測試", "period": "2026Q1"},
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        )
        assert r.status_code == 400

    def test_缺公司或期間被擋下(self, client):
        r = client.post(
            "/api/upload",
            data={"company": "  ", "period": "2026Q1"},
            files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert r.status_code == 400
