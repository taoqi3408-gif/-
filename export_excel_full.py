"""
完整版 STDF 导出 Excel - 包含每颗 die 的全部测试数据
Sheets:
  00 Summary
  01 Action_Items
  02 Site_Yield
  03 HBin_Pareto
  04 SBin_Pareto
  05 Top_Fail_Tests
  06 All_Tests_Stats
  07 Site_x_Test
  08 All_Parts
  09 Failed_Parts
  10 SBin65535
  11 Test_Catalog     - 所有测试号字典 (TestNum / TestName / Units / LL / UL)
  12 PTR_Result_Wide  - 每行 1 die，每列 1 测试，cell = 测量值
  13 PTR_PassFail_Wide- 每行 1 die，每列 1 测试，cell = P / F / -
  14 PTR_Failed_Long  - 长表，只列失效 PTR (~33 万行)
  15 FTR_All_Long     - 长表，全部 FTR (~40 千行)
  16 MPR_All_Long     - 长表，全部 MPR (~10 千行)
"""
import os, time, datetime, gc
from collections import defaultdict, Counter
import numpy as np
import pandas as pd
from pystdf.IO import Parser
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule

STDF_PATH = "/home/user/stdf_analysis/test.stdf"
OUT_XLSX  = "/home/user/stdf_analysis/STDF_FULL_X15274_CV9480-A11.xlsx"

# ===== 数据容器 =====
mir_info, mrr_info = {}, {}
sdr_info = []
hbr_defs, sbr_defs = {}, {}
part_meta = {}                         # part_idx -> {...}
fails_by_part = defaultdict(list)
site_to_current_part = {}
part_idx_counter = 0

# 测试目录: (tnum, tname) -> col_idx + meta
test_key_to_col = {}
test_meta = []   # list of dicts in col_idx order
# 累积的 raw 数据流
ptr_stream = []   # (part_idx, col_idx, result, fail_flag, valid_result)
ftr_records = []
mpr_records = []

