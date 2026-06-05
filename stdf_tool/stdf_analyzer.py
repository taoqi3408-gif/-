"""
STDF Analyzer - 核心解析与分析引擎
解析 STDF 文件，提供 KPI / Yield / Bin Pareto / Test Stats / Wide-table 等接口
"""
import io
import os
import gc
import time
import datetime
from collections import defaultdict, Counter
from typing import Optional, Callable, Dict, List, Tuple

import numpy as np
import pandas as pd
from pystdf.IO import Parser


class STDFAnalyzer:
    """解析 STDF 并缓存所有数据；提供后续聚合查询接口。"""

    def __init__(self):
        self._reset()

    # ---------------------- 解析 ----------------------
    def _reset(self):
        self.mir: Dict = {}
        self.mrr: Dict = {}
        self.sdr: List[Dict] = []
        self.hbr_defs: Dict = {}
        self.sbr_defs: Dict = {}

        self._part_meta: Dict[int, dict] = {}        # idx -> dict
        self._fails_by_part: Dict[int, list] = defaultdict(list)
        self._test_key_to_col: Dict[Tuple, int] = {}
        self._test_meta: List[dict] = []
        self._ptr_stream: List[tuple] = []           # (pidx, cidx, result, fail, valid)
        self._ftr_records: List[dict] = []
        self._mpr_records: List[dict] = []

        # 解析过程中
        self._site_current_part: Dict[Tuple, int] = {}
        self._part_idx_counter = 0

        # 结果
        self.df_parts: Optional[pd.DataFrame] = None
        self.df_catalog: Optional[pd.DataFrame] = None
        self.result_mat: Optional[np.ndarray] = None
        self.fail_mat: Optional[np.ndarray] = None
        self.valid_part_indices: List[int] = []
        self.parse_seconds: float = 0.0

        self.total_ptr = 0
        self.total_ftr = 0
        self.total_mpr = 0

    def parse(self, file_path: str, progress_cb: Optional[Callable[[str], None]] = None):
        """主解析入口。file_path 是 .stdf 或 .stdf.gz 文件路径。"""
        self._reset()
        t0 = time.time()
        if progress_cb: progress_cb("Opening file...")

        # 支持 .gz
        if file_path.endswith(".gz"):
            import gzip
            f = gzip.open(file_path, "rb")
        else:
            f = open(file_path, "rb")

        try:
            p = Parser(inp=f)
            p.addSink(self._Sink(self))
            if progress_cb: progress_cb("Parsing records...")
            p.parse()
        finally:
            f.close()

        if progress_cb: progress_cb("Building analytics tables...")
        self._build_tables()
        self.parse_seconds = time.time() - t0
        if progress_cb: progress_cb(f"Done in {self.parse_seconds:.1f}s")

    # 内部 Sink class，pystdf 回调
    class _Sink:
        def __init__(self, parent):
            self.p = parent
        def after_begin(self, ds): pass
        def after_complete(self, ds): pass
        def after_cancel(self, ds, exc): pass

        def after_send(self, ds, data):
            rec_type, fields = data
            name = rec_type.__class__.__name__
            d = dict(zip(rec_type.fieldNames, fields))
            self.p._handle(name, d)

    def _handle(self, name: str, d: dict):
        if name == "Mir":
            self.mir.update(d)
        elif name == "Mrr":
            self.mrr.update(d)
        elif name == "Sdr":
            self.sdr.append(d)
        elif name == "Hbr":
            self.hbr_defs[d.get("HBIN_NUM")] = d
        elif name == "Sbr":
            self.sbr_defs[d.get("SBIN_NUM")] = d
        elif name == "Pir":
            key = (d.get("HEAD_NUM"), d.get("SITE_NUM"))
            pi = self._part_idx_counter
            self._part_idx_counter += 1
            self._site_current_part[key] = pi
            self._part_meta[pi] = {"part_idx": pi, "site": d.get("SITE_NUM")}
        elif name == "Prr":
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = self._site_current_part.pop((head, site), None)
            if pi is None:
                return
            pf = d.get("PART_FLG", 0)
            self._part_meta[pi].update({
                "part_id": d.get("PART_ID"),
                "hbin": d.get("HARD_BIN"),
                "sbin": d.get("SOFT_BIN"),
                "pass": (pf & 0x08) == 0,
                "x": d.get("X_COORD"),
                "y": d.get("Y_COORD"),
                "test_time_ms": d.get("TEST_T"),
                "num_test": d.get("NUM_TEST"),
            })
        elif name == "Ptr":
            self.total_ptr += 1
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT") or ""
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = self._site_current_part.get((head, site))
            res = d.get("RESULT")
            tflg = d.get("TEST_FLG", 0)
            pflg = d.get("PARM_FLG", 0)
            is_fail = bool(tflg & 0xC0) or bool(pflg & 0x08)
            valid = (res is not None) and not (tflg & 0x40)
            key = (tnum, tname)
            cidx = self._test_key_to_col.get(key)
            if cidx is None:
                cidx = len(self._test_meta)
                self._test_key_to_col[key] = cidx
                self._test_meta.append({
                    "col_idx": cidx, "tnum": tnum, "tname": tname,
                    "units": d.get("UNITS") or "",
                    "ll": d.get("LO_LIMIT"), "ul": d.get("HI_LIMIT"),
                })
            else:
                m = self._test_meta[cidx]
                if not m["units"] and d.get("UNITS"): m["units"] = d.get("UNITS")
                if m["ll"] is None and d.get("LO_LIMIT") is not None: m["ll"] = d.get("LO_LIMIT")
                if m["ul"] is None and d.get("HI_LIMIT") is not None: m["ul"] = d.get("HI_LIMIT")
            if pi is not None:
                self._ptr_stream.append((
                    pi, cidx,
                    float(res) if valid else np.nan,
                    1 if is_fail else 0,
                    1 if valid else 0,
                ))
                if is_fail:
                    self._fails_by_part[pi].append((tnum, tname))
        elif name == "Ftr":
            self.total_ftr += 1
            tnum = d.get("TEST_NUM")
            tname = d.get("TEST_TXT") or ""
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = self._site_current_part.get((head, site))
            tflg = d.get("TEST_FLG", 0)
            is_fail = bool(tflg & 0xC0)
            self._ftr_records.append({
                "part_idx": pi, "site": site, "tnum": tnum, "tname": tname,
                "fail": is_fail,
                "vect_nam": d.get("VECT_NAM"),
                "fail_pin": d.get("FAIL_PIN"),
            })
            if is_fail and pi is not None:
                self._fails_by_part[pi].append((tnum, tname))
        elif name == "Mpr":
            self.total_mpr += 1
            results = d.get("RTN_RSLT") or []
            head, site = d.get("HEAD_NUM"), d.get("SITE_NUM")
            pi = self._site_current_part.get((head, site))
            tflg = d.get("TEST_FLG", 0)
            pflg = d.get("PARM_FLG", 0)
            is_fail = bool(tflg & 0xC0) or bool(pflg & 0x08)
            for k, r in enumerate(results):
                self._mpr_records.append({
                    "part_idx": pi, "site": site,
                    "tnum": d.get("TEST_NUM"), "tname": d.get("TEST_TXT") or "",
                    "pin_idx": k, "result": r,
                    "ll": d.get("LO_LIMIT"), "ul": d.get("HI_LIMIT"),
                    "units": d.get("UNITS"), "fail": is_fail,
                })

    def _build_tables(self):
        # Parts table
        self.valid_part_indices = sorted(
            i for i, m in self._part_meta.items() if "hbin" in m)
        df_parts = pd.DataFrame([self._part_meta[pi] for pi in self.valid_part_indices])
        df_parts["hbin_name"] = df_parts["hbin"].map(
            lambda h: self.hbr_defs.get(h, {}).get("HBIN_NAM", ""))
        df_parts["sbin_name"] = df_parts["sbin"].map(
            lambda s: self.sbr_defs.get(s, {}).get("SBIN_NAM", ""))
        df_parts["test_time_s"] = df_parts["test_time_ms"] / 1000.0
        df_parts["num_fail_tests"] = df_parts["part_idx"].map(
            lambda i: len(self._fails_by_part.get(i, [])))
        self.df_parts = df_parts

        # Wide result/fail matrix
        n_parts = len(self.valid_part_indices)
        n_tests = len(self._test_meta)
        partidx_to_row = {pi: r for r, pi in enumerate(self.valid_part_indices)}
        result_mat = np.full((n_parts, n_tests), np.nan, dtype=np.float32)
        fail_mat = np.full((n_parts, n_tests), -1, dtype=np.int8)
        for pi, cidx, res, fail, valid in self._ptr_stream:
            r = partidx_to_row.get(pi)
            if r is None: continue
            if valid: result_mat[r, cidx] = res
            fail_mat[r, cidx] = 1 if fail else 0
        self.result_mat = result_mat
        self.fail_mat = fail_mat

        # 释放 stream
        self._ptr_stream.clear()
        gc.collect()

        # Catalog DataFrame with stats
        fail_counts = (fail_mat == 1).sum(axis=0)
        exec_counts = (fail_mat != -1).sum(axis=0)
        valid_counts = (~np.isnan(result_mat)).sum(axis=0)

        mean_arr = np.full(n_tests, np.nan)
        std_arr = np.full(n_tests, np.nan)
        min_arr = np.full(n_tests, np.nan)
        max_arr = np.full(n_tests, np.nan)
        for c in range(n_tests):
            col = result_mat[:, c]
            col = col[~np.isnan(col)]
            if len(col) >= 1:
                mean_arr[c] = col.mean()
                min_arr[c] = col.min()
                max_arr[c] = col.max()
            if len(col) >= 2:
                std_arr[c] = col.std(ddof=1)

        cps, cpks = [None]*n_tests, [None]*n_tests
        for c in range(n_tests):
            std = std_arr[c]
            if std is None or std == 0 or np.isnan(std):
                continue
            ll = self._test_meta[c]["ll"]
            ul = self._test_meta[c]["ul"]
            if ll is not None and ul is not None:
                cps[c] = round((ul-ll)/(6*std), 3)
            cands = []
            if ll is not None: cands.append((mean_arr[c]-ll)/(3*std))
            if ul is not None: cands.append((ul-mean_arr[c])/(3*std))
            if cands: cpks[c] = round(min(cands), 3)

        df_catalog = pd.DataFrame(self._test_meta).rename(columns={
            "col_idx":"ColIdx","tnum":"TestNum","tname":"TestName",
            "units":"Units","ll":"LL","ul":"UL"})
        df_catalog["N_Exec"] = exec_counts
        df_catalog["N_Fail"] = fail_counts
        df_catalog["FailRate(%)"] = np.where(
            exec_counts > 0, (fail_counts/exec_counts*100).round(3), 0)
        df_catalog["N_Valid"] = valid_counts
        df_catalog["Mean"] = mean_arr
        df_catalog["Std"]  = std_arr
        df_catalog["Min"]  = min_arr
        df_catalog["Max"]  = max_arr
        df_catalog["Cp"]   = cps
        df_catalog["Cpk"]  = cpks
        self.df_catalog = df_catalog[
            ["ColIdx","TestNum","TestName","Units","LL","UL",
             "N_Exec","N_Fail","FailRate(%)","N_Valid",
             "Mean","Std","Min","Max","Cp","Cpk"]]

    # ---------------------- 查询接口 ----------------------
    def get_kpi(self) -> dict:
        total = len(self.df_parts)
        passn = int(self.df_parts["pass"].sum())
        return {
            "lot_id":  self.mir.get("LOT_ID", "?"),
            "part":    self.mir.get("PART_TYP", "?"),
            "job":     self.mir.get("JOB_NAM", "?"),
            "tester":  self.mir.get("TSTR_TYP", "?"),
            "node":    self.mir.get("NODE_NAM", ""),
            "start":   self._fmt_t(self.mir.get("START_T")),
            "finish":  self._fmt_t(self.mrr.get("FINISH_T")),
            "total":   total,
            "pass":    passn,
            "fail":    total - passn,
            "yield":   passn/total*100 if total else 0,
            "n_tests": len(self._test_meta),
            "n_ptr":   self.total_ptr,
            "n_ftr":   self.total_ftr,
            "n_mpr":   self.total_mpr,
            "sites":   sorted(self.df_parts["site"].unique().tolist()),
        }

    @staticmethod
    def _fmt_t(v):
        try: return datetime.datetime.utcfromtimestamp(v).strftime("%Y-%m-%d %H:%M:%S UTC")
        except: return ""

    def get_site_yield(self) -> pd.DataFrame:
        g = self.df_parts.groupby("site").agg(
            Total=("pass", "size"), Pass=("pass", "sum")).reset_index()
        g["Fail"] = g["Total"] - g["Pass"]
        g["Yield(%)"] = (g["Pass"]/g["Total"]*100).round(3)
        return g

    def get_hbin_pareto(self) -> pd.DataFrame:
        c = self.df_parts["hbin"].value_counts().reset_index()
        c.columns = ["HBin", "Count"]
        total = len(self.df_parts)
        c["Ratio(%)"] = (c["Count"]/total*100).round(3)
        c["P/F"] = c["HBin"].map(lambda h: self.hbr_defs.get(h, {}).get("HBIN_PF", ""))
        c["Name"] = c["HBin"].map(lambda h: self.hbr_defs.get(h, {}).get("HBIN_NAM", ""))
        return c[["HBin", "Name", "P/F", "Count", "Ratio(%)"]]

    def get_sbin_pareto(self) -> pd.DataFrame:
        c = self.df_parts["sbin"].value_counts().reset_index()
        c.columns = ["SBin", "Count"]
        total = len(self.df_parts)
        c["Ratio(%)"] = (c["Count"]/total*100).round(3)
        c["P/F"] = c["SBin"].map(lambda s: self.sbr_defs.get(s, {}).get("SBIN_PF", ""))
        c["Name"] = c["SBin"].map(lambda s: self.sbr_defs.get(s, {}).get("SBIN_NAM", ""))
        return c[["SBin", "Name", "P/F", "Count", "Ratio(%)"]]

    def get_top_failing_tests(self, n=50) -> pd.DataFrame:
        return (self.df_catalog[self.df_catalog["N_Fail"] > 0]
                .sort_values("N_Fail", ascending=False).head(n).reset_index(drop=True))

    def get_site_x_test(self, n=50) -> pd.DataFrame:
        n_tests = len(self._test_meta)
        sites = sorted(self.df_parts["site"].unique().tolist())
        site_of_part = self.df_parts["site"].values

        fail_counts = (self.fail_mat == 1).sum(axis=0)
        # 取 top n by total fails
        top_idx = np.argsort(-fail_counts)[:n]
        rows = []
        for c in top_idx:
            if fail_counts[c] == 0: continue
            ex_all = (self.fail_mat[:, c] != -1).sum()
            fa_all = fail_counts[c]
            row = {
                "TestNum": self._test_meta[c]["tnum"],
                "TestName": self._test_meta[c]["tname"],
                "N_Exec": int(ex_all), "N_Fail": int(fa_all),
                "FailRate(%)": round(fa_all/ex_all*100, 3) if ex_all else 0,
            }
            for s in sites:
                mask = site_of_part == s
                col = self.fail_mat[mask, c]
                ex = (col != -1).sum()
                fa = (col == 1).sum()
                row[f"S{s}_FailRate(%)"] = round(fa/ex*100, 3) if ex > 0 else None
            rows.append(row)
        return pd.DataFrame(rows)

    def get_test_values(self, col_idx: int):
        """返回 (values_array, sites_array, fail_array, ll, ul, units, tnum, tname)"""
        m = self._test_meta[col_idx]
        site_of_part = self.df_parts["site"].values
        return {
            "values": self.result_mat[:, col_idx],
            "sites":  site_of_part,
            "fail":   self.fail_mat[:, col_idx],
            "ll":     m["ll"], "ul": m["ul"], "units": m["units"],
            "tnum":   m["tnum"], "tname": m["tname"],
        }

    def get_failed_parts_df(self) -> pd.DataFrame:
        df = self.df_parts[~self.df_parts["pass"]].copy()
        def join_fails(idx):
            fl = self._fails_by_part.get(idx, [])
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
        df["fail_tests"] = df["part_idx"].map(join_fails)
        return df[["part_idx","part_id","site","hbin","hbin_name",
                   "sbin","sbin_name","num_fail_tests","fail_tests"]].reset_index(drop=True)

    # ---------------------- 导出 Excel ----------------------
    def export_excel(self, output) -> None:
        """导出多 sheet Excel 报告到 output (path or BytesIO)。"""
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter
        from openpyxl.formatting.rule import ColorScaleRule

        kpi = self.get_kpi()
        summary_rows = [
            ("Lot ID", kpi["lot_id"]),
            ("Part Type", kpi["part"]),
            ("Job", kpi["job"]),
            ("Tester", f"{kpi['tester']} ({kpi['node']})"),
            ("Start", kpi["start"]), ("Finish", kpi["finish"]),
            ("Total Parts", kpi["total"]),
            ("Pass", kpi["pass"]), ("Fail", kpi["fail"]),
            ("Yield", f"{kpi['yield']:.2f}%"),
            ("Unique Tests", kpi["n_tests"]),
            ("PTR Records", kpi["n_ptr"]),
            ("FTR Records", kpi["n_ftr"]),
            ("MPR Records", kpi["n_mpr"]),
        ]
        df_sum = pd.DataFrame(summary_rows, columns=["Item", "Value"])

        with pd.ExcelWriter(output, engine="openpyxl") as wr:
            df_sum.to_excel(wr, sheet_name="00_Summary", index=False)
            self.get_site_yield().to_excel(wr, sheet_name="01_Site_Yield", index=False)
            self.get_hbin_pareto().to_excel(wr, sheet_name="02_HBin_Pareto", index=False)
            self.get_sbin_pareto().to_excel(wr, sheet_name="03_SBin_Pareto", index=False)
            self.get_top_failing_tests(100).to_excel(wr, sheet_name="04_Top_Fail_Tests", index=False)
            self.df_catalog.sort_values(["N_Fail","TestNum"], ascending=[False,True]).to_excel(
                wr, sheet_name="05_All_Tests_Stats", index=False)
            self.get_site_x_test(100).to_excel(wr, sheet_name="06_Site_x_Test", index=False)
            self.get_failed_parts_df().to_excel(wr, sheet_name="07_Failed_Parts", index=False)

            wb = wr.book
            hdr_fill = PatternFill("solid", fgColor="305496")
            hdr_font = Font(color="FFFFFF", bold=True)
            for sh in wb.sheetnames:
                ws = wb[sh]
                for cell in ws[1]:
                    cell.fill = hdr_fill; cell.font = hdr_font
                    cell.alignment = Alignment(horizontal="center")
                ws.freeze_panes = "A2"
                for ci, col in enumerate(ws.columns, 1):
                    mx = 0
                    for cell in col:
                        v = "" if cell.value is None else str(cell.value)
                        mx = max(mx, min(len(v), 60))
                    ws.column_dimensions[get_column_letter(ci)].width = mx + 2
                # Cp/Cpk/FailRate 着色
                for i, cell in enumerate(ws[1], 1):
                    v = cell.value
                    if v and "FailRate" in str(v):
                        col = get_column_letter(i)
                        ws.conditional_formatting.add(
                            f"{col}2:{col}{ws.max_row}",
                            ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                                           mid_type="num", mid_value=5, mid_color="FFEB9C",
                                           end_type="num", end_value=15, end_color="FF6B6B"))
                    if v in ("Cp", "Cpk"):
                        col = get_column_letter(i)
                        ws.conditional_formatting.add(
                            f"{col}2:{col}{ws.max_row}",
                            ColorScaleRule(start_type="num", start_value=0, start_color="FF6B6B",
                                           mid_type="num", mid_value=1.33, mid_color="FFEB9C",
                                           end_type="num", end_value=2.0, end_color="63BE7B"))
