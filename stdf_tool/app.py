"""
STDF Analyzer - StdfAnalyzer-Style 3-Pane Layout
左：File 树 + Summary
中：测试项表格 (Idx/TestNumber/TestText/LL/UL/Unit/PassCnt/FailCnt)
右：Basic Info + Qty Statistic + Soft/Hard Bin Statistic
"""
import io
import os
import tempfile
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from stdf_analyzer import STDFAnalyzer

st.set_page_config(
    page_title="StdfAnalyzer",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ===== 紧凑专业风格 CSS =====
st.markdown("""
<style>
.block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
.stDataFrame { font-size: 12px; }
section[data-testid="stSidebar"] { display: none; }
div[data-testid="stMetric"] { background-color: #f7f9fc; padding: 6px; border-radius: 4px; }
div[data-testid="stMetricLabel"] { font-size: 11px; }
div[data-testid="stMetricValue"] { font-size: 18px; }
.basic-info-table { font-family: 'Consolas', monospace; font-size: 12px; }
.basic-info-table td { padding: 2px 8px; border: none; }
.basic-info-table td:first-child { color: #1f4e79; font-weight: 600; width: 110px; }
hr { margin: 0.4rem 0; }
.bin-stat-table { font-family: 'Consolas', monospace; font-size: 11px; }
.bin-stat-table th { background-color: #305496; color: white; padding: 4px 6px; text-align: right; }
.bin-stat-table td { padding: 2px 6px; border-bottom: 1px solid #eee; text-align: right; }
.bin-stat-table td:first-child, .bin-stat-table td:nth-child(2) { text-align: left; }
.bin-stat-table tr:hover { background-color: #f7f9fc; }
.pass-row { background-color: #e2f0d9; }
.fail-row { background-color: #fce4d6; }
.menu-bar {
    background: #f5f5f5; border-bottom: 1px solid #ddd;
    padding: 4px 12px; font-size: 13px;
    margin: -1rem -1rem 0.6rem -1rem;
}
.menu-bar span { margin-right: 18px; cursor: pointer; }
.menu-bar span:hover { color: #1f77b4; }
</style>
<div class="menu-bar">
<b>StdfAnalyzer</b> &nbsp;|&nbsp;
<span>📁 File</span><span>🔗 Correlation</span><span>🔧 Tool</span><span>❓ Help</span>
</div>
""", unsafe_allow_html=True)


# ===================== 解析缓存 =====================
@st.cache_resource(show_spinner=False)
def parse_stdf_cached(file_bytes: bytes, name: str) -> STDFAnalyzer:
    suffix = ".stdf.gz" if name.endswith(".gz") else ".stdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
        f.write(file_bytes)
        path = f.name
    try:
        a = STDFAnalyzer()
        a.parse(path)
        return a
    finally:
        try: os.unlink(path)
        except: pass


# ===================== 文件上传 =====================
if "loaded" not in st.session_state:
    st.session_state.loaded = False

if not st.session_state.loaded:
    st.markdown("### 📁 Open STDF File")
    up = st.file_uploader(
        "Drag and drop .stdf or .stdf.gz",
        type=["stdf", "gz"], label_visibility="collapsed"
    )
    if up is None:
        st.info("Please upload an STDF file to begin analysis.")
        st.stop()
    file_bytes = up.getvalue()
    file_name = up.name
    with st.spinner(f"Parsing {file_name} ({up.size/1024/1024:.1f} MB)..."):
        analyzer = parse_stdf_cached(file_bytes, file_name)
    st.session_state.loaded = True
    st.session_state.analyzer = analyzer
    st.session_state.file_name = file_name
    st.rerun()
else:
    analyzer: STDFAnalyzer = st.session_state.analyzer
    file_name = st.session_state.file_name


# ===================== 3 栏布局 =====================
left, mid, right = st.columns([1.0, 2.4, 2.6], gap="small")


# --------------- 左栏 ---------------
with left:
    # File tree
    st.markdown(f"""
<div style="font-size:12px; font-family:monospace; background:#fff; border:1px solid #ddd; padding:6px;">
<b>📂 File_1</b>: {file_name[:38]}{'...' if len(file_name)>38 else ''}<br>
&nbsp;&nbsp;▸ Site List<br>
&nbsp;&nbsp;&nbsp;&nbsp;{ ' / '.join([f'Site {s}' for s in analyzer.get_kpi()['sites']]) }<br>
&nbsp;&nbsp;<b>Filter_0</b>: ALL
</div>
""", unsafe_allow_html=True)

    tab_sum, tab_filter = st.tabs(["File Summary", "Filter Setup"])

    with tab_sum:
        kpi = analyzer.get_kpi()
        st.markdown(f"""
<table class="basic-info-table" style="width:100%">
<tr><td>Lot Number:</td><td>{kpi['lot_id']}</td></tr>
<tr><td>Setup Time:</td><td>{kpi['start'][:19]}</td></tr>
<tr><td>Start Time:</td><td>{kpi['start'][:19]}</td></tr>
<tr><td>Finish Time:</td><td>{kpi['finish'][:19]}</td></tr>
</table>
<hr>
<b>Qty Statistic</b>
<table class="basic-info-table" style="width:100%">
<tr><td>Total QTY:</td><td>{kpi['total']:,}</td></tr>
<tr><td>Pass QTY:</td><td>{kpi['pass']:,} ({kpi['pass']/kpi['total']*100:.2f}%)</td></tr>
<tr style="color:#c00"><td>Fail QTY:</td><td>{kpi['fail']:,} ({kpi['fail']/kpi['total']*100:.2f}%)</td></tr>
<tr><td>Abort QTY:</td><td>0 (0.00%)</td></tr>
<tr><td>Null QTY:</td><td>0 (0.00%)</td></tr>
<tr><td>Fresh QTY:</td><td>{kpi['total']:,} (100.00%)</td></tr>
<tr><td>Retest QTY:</td><td>0 (0.00%)</td></tr>
</table>
<hr>
<b>Test Time</b>
<table class="basic-info-table" style="width:100%">
<tr><td>Avg Test Time:</td><td>{analyzer.get_test_time_stats()[0]:,} ms</td></tr>
<tr><td>Pass Only:</td><td>{analyzer.get_test_time_stats()[1]:,} ms</td></tr>
</table>
""", unsafe_allow_html=True)

    with tab_filter:
        st.markdown("**Filter Setup**")
        site_filter = st.multiselect("Sites", kpi['sites'], default=kpi['sites'])
        pf_filter = st.radio("Pass/Fail", ["All","Pass Only","Fail Only"], horizontal=True)
        st.session_state.site_filter = site_filter
        st.session_state.pf_filter = pf_filter

    if st.button("🔄 Reload File", use_container_width=True):
        st.session_state.loaded = False
        st.rerun()


# --------------- 中栏：测试项表 ---------------
with mid:
    file_tabs = st.tabs([f"📄 {file_name[:24]}", "🔍 Filter_0"])

    with file_tabs[0]:
        # 工具栏图标按钮
        btn_cols = st.columns(8)
        view_mode = st.session_state.get("view_mode", "table")
        if btn_cols[0].button("📋 Table", help="测试列表"):       st.session_state.view_mode = "table"
        if btn_cols[1].button("📊 Bar",   help="失效柱状图"):     st.session_state.view_mode = "bar"
        if btn_cols[2].button("📈 Hist",  help="参数直方图"):     st.session_state.view_mode = "hist"
        if btn_cols[3].button("🔵 Scatter",help="参数散点"):      st.session_state.view_mode = "scatter"
        if btn_cols[4].button("🌡️ Heat",  help="Site×Test 热图"): st.session_state.view_mode = "heat"
        if btn_cols[5].button("💾 Export",help="导出 Excel"):     st.session_state.view_mode = "export"

        view_mode = st.session_state.get("view_mode", "table")

        if view_mode == "table":
            df = analyzer.get_test_list_with_passfail()
            search = st.text_input("🔍 Filter", placeholder="filter by test number / name...", label_visibility="collapsed")
            if search:
                m = (df["TestText"].str.contains(search, case=False, na=False) |
                     df["TestNumber"].astype(str).str.contains(search, na=False))
                df = df[m]
            st.caption(f"{len(df)} tests")
            st.dataframe(df, use_container_width=True, hide_index=True, height=720,
                column_config={
                    "Idx":        st.column_config.NumberColumn(width="small"),
                    "TestNumber": st.column_config.NumberColumn(width="small"),
                    "TestText":   st.column_config.TextColumn(width="medium"),
                    "LoLimit":    st.column_config.NumberColumn(format="%.4g", width="small"),
                    "HiLimit":    st.column_config.NumberColumn(format="%.4g", width="small"),
                    "Unit":       st.column_config.TextColumn(width="small"),
                    "PassCnt":    st.column_config.NumberColumn(width="small"),
                    "FailCnt":    st.column_config.NumberColumn(width="small"),
                })

        elif view_mode == "bar":
            df = analyzer.get_top_failing_tests(30)
            df_plot = df.copy()
            df_plot["Label"] = df_plot.apply(lambda r: f"T{r['TestNum']} · {r['TestName'][:30]}", axis=1)
            fig = px.bar(df_plot.sort_values("N_Fail"), y="Label", x="N_Fail",
                         orientation="h", title="Top 30 Failing Tests",
                         text="FailRate(%)", color="FailRate(%)",
                         color_continuous_scale="Reds")
            fig.update_layout(height=720, yaxis_title="", xaxis_title="Fail Count")
            st.plotly_chart(fig, use_container_width=True)

        elif view_mode == "hist":
            df_cat = analyzer.df_catalog
            search = st.text_input("Search test", placeholder="e.g. RX_GOOD or 54000")
            opt = df_cat[df_cat["TestName"].str.contains(search, case=False, na=False) |
                          df_cat["TestNum"].astype(str).str.contains(search, na=False)] if search else df_cat.head(500)
            if not opt.empty:
                labels = [f"T{r['TestNum']} · {r['TestName'][:50]} (fail={r['N_Fail']})" for _, r in opt.iterrows()]
                idx = st.selectbox("Test", range(len(labels)), format_func=lambda i: labels[i])
                col_idx = int(opt.iloc[idx]["ColIdx"])
                data = analyzer.get_test_values(col_idx)
                vals = data["values"]; sites = data["sites"]
                valid = ~np.isnan(vals)
                if valid.sum() > 0:
                    df_plot = pd.DataFrame({"value": vals[valid], "site": [f"S{s}" for s in sites[valid]]})
                    fig = px.histogram(df_plot, x="value", color="site", barmode="overlay",
                                       opacity=0.6, nbins=80,
                                       title=f"T{data['tnum']} · {data['tname']}",
                                       labels={"value": data["units"] or ""})
                    if data["ll"] is not None: fig.add_vline(x=data["ll"], line_dash="dash", line_color="red")
                    if data["ul"] is not None: fig.add_vline(x=data["ul"], line_dash="dash", line_color="red")
                    fig.update_layout(height=640)
                    st.plotly_chart(fig, use_container_width=True)

        elif view_mode == "scatter":
            df_cat = analyzer.df_catalog
            search = st.text_input("Search test", placeholder="e.g. RX_GOOD or 54000", key="scatter_search")
            opt = df_cat[df_cat["TestName"].str.contains(search, case=False, na=False) |
                          df_cat["TestNum"].astype(str).str.contains(search, na=False)] if search else df_cat.head(500)
            if not opt.empty:
                labels = [f"T{r['TestNum']} · {r['TestName'][:50]}" for _, r in opt.iterrows()]
                idx = st.selectbox("Test", range(len(labels)), format_func=lambda i: labels[i], key="scatter_sel")
                col_idx = int(opt.iloc[idx]["ColIdx"])
                data = analyzer.get_test_values(col_idx)
                vals = data["values"]; sites = data["sites"]
                valid = ~np.isnan(vals)
                if valid.sum() > 0:
                    df_plot = pd.DataFrame({
                        "die_idx": np.where(valid)[0],
                        "value": vals[valid],
                        "site": [f"S{s}" for s in sites[valid]],
                    })
                    fig = px.scatter(df_plot, x="die_idx", y="value", color="site",
                                     title=f"T{data['tnum']} · {data['tname']} (per die)",
                                     labels={"value": data["units"] or ""})
                    if data["ll"] is not None: fig.add_hline(y=data["ll"], line_dash="dash", line_color="red")
                    if data["ul"] is not None: fig.add_hline(y=data["ul"], line_dash="dash", line_color="red")
                    fig.update_layout(height=640)
                    st.plotly_chart(fig, use_container_width=True)

        elif view_mode == "heat":
            df_sxt = analyzer.get_site_x_test(40)
            if not df_sxt.empty:
                site_cols = [c for c in df_sxt.columns if c.startswith("S") and "FailRate" in c]
                mat = df_sxt[site_cols].values.astype(float)
                yl = [f"T{r['TestNum']} · {r['TestName'][:35]}" for _, r in df_sxt.iterrows()]
                fig = go.Figure(data=go.Heatmap(
                    z=mat, x=site_cols, y=yl, colorscale="RdYlGn_r",
                    text=np.round(mat, 2), texttemplate="%{text}", colorbar_title="Fail %"))
                fig.update_layout(height=max(500, 22*len(yl)),
                                  title="Top 40 Failing Tests × Site")
                st.plotly_chart(fig, use_container_width=True)

        elif view_mode == "export":
            st.markdown("**Generate Excel Report (8 sheets)**")
            if st.button("🚀 Generate", type="primary"):
                buf = io.BytesIO()
                with st.spinner("Building..."):
                    analyzer.export_excel(buf)
                st.download_button(
                    "⬇️ Download Excel",
                    data=buf.getvalue(),
                    file_name=f"STDF_Report_{analyzer.mir.get('LOT_ID','x')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )


# --------------- 右栏：Basic Info + Bin Statistic ---------------
with right:
    # Basic Info
    info = analyzer.get_basic_info_dict()
    st.markdown("**Basic Info**")
    rows_html = "".join(
        f"<tr><td>{k}:</td><td>{v}</td></tr>" for k, v in info.items() if v not in (None, "", " ")
    )
    st.markdown(f"""<table class="basic-info-table" style="width:100%">{rows_html}</table>""",
                unsafe_allow_html=True)

    st.markdown("<hr><b>Qty Statistic</b>", unsafe_allow_html=True)
    # 渲染 site-wise qty 表
    df_qty = analyzer.get_qty_statistic_by_site()
    qty_html = df_qty.to_html(classes="bin-stat-table", border=0)
    st.markdown(qty_html, unsafe_allow_html=True)

    st.markdown("<hr><b>Soft Bin Statistic</b>", unsafe_allow_html=True)
    df_sbin = analyzer.get_sbin_by_site()
    sbin_rows_html = ""
    for _, r in df_sbin.iterrows():
        cls = "pass-row" if str(r["Bin Name"]).startswith("P:") else "fail-row" if str(r["Bin Name"]).startswith("F:") else ""
        cells = "".join(f"<td>{r[c]}</td>" for c in df_sbin.columns)
        sbin_rows_html += f'<tr class="{cls}">{cells}</tr>'
    header = "".join(f"<th>{c}</th>" for c in df_sbin.columns)
    st.markdown(
        f'<table class="bin-stat-table" style="width:100%"><thead><tr>{header}</tr></thead>'
        f'<tbody>{sbin_rows_html}</tbody></table>', unsafe_allow_html=True)

    st.markdown("<hr><b>Hard Bin Statistic</b>", unsafe_allow_html=True)
    df_hbin = analyzer.get_hbin_by_site()
    hbin_rows_html = ""
    for _, r in df_hbin.iterrows():
        cls = "pass-row" if str(r["Bin Name"]).startswith("P:") else "fail-row" if str(r["Bin Name"]).startswith("F:") else ""
        cells = "".join(f"<td>{r[c]}</td>" for c in df_hbin.columns)
        hbin_rows_html += f'<tr class="{cls}">{cells}</tr>'
    header = "".join(f"<th>{c}</th>" for c in df_hbin.columns)
    st.markdown(
        f'<table class="bin-stat-table" style="width:100%"><thead><tr>{header}</tr></thead>'
        f'<tbody>{hbin_rows_html}</tbody></table>', unsafe_allow_html=True)
