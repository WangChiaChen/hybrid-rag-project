"""Phase 6：Streamlit 聊天介面 —— demo 用的前端（進化版 v3）
在專案根目錄執行：streamlit run src/app.py
"""
import streamlit as st
import os

# 部署到 Streamlit Cloud 時，金鑰是放在雲端的 Secrets 設定裡（不是 .env 檔），
# 這裡把它接進 os.environ，這樣其他模組（agent_router.py 等）原本讀 .env 的
# os.getenv() 呼叫方式完全不用改，本機開發跟雲端部署共用同一套程式碼。
try:
    for _key in ("GEMINI_API_KEY", "EAP_API_BASE_URL", "EAP_API_KEY"):
        if _key in st.secrets:
            os.environ[_key] = st.secrets[_key]
except Exception:
    pass  # 本機開發沒有 secrets.toml 是正常的，會改用 .env

from agent_router import answer_question
from report_generator import generate_report
from graph_rag import list_companies, list_periods, list_metrics, calc_change

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

st.set_page_config(page_title="金融同業績效分析 AI", layout="wide", page_icon="📊")

st.markdown("""
<style>
.badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 13px;
    font-weight: 600;
    margin-right: 6px;
}
.badge-calc { background-color: #E1F5EE; color: #085041; }
.badge-narrative { background-color: #EEEDFE; color: #3C3489; }
.badge-both { background-color: #FAEEDA; color: #633806; }
.source-tag {
    display: inline-block;
    background-color: #F1EFE8;
    color: #444441;
    padding: 2px 10px;
    border-radius: 8px;
    font-size: 12px;
    margin-right: 6px;
    margin-top: 4px;
}
</style>
""", unsafe_allow_html=True)

st.title("📊 金融同業績效分析 Chatbot")
st.caption("Hybrid RAG 架構：Vector RAG（語意檢索）＋ Graph RAG（精準計算）＋ AI Agent 路由")

# ============ 側邊欄：資料範圍選擇 + 上傳新資料 ============
with st.sidebar:
    st.header("資料範圍")
    companies = list_companies()
    if not companies:
        st.info("知識庫目前是空的，請用下方「上傳新簡報」開始")
        company = st.text_input("公司名稱", value="中信金控")
        this_period = st.text_input("分析期間", value="2026Q1")
        last_period = None
    else:
        company = st.selectbox("公司", companies)
        from vector_rag import list_periods_from_vector
        periods = sorted(set(list_periods(company)) | list_periods_from_vector(company))
        this_period = st.selectbox("分析期間（本期）", periods, index=len(periods) - 1 if periods else 0)
        compare_options = ["（不比較）"] + [p for p in periods if p != this_period]
        last_period_choice = st.selectbox("比較期間（上一期）", compare_options)
        last_period = None if last_period_choice == "（不比較）" else last_period_choice

    st.divider()
    st.header("知識庫狀態")
    if company and this_period:
        metrics = list_metrics(company, this_period)
        st.metric("已收錄指標數", len(metrics))
    st.caption("資料來源：VLM 簡報解析 ＋ STT 錄音轉文字 ＋ Vector RAG 語意索引")

    st.divider()
    st.header("📤 上傳新資料")
    st.caption("上傳 → 自動解析 → 匯入知識庫，一次完成")

    with st.form("upload_form", clear_on_submit=True):
        up_company = st.text_input("公司名稱", placeholder="例如：國泰金控")
        up_period = st.text_input("期間", placeholder="例如：2026Q1")
        up_max_pages = st.slider("最多處理頁數", min_value=1, max_value=50, value=15)
        uploaded_pdf = st.file_uploader("簡報 PDF（與錄音擇一必填）", type=["pdf"])
        uploaded_audio = st.file_uploader("法說會錄音（與 PDF 擇一必填）", type=["mp3", "wav", "m4a", "mp4", "aac", "ogg"])
        submitted = st.form_submit_button("開始解析並匯入", use_container_width=True)

    if submitted:
        if (not uploaded_pdf and not uploaded_audio) or not up_company or not up_period:
            st.error("請至少填寫公司名稱、期間，並選擇 PDF 或錄音檔其中一項")
        else:
            temp_dir = os.path.join(BASE_DIR, "uploads_temp")
            os.makedirs(temp_dir, exist_ok=True)
            any_success = False

            # ---- 處理 PDF（如果有上傳）----
            if uploaded_pdf:
                temp_pdf_path = os.path.join(temp_dir, f"{up_company}_{up_period}.pdf")
                with open(temp_pdf_path, "wb") as f:
                    f.write(uploaded_pdf.getbuffer())

                progress_bar = st.progress(0, text="準備開始解析 PDF...")

                def update_progress(current, total):
                    progress_bar.progress(current / total, text=f"VLM 正在解析第 {current}/{total} 頁（刻意放慢速度避免超過免費額度限制）...")

                try:
                    from vlm_parse import run_vlm_parse
                    from ingest import run_ingest

                    results, json_path = run_vlm_parse(
                        temp_pdf_path, up_company, up_period,
                        max_pages=up_max_pages, progress_callback=update_progress
                    )
                    progress_bar.progress(0.9, text="PDF 解析完成，正在匯入知識庫...")
                    success = run_ingest(up_company, up_period, parsed_json_path=json_path)
                    progress_bar.progress(1.0, text="PDF 處理完成")
                    any_success = any_success or success
                    if not success:
                        st.warning("PDF 解析完成，但匯入時發生問題，請檢查終端機訊息")
                except Exception as e:
                    st.error(f"PDF 處理過程發生錯誤：{e}")
                    st.caption("常見原因：免費 API 額度用完（錯誤訊息會包含 RESOURCE_EXHAUSTED 或 429）")

            # ---- 處理錄音（如果有上傳，PDF 不是必要條件）----
            if uploaded_audio:
                audio_ext = uploaded_audio.name.split(".")[-1]
                temp_audio_path = os.path.join(temp_dir, f"{up_company}_{up_period}.{audio_ext}")
                with open(temp_audio_path, "wb") as f:
                    f.write(uploaded_audio.getbuffer())

                try:
                    with st.spinner("正在聽錄音、轉成逐字稿..."):
                        from stt_parse import run_stt_and_ingest
                        transcript, transcript_path = run_stt_and_ingest(temp_audio_path, up_company, up_period)
                    st.success("🎙️ 錄音已轉成逐字稿並匯入 Vector RAG")
                    with st.expander("查看逐字稿內容"):
                        st.write(transcript)
                    any_success = True
                except Exception as e:
                    st.error(f"錄音處理過程發生錯誤：{e}")
                    st.caption("常見原因：免費 API 額度用完（錯誤訊息會包含 RESOURCE_EXHAUSTED 或 429）")

            if any_success:
                st.success(f"✅ {up_company} {up_period} 的資料已匯入！重新整理頁面即可看到最新結果")


