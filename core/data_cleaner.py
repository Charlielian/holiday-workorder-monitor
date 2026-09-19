import pandas as pd
import numpy as np
from typing import Dict, Any, List
from loguru import logger

class DataCleaner:
    """
    数据清洗、标准化与 5G CU/DU 关联对齐模块
    """

    @staticmethod
    def clean_4g_kpi(df: pd.DataFrame, target_hour: str) -> List[Dict[str, Any]]:
        """
        清洗并归一化 4G KPI 数据
        """
        if df is None or df.empty:
            return []

        cleaned_rows = []
        for _, row in df.iterrows():
            cgi = str(row.get("cgi", "")).strip()
            if not cgi or cgi == "nan":
                continue

            # 指标提取与异常清洗 (转为浮点数并做除以100等标准化处理)
            def _get_float(key, default=0.0, divide_100=False):
                val = row.get(key, default)
                try:
                    f = float(val) if pd.notnull(val) else default
                    return f / 100.0 if divide_100 and f > 1.0 else f
                except:
                    return default

            cleaned_rows.append({
                "start_time": target_hour,
                "cgi": cgi,
                "cell_name": str(row.get("cell_name", row.get("小区名称", ""))),
                "city": str(row.get("city", row.get("所属地市", ""))),
                "vendor": str(row.get("vendor", row.get("厂家", ""))),
                "rrc_max_conn": _get_float("rrc_max_conn", _get_float("attconnestab", 0.0)),
                "wireless_drop_rate": _get_float("wireless_drop_rate", _get_float("无线掉线率", 0.0, divide_100=True)),
                "wireless_setup_rate": _get_float("wireless_setup_rate", _get_float("无线接通率", 1.0, divide_100=True)),
                "volte_drop_rate": _get_float("volte_drop_rate", _get_float("cs_sbc_drops_rate", 0.0, divide_100=True)),
                "volte_traffic": _get_float("volte_traffic", _get_float("话务量", 0.0)),
                "volte_setup_rate": _get_float("volte_setup_rate", _get_float("cs_sbc_suss_rate", 1.0, divide_100=True)),
                "dl_prb_util": _get_float("dl_prb_util", _get_float("下行PRB利用率", 0.0, divide_100=True)),
                "dl_perceived_rate": _get_float("dl_perceived_rate", _get_float("下行感知速率", 0.0)),
            })
        return cleaned_rows

    @staticmethod
    def clean_and_merge_5g(cu_df: pd.DataFrame, du_df: pd.DataFrame, target_hour: str) -> List[Dict[str, Any]]:
        """
        清洗并对齐 5G CU 与 5G DU 数据 (按 ncgi 关联)
        """
        if cu_df is None or cu_df.empty:
            return []

        # 构建 DU 字典: {ncgi: dl_prb_util}
        du_dict = {}
        if du_df is not None and not du_df.empty:
            for _, row in du_df.iterrows():
                ncgi = str(row.get("ncgi", row.get("NCGI", ""))).strip()
                if ncgi:
                    try:
                        prb = float(row.get("dl_prb_util", row.get("下行PRB平均利用率(%)", 0.0)))
                        du_dict[ncgi] = prb / 100.0 if prb > 1.0 else prb
                    except:
                        du_dict[ncgi] = 0.0

        cleaned_rows = []
        for _, row in cu_df.iterrows():
            ncgi = str(row.get("ncgi", "")).strip()
            if not ncgi or ncgi == "nan":
                continue

            def _get_float(key, default=0.0, divide_100=False):
                val = row.get(key, default)
                try:
                    f = float(val) if pd.notnull(val) else default
                    return f / 100.0 if divide_100 and f > 1.0 else f
                except:
                    return default

            # PRB 优先从 DU 匹配获取，若无则读 CU 中自带字段
            dl_prb = du_dict.get(ncgi, _get_float("dl_prb_util", 0.0, divide_100=True))

            cleaned_rows.append({
                "start_time": target_hour,
                "ncgi": ncgi,
                "cell_name": str(row.get("nrcell_name", row.get("小区名称", ""))),
                "city": str(row.get("city", row.get("所属地市", ""))),
                "vendor": str(row.get("vendor", row.get("设备厂家", ""))),
                "rrc_max_conn": _get_float("rrc_max_conn", _get_float("bh_rrc_connmax", 0.0)),
                "sa_drop_rate": _get_float("sa_drop_rate", _get_float("SA无线掉线率", 0.0, divide_100=True)),
                "sa_setup_rate": _get_float("sa_setup_rate", _get_float("SA无线接通率", 1.0, divide_100=True)),
                "vonr_flow_drop_rate": _get_float("vonr_flow_drop_rate", _get_float("sacs_start_vonr_call_drop_rate", 0.0, divide_100=True)),
                "vonr_traffic": _get_float("vonr_traffic", _get_float("vonr_ans_voice_call", 0.0)),
                "vonr_setup_rate": _get_float("vonr_setup_rate", _get_float("sacs_start_vonr_call_net_succ_rate", 1.0, divide_100=True)),
                "dl_prb_util": dl_prb,
                "dl_user_rate": _get_float("dl_user_rate", _get_float("下行用户平均感知速率", 0.0)),
            })
        return cleaned_rows