class Sink:
    def after_begin(self, ds): pass
    def after_complete(self, ds): pass
    def after_cancel(self, ds, exc): pass

    def after_send(self, ds, data):
        global part_idx_counter
        rec_type, fields = data
        name = rec_type.__class__.__name__
        d = dict(zip(rec_type.fieldNames, fields))

        if name == "Mir":
            mir_info.update(d)
        elif name == "Mrr":
            mrr_info.update(d)
        elif name == "Sdr":
            sdr_info.append(d)
        elif name == "Hbr":
            hbr_defs[d.get("HBIN_NUM")] = d
        elif name == "Sbr":
            sbr_defs[d.get("SBIN_NUM")] = d
        elif name == "Pir":
            key = (d.get("HEAD_NUM"), d.get("SITE_NUM"))
            site_to_current_part[key] = part_idx_counter
            part_meta[part_idx_counter] = {
                "part_idx": part_idx_counter,
                "site": d.get("SITE_NUM"),
            }
            part_idx_counter += 1
        elif name == "Prr":
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = site_to_current_part.pop((head, site), None)
            if pi is not None:
                pf = d.get("PART_FLG", 0)
                m = part_meta[pi]
                m.update({
                    "part_id":      d.get("PART_ID"),
                    "hbin":         d.get("HARD_BIN"),
                    "sbin":         d.get("SOFT_BIN"),
                    "pass":         (pf & 0x08) == 0,
                    "x":            d.get("X_COORD"),
                    "y":            d.get("Y_COORD"),
                    "test_time_ms": d.get("TEST_T"),
                    "num_test":     d.get("NUM_TEST"),
                })
        elif name == "Ptr":
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT", "") or ""
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = site_to_current_part.get((head, site))
            res = d.get("RESULT")
            tflg = d.get("TEST_FLG", 0)
            pflg = d.get("PARM_FLG", 0)
            is_fail   = bool(tflg & 0xC0) or bool(pflg & 0x08)
            valid_res = (res is not None) and not (tflg & 0x40)

            key = (tnum, tname)
            cidx = test_key_to_col.get(key)
            if cidx is None:
                cidx = len(test_meta)
                test_key_to_col[key] = cidx
                test_meta.append({
                    "col_idx": cidx, "tnum": tnum, "tname": tname,
                    "units": d.get("UNITS") or "",
                    "ll": d.get("LO_LIMIT"), "ul": d.get("HI_LIMIT"),
                })
            else:
                m = test_meta[cidx]
                # 用第一次出现的非空 LL/UL/units
                if not m["units"] and d.get("UNITS"):    m["units"] = d.get("UNITS")
                if m["ll"] is None and d.get("LO_LIMIT") is not None: m["ll"] = d.get("LO_LIMIT")
                if m["ul"] is None and d.get("HI_LIMIT") is not None: m["ul"] = d.get("HI_LIMIT")

            if pi is not None:
                ptr_stream.append((
                    pi, cidx,
                    float(res) if valid_res else np.nan,
                    1 if is_fail else 0,
                    1 if valid_res else 0,
                ))
                if is_fail:
                    fails_by_part[pi].append((tnum, tname))

        elif name == "Ftr":
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT", "") or ""
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = site_to_current_part.get((head, site))
            tflg = d.get("TEST_FLG", 0)
            is_fail = bool(tflg & 0xC0)
            ftr_records.append({
                "part_idx": pi, "site": site,
                "tnum": tnum, "tname": tname,
                "fail": is_fail,
                "cycl_cnt": d.get("CYCL_CNT"),
                "vect_nam": d.get("VECT_NAM"),
                "time_set": d.get("TIME_SET"),
                "fail_pin": d.get("FAIL_PIN"),
            })
            if is_fail and pi is not None:
                fails_by_part[pi].append((tnum, tname))

        elif name == "Mpr":
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT", "") or ""
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = site_to_current_part.get((head, site))
            tflg = d.get("TEST_FLG", 0)
            pflg = d.get("PARM_FLG", 0)
            is_fail = bool(tflg & 0xC0) or bool(pflg & 0x08)
            results = d.get("RTN_RSLT") or []
            for k, r in enumerate(results):
                mpr_records.append({
                    "part_idx": pi, "site": site,
                    "tnum": tnum, "tname": tname,
                    "pin_idx": k,
                    "result": r,
                    "ll": d.get("LO_LIMIT"), "ul": d.get("HI_LIMIT"),
                    "units": d.get("UNITS"),
                    "fail": is_fail,
                })


print("Parsing STDF (full data extraction)...", flush=True)
t0 = time.time()
p = Parser(inp=open(STDF_PATH, "rb"))
p.addSink(Sink())
p.parse()
print(f"Parse done in {time.time()-t0:.1f}s")
print(f"  Parts: {len(part_meta)}")
print(f"  Unique PTR tests: {len(test_meta)}")
print(f"  PTR stream rows : {len(ptr_stream):,}")
print(f"  FTR records     : {len(ftr_records):,}")
print(f"  MPR records     : {len(mpr_records):,}")

# ===== 构建宽表 =====
n_parts = len(part_meta)
n_tests = len(test_meta)
print(f"\nBuilding wide matrices {n_parts}×{n_tests} (~{n_parts*n_tests/1e6:.2f}M cells)...")

# 把 part_idx 重排成连续 row_idx (有些 part_idx 在 PRR 没收到，过滤)
valid_part_indices = sorted([i for i, m in part_meta.items() if "hbin" in m])
n_parts_valid = len(valid_part_indices)
partidx_to_row = {pi: r for r, pi in enumerate(valid_part_indices)}

result_mat = np.full((n_parts_valid, n_tests), np.nan, dtype=np.float32)
fail_mat   = np.zeros((n_parts_valid, n_tests), dtype=np.int8)  # 0=untested/pass, 1=fail, -1=untested
# 先全部标记 untested
fail_mat.fill(-1)