# ============ 主畫面：分頁呈現 ============
tab_dashboard, tab_compare, tab_chat, tab_sources = st.tabs(
    ["📊 分析儀表板", "🏦 跨機構比較", "💬 問答與報告", "📚 資料來源總覽"]
)

# ---------- 分頁 1：儀表板 ----------
with tab_dashboard:
    if company and this_period:
        metrics = list_metrics(company, this_period)
        if metrics:
            st.subheader(f"{company}　{this_period}　關鍵指標")
            tab_cards, tab_chart = st.tabs(["卡片檢視", "圖表檢視"])

            with tab_cards:
                cols = st.columns(min(len(metrics), 4))
                for i, m in enumerate(metrics):
                    delta = None
                    if last_period:
                        change = calc_change(company, m["metric"], this_period, last_period)
                        if change is not None:
                            delta = f"{change}%"
                    with cols[i % 4]:
                        st.metric(m["metric"], m["value"], delta=delta)

            with tab_chart:
                import pandas as pd
                import plotly.express as px

                chart_data = []
                for m in metrics:
                    try:
                        val = float(str(m["value"]).replace(",", ""))
                        chart_data.append({"指標": m["metric"], "數值": val})
                    except ValueError:
                        continue

                if chart_data:
                    df = pd.DataFrame(chart_data)
                    chart_type = st.radio(
                        "圖表類型", ["長條圖", "橫向長條圖", "圓餅圖", "雷達圖"],
                        horizontal=True, key="dashboard_chart_type"
                    )
                    st.caption("提示：圓餅圖／雷達圖較適合比較單位相近的指標，指標單位差異大時建議用長條圖")

                    palette = px.colors.qualitative.Set2

                    if chart_type == "長條圖":
                        fig = px.bar(df, x="指標", y="數值", color="指標", text="數值",
                                     color_discrete_sequence=palette)
                        fig.update_traces(texttemplate="%{text:,.2f}", textposition="outside")
                        fig.update_layout(showlegend=False, xaxis_title=None, yaxis_title=None)
                    elif chart_type == "橫向長條圖":
                        fig = px.bar(df, x="數值", y="指標", orientation="h", color="指標", text="數值",
                                     color_discrete_sequence=palette)
                        fig.update_traces(texttemplate="%{text:,.2f}", textposition="outside")
                        fig.update_layout(showlegend=False, xaxis_title=None, yaxis_title=None)
                    elif chart_type == "圓餅圖":
                        fig = px.pie(df, names="指標", values="數值", hole=0.45,
                                     color_discrete_sequence=palette)
                        fig.update_traces(textposition="outside", textinfo="label+percent")
                    else:  # 雷達圖
                        fig = px.line_polar(df, r="數值", theta="指標", line_close=True)
                        fig.update_traces(fill="toself", line_color=palette[0])

                    fig.update_layout(margin=dict(t=30, b=30, l=10, r=10), height=440)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.caption("目前指標數值無法轉換為圖表格式")

            st.divider()
        else:
            st.info(f"{company} {this_period} 目前還沒有數值指標，請確認上傳的簡報是否包含圖表頁")

