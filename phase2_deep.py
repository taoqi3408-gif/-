"""
Phase 2: 深度分析
- 失效热点测试的参数分布 + Cp/Cpk
- Site x Bin 矩阵
- Site x 失效测试 矩阵（找 site-only 失效）
- 关键参数分布图
- SBin 65535 调查（这些 part 倒在哪个测试）
"""
import os, sys, datetime, time
from collections import defaultdict, Counter
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from pystdf.IO import Parser
import pystdf.V4 as V4

STDF_PATH = "/home/user/stdf_analysis/test.stdf"
OUT_DIR = "/home/user/stdf_analysis/out"
os.makedirs(OUT_DIR, exist_ok=True)

# 关心的 top fail tests（来自 phase1）
WATCH_TESTS = {
    580: "RX_GOOD_PHASE1:good_count",
    575: "RX_GOOD_PHASE0:good_count",
    54000: "TX_RTERM_TRIM",
    53000: "RX_RESISTANCE",
}

# 数据容器
parts = []     # 每颗 die 的 PRR 记录
ptr_rows = []  # 关键测试的 PTR 记录
all_ptr_summary = defaultdict(lambda: {"n":0, "fail":0, "values":[], "sites":Counter(),
                                       "ll":None, "ul":None, "units":""})

# 当前 part 上下文
current_pir = {}  # (head, site) -> {part_id...}
current_site = {}

hbr_defs = {}
sbr_defs = {}

# 追 65535 sbin 的 part 失效在哪
# 思路：记录每个 part 在测试中失败的 test list
fails_by_part = defaultdict(list)
part_sbin = {}  # part_idx -> SBIN
part_hbin = {}
part_site = {}

part_idx_counter = 0
site_to_current_part = {}  # site -> current part_idx (assigned at PIR, cleared at PRR)


class Sink:
    def after_begin(self, ds): pass
    def after_complete(self, ds): pass
    def after_cancel(self, ds, exc): pass
    def after_send(self, ds, data):
        global part_idx_counter
        rec_type, fields = data
        name = rec_type.__class__.__name__
        d = dict(zip(rec_type.fieldNames, fields))

        if name == "Hbr":
            hbr_defs[d.get("HBIN_NUM")] = d
        elif name == "Sbr":
            sbr_defs[d.get("SBIN_NUM")] = d
        elif name == "Pir":
            key = (d.get("HEAD_NUM"), d.get("SITE_NUM"))
            site_to_current_part[key] = part_idx_counter
            part_site[part_idx_counter] = d.get("SITE_NUM")
            part_idx_counter += 1
        elif name == "Prr":
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            key = (head, site)
            pi = site_to_current_part.get(key)
            if pi is not None:
                part_sbin[pi] = d.get("SOFT_BIN")
                part_hbin[pi] = d.get("HARD_BIN")
                pf = d.get("PART_FLG", 0)
                parts.append({
                    "part_idx": pi,
                    "site": site,
                    "hbin": d.get("HARD_BIN"),
                    "sbin": d.get("SOFT_BIN"),
                    "pass": (pf & 0x08) == 0,
                    "x": d.get("X_COORD"),
                    "y": d.get("Y_COORD"),
                    "test_time": d.get("TEST_T"),
                    "part_id": d.get("PART_ID"),
                })
            site_to_current_part.pop(key, None)
        elif name == "Ptr":
            tnum = d.get("TEST_NUM")
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = site_to_current_part.get((head, site))
            res = d.get("RESULT")
            tflg = d.get("TEST_FLG", 0)
            pflg = d.get("PARM_FLG", 0)
            tname = d.get("TEST_TXT", "")
            is_fail = bool(tflg & 0xC0) or bool(pflg & 0x08)
            # 收集每个测试整体汇总
            s = all_ptr_summary[(tnum, tname)]
            s["n"] += 1
            if is_fail:
                s["fail"] += 1
                if pi is not None:
                    fails_by_part[pi].append((tnum, tname))
            if res is not None and not (tflg & 0x40):  # 0x40 = result not valid
                s["values"].append(res)
            s["sites"][site] += 1
            if d.get("OPT_FLAG") is not None:
                opt = d.get("OPT_FLAG", 0)
                # OPT_FLAG bit 4 (0x10) = LO_LIMIT invalid, bit 5 (0x20) = HI_LIMIT invalid
                if not (opt & 0x40) and d.get("LO_LIMIT") is not None:
                    s["ll"] = d.get("LO_LIMIT")
                if not (opt & 0x80) and d.get("HI_LIMIT") is not None:
                    s["ul"] = d.get("HI_LIMIT")
                if d.get("UNITS"):
                    s["units"] = d.get("UNITS")

            # 关心的测试号 → 详细
            if tnum in WATCH_TESTS:
                ptr_rows.append({
                    "tnum": tnum,
                    "tname": tname,
                    "site": site,
                    "result": res,
                    "ll": d.get("LO_LIMIT"),
                    "ul": d.get("HI_LIMIT"),
                    "units": d.get("UNITS"),
                    "fail": is_fail,
                    "part_idx": pi,
                })


