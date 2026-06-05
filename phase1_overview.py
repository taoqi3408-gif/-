"""
Phase 1: STDF 文件概览 - lot 信息、yield、bin 分布、测试项清单
快速扫描，不做参数统计。
"""
import sys
from collections import defaultdict, Counter
from pystdf.IO import Parser
import pystdf.V4 as V4

STDF_PATH = "/home/user/stdf_analysis/test.stdf"

mir_info = {}
mrr_info = {}
sdr_info = []
pcr_records = []
hbr_defs = {}
sbr_defs = {}
prr_hbins = Counter()
prr_sbins = Counter()
prr_pass_count = 0
prr_fail_count = 0
prr_total = 0

test_seen = {}
test_fail_count = Counter()
test_exec_count = Counter()
site_pass = Counter()
site_fail = Counter()

ptr_count = 0
mpr_count = 0
ftr_count = 0
pir_count = 0

class Sink:
    def after_begin(self, dataSource): pass
    def after_complete(self, dataSource): pass
    def after_cancel(self, dataSource, exc): pass

    def after_send(self, dataSource, data):
        global ptr_count, mpr_count, ftr_count, pir_count
        global prr_pass_count, prr_fail_count, prr_total
        rec_type, fields = data
        name = rec_type.__class__.__name__

        if name == "Mir":
            for f, v in zip(rec_type.fieldNames, fields):
                mir_info[f] = v
        elif name == "Mrr":
            for f, v in zip(rec_type.fieldNames, fields):
                mrr_info[f] = v
        elif name == "Sdr":
            sdr_info.append(dict(zip(rec_type.fieldNames, fields)))
        elif name == "Pcr":
            pcr_records.append(dict(zip(rec_type.fieldNames, fields)))
        elif name == "Hbr":
            d = dict(zip(rec_type.fieldNames, fields))
            hbr_defs[d.get("HBIN_NUM")] = d
        elif name == "Sbr":
            d = dict(zip(rec_type.fieldNames, fields))
            sbr_defs[d.get("SBIN_NUM")] = d
        elif name == "Pir":
            pir_count += 1
        elif name == "Prr":
            d = dict(zip(rec_type.fieldNames, fields))
            prr_total += 1
            prr_hbins[d.get("HARD_BIN")] += 1
            prr_sbins[d.get("SOFT_BIN")] += 1
            # PART_FLG bit 3 (0x08) = fail
            pf = d.get("PART_FLG", 0)
            site = d.get("SITE_NUM", -1)
            if pf & 0x08:
                prr_fail_count += 1
                site_fail[site] += 1
            else:
                prr_pass_count += 1
                site_pass[site] += 1
        elif name == "Ptr":
            ptr_count += 1
            d = dict(zip(rec_type.fieldNames, fields))
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT", "")
            key = (tnum, tname)
            if key not in test_seen:
                test_seen[key] = d
            test_exec_count[key] += 1
            tflg = d.get("TEST_FLG", 0)
            pflg = d.get("PARM_FLG", 0)
            # Fail if TEST_FLG bit 7 (0x80) set OR result outside limits flagged
            if tflg & 0xC0 or (pflg & 0x08):
                test_fail_count[key] += 1
        elif name == "Mpr":
            mpr_count += 1
            d = dict(zip(rec_type.fieldNames, fields))
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT", "")
            key = ("MPR", tnum, tname)
            if key not in test_seen:
                test_seen[key] = d
            test_exec_count[key] += 1
        elif name == "Ftr":
            ftr_count += 1
            d = dict(zip(rec_type.fieldNames, fields))
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT", "")
            key = ("FTR", tnum, tname)
            if key not in test_seen:
                test_seen[key] = d
            test_exec_count[key] += 1
            tflg = d.get("TEST_FLG", 0)
            if tflg & 0xC0:
                test_fail_count[key] += 1


print("Parsing STDF (this may take a couple minutes for 200MB)...", flush=True)
import time
t0 = time.time()
p = Parser(inp=open(STDF_PATH, "rb"))
p.addSink(Sink())
p.parse()
print(f"Parse complete in {time.time()-t0:.1f}s\n")

print("=" * 80)
print("MIR (Master Information Record) - 测试基本信息")
print("=" * 80)
for k in ["SETUP_T", "START_T", "STAT_NUM", "MODE_COD", "RTST_COD",
          "PROT_COD", "BURN_TIM", "CMOD_COD", "LOT_ID", "PART_TYP",
          "NODE_NAM", "TSTR_TYP", "JOB_NAM", "JOB_REV", "SBLOT_ID",
          "OPER_NAM", "EXEC_TYP", "EXEC_VER", "TEST_COD", "TST_TEMP",
          "USER_TXT", "AUX_FILE", "PKG_TYP", "FAMLY_ID", "DATE_COD",
          "FACIL_ID", "FLOOR_ID", "PROC_ID", "OPER_FRQ", "SPEC_NAM",
          "SPEC_VER", "FLOW_ID", "SETUP_ID", "DSGN_REV", "ENG_ID",
          "ROM_COD", "SERL_NUM", "SUPR_NAM"]:
    if k in mir_info and mir_info[k]:
        v = mir_info[k]
        if k in ("SETUP_T", "START_T"):
            import datetime
            v = datetime.datetime.utcfromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"  {k:12s}: {v}")

