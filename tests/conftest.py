"""測試共用設定。

兩個重點：

一、src/ 不是套件，程式之間互相 import 是靠「src 在 sys.path 上」（api.py 用
    `from graph_rag import ...` 而不是 `from src.graph_rag import ...`）。測試也要照做，
    否則會撞到 ModuleNotFoundError。

二、圖譜是**單一全域物件**（graph_rag.G），而且 add_metric_datapoint 預設會寫檔。
    測試若直接呼叫它，會把假資料寫進 vector_db/graph_data.json——那是整個系統唯一的
    結構化資料來源，測試污染了它，畫面上就會多出「＿測試公司＿」。
    所以一律用 temp_metrics fixture：只改記憶體、不存檔、結束後移除並清快取。
"""
import os
import sys

import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))


@pytest.fixture
def temp_metrics():
    """往圖譜塞暫時的指標，測完自動移除。

    用法：
        temp_metrics("測試公司", "EPS", {"2025Q1": "0.55", "2025Q2": "1.05"})

    刻意不呼叫 add_metric_datapoint（它會 save_graph）——直接動 G 再手動清快取，
    全程不碰磁碟。is_cumulative 有快取，不清的話同一個 session 裡的下一個測試
    會拿到上一批假資料算出來的結果。
    """
    import graph_rag

    added = []

    def _add(company, metric, values, unit=None):
        for period, value in values.items():
            node_id = f"{company}|{metric}|{period}"
            attrs = {"company": company, "metric": metric,
                     "period": period, "value": value}
            if unit:
                attrs["unit"] = unit
            graph_rag.G.add_node(node_id, **attrs)
            added.append(node_id)
        graph_rag._invalidate_caches()
        return company

    yield _add

    graph_rag.G.remove_nodes_from(added)
    graph_rag._invalidate_caches()
