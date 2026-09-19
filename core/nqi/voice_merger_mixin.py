# -*- coding: utf-8 -*-
"""
4G 语音合并查询 mixin
负责 VoLTE + EPSFB 预警数据的联合查询与合并
"""
import logging

import pandas as pd

from core.nqi.utils.logger import get_report_logger

logger = logging.getLogger(__name__)


class VoiceMergerMixin:
    """4G 语音合并查询方法。

    VoLTE 与 EPSFB 是两个独立后端报表，结果按时间和小区标识做外连接；
    有键时保留任一报表出现的记录，无键时退化为纵向 concat，避免凭空
    猜测关联关系。返回值同时保留两个原始 DataFrame 和 merged 结果。
    """

    def get_4g_voice_table(self, volte_payload, epsfb_payload, to_df=True):
        """查询并合并 VoLTE/EPSFB 预警数据。

        ``to_df=True`` 返回合并后的 DataFrame；否则返回旧接口要求的
        ``{'data': records}``。内部结果还会保留两个原始报表，便于日志和
        后续计算区分两个制式的数据来源。
        """
        result = self._get_4g_voice_table_internal(volte_payload, epsfb_payload)
        if to_df:
            return result['merged']
        else:
            return {'data': result['merged'].to_dict('records')}

    def _get_4g_voice_table_internal(self, volte_payload, epsfb_payload):
        """获取4G语音小区报表数据（内部方法）

        按时间+CGI唯一值联合VoLTE预警和EPSFB预警表
        """
        result = {'volte': pd.DataFrame(), 'epsfb': pd.DataFrame(), 'merged': pd.DataFrame()}

        report_logger = get_report_logger("4G语音小区")

        # ========== 4G语音报表开始 ==========
        report_logger.info("▶ 开始提取4G语音小区报表（预警报表联合）")

        if not self.enabled:
            report_logger.info("JXCX 未启用，尝试进入...")
            if not self.enter_jxcx():
                report_logger.error("无法进入即席查询模块")
                return result

        # ========== 步骤1：获取VoLTE数据 ==========
        report_logger.info("获取VoLTE预警数据...")

        volte_df = self.get_table(volte_payload, to_df=True, report_name="4G语音-VoLTE预警")
        report_logger.info("[VoLTE预警] 获取数据: %d 行", len(volte_df))

        result['volte'] = volte_df

        # ========== 步骤2：获取EPSFB数据 ==========
        report_logger.info("获取EPSFB预警数据...")

        epsfb_df = self.get_table(epsfb_payload, to_df=True, report_name="4G语音-EPSFB预警")
        report_logger.info("[EPSFB预警] 获取数据: %d 行", len(epsfb_df))

        result['epsfb'] = epsfb_df

        if volte_df.empty and epsfb_df.empty:
            report_logger.warning("VoLTE和EPSFB预警数据均为空")
            return result

        # ========== 步骤3：数据合并（按时间+CGI唯一值） ==========
        report_logger.debug("开始数据合并处理（按时间+CGI唯一值）...")

        # 确定合并键（支持多种字段名）
        time_col = None
        cgi_col = None
        for col in volte_df.columns:
            col_lower = col.lower()
            if col_lower == 'starttime' or col == '时间' or col_lower == 'result_time':
                time_col = col
            if col_lower == 'cgi' or col == '小区' or col_lower == 'cell':
                cgi_col = col

        if time_col is None:
            for col in volte_df.columns:
                if 'time' in col.lower() or '时间' in col:
                    time_col = col
                    break

        report_logger.info("[合并] 检测到的合并键 - 时间字段: %s, 小区字段: %s", time_col, cgi_col)
        report_logger.debug("[合并] VoLTE所有列: %s", list(volte_df.columns))
        report_logger.debug("[合并] EPSFB所有列: %s", list(epsfb_df.columns))

        # 按时间+CGI去重（取唯一值）
        if time_col and cgi_col:
            report_logger.info("[合并] 按 %s + %s 去重", time_col, cgi_col)
            if time_col in volte_df.columns and cgi_col in volte_df.columns:
                volte_df = volte_df.drop_duplicates(subset=[time_col, cgi_col], keep='first')
                report_logger.info("[合并] VoLTE去重后: %d 行", len(volte_df))
            if time_col in epsfb_df.columns and cgi_col in epsfb_df.columns:
                epsfb_df = epsfb_df.drop_duplicates(subset=[time_col, cgi_col], keep='first')
                report_logger.info("[合并] EPSFB去重后: %d 行", len(epsfb_df))

        # 重命名中文字段
        col_name_map = {
            'starttime': '时间', 'city': '地市', 'cgi': '小区', 'grid': '责任网格',
            'area': '区县', 'nrcell_name': '小区名称'
        }

        volte_rename = {}
        for en_col in volte_df.columns:
            if en_col in col_name_map:
                volte_rename[en_col] = col_name_map[en_col]
        volte_df = volte_df.rename(columns=volte_rename)

        epsfb_rename = {}
        for en_col in epsfb_df.columns:
            if en_col in col_name_map:
                epsfb_rename[en_col] = col_name_map[en_col]
        epsfb_df = epsfb_df.rename(columns=epsfb_rename)

        report_logger.debug("[合并] VoLTE转换后列名: %s", list(volte_df.columns))
        report_logger.debug("[合并] EPSFB转换后列名: %s", list(epsfb_df.columns))

        # 确定合并键（使用实际存在的字段）
        merge_keys = []
        if time_col and time_col in volte_df.columns:
            merge_keys.append(time_col)
        if cgi_col and cgi_col in volte_df.columns:
            merge_keys.append(cgi_col)

        report_logger.info("[合并] 合并键: %s", merge_keys)

        if not merge_keys:
            report_logger.warning("[合并] 无法确定合并键，使用concat合并")
            merged_df = pd.concat([volte_df, epsfb_df], ignore_index=True)
            result['merged'] = merged_df
        else:
            # 确定VoLTE和EPSFB的特有字段（支持多种前缀格式，排除合并键）
            merge_key_names = {'时间', '小区', 'starttime', 'cgi', 'result_time', 'cell'}

            # 匹配VoLTE字段
            volte_cols = []
            for c in volte_df.columns:
                if c in merge_key_names:
                    continue
                c_lower = c.lower()
                if (c_lower.startswith('volte') or 'volte' in c_lower or
                    c_lower.startswith('lte_')):
                    if c not in volte_cols:
                        volte_cols.append(c)

            # 匹配EPSFB字段
            epsfb_cols = []
            for c in epsfb_df.columns:
                if c in merge_key_names:
                    continue
                c_lower = c.lower()
                if (c_lower.startswith('epsfb') or 'epsfb' in c_lower or
                    c_lower.startswith('lte_')):
                    if c not in epsfb_cols:
                        epsfb_cols.append(c)

            report_logger.info("[合并] VoLTE特有字段 (%d个): %s", len(volte_cols), volte_cols[:10] if len(volte_cols) > 10 else volte_cols)
            report_logger.info("[合并] EPSFB特有字段 (%d个): %s", len(epsfb_cols), epsfb_cols[:10] if len(epsfb_cols) > 10 else epsfb_cols)

            # 构建VoLTE合并数据
            volte_merge_cols = [c for c in merge_keys if c in volte_df.columns] + [c for c in volte_cols if c in volte_df.columns]
            for name_col in ['nrcell_name', '小区名称', 'cell_name']:
                if name_col in volte_df.columns and name_col not in volte_merge_cols and name_col not in merge_keys:
                    volte_merge_cols.append(name_col)
                    break
            volte_for_merge = volte_df[volte_merge_cols].copy()

            # 构建EPSFB合并数据
            epsfb_merge_cols = [c for c in merge_keys if c in epsfb_df.columns] + [c for c in epsfb_cols if c in epsfb_df.columns]
            for name_col in ['nrcell_name', '小区名称', 'cell_name']:
                if name_col in epsfb_df.columns and name_col not in epsfb_merge_cols and name_col not in merge_keys:
                    epsfb_merge_cols.append(name_col)
                    break
            epsfb_for_merge = epsfb_df[epsfb_merge_cols].copy()

            # 补充小区名称
            epsfb_name_col = None
            volte_name_col = None

            for col in epsfb_for_merge.columns:
                if col in merge_keys:
                    continue
                if col == 'nrcell_name' or col == '小区名称' or col == 'cell_name':
                    epsfb_name_col = col
                    break

            for col in volte_for_merge.columns:
                if col in merge_keys:
                    continue
                if col == 'nrcell_name' or col == '小区名称' or col == 'cell_name':
                    volte_name_col = col
                    break

            # 重命名EPSFB的小区名称列避免冲突
            if epsfb_name_col:
                epsfb_for_merge = epsfb_for_merge.rename(columns={epsfb_name_col: 'nrcell_name_epsfb'})

            if not merge_keys:
                merged_df = pd.concat([volte_for_merge, epsfb_for_merge], axis=1)
            else:
                # 使用外连接合并（union all效果）
                merged_df = pd.merge(volte_for_merge, epsfb_for_merge, on=merge_keys, how='outer')

            # 补充小区名称（VoLTE优先，EPSFB备用）
            if 'nrcell_name_epsfb' in merged_df.columns:
                if 'nrcell_name' not in merged_df.columns:
                    merged_df['nrcell_name'] = merged_df['nrcell_name_epsfb']
                else:
                    merged_df['nrcell_name'] = merged_df['nrcell_name'].fillna(merged_df['nrcell_name_epsfb'])
                merged_df = merged_df.drop(columns=['nrcell_name_epsfb'])
                report_logger.debug("[合并] 小区名称已补充（VoLTE优先，EPSFB备用）")

            # 删除全为空的列
            merged_df = merged_df.dropna(axis=1, how='all')

            # 清理重复的列（如city_x/city_y）
            cols_to_drop = []
            for col in merged_df.columns:
                if col.endswith('_x'):
                    base_col = col[:-2]
                    y_col = base_col + '_y'
                    if y_col in merged_df.columns:
                        merged_df[base_col] = merged_df[col].fillna(merged_df[y_col])
                        cols_to_drop.extend([col, y_col])

            if cols_to_drop:
                merged_df = merged_df.drop(columns=cols_to_drop)
                report_logger.debug("[合并] 已清理重复列: %s", set(cols_to_drop))

            # 调整列顺序
            cols = list(merged_df.columns)
            if 'nrcell_name' in cols and 'cgi' in cols:
                cols.remove('nrcell_name')
                cgi_idx = cols.index('cgi')
                cols.insert(cgi_idx + 1, 'nrcell_name')
                merged_df = merged_df[cols]
                report_logger.debug("[合并] 已将小区名称列移到小区列后面")

        result['merged'] = merged_df

        # ========== 4G语音报表完成 ==========
        report_logger.info("✓ 4G语音小区报表完成: VoLTE=%d行, EPSFB=%d行, 合并=%d行",
                          len(volte_df), len(epsfb_df), len(merged_df))

        return result