print("Parsing STDF for phase 2 (~60s)...", flush=True)
t0 = time.time()
p = Parser(inp=open(STDF_PATH, "rb"))
p.addSink(Sink())
p.parse()
print(f"Parse complete in {time.time()-t0:.1f}s\n")

df_parts = pd.DataFrame(parts)
df_ptr = pd.DataFrame(ptr_rows)
print(f"Parts collected: {len(df_parts)}, watched-PTR rows: {len(df_ptr)}")
print(f"Total unique tests: {len(all_ptr_summary)}")

# ============ 1. Site x HBin 矩阵 ============
print("\n" + "="*80)
print("Site x HBin 计数矩阵")
print("="*80)
site_hbin = df_parts.groupby(["site", "hbin"]).size().unstack(fill_value=0)
print(site_hbin.to_string())

print("\nSite x HBin 比例 (%)")
site_hbin_pct = site_hbin.div(site_hbin.sum(axis=1), axis=0) * 100
print(site_hbin_pct.round(2).to_string())

# ============ 2. SBin 65535 调查 ============
print("\n" + "="*80)
print("SBin 65535 失效 die 的失效测试 Pareto")
print("="*80)
sbin_65535_parts = [pi for pi, sb in part_sbin.items() if sb == 65535]
print(f"SBin 65535 共 {len(sbin_65535_parts)} 颗 die")
fail_counter = Counter()
for pi in sbin_65535_parts:
    for (tn, tname) in fails_by_part.get(pi, []):
        fail_counter[(tn, tname)] += 1
print(f"\n这些 die 失效测试 Top 20:")
for (tn, tname), cnt in fail_counter.most_common(20):
    print(f"  TNum={tn:>10}  count={cnt:>4}  {tname}")

# 看看这些 die 里有哪些 HBin
hbin_of_sbin_65535 = Counter(part_hbin.get(pi) for pi in sbin_65535_parts)
print(f"\nSBin 65535 的 die 分布在哪些 HBin: {dict(hbin_of_sbin_65535)}")

# ============ 3. 跨 site 测试失效率热图 (Top 50 failing tests) ============
print("\n" + "="*80)
print("Site x 测试失效率 - Top 30 failing tests")
print("="*80)

# 重新跑：站在 site 维度统计 (per test per site)
# 用我们已有的 all_ptr_summary 没法分 site fail，需要再过一遍数据，但太重。
# 用近似：从 fails_by_part 反向构建
test_fail_per_site = defaultdict(lambda: defaultdict(int))   # (tnum,tname) -> site -> fail count
test_exec_per_site = defaultdict(lambda: defaultdict(int))   # 我们暂没记录 per site execs，先用近似：site count per test
# 这里只能用 all_ptr_summary["sites"] 作为 per-site exec count
for pi, fails in fails_by_part.items():
    site = part_site.get(pi, -1)
    for key in fails:
        test_fail_per_site[key][site] += 1