for pi, cidx, res, fail, valid in ptr_stream:
    r = partidx_to_row.get(pi)
    if r is None: continue
    if valid: result_mat[r, cidx] = res
    fail_mat[r, cidx] = 1 if fail else 0

print("  Wide matrices built.")
ptr_stream_count = len(ptr_stream)
ptr_stream.clear()
gc.collect()

# ===== Test Catalog DataFrame =====
df_catalog = pd.DataFrame(test_meta).rename(columns={
    "col_idx":"ColIdx","tnum":"TestNum","tname":"TestName",
    "units":"Units","ll":"LL","ul":"UL"})

# 计算每测试的统计
fail_counts = (fail_mat == 1).sum(axis=0)
exec_counts = (fail_mat != -1).sum(axis=0)
valid_counts = (~np.isnan(result_mat)).sum(axis=0)
df_catalog["N_Exec"] = exec_counts
df_catalog["N_Fail"] = fail_counts
df_catalog["FailRate(%)"] = np.where(exec_counts>0, (fail_counts/exec_counts*100).round(3), 0)
df_catalog["N_Valid"] = valid_counts

mean_arr = np.full(n_tests, np.nan)
std_arr  = np.full(n_tests, np.nan)
min_arr  = np.full(n_tests, np.nan)
max_arr  = np.full(n_tests, np.nan)
for c in range(n_tests):
    col = result_mat[:, c]
    col = col[~np.isnan(col)]
    if len(col) >= 1:
        mean_arr[c] = col.mean()
        min_arr[c]  = col.min()
        max_arr[c]  = col.max()
    if len(col) >= 2:
        std_arr[c] = col.std(ddof=1)
df_catalog["Mean"] = mean_arr
df_catalog["Std"]  = std_arr
df_catalog["Min"]  = min_arr
df_catalog["Max"]  = max_arr

def cp_cpk_arr(mean, std, ll, ul):
    if std is None or std==0 or np.isnan(std): return None, None
    cp = (ul-ll)/(6*std) if (ll is not None and ul is not None) else None
    cands = []
    if ll is not None: cands.append((mean-ll)/(3*std))
    if ul is not None: cands.append((ul-mean)/(3*std))
    cpk = min(cands) if cands else None
    return cp, cpk

cps = [None]*n_tests; cpks = [None]*n_tests
for c in range(n_tests):
    cp, cpk = cp_cpk_arr(mean_arr[c], std_arr[c],
                         test_meta[c]["ll"], test_meta[c]["ul"])
    cps[c] = round(cp, 3) if cp is not None else None
    cpks[c] = round(cpk, 3) if cpk is not None else None
df_catalog["Cp"] = cps
df_catalog["Cpk"] = cpks

df_catalog = df_catalog[["ColIdx","TestNum","TestName","Units","LL","UL",
                         "N_Exec","N_Fail","FailRate(%)","N_Valid",
                         "Mean","Std","Min","Max","Cp","Cpk"]]

# ===== Parts DataFrame =====
df_parts = pd.DataFrame([part_meta[pi] for pi in valid_part_indices])
df_parts["hbin_name"] = df_parts["hbin"].map(lambda h: hbr_defs.get(h, {}).get("HBIN_NAM",""))
df_parts["sbin_name"] = df_parts["sbin"].map(lambda s: sbr_defs.get(s, {}).get("SBIN_NAM",""))
df_parts["test_time_s"] = df_parts["test_time_ms"]/1000.0
df_parts["num_fail_tests"] = df_parts["part_idx"].map(lambda i: len(fails_by_part.get(i, [])))

def fail_tests_str(idx):
    fl = fails_by_part.get(idx, [])
    if not fl: return ""
    by_num = defaultdict(list)
    for tn, tname in fl:
        by_num[tn].append(tname)
    parts_s = []
    for tn, names in sorted(by_num.items()):
        if len(names) <= 3:
            parts_s.append(f"T{tn}[{'; '.join(names)}]")
        else:
            parts_s.append(f"T{tn}×{len(names)}[{names[0]} ...]")
    return " | ".join(parts_s)[:2000]