# ---------- 分頁 2：跨機構比較 ----------
with tab_compare:
    all_companies = list_companies()
    if len(all_companies) < 2:
        st.info("目前知識庫只有一家公司的資料，上傳第二家公司的簡報後就能在這裡比較")
    else:
        st.subheader("🏦 跨機構比較")
        st.caption("對應「外部資訊落差」痛點：把不同銀行的財報數字並排比較")

        comp_cols = st.columns(2)
        with comp_cols[0]:
            company_a = st.selectbox("機構 A", all_companies, key="cmp_a")
            periods_a = list_periods(company_a)
            period_a = st.selectbox("期間 A", periods_a, key="cmp_pa")
        with comp_cols[1]:
            other_companies = [c for c in all_companies if c != company_a] or all_companies
            company_b = st.selectbox("機構 B", other_companies, key="cmp_b")
            periods_b = list_periods(company_b)
            period_b = st.selectbox("期間 B", periods_b, key="cmp_pb")

        metrics_a = {m["metric"]: m["value"] for m in list_metrics(company_a, period_a)}
        metrics_b = {m["metric"]: m["value"] for m in list_metrics(company_b, period_b)}
        common = sorted(set(metrics_a) & set(metrics_b))

        import pandas as pd
        import plotly.express as px

        if common:
            rows = []
            for name in common:
                try:
                    va = float(str(metrics_a[name]).replace(",", ""))
                    vb = float(str(metrics_b[name]).replace(",", ""))
                    rows.append({"指標": name, company_a: va, company_b: vb})
                except ValueError:
                    continue
            if rows:
                df = pd.DataFrame(rows)
                df_melt = df.melt(id_vars="指標", var_name="機構", value_name="數值")
                fig = px.bar(df_melt, x="指標", y="數值", color="機構", barmode="group",
                             text="數值", color_discrete_sequence=px.colors.qualitative.Set2)
                fig.update_traces(texttemplate="%{text:,.2f}", textposition="outside")
                fig.update_layout(margin=dict(t=30, b=30, l=10, r=10), height=440, xaxis_title=None, yaxis_title=None)
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df.set_index("指標"), use_container_width=True)
        else:
            from metric_alignment import align_metrics
            with st.spinner("兩家機構用詞不同，正在用語意相似度自動配對..."):
                pairs = align_metrics(list(metrics_a.keys()), list(metrics_b.keys()), threshold=0.7)

            if pairs:
                st.success(f"✨ 語意對齊：找到 {len(pairs)} 組意思相近但用詞不同的指標")
                rows = []
                for p in pairs:
                    try:
                        va = float(str(metrics_a[p["a"]]).replace(",", ""))
                        vb = float(str(metrics_b[p["b"]]).replace(",", ""))
                        rows.append({
                            "指標對照": f"{p['a']} ≈ {p['b']}",
                            company_a: va,
                            company_b: vb,
                            "語意相似度": f"{p['similarity']*100:.1f}%",
                        })
                    except ValueError:
                        continue
                if rows:
                    df = pd.DataFrame(rows)
                    df_melt = df[["指標對照", company_a, company_b]].melt(
                        id_vars="指標對照", var_name="機構", value_name="數值"
                    )
                    fig = px.bar(df_melt, x="指標對照", y="數值", color="機構", barmode="group",
                                 text="數值", color_discrete_sequence=px.colors.qualitative.Set2)
                    fig.update_traces(texttemplate="%{text:,.2f}", textposition="outside")
                    fig.update_layout(margin=dict(t=30, b=30, l=10, r=10), height=440, xaxis_title=None, yaxis_title=None)
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(df.set_index("指標對照"), use_container_width=True)
            else:
                st.info("兩家機構目前沒有名稱相同或語意相近的指標可比較，可以直接問 Chatbot 進行語意層面的比較。")
            with st.expander(f"查看 {company_a} {period_a} 的原始指標"):
                st.write(metrics_a)
            with st.expander(f"查看 {company_b} {period_b} 的原始指標"):
                st.write(metrics_b)

