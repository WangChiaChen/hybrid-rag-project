"""Phase 3：Graph RAG —— 財務指標知識圖譜，確保計算 100% 精準
正式版可把 networkx 換成 Neo4j，邏輯不變
資料會存成 JSON 檔，重開程式不會消失
"""
import networkx as nx
import json
import os

GRAPH_FILE = os.path.join(os.path.dirname(__file__), "..", "vector_db", "graph_data.json")


def _load_graph():
    g = nx.DiGraph()
    if os.path.exists(GRAPH_FILE):
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for node in data.get("nodes", []):
            node_id = node["id"]
            attrs = {k: v for k, v in node.items() if k != "id"}
            g.add_node(node_id, **attrs)
    return g


G = _load_graph()


def save_graph():
    os.makedirs(os.path.dirname(GRAPH_FILE), exist_ok=True)
    data = {"nodes": [{"id": n, **d} for n, d in G.nodes(data=True)]}
    with open(GRAPH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_metric_datapoint(company, metric, period, value):
    node_id = f"{company}|{metric}|{period}"
    G.add_node(node_id, company=company, metric=metric, period=period, value=value)
    save_graph()
    return node_id


def ingest_metrics(company, period, key_metrics):
    """key_metrics 是 vlm_parse.py 解析出來的 list，例如：
    [{"指標名稱": "手續費淨收益", "數值": "8054", ...}]
    """
    for m in key_metrics:
        name = m.get("指標名稱")
        value = m.get("數值")
        if name and value:
            add_metric_datapoint(company, name, period, value)


def _clean_number(v):
    return float(str(v).replace(",", "").replace("%", ""))


def calc_change(company, metric, this_period, last_period):
    """公式計算核心 —— 直接算，不讓 LLM 猜"""
    n1 = f"{company}|{metric}|{this_period}"
    n2 = f"{company}|{metric}|{last_period}"
    if n1 in G.nodes and n2 in G.nodes:
        try:
            v1 = _clean_number(G.nodes[n1]["value"])
            v2 = _clean_number(G.nodes[n2]["value"])
            if v2 == 0:
                return None
            return round((v1 - v2) / v2 * 100, 2)
        except ValueError:
            return None
    return None


def list_companies():
    return sorted(set(d["company"] for _, d in G.nodes(data=True)))


def list_periods(company):
    return sorted(set(d["period"] for _, d in G.nodes(data=True) if d["company"] == company))


def list_metrics(company, period):
    return [
        {"metric": d["metric"], "value": d["value"]}
        for _, d in G.nodes(data=True)
        if d["company"] == company and d["period"] == period
    ]


if __name__ == "__main__":
    # 快速自我測試
    add_metric_datapoint("中信金控", "手續費淨收益", "2026Q1", "8054")
    add_metric_datapoint("中信金控", "手續費淨收益", "2025Q4", "7805")
    change = calc_change("中信金控", "手續費淨收益", "2026Q1", "2025Q4")
    print(f"QoQ 變化：{change}%")
    print("目前所有公司：", list_companies())