df_parts["fail_tests"] = df_parts["part_idx"].map(fail_tests_str)

# ===== Wide DataFrames =====
print("Building wide DataFrames...")
# 列名: "T{tnum}_{tname}"，长度限制 60
def short_name(t):
    s = f"T{t['tnum']}__{t['tname']}"
    return s[:60]
col_names = [short_name(t) for t in test_meta]

# 避免重复列名
seen = {}
for i, c in enumerate(col_names):
    if c in seen:
        seen[c] += 1
        col_names[i] = f"{c}#{seen[c]}"
    else:
        seen[c] = 0

# 共享前缀列
prefix_df = df_parts[["part_idx","part_id","site","hbin","hbin_name","sbin","sbin_name","pass"]].reset_index(drop=True)

df_wide_result = pd.DataFrame(result_mat, columns=col_names)
df_wide_result = pd.concat([prefix_df, df_wide_result], axis=1)

# Pass/Fail wide 转字符
print("Converting pass/fail to string...")
# -1 -> "", 0 -> "P", 1 -> "F"
str_arr = np.full(fail_mat.shape, "", dtype=object)
str_arr[fail_mat == 0] = "P"
str_arr[fail_mat == 1] = "F"
df_wide_pf = pd.DataFrame(str_arr, columns=col_names)
df_wide_pf = pd.concat([prefix_df, df_wide_pf], axis=1)

# ===== Failed PTR Long table =====
print("Building Failed PTR long table...")
fail_rows = np.argwhere(fail_mat == 1)
fail_long_data = []
for r, c in fail_rows:
    pi = valid_part_indices[r]
    pm = part_meta[pi]
    tm = test_meta[c]
    fail_long_data.append({
        "part_idx": pi,
        "part_id":  pm.get("part_id"),
        "site":     pm.get("site"),
        "hbin":     pm.get("hbin"),
        "sbin":     pm.get("sbin"),
        "TestNum":  tm["tnum"],
        "TestName": tm["tname"],
        "Units":    tm["units"],
        "LL":       tm["ll"],
        "UL":       tm["ul"],
        "Result":   result_mat[r, c] if not np.isnan(result_mat[r, c]) else None,
    })
df_failed_long = pd.DataFrame(fail_long_data)
print(f"  Failed PTR rows: {len(df_failed_long):,}")

# ===== FTR / MPR DataFrames =====
print("Building FTR/MPR long tables...")
df_ftr = pd.DataFrame(ftr_records)
if not df_ftr.empty:
    df_ftr["part_id"] = df_ftr["part_idx"].map(lambda i: part_meta.get(i, {}).get("part_id") if i is not None else None)
    df_ftr = df_ftr[["part_idx","part_id","site","tnum","tname","fail",
                     "vect_nam","time_set","cycl_cnt","fail_pin"]]
df_mpr = pd.DataFrame(mpr_records)
if not df_mpr.empty:
    df_mpr["part_id"] = df_mpr["part_idx"].map(lambda i: part_meta.get(i, {}).get("part_id") if i is not None else None)
    df_mpr = df_mpr[["part_idx","part_id","site","tnum","tname","pin_idx",
                     "result","ll","ul","units","fail"]]

print(f"  FTR rows: {len(df_ftr):,}")
print(f"  MPR rows: {len(df_mpr):,}")

# ===== 之前 11 sheet 的数据（复用） =====
print("Building report sheets...")

def fmt_t(v):
    try: return datetime.datetime.utcfromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S UTC")
    except: return v

total = len(df_parts)
pass_n = int(df_parts["pass"].sum())
fail_n = total - pass_n
yield_pct = pass_n/total*100 if total else 0