for key, s in all_ptr_summary.items():
    for site, cnt in s["sites"].items():
        test_exec_per_site[key][site] = cnt

# 取按总失效降序排列的 top 30 测试
top_fail_tests = sorted(
    [(k, sum(v.values())) for k, v in test_fail_per_site.items()],
    key=lambda x: -x[1])[:30]

print(f"  {'TNum':>10} | {'S1f/exec':>14} | {'S2f/exec':>14} | {'S3f/exec':>14} | {'S4f/exec':>14} | Name")
print(f"  {'-'*10}-+-{'-'*14}-+-{'-'*14}-+-{'-'*14}-+-{'-'*14}-+-{'-'*40}")
for key, total_fail in top_fail_tests:
    tnum, tname = key
    parts_str = []
    for site in [1,2,3,4]:
        f = test_fail_per_site[key].get(site, 0)
        e = test_exec_per_site[key].get(site, 0)
        parts_str.append(f"{f:>4}/{e:>4} {f/e*100 if e else 0:>4.1f}%")
    print(f"  {tnum:>10} | {parts_str[0]} | {parts_str[1]} | {parts_str[2]} | {parts_str[3]} | {tname[:50]}")

# ============ 4. 重点参数统计 Cp/Cpk ============
print("\n" + "="*80)
print("重点测试参数统计 (Cp/Cpk)")
print("="*80)

