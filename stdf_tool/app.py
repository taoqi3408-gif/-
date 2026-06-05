"""
STDF Analyzer - Streamlit Web UI
启动方式: streamlit run app.py
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
    page_title="STDF Analyzer",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ===================== Cache wrapper =====================
@st.cache_resource(show_spinner=False)
def parse_stdf_cached(file_bytes: bytes, name: str) -> STDFAnalyzer:
    """缓存解析结果。基于文件 bytes hash 自动去重。"""
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

# ===================== Sidebar =====================
with st.sidebar:
    st.title("🔬 STDF Analyzer")
    st.caption("ATE 测试数据快速分析 · v0.1")
    st.divider()

    uploaded = st.file_uploader(
        "📁 上传 STDF 文件",
        type=["stdf", "gz"],
        help="支持 .stdf 和 .stdf.gz（gzip 压缩）。大文件建议先压缩。"
    )
    if uploaded:
        st.success(f"✓ {uploaded.name}\n{uploaded.size/1024/1024:.1f} MB")

    st.divider()
    st.caption("💡 解析大文件（>100MB）需要 1-2 分钟，首次解析后会缓存。")
    st.caption("🔗 源码：`stdf_tool/` 目录")

# ===================== Main =====================
if not uploaded:
    st.title("🔬 STDF Analyzer")
    st.markdown("""
    Upload an STDF file from your ATE (V93000 / Teradyne / etc.) to get instant analysis:

    - **Yield by Site** — site 良率对比，找 site-specific 失效
    - **HBin / SBin Pareto** — 失效模式分布
    - **Top Failing Tests** — Cp / Cpk / Fail rate 排行
    - **Test Drilldown** — 单个测试的参数分布 + site 对比
    - **Failed Parts** — 失效 die 清单 + 每颗失效测试明细
    - **Excel Export** — 一键导出多 sheet 分析报告

    👈 **请在左侧上传 STDF 文件开始。**
    """)
    st.stop()

# Parse with progress
file_bytes = uploaded.getvalue()
with st.spinner(f"解析 {uploaded.name} ({uploaded.size/1024/1024:.1f} MB)... 大文件可能需要 1-2 分钟"):
    analyzer = parse_stdf_cached(file_bytes, uploaded.name)

kpi = analyzer.get_kpi()

# Header KPI
st.title(f"📊 {kpi['lot_id']} · {kpi['part']}")
st.caption(f"Job: `{kpi['job']}` · Tester: `{kpi['tester']} ({kpi['node']})` · {kpi['start']} → {kpi['finish']}")

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total Parts", f"{kpi['total']:,}")
c2.metric("Pass", f"{kpi['pass']:,}")
c3.metric("Fail", f"{kpi['fail']:,}", delta=f"-{kpi['fail']/kpi['total']*100:.2f}%", delta_color="inverse")
yield_color = "normal" if kpi['yield']>=95 else ("inverse" if kpi['yield']<80 else "off")
c4.metric("Yield", f"{kpi['yield']:.2f}%")
c5.metric("Unique Tests", f"{kpi['n_tests']:,}")
c6.metric("Sites", len(kpi['sites']))

st.divider()

# ============== Tabs ==============
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🎯 Yield by Site", "📊 Bin Pareto", "🔥 Top Fail Tests",
    "🔍 Test Drilldown", "💀 Failed Parts", "📋 Lot Info", "📥 Export"
])

# --- Tab 1: Site Yield ---
with tab1:
    df = analyzer.get_site_yield()
    col_l, col_r = st.columns([2, 1])
    with col_l:
        fig = px.bar(df, x="site", y="Yield(%)", text="Yield(%)",
                     color="Yield(%)", color_continuous_scale="RdYlGn",
                     range_color=[max(0, df["Yield(%)"].min()-5), 100],
                     title="Yield by Site")
        fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
        fig.add_hline(y=kpi["yield"], line_dash="dash", line_color="black",
                      annotation_text=f"Overall {kpi['yield']:.2f}%")
        fig.update_yaxes(range=[max(0, df["Yield(%)"].min()-10), 102])
        st.plotly_chart(fig, use_container_width=True)
    with col_r:
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Site x HBin 堆叠
    st.subheader("Site × HBin Distribution")
    pivot = analyzer.df_parts.groupby(["site","hbin_name"]).size().unstack(fill_value=0)
    pivot_long = pivot.reset_index().melt(id_vars="site", var_name="HBin", value_name="Count")
    fig2 = px.bar(pivot_long, x="site", y="Count", color="HBin",
                  title="Site × HBin Stacked")
    st.plotly_chart(fig2, use_container_width=True)

# --- Tab 2: Bin Pareto ---
with tab2:
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("HardBin Pareto")
        dfh = analyzer.get_hbin_pareto()
        fig = px.bar(dfh, x="Name", y="Count", color="P/F",
                     color_discrete_map={"P":"#2ecc71","F":"#e74c3c"},
                     text="Count", title="HBin Distribution")
        fig.update_traces(textposition="outside")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(dfh, use_container_width=True, hide_index=True)

    with col_b:
        st.subheader("SoftBin Pareto (Fail Only, Top 15)")
        dfs = analyzer.get_sbin_pareto()
        dfs_f = dfs[dfs["P/F"]=="F"].head(15)
        fig = px.bar(dfs_f, x="Name", y="Count", text="Count",
                     color="Count", color_continuous_scale="Reds",
                     title="SBin Fail Distribution")
        fig.update_traces(textposition="outside")
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(dfs, use_container_width=True, hide_index=True)

# --- Tab 3: Top Fail Tests ---
with tab3:
    n = st.slider("Top N failing tests", 10, 200, 50, 10)
    df_top = analyzer.get_top_failing_tests(n)
    st.dataframe(df_top, use_container_width=True, hide_index=True,
                 column_config={
                     "FailRate(%)": st.column_config.ProgressColumn(
                         "FailRate(%)", min_value=0, max_value=20, format="%.2f%%"),
                     "Cp":  st.column_config.NumberColumn(format="%.3f"),
                     "Cpk": st.column_config.NumberColumn(format="%.3f"),
                 })

    st.subheader("Site × Test Failure Rate Heatmap")
    df_sxt = analyzer.get_site_x_test(n=30)
    if not df_sxt.empty:
        # 取 site fail rate 列
        site_cols = [c for c in df_sxt.columns if c.startswith("S") and "FailRate" in c]
        mat = df_sxt[site_cols].values
        ylabels = [f"T{r['TestNum']} · {r['TestName'][:40]}" for _, r in df_sxt.iterrows()]
        fig = go.Figure(data=go.Heatmap(
            z=mat, x=site_cols, y=ylabels,
            colorscale="RdYlGn_r",
            text=np.where(np.isnan(mat.astype(float)), "", np.round(mat.astype(float), 2)),
            texttemplate="%{text}", colorbar_title="Fail %"))
        fig.update_layout(height=max(400, 25*len(ylabels)),
                          title="Top 30 Failing Tests × Site")
        st.plotly_chart(fig, use_container_width=True)

# --- Tab 4: Test Drilldown ---
with tab4:
    st.subheader("Test Distribution Drilldown")
    # 搜索框
    search = st.text_input("🔎 Search test by number or name", placeholder="e.g. RX_GOOD or 54000")
    df_cat = analyzer.df_catalog.copy()
    if search:
        mask = (df_cat["TestName"].str.contains(search, case=False, na=False) |
                df_cat["TestNum"].astype(str).str.contains(search, na=False))
        df_cat = df_cat[mask]
    options = df_cat.head(500)
    if options.empty:
        st.warning("No matching tests.")
    else:
        labels = [f"T{r['TestNum']} · {r['TestName'][:60]} (n={r['N_Exec']}, fail={r['N_Fail']})"
                  for _, r in options.iterrows()]
        idx = st.selectbox("Select a test", range(len(labels)), format_func=lambda i: labels[i])
        col_idx = int(options.iloc[idx]["ColIdx"])
        data = analyzer.get_test_values(col_idx)
        vals = data["values"]
        sites = data["sites"]
        ll, ul = data["ll"], data["ul"]
        valid_mask = ~np.isnan(vals)

        cstats1, cstats2, cstats3, cstats4 = st.columns(4)
        if valid_mask.sum() > 0:
            v = vals[valid_mask]
            cstats1.metric("N Valid", f"{valid_mask.sum():,}")
            cstats2.metric("Mean", f"{v.mean():.4g}")
            cstats3.metric("Std", f"{v.std(ddof=1):.4g}" if len(v) > 1 else "—")
            cstats4.metric("Limits", f"[{ll}, {ul}]" if (ll is not None and ul is not None) else f"LL={ll}, UL={ul}")

            # 分布直方图 by site
            df_plot = pd.DataFrame({"value": v, "site": [f"S{s}" for s in sites[valid_mask]]})
            fig = px.histogram(df_plot, x="value", color="site", barmode="overlay",
                               opacity=0.55, nbins=80,
                               title=f"T{data['tnum']} · {data['tname']}",
                               labels={"value": data["units"] or "value"})
            if ll is not None:
                fig.add_vline(x=ll, line_dash="dash", line_color="red",
                              annotation_text=f"LL={ll}")
            if ul is not None:
                fig.add_vline(x=ul, line_dash="dash", line_color="red",
                              annotation_text=f"UL={ul}")
            st.plotly_chart(fig, use_container_width=True)

            # Box plot by site
            fig2 = px.box(df_plot, x="site", y="value", color="site",
                          title="Per-Site Distribution (Box)",
                          labels={"value": data["units"] or "value"})
            if ll is not None: fig2.add_hline(y=ll, line_dash="dash", line_color="red")
            if ul is not None: fig2.add_hline(y=ul, line_dash="dash", line_color="red")
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.warning("No valid numerical data for this test.")

# --- Tab 5: Failed Parts ---
with tab5:
    df_fp = analyzer.get_failed_parts_df()
    st.metric("Failed Parts Count", f"{len(df_fp):,}")
    st.dataframe(df_fp, use_container_width=True, hide_index=True)
    csv_bytes = df_fp.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download Failed Parts CSV", csv_bytes,
                       file_name=f"failed_parts_{kpi['lot_id']}.csv",
                       mime="text/csv")

# --- Tab 6: Lot Info ---
with tab6:
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("MIR (Master Information Record)")
        mir_show = {k: v for k, v in analyzer.mir.items()
                    if v not in (None, "", " ", 65535, 0)}
        st.json(mir_show)
    with col_r:
        st.subheader("MRR (Master Result Record)")
        st.json(analyzer.mrr)
        st.subheader("SDR (Site Description)")
        st.json(analyzer.sdr)

# --- Tab 7: Export ---
with tab7:
    st.subheader("Export Excel Report")
    st.write("生成多 sheet 分析 Excel（Summary / Yield / Bin / Top Fail / Site×Test / Failed Parts ...）")
    if st.button("🚀 Generate Excel Report", type="primary"):
        with st.spinner("Generating Excel..."):
            buf = io.BytesIO()
            analyzer.export_excel(buf)
            buf.seek(0)
            st.success(f"✓ Excel generated ({len(buf.getvalue())/1024:.1f} KB)")
            st.download_button(
                "⬇️ Download Excel",
                data=buf.getvalue(),
                file_name=f"STDF_Report_{kpi['lot_id']}_{kpi['part']}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