summary_rows = [
    ("== Lot 信息 ==", ""),
    ("Lot ID",        mir_info.get("LOT_ID")),
    ("Sub-lot ID",    mir_info.get("SBLOT_ID")),
    ("Part Type",     mir_info.get("PART_TYP")),
    ("Job Name",      mir_info.get("JOB_NAM")),
    ("Test Code",     mir_info.get("TEST_COD")),
    ("Flow ID",       mir_info.get("FLOW_ID")),
    ("Operator",      mir_info.get("OPER_NAM")),
    ("Tester Type",   mir_info.get("TSTR_TYP")),
    ("Tester Node",   mir_info.get("NODE_NAM")),
    ("SmartTest Ver", mir_info.get("EXEC_VER")),
    ("Setup Time",    fmt_t(mir_info.get("SETUP_T"))),
    ("Start Time",    fmt_t(mir_info.get("START_T"))),
    ("Finish Time",   fmt_t(mrr_info.get("FINISH_T"))),
    ("", ""),
    ("== 数据规模 ==", ""),
    ("Total Parts",          total),
    ("Unique PTR Tests",     n_tests),
    ("Total PTR Records",    ptr_stream_count),
    ("Total FTR Records",    len(df_ftr)),
    ("Total MPR Records",    len(df_mpr)),
    ("Wide Matrix Cells",    n_parts_valid * n_tests),
    ("", ""),
    ("== 总体良率 ==", ""),
    ("Pass",  f"{pass_n} ({pass_n/total*100:.2f}%)" if total else 0),
    ("Fail",  f"{fail_n} ({fail_n/total*100:.2f}%)" if total else 0),
    ("Yield", f"{yield_pct:.2f}%"),
    ("", ""),
    ("== Sheet 速查 ==", ""),
    ("00 Summary", "本表"),
    ("01 Action_Items", "P0/P1/P2 行动建议"),
    ("02 Site_Yield", "Site x HBin 矩阵"),
    ("03 HBin_Pareto", "硬件 Bin 分布"),
    ("04 SBin_Pareto", "软件 Bin 分布"),
    ("05 Top_Fail_Tests", "Top 100 失效测试"),
    ("06 All_Tests_Stats", "全部测试统计 (Mean/Std/Cp/Cpk)"),
    ("07 Site_x_Test", "Site x Test 失效率矩阵"),
    ("08 All_Parts", "全部 2128 颗 die 清单"),
    ("09 Failed_Parts", "失效 338 颗 die + 失效测试列表"),
    ("10 SBin65535", "SBin 65535 调查"),
    ("11 Test_Catalog", "全部 2427 个 PTR 测试字典 ⭐"),
    ("12 PTR_Result_Wide", "宽表: 每行 1 die, 每列 1 测试, cell=测量值 ⭐"),
    ("13 PTR_PassFail_Wide", "宽表: 每行 1 die, 每列 1 测试, cell=P/F ⭐"),
    ("14 PTR_Failed_Long", "长表: 只列失效 PTR 详情"),
    ("15 FTR_All_Long", "长表: 全部 FTR (功能测试)"),
    ("16 MPR_All_Long", "长表: 全部 MPR (多参数测试)"),
]
df_summary = pd.DataFrame(summary_rows, columns=["Item","Value"])