def cp_cpk(vals, ll, ul):
    arr = np.asarray(vals, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 5: return None, None, None, None, None
    mean = arr.mean()
    std = arr.std(ddof=1)
    if std == 0: return mean, std, None, None, None
    cp = None
    if ll is not None and ul is not None:
        cp = (ul - ll) / (6*std)
    cpk_low = (mean - ll) / (3*std) if ll is not None else None
    cpk_high = (ul - mean) / (3*std) if ul is not None else None
    cpk = None
    cands = [c for c in (cpk_low, cpk_high) if c is not None]
    if cands: cpk = min(cands)
    return mean, std, cp, cpk, len(arr)

# 选 Top 100 失效 tests 看 Cp/Cpk
print(f"\nTop 30 失效测试的统计：")
print(f"  {'TNum':>10} | {'N':>6} | {'Fail%':>7} | {'Mean':>11} | {'Std':>11} | {'LL':>10} | {'UL':>10} | {'Cp':>6} | {'Cpk':>6} | TestName")
print(f"  " + "-"*150)
for key, fail_total in top_fail_tests:
    tnum, tname = key
    s = all_ptr_summary[key]
    mean, std, cp, cpk, n = cp_cpk(s["values"], s["ll"], s["ul"])
    cp_str = f"{cp:>6.2f}" if cp is not None else "  N/A "
    cpk_str = f"{cpk:>6.2f}" if cpk is not None else "  N/A "
    mean_str = f"{mean:>11.4g}" if mean is not None else "  N/A      "
    std_str = f"{std:>11.4g}" if std is not None else "  N/A      "
    ll_str = f"{s['ll']:>10.4g}" if s["ll"] is not None else "  N/A     "
    ul_str = f"{s['ul']:>10.4g}" if s["ul"] is not None else "  N/A     "
    fp = s["fail"]/s["n"]*100 if s["n"] else 0
    print(f"  {tnum:>10} | {s['n']:>6} | {fp:>6.2f}% | {mean_str} | {std_str} | {ll_str} | {ul_str} | {cp_str} | {cpk_str} | {tname[:60]}")

# ============ 5. 绘图 ============
print("\n" + "="*80)
print("生成可视化图表 -> out/")
print("="*80)

sns.set_style("whitegrid")

# 5.1 Site Yield
fig, ax = plt.subplots(figsize=(7, 4))
site_yield = df_parts.groupby("site")["pass"].mean() * 100
bars = ax.bar(site_yield.index.astype(str), site_yield.values,
              color=["#3498db","#2ecc71","#f39c12","#e74c3c"])
for bar, v in zip(bars, site_yield.values):
    ax.text(bar.get_x()+bar.get_width()/2, v+0.2, f"{v:.2f}%",
            ha="center", fontsize=10, fontweight="bold")
ax.set_xlabel("Site")
ax.set_ylabel("Yield (%)")
ax.set_title(f"Yield by Site  (Lot X15274 · CV9480-A11 · n={len(df_parts):,})")
ax.set_ylim(70, 100)
ax.axhline(df_parts["pass"].mean()*100, color="red", linestyle="--",
           label=f"Overall {df_parts['pass'].mean()*100:.2f}%")
ax.legend()
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/01_yield_by_site.png", dpi=150)
plt.close()
print("  saved 01_yield_by_site.png")

# 5.2 HBin Pareto
fig, ax = plt.subplots(figsize=(9, 5))
hbin_counts = df_parts["hbin"].value_counts()
labels = [f"HB{int(h)}\n{hbr_defs.get(h,{}).get('HBIN_NAM','')[:18]}" for h in hbin_counts.index]
colors = ["#2ecc71" if hbr_defs.get(h,{}).get("HBIN_PF")=="P" else "#e74c3c"
          for h in hbin_counts.index]
bars = ax.bar(labels, hbin_counts.values, color=colors)
for bar, v in zip(bars, hbin_counts.values):
    ax.text(bar.get_x()+bar.get_width()/2, v, f"{v}\n({v/len(df_parts)*100:.1f}%)",
            ha="center", va="bottom", fontsize=9)
ax.set_ylabel("Count")
ax.set_title("HardBin Pareto")
plt.xticks(rotation=20, ha="right")
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/02_hbin_pareto.png", dpi=150)
plt.close()
print("  saved 02_hbin_pareto.png")

# 5.3 SBin Top 15 Pareto
fig, ax = plt.subplots(figsize=(11, 5))
sbin_counts = df_parts[~df_parts["pass"]]["sbin"].value_counts().head(15)
labels = [f"SB{int(s)}\n{sbr_defs.get(s,{}).get('SBIN_NAM','')[:22]}" for s in sbin_counts.index]
ax.bar(labels, sbin_counts.values, color="#e74c3c")
for i, v in enumerate(sbin_counts.values):
    ax.text(i, v, f"{v}", ha="center", va="bottom", fontsize=9)
ax.set_ylabel("Fail Count")
ax.set_title("SoftBin (Fail Only) Top 15 Pareto")
plt.xticks(rotation=30, ha="right", fontsize=8)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/03_sbin_pareto.png", dpi=150)
plt.close()
print("  saved 03_sbin_pareto.png")

# 5.4 Test fail rate Top 25
fig, ax = plt.subplots(figsize=(11, 7))
test_fr = []
for key, total_fail in top_fail_tests[:25]:
    tnum, tname = key
    s = all_ptr_summary[key]
    fr = s["fail"]/s["n"]*100 if s["n"] else 0
    test_fr.append((f"T{tnum} · {tname[:36]}", fr))
test_fr = sorted(test_fr, key=lambda x:x[1])
ax.barh([t[0] for t in test_fr], [t[1] for t in test_fr], color="#9b59b6")
ax.set_xlabel("Fail Rate (%)")
ax.set_title("Top 25 Failing Tests")
for i, (lbl, v) in enumerate(test_fr):
    ax.text(v+0.05, i, f"{v:.2f}%", va="center", fontsize=8)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/04_top_failing_tests.png", dpi=150)
plt.close()
print("  saved 04_top_failing_tests.png")

# 5.5 关键参数分布 (4 个 watch test) by site
for tnum, name_hint in WATCH_TESTS.items():
    sub = df_ptr[df_ptr["tnum"] == tnum]
    if sub.empty: continue
    # 取该 tnum 下首个 tname 作主图（同 tnum 多 pin 时各画一个）
    tnames = sub["tname"].unique()[:4]
    fig, axes = plt.subplots(1, len(tnames), figsize=(5*len(tnames), 4))
    if len(tnames)==1: axes=[axes]
    for ax, tnm in zip(axes, tnames):
        d = sub[sub["tname"]==tnm].copy()
        d = d[d["result"].notna()]
        if d.empty: continue
        ll = d["ll"].dropna().iloc[0] if d["ll"].notna().any() else None
        ul = d["ul"].dropna().iloc[0] if d["ul"].notna().any() else None
        units = d["units"].dropna().iloc[0] if d["units"].notna().any() else ""
        for s in sorted(d["site"].unique()):
            ax.hist(d[d["site"]==s]["result"], bins=60, alpha=0.5, label=f"S{s}")
        if ll is not None: ax.axvline(ll, color="red", linestyle="--", label=f"LL={ll:.3g}")
        if ul is not None: ax.axvline(ul, color="red", linestyle="--", label=f"UL={ul:.3g}")
        ax.set_title(f"T{tnum} · {tnm[:48]}", fontsize=9)
        ax.set_xlabel(units)
        ax.legend(fontsize=7)
    plt.suptitle(f"Test #{tnum} distribution by site", fontsize=12, fontweight="bold")
    plt.tight_layout()
    fname = f"{OUT_DIR}/05_dist_T{tnum}.png"
    plt.savefig(fname, dpi=150)
    plt.close()
    print(f"  saved {fname.split('/')[-1]}")

# 5.6 Site × HBin 堆叠
fig, ax = plt.subplots(figsize=(8, 5))
sh = site_hbin.copy()
sh.columns = [f"HB{int(c)} {hbr_defs.get(c,{}).get('HBIN_NAM','')[:14]}" for c in sh.columns]
colors_hbin = ["#2ecc71" if hbr_defs.get(c,{}).get("HBIN_PF")=="P" else None
               for c in site_hbin.columns]
sh.plot(kind="bar", stacked=True, ax=ax, colormap="tab10")
ax.set_xlabel("Site")
ax.set_ylabel("Part Count")
ax.set_title("Site × HardBin Distribution")
ax.legend(loc="upper right", fontsize=8)
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/06_site_x_hbin.png", dpi=150)
plt.close()
print("  saved 06_site_x_hbin.png")

# 5.7 测试时间分布
if "test_time" in df_parts.columns and df_parts["test_time"].notna().any():
    fig, ax = plt.subplots(figsize=(8,4))
    tt = df_parts["test_time"].dropna() / 1000.0  # ms -> s? STDF spec: ms
    # TEST_T is total test time in ms
    ax.hist(tt, bins=80, color="#34495e")
    ax.set_xlabel("Test Time (s)")
    ax.set_ylabel("Count")
    ax.set_title(f"Per-Part Test Time   (Mean={tt.mean():.1f}s, Median={tt.median():.1f}s)")
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/07_test_time.png", dpi=150)
    plt.close()
    print("  saved 07_test_time.png")

# 5.8 Wafer-coord 散点（如果有坐标）
if "x" in df_parts.columns and df_parts["x"].notna().any():
    fig, ax = plt.subplots(figsize=(7,7))
    valid = df_parts.dropna(subset=["x","y"])
    if not valid.empty:
        pass_p = valid[valid["pass"]]
        fail_p = valid[~valid["pass"]]
        ax.scatter(pass_p["x"], pass_p["y"], c="#2ecc71", s=15, label=f"Pass {len(pass_p)}")
        ax.scatter(fail_p["x"], fail_p["y"], c="#e74c3c", s=20, label=f"Fail {len(fail_p)}", marker="x")
        ax.set_aspect("equal")
        ax.legend()
        ax.set_title("Die layout (XY from PRR) Pass/Fail")
        plt.tight_layout()
        plt.savefig(f"{OUT_DIR}/08_wafer_xy.png", dpi=150)
        plt.close()
        print("  saved 08_wafer_xy.png")

print(f"\n所有图表保存在 {OUT_DIR}/")