# ---------- 分頁 3：對話 + 匯出報告 ----------
with tab_chat:
    if "history" not in st.session_state:
        st.session_state.history = []

    for entry in st.session_state.history:
        with st.chat_message(entry["role"]):
            if entry["role"] == "user":
                st.write(entry["content"])
            else:
                route = entry.get("route", "")
                badge_class = {"CALC": "badge-calc", "NARRATIVE": "badge-narrative", "BOTH": "badge-both"}.get(route, "badge-narrative")
                badge_label = {"CALC": "精準計算 Graph RAG", "NARRATIVE": "語意檢索 Vector RAG", "BOTH": "雙引擎 Hybrid RAG"}.get(route, "")
                if badge_label:
                    st.markdown(f'<span class="badge {badge_class}">{badge_label}</span>', unsafe_allow_html=True)
                st.write(entry["content"])
                if entry.get("calc_result"):
                    cr = entry["calc_result"]
                    change_text = f"　|　較上期變化：**{cr['change']}%**" if cr.get("change") is not None else ""
                    st.success(f"✅ 公式計算驗證無誤　{cr['metric']} = {cr['value']}{change_text}")
                if entry.get("sources"):
                    tags = "".join(
                        f'<span class="source-tag">📄 {s.get("source", "")} 第{s.get("page", "?")}頁</span>'
                        for s in entry["sources"]
                    )
                    st.markdown(tags, unsafe_allow_html=True)

    question = st.chat_input("問我任何關於財報/法說會的問題...")
    if question and company and this_period:
        st.session_state.history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.write(question)

        with st.spinner("查詢中（可能需要幾秒）..."):
            result = answer_question(question, company=company, this_period=this_period, last_period=last_period)

        st.session_state.history.append({
            "role": "assistant",
            "content": result["answer"],
            "route": result["route"],
            "calc_result": result["calc_result"],
            "sources": result["sources"],
        })
        st.rerun()
    elif question and not company:
        st.warning("請先完成資料匯入")

    st.divider()
    if st.button("📄 匯出本次分析為 Word 報告") and company and this_period:
        metrics = list_metrics(company, this_period)
        metrics_summary = []
        for m in metrics:
            change = None
            if last_period:
                change = calc_change(company, m["metric"], this_period, last_period)
            metrics_summary.append({"name": m["metric"], "value": m["value"], "change": change if change is not None else ""})

        narrative_summary = "\n".join(
            entry["content"] for entry in st.session_state.history if entry["role"] == "assistant"
        ) or "（尚無對話紀錄）"

        generate_report(
            company=company,
            period=this_period,
            metrics_summary=metrics_summary,
            narrative_summary=narrative_summary,
            output_path=os.path.join(BASE_DIR, "outputs", "report.docx")
        )
        st.success("報告已生成，位於 outputs/report.docx")

# ---------- 分頁 3：資料來源總覽 ----------
with tab_sources:
    st.subheader("📚 資料來源總覽")
    st.caption("目前系統裡已經吃進多少份文件、多少指標、多少語意段落")

    from vector_rag import get_all_sources
    from collections import defaultdict

    all_meta = get_all_sources()
    source_counts = defaultdict(int)
    for m in all_meta:
        source_counts[m.get("source", "未知")] += 1

    all_companies = list_companies()
    if not all_companies:
        st.info("目前知識庫是空的，請先用側邊欄「上傳新資料」")
    else:
        import pandas as pd
        rows = []
        for c in all_companies:
            for p in list_periods(c):
                metric_count = len(list_metrics(c, p))
                narrative_count = source_counts.get(f"{c} {p}", 0) + source_counts.get(f"{c} {p} 法說會錄音", 0)
                rows.append({
                    "公司": c,
                    "期間": p,
                    "已收錄指標數": metric_count,
                    "已收錄語意段落數": narrative_count,
                })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)

        col1, col2, col3 = st.columns(3)
        col1.metric("已收錄公司數", len(all_companies))
        col2.metric("已收錄期間數", len(rows))
        col3.metric("總指標數", df["已收錄指標數"].sum() if not df.empty else 0)

    st.divider()
    st.subheader("📝 法說會逐字稿")
    st.caption("錄音轉出來的逐字稿都存在這裡，重新整理頁面也不會消失")

    outputs_dir = os.path.join(BASE_DIR, "outputs")
    transcript_files = sorted([
        f for f in os.listdir(outputs_dir)
        if f.startswith("transcript_") and f.endswith(".txt")
    ]) if os.path.exists(outputs_dir) else []

    if transcript_files:
        def _label(filename):
            name = filename.replace("transcript_", "").replace(".txt", "")
            parts = name.rsplit("_", 1)
            return f"{parts[0]}　{parts[1]}" if len(parts) == 2 else name

        selected = st.selectbox(
            "選擇要查看的逐字稿",
            transcript_files,
            format_func=_label
        )
        with open(os.path.join(outputs_dir, selected), "r", encoding="utf-8") as f:
            transcript_content = f.read()
        st.text_area("逐字稿內容", transcript_content, height=400, label_visibility="collapsed")
    else:
        st.caption("目前還沒有已儲存的逐字稿，上傳法說會錄音後會自動出現在這裡")