# Action items
actions = [
    ("P0", "Site 4 DFT_fail 异常 (6.95% vs 平均 1.5%)",
     "清洁 S4 探针/socket，重测一批做相关性验证；检查 S4 DFT pin map / 电源完整性",
     "Site 4 HBin 4 占比为 Site 2 的 18 倍"),
    ("P0", "TX_RTERM_TRIM (T54000) Cp=0.07, Cpk=0.01",
     "复核 95-105 Ω 规格合理性；检查 trim 算法收敛；研究 Site 3 完全 0% fail 的原因",
     "Std=24 远大于 spec width=10；16 个 TX pin 完全同步失效"),
    ("P1", "RX_GOOD_PHASE0 (T575) Site 3 失效率 10.5%",
     "排查 Site 3 RX recovery / clock 链；与其他 site 做硬件对照",
     "其他 site 仅 3.6~7.9%"),
    ("P1", "RX_RESISTANCE (T53000) S2/S4 = 0% 失效",
     "拉 S1/S3 失效 die 到 S2/S4 重测；检查 S2/S4 RX 端 reference 电阻测量链路",
     "S1=4.7%, S3=5.8%, S2/S4=0%"),
    ("P1", "RX_GOOD_PHASE0/1 整体 Cpk≈0.55", "复核 RX recovery 设计裕度",
     "中心 11/12 vs LL=1, UL=70 — 偏向下限"),
    ("P2", "SBin 65535 缺失定义 (169 颗)",
     "为 TX_RTERM_TRIM fail 路径新增专属 SBin (如 SBin 540)",
     "168/169 颗最终落 HBin 5"),
    ("P2", "测试时间优化",
     "排序 Top 10 耗时 test，看能否并行/裁剪",
     f"Mean ~ {df_parts['test_time_ms'].mean()/1000:.1f}s/颗" if df_parts['test_time_ms'].notna().any() else ""),
]
df_actions = pd.DataFrame(actions, columns=["Priority","Issue","Recommended Action","Evidence"])

# Site yield
site_grp = df_parts.groupby("site").agg(Total=("pass","size"), Pass=("pass","sum")).reset_index()
site_grp["Fail"] = site_grp["Total"] - site_grp["Pass"]
site_grp["Yield(%)"] = (site_grp["Pass"]/site_grp["Total"]*100).round(3)
site_hbin = df_parts.groupby(["site","hbin"]).size().unstack(fill_value=0)
site_hbin.columns = [f"HB{int(c)}_{hbr_defs.get(c,{}).get('HBIN_NAM','')}" for c in site_hbin.columns]
df_site_yield = site_grp.merge(site_hbin.reset_index(), on="site")

# HBin
hbin_count = df_parts["hbin"].value_counts().reset_index()
hbin_count.columns = ["HBin","Count"]
hbin_count["Ratio(%)"] = (hbin_count["Count"]/total*100).round(3)
hbin_count["P/F"]  = hbin_count["HBin"].map(lambda h: hbr_defs.get(h,{}).get("HBIN_PF",""))
hbin_count["Name"] = hbin_count["HBin"].map(lambda h: hbr_defs.get(h,{}).get("HBIN_NAM",""))
hbin_count = hbin_count[["HBin","Name","P/F","Count","Ratio(%)"]]

# SBin
sbin_count = df_parts["sbin"].value_counts().reset_index()
sbin_count.columns = ["SBin","Count"]
sbin_count["Ratio(%)"] = (sbin_count["Count"]/total*100).round(3)
sbin_count["P/F"]  = sbin_count["SBin"].map(lambda s: sbr_defs.get(s,{}).get("SBIN_PF",""))
sbin_count["Name"] = sbin_count["SBin"].map(lambda s: sbr_defs.get(s,{}).get("SBIN_NAM",""))
sbin_count = sbin_count[["SBin","Name","P/F","Count","Ratio(%)"]]

# Site x Test fail-rate matrix (Top 100 fail tests)
site_n_per_test = np.zeros((n_tests, 5), dtype=np.int32)   # 1..4 + total
site_f_per_test = np.zeros((n_tests, 5), dtype=np.int32)
site_of_part = np.array([part_meta[pi]["site"] for pi in valid_part_indices])
for c in range(n_tests):
    col_f = fail_mat[:, c]
    for s in [1,2,3,4]:
        mask_s = site_of_part == s
        ex = (col_f[mask_s] != -1).sum()
        fa = (col_f[mask_s] == 1).sum()
        site_n_per_test[c, s] = ex
        site_f_per_test[c, s] = fa