print()
print("=" * 80)
print("MRR (Master Result Record) - 测试结束信息")
print("=" * 80)
for k, v in mrr_info.items():
    if v:
        if k == "FINISH_T":
            import datetime
            v = datetime.datetime.utcfromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"  {k:12s}: {v}")

print()
print("=" * 80)
print("SDR (Site Description Record)")
print("=" * 80)
for sdr in sdr_info[:3]:
    print(f"  HEAD={sdr.get('HEAD_NUM')} SITES={sdr.get('SITE_CNT')} SITE_NUMS={sdr.get('SITE_GRP')} HAND={sdr.get('HAND_ID')} TSTR={sdr.get('CARD_ID')}")

print()
print("=" * 80)
print("总体测试统计")
print("=" * 80)
print(f"  PIR (Part Information Records): {pir_count:,}")
print(f"  PRR (Part Result Records)     : {prr_total:,}")
print(f"  PTR (Parametric Test Records) : {ptr_count:,}")
print(f"  MPR (Multi-Param Test Records): {mpr_count:,}")
print(f"  FTR (Functional Test Records) : {ftr_count:,}")

print()
print("=" * 80)
print("整体良率")
print("=" * 80)
if prr_total > 0:
    yld = prr_pass_count / prr_total * 100
    print(f"  Total Parts Tested : {prr_total:,}")
    print(f"  Pass               : {prr_pass_count:,} ({prr_pass_count/prr_total*100:.2f}%)")
    print(f"  Fail               : {prr_fail_count:,} ({prr_fail_count/prr_total*100:.2f}%)")
    print(f"  Yield              : {yld:.2f}%")

print()
print("=" * 80)
print("Site 间 Yield 对比")
print("=" * 80)
all_sites = sorted(set(list(site_pass.keys()) + list(site_fail.keys())))
print(f"  {'Site':>6} | {'Pass':>8} | {'Fail':>8} | {'Total':>8} | {'Yield':>8}")
print(f"  {'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
for s in all_sites:
    p = site_pass.get(s, 0)
    f = site_fail.get(s, 0)
    t = p + f
    y = p / t * 100 if t > 0 else 0
    print(f"  {s:>6} | {p:>8,} | {f:>8,} | {t:>8,} | {y:>7.2f}%")

print()
print("=" * 80)
print("Hardware Bin (HBR) 分布 - Top 20")
print("=" * 80)
print(f"  {'HBin':>6} | {'Count':>8} | {'Ratio':>8} | {'PF':>4} | Name")
print(f"  {'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*4}-+-{'-'*30}")
for hb, cnt in prr_hbins.most_common(20):
    ratio = cnt / prr_total * 100 if prr_total > 0 else 0
    info = hbr_defs.get(hb, {})
    pf = info.get("HBIN_PF", "?")
    nm = info.get("HBIN_NAM", "")
    print(f"  {hb:>6} | {cnt:>8,} | {ratio:>7.2f}% | {pf:>4} | {nm}")

print()
print("=" * 80)
print("Software Bin (SBR) 分布 - Top 30")
print("=" * 80)
print(f"  {'SBin':>6} | {'Count':>8} | {'Ratio':>8} | {'PF':>4} | Name")
print(f"  {'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*4}-+-{'-'*30}")
for sb, cnt in prr_sbins.most_common(30):
    ratio = cnt / prr_total * 100 if prr_total > 0 else 0
    info = sbr_defs.get(sb, {})
    pf = info.get("SBIN_PF", "?")
    nm = info.get("SBIN_NAM", "")
    print(f"  {sb:>6} | {cnt:>8,} | {ratio:>7.2f}% | {pf:>4} | {nm}")

print()
print("=" * 80)
print(f"测试项总数: {len(test_seen)}")
print(f"  其中 PTR(参数测试): {sum(1 for k in test_seen if not (isinstance(k, tuple) and len(k)==3 and k[0] in ('MPR','FTR')))}")
print(f"  其中 MPR(多参数测试): {sum(1 for k in test_seen if isinstance(k, tuple) and len(k)==3 and k[0]=='MPR')}")
print(f"  其中 FTR(功能测试): {sum(1 for k in test_seen if isinstance(k, tuple) and len(k)==3 and k[0]=='FTR')}")

print()
print("=" * 80)
print("Top 30 失效测试项 (Test Fail Pareto)")
print("=" * 80)
print(f"  {'TestNum':>10} | {'Fails':>8} | {'Execs':>8} | {'FailRate':>9} | TestName")
print(f"  {'-'*10}-+-{'-'*8}-+-{'-'*8}-+-{'-'*9}-+-{'-'*40}")
for key, fails in test_fail_count.most_common(30):
    execs = test_exec_count[key]
    rate = fails / execs * 100 if execs > 0 else 0
    if isinstance(key, tuple) and len(key) == 3:
        kind, tnum, tname = key
        tag = f"[{kind}]"
    else:
        tnum, tname = key
        tag = ""
    print(f"  {tnum:>10} | {fails:>8,} | {execs:>8,} | {rate:>8.2f}% | {tag}{tname}")