sxt = []
for c in range(n_tests):
    if exec_counts[c] == 0 or fail_counts[c] == 0: continue
    row = {"TestNum":test_meta[c]["tnum"], "TestName":test_meta[c]["tname"],
           "N_Exec":int(exec_counts[c]), "N_Fail":int(fail_counts[c]),
           "FailRate(%)":round(fail_counts[c]/exec_counts[c]*100,3)}
    for s in [1,2,3,4]:
        ex = site_n_per_test[c, s]
        fa = site_f_per_test[c, s]
        row[f"S{s}_n"] = int(ex)
        row[f"S{s}_f"] = int(fa)
        row[f"S{s}_FailRate(%)"] = round(fa/ex*100, 3) if ex>0 else None
    sxt.append(row)
df_sxt = pd.DataFrame(sxt).sort_values("N_Fail", ascending=False).head(200).reset_index(drop=True)

# Top Fail tests = subset of catalog
df_top_fail = df_catalog[df_catalog["N_Fail"]>0].sort_values("N_Fail", ascending=False).head(100).reset_index(drop=True)

# All tests stats = full catalog ordered
df_all_tests_stats = df_catalog.sort_values(["N_Fail","TestNum"], ascending=[False, True]).reset_index(drop=True)

# All parts
df_all_parts = df_parts[["part_idx","part_id","site","hbin","hbin_name","sbin","sbin_name",
                          "pass","x","y","test_time_s","num_test","num_fail_tests"]]

# Failed parts
df_failed_parts = df_parts[~df_parts["pass"]][
    ["part_idx","part_id","site","hbin","hbin_name","sbin","sbin_name",
     "num_fail_tests","fail_tests"]].reset_index(drop=True)

# SBin 65535 investigation
sb65535_part_idx = df_parts[df_parts["sbin"]==65535]["part_idx"].tolist()
sb_cnt = Counter()
for pi in sb65535_part_idx:
    for tn, tname in fails_by_part.get(pi, []):
        sb_cnt[(tn, tname)] += 1
df_sb65535 = pd.DataFrame(
    [{"TestNum":k[0],"TestName":k[1],"FailCount":v} for k,v in sb_cnt.most_common()])

# ===== 写 Excel =====
print(f"\nWriting Excel -> {OUT_XLSX} (this may take 2-3 minutes)...")
tw = time.time()

with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
    df_summary.to_excel       (writer, sheet_name="00_Summary",           index=False)
    df_actions.to_excel       (writer, sheet_name="01_Action_Items",      index=False)
    df_site_yield.to_excel    (writer, sheet_name="02_Site_Yield",        index=False)
    hbin_count.to_excel       (writer, sheet_name="03_HBin_Pareto",       index=False)
    sbin_count.to_excel       (writer, sheet_name="04_SBin_Pareto",       index=False)
    df_top_fail.to_excel      (writer, sheet_name="05_Top_Fail_Tests",    index=False)
    df_all_tests_stats.to_excel(writer, sheet_name="06_All_Tests_Stats",  index=False)
    df_sxt.to_excel           (writer, sheet_name="07_Site_x_Test",       index=False)
    df_all_parts.to_excel     (writer, sheet_name="08_All_Parts",         index=False)
    df_failed_parts.to_excel  (writer, sheet_name="09_Failed_Parts",      index=False)
    df_sb65535.to_excel       (writer, sheet_name="10_SBin65535",         index=False)
    df_catalog.to_excel       (writer, sheet_name="11_Test_Catalog",      index=False)
    df_wide_result.to_excel   (writer, sheet_name="12_PTR_Result_Wide",   index=False)
    df_wide_pf.to_excel       (writer, sheet_name="13_PTR_PassFail_Wide", index=False)
    df_failed_long.to_excel   (writer, sheet_name="14_PTR_Failed_Long",   index=False)
    df_ftr.to_excel           (writer, sheet_name="15_FTR_All_Long",      index=False)
    df_mpr.to_excel           (writer, sheet_name="16_MPR_All_Long",      index=False)

    # ----- 格式化 -----
    print(f"  Data written in {time.time()-tw:.1f}s, formatting...")
    wb = writer.book
    header_fill = PatternFill("solid", fgColor="305496")
    header_font = Font(color="FFFFFF", bold=True)
    pass_fill = PatternFill("solid", fgColor="C6EFCE")
    fail_fill = PatternFill("solid", fgColor="FFC7CE")
    p0_fill = PatternFill("solid", fgColor="FFC7CE")
    p1_fill = PatternFill("solid", fgColor="FFEB9C")
    p2_fill = PatternFill("solid", fgColor="DDEBF7")

    # 小 sheet 列宽自适应
    small_sheets = ["00_Summary","01_Action_Items","02_Site_Yield","03_HBin_Pareto",
                    "04_SBin_Pareto","05_Top_Fail_Tests","06_All_Tests_Stats",
                    "07_Site_x_Test","08_All_Parts","09_Failed_Parts","10_SBin65535",
                    "11_Test_Catalog","14_PTR_Failed_Long","15_FTR_All_Long","16_MPR_All_Long"]
    for sh in small_sheets:
        if sh not in wb.sheetnames: continue
        ws = wb[sh]
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"
        for col_idx, col in enumerate(ws.columns, 1):
            max_len = 0
            for cell in col:
                v = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, min(len(v), 60))
            ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    # 大宽表 sheet 固定列宽
    for sh in ["12_PTR_Result_Wide","13_PTR_PassFail_Wide"]:
        ws = wb[sh]
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        ws.freeze_panes = "I2"  # 冻结前 8 列(part 元数据) + 1 行
        # 前 8 列设置合适宽度
        widths = [10, 18, 6, 8, 18, 8, 22, 8]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        # 测试列固定宽度
        for c in range(9, ws.max_column+1):
            ws.column_dimensions[get_column_letter(c)].width = 14

    # P/F 着色 (03, 04)
    for sh in ("03_HBin_Pareto","04_SBin_Pareto"):
        ws = wb[sh]
        pf_col = None
        for i, cell in enumerate(ws[1], 1):
            if cell.value == "P/F": pf_col = i; break
        if pf_col:
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                v = row[pf_col-1].value
                fill = pass_fill if v=="P" else (fail_fill if v=="F" else None)
                if fill:
                    for c in row: c.fill = fill

    # Action items 着色
    ws = wb["01_Action_Items"]
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        pv = row[0].value
        fill = p0_fill if pv=="P0" else (p1_fill if pv=="P1" else (p2_fill if pv=="P2" else None))
        if fill:
            for c in row: c.fill = fill

    # 失效条件着色 (Cp/Cpk/FailRate)
    for sh in ("05_Top_Fail_Tests","06_All_Tests_Stats","07_Site_x_Test","11_Test_Catalog"):
        ws = wb[sh]
        for i, cell in enumerate(ws[1], 1):
            if cell.value and "FailRate" in str(cell.value):
                col = get_column_letter(i)
                rng = f"{col}2:{col}{ws.max_row}"
                ws.conditional_formatting.add(rng,
                    ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                                   mid_type="num", mid_value=5, mid_color="FFEB9C",
                                   end_type="num", end_value=15, end_color="FF6B6B"))
            if cell.value in ("Cp","Cpk"):
                col = get_column_letter(i)
                rng = f"{col}2:{col}{ws.max_row}"
                ws.conditional_formatting.add(rng,
                    ColorScaleRule(start_type="num", start_value=0, start_color="FF6B6B",
                                   mid_type="num", mid_value=1.33, mid_color="FFEB9C",
                                   end_type="num", end_value=2.0, end_color="63BE7B"))

    # 09 整 sheet 淡红
    ws = wb["09_Failed_Parts"]
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for c in row: c.fill = fail_fill

    # 14 Failed PTR long: 整 sheet 淡红
    ws = wb["14_PTR_Failed_Long"]
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for c in row: c.fill = fail_fill

print(f"Done. Total {time.time()-t0:.1f}s")
print(f"File: {OUT_XLSX}")
print(f"Size: {os.path.getsize(OUT_XLSX)/1024/1024:.2f} MB")
