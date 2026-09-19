# -*- coding: utf-8 -*-
"""
Payload 构建 mixin
负责从字段配置构建查询参数，支持动态获取和硬编码字段
"""
import json
import logging

from core.nqi.common import convert_where_conditions
from core.nqi.utils.config import JXCX_SEARCH_URL, JXCX_TABLE_URL, HEADERS
from core.nqi.utils.constants import TIMEOUT_MEDIUM
from core.nqi.utils.helpers import datatype_to_code

logger = logging.getLogger(__name__)


class PayloadBuilderMixin:
    """构造 JXCX/DataTables 请求体。

    字段配置既可由后端 search/table 接口动态取得，也可由报表配置直接
    提供。构造结果保留服务端原始协议键名（包括 ``feild``），并把字段
    元数据、维度、where 条件和 DataTables 参数放在同一个 payload 中。
    """

    def get_field_config(self, table_key, fieldtype, api_type='search', table_name=None):
        """动态获取表字段配置（从 API 获取）。

        search 接口按报表关键字查询字段元数据，table 接口按数据库表名
        查询；任一接口返回空配置时切换到另一接口。结果按完整请求维度
        缓存，避免每个报表日期任务重复访问元数据接口。

        Args:
            table_key: API 查询关键字或显示名称。
            fieldtype: 字段类型过滤条件（由上游协议保留）。
            api_type: ``search`` 或 ``table``。
            table_name: 数据库表名（table 模式使用）。
        """
        cache_key = f"{table_key}_{fieldtype}_{api_type}_{table_name or ''}"
        if cache_key in self._field_config_cache:
            logger.info("使用缓存的字段配置: %s", cache_key)
            return self._field_config_cache[cache_key]

        logger.info("动态获取字段配置: table_key=%s, fieldtype=%s, api_type=%s, table_name=%s",
                    table_key, fieldtype, api_type, table_name)

        try:
            if api_type == 'table':
                db_table = table_name if table_name else table_key
                data = {'tablename': db_table}
                logger.info("调用getSelectTable接口，使用表名: %s", db_table)
                res = self.sess.post(JXCX_TABLE_URL, data=data, headers=HEADERS, timeout=TIMEOUT_MEDIUM)

                if res.status_code == 200:
                    result = json.loads(res.content)
                    configs = result.get('CFG_ADHOC_CONF_TABLE', [])

                    if not configs:
                        configs = result.get('row', [])
                        if configs:
                            logger.info("从getSelectTable接口(row字段)获取到 %d 个字段配置", len(configs))
                        else:
                            logger.warning("getSelectTable接口返回空配置，尝试使用search接口")
                            return self.get_field_config(table_key, fieldtype, 'search', table_name)

                    logger.info("从getSelectTable接口获取到 %d 个字段配置", len(configs))

                    self._field_config_cache[cache_key] = configs
                    return configs
                else:
                    logger.error("获取字段配置失败: %s", res.status_code)
                    return None
            else:
                data = {'key': table_key}
                fields_to_fetch = [
                    'columnname_cn', 'columnname', 'fieldtype', 'datatype',
                    'tablename', 'tablename_cn', 'columntype', 'sort',
                    'geographicdimension', 'timedimension', 'enodeb_field', 'cgi_field',
                    'time_field', 'cell_field', 'city_field',
                    'supporteddimension', 'supportedtimedimension'
                ]
                # search接口需要传递多个field参数，requests 会自动将列表展开为多个同名参数
                data['field'] = fields_to_fetch

                res = self.sess.post(JXCX_SEARCH_URL, data=data, headers=HEADERS, timeout=TIMEOUT_MEDIUM)

                if res.status_code == 200:
                    result = json.loads(res.content)
                    configs = result.get('CFG_ADHOC_CONF_SEARCH', [])
                    logger.info("从search接口获取到 %d 个字段配置", len(configs))

                    if not configs:
                        configs = result.get('data', [])
                        if configs:
                            logger.info("从search接口(data字段)获取到 %d 个字段配置", len(configs))

                    if not configs:
                        logger.warning("search接口返回空配置，尝试使用table接口")
                        return self.get_field_config(table_key, fieldtype, 'table', table_name)

                    self._field_config_cache[cache_key] = configs
                    return configs
                else:
                    logger.error("获取字段配置失败: %s", res.status_code)
                    return None
        except Exception as e:
            logger.error("获取字段配置异常: %s", e)
            import traceback
            traceback.print_exc()
            return None

    def build_payload_from_config(self, table_key, fieldtype, where_conditions, api_type='search',
                                  dimension_override=None, fields_override=None, table_name=None,
                                  table_params=None, indexcount=0):
        """从动态或预定义字段配置构建 JXCX payload。

        ``tableParams`` 只覆盖服务端支持的维度元数据，``indexcount`` 则
        原样写入协议字段，不能当作 DataTables 的 ``start``/``length``。
        动态配置按 ``sort`` 排序，第一项提供默认维度字段；预定义字段
        则绕过元数据请求，但仍生成完整的 columns/result/where 结构。
        """
        converted_conditions = convert_where_conditions(where_conditions)

        if dimension_override:
            geographicdimension = dimension_override.get('geographicdimension', '小区')
            timedimension = dimension_override.get('timedimension', '天')
            enodeb_field = dimension_override.get('enodebField', 'enodeb_id')
            cgi_field = dimension_override.get('cgiField', 'cgi')
            time_field = dimension_override.get('timeField', 'starttime')
            cell_field = dimension_override.get('cellField', 'cell')
            city_field = dimension_override.get('cityField', 'city')
            supporteddimension = dimension_override.get('supporteddimension', None)
            supportedtimedimension = dimension_override.get('supportedtimedimension', '')
        else:
            geographicdimension = '小区'
            timedimension = '天'
            enodeb_field = 'enodeb_id'
            cgi_field = 'cgi'
            time_field = 'starttime'
            cell_field = 'cell'
            city_field = 'city'
            supporteddimension = None
            supportedtimedimension = ''

        if table_params:
            supporteddimension = table_params.get('supporteddimension', supporteddimension)
            supportedtimedimension = table_params.get('supportedtimedimension', supportedtimedimension)

        if fields_override:
            logger.info("使用预定义的字段覆盖，共 %d 个字段", len(fields_override))
            return self._build_payload_with_field_configs(
                table_key, fieldtype, converted_conditions, api_type,
                geographicdimension, timedimension, enodeb_field, cgi_field,
                time_field, cell_field, city_field, fields_override,
                supporteddimension, supportedtimedimension, indexcount
            )

        logger.info("【字段配置】开始获取字段配置: table_key=%s, fieldtype=%s, api_type=%s, table_name=%s",
                    table_key, fieldtype, api_type, table_name)
        logger.info(f"[调试] table_key={table_key}, fieldtype={fieldtype}, api_type={api_type}")
        configs = self.get_field_config(table_key, fieldtype, api_type, table_name=table_name)
        if not configs:
            logger.error("【错误】无法获取字段配置，table_key=%s, fieldtype=%s, api_type=%s", table_key, fieldtype, api_type)
            logger.warning("API返回空配置，尝试备用接口...")
            fallback_api_type = 'search' if api_type == 'table' else 'table'
            logger.info("【备用方案】尝试使用备用API类型: %s", fallback_api_type)
            configs = self.get_field_config(table_key, fieldtype, fallback_api_type, table_name=table_name)
            if not configs:
                logger.error("【错误】备用方案也失败，返回None")
                return None
            logger.info("【备用方案】成功使用 %s 获取到 %d 个字段配置", fallback_api_type, len(configs))
            api_type = fallback_api_type

        logger.info("API返回的字段配置数量: %d", len(configs))
        valid_configs = [c for c in configs if isinstance(c, dict)]
        if len(valid_configs) != len(configs):
            logger.warning("过滤掉了 %d 个非字典类型的配置项", len(configs) - len(valid_configs))
        configs = valid_configs

        if not configs:
            logger.error("没有有效的字段配置，返回None")
            return None

        logger.info("有效字段配置数量: %d", len(configs))
        logger.info("API返回的字段名(前10个): %s", [c.get('columnname', '') for c in configs[:10]])
        logger.info("API返回的fieldtype(前3个): %s", list(set(c.get('fieldtype', '') for c in configs[:3])))

        sorted_configs = sorted(configs, key=lambda x: x.get('sort', 0) if isinstance(x, dict) else 0)
        first_config = sorted_configs[0]

        geographicdimension = first_config.get('geographicdimension', geographicdimension)
        timedimension = first_config.get('timedimension', timedimension)
        enodeb_field = first_config.get('enodeb_field', first_config.get('enodebField', enodeb_field))
        cgi_field = first_config.get('cgi_field', first_config.get('cgiField', cgi_field))
        time_field = first_config.get('time_field', first_config.get('timeField', time_field))
        cell_field = first_config.get('cell_field', first_config.get('cellField', cell_field))
        city_field = first_config.get('city_field', first_config.get('cityField', city_field))
        supporteddimension = first_config.get('supporteddimension', supporteddimension)
        supportedtimedimension = first_config.get('supportedtimedimension', supportedtimedimension)

        logger.info("使用的维度参数: geographicdimension=%s, timedimension=%s", geographicdimension, timedimension)

        field_list = [c['columnname'] for c in sorted_configs]

        columns = []
        for field in field_list:
            columns.append({
                'data': field, 'name': '',
                'searchable': True, 'orderable': True,
                'search': {'value': '', 'regex': False}
            })

        table_name_from_config = first_config.get('tablename', '')
        table_name_cn = first_config.get('tablename_cn', '')

        result_list = []
        for c in sorted_configs:
            datatype_str = c.get('datatype', 'character varying')
            datatype_code = datatype_to_code(datatype_str)
            result_list.append({
                'feildtype': c.get('fieldtype', ''),
                'table': c.get('tablename', ''),
                'tableName': c.get('tablename_cn', ''),
                'datatype': datatype_code,
                'columntype': c.get('columntype', 1),
                'feildName': c.get('columnname_cn', ''),
                'feild': c.get('columnname', ''),
                'poly': '无', 'anyWay': '无', 'chart': '无', 'chartpoly': '无'
            })

        result = {
            'result': result_list,
            'tableParams': {
                'supporteddimension': None if not supporteddimension else supporteddimension,
                'supportedtimedimension': supportedtimedimension if supportedtimedimension else ''
            },
            'columnname': ''
        }

        payload = {
            'draw': 1, 'start': 0, 'length': 200, 'total': 0,
            'geographicdimension': geographicdimension,
            'timedimension': timedimension,
            'enodebField': enodeb_field, 'cgiField': cgi_field,
            'timeField': time_field, 'cellField': cell_field, 'cityField': city_field,
            'columns': columns,
            'order': [{'column': 0, 'dir': 'desc'}],
            'search': {'value': '', 'regex': False},
            'result': result,
            'where': converted_conditions,
            'indexcount': indexcount
        }

        logger.info("构建的payload包含 %d 个字段", len(columns))
        return payload

    def _build_payload_with_fields(self, table_key, fieldtype, where_conditions, api_type,
                                   dimension_override, fields_list):
        """使用字段列表构建payload（当API获取失败时使用）"""
        converted_conditions = convert_where_conditions(where_conditions)

        geographicdimension = dimension_override.get('geographicdimension', '小区')
        timedimension = dimension_override.get('timedimension', '天')
        enodeb_field = dimension_override.get('enodebField', 'enodeb_id')
        cgi_field = dimension_override.get('cgiField', 'cgi')
        time_field = dimension_override.get('timeField', 'starttime')
        cell_field = dimension_override.get('cellField', 'cell')
        city_field = dimension_override.get('cityField', 'city')
        table_name = dimension_override.get('table_name', '')

        columns = []
        for field in fields_list:
            columns.append({
                'data': field, 'name': '',
                'searchable': True, 'orderable': True,
                'search': {'value': '', 'regex': False}
            })

        result_list = []
        for field in fields_list:
            datatype_code = datatype_to_code('character varying')
            result_list.append({
                'feildtype': fieldtype,
                'table': table_name, 'tableName': '',
                'datatype': datatype_code, 'columntype': 1,
                'feildName': field, 'feild': field,
                'poly': '无', 'anyWay': '无', 'chart': '无', 'chartpoly': '无'
            })

        result = {
            'result': result_list,
            'tableParams': {'supporteddimension': None, 'supportedtimedimension': ''},
            'columnname': ''
        }

        payload = {
            'draw': 1, 'start': 0, 'length': 200, 'total': 0,
            'geographicdimension': geographicdimension,
            'timedimension': timedimension,
            'enodebField': enodeb_field, 'cgiField': cgi_field,
            'timeField': time_field, 'cellField': cell_field, 'cityField': city_field,
            'columns': columns,
            'order': [{'column': 0, 'dir': 'desc'}],
            'search': {'value': '', 'regex': False},
            'result': result,
            'where': converted_conditions,
            'indexcount': 0
        }

        logger.info("使用字段列表构建payload，包含 %d 个字段", len(columns))
        return payload

    def _build_payload_with_field_configs(self, table_key, fieldtype, where_conditions, api_type,
                                         geographicdimension, timedimension, enodeb_field, cgi_field,
                                         time_field, cell_field, city_field, fields_override,
                                         supporteddimension=None, supportedtimedimension='', indexcount=0):
        """使用字段配置列表构建payload"""
        converted_conditions = convert_where_conditions(where_conditions)

        logger.info("[DEBUG] 输入条件数量: %d", len(where_conditions) if where_conditions else 0)
        logger.info("[DEBUG] 转换后条件数量: %d", len(converted_conditions) if converted_conditions else 0)
        if where_conditions:
            logger.info("[DEBUG] 输入条件[0]: %s", where_conditions[0])
        if converted_conditions:
            logger.info("[DEBUG] 转换后条件[0]: %s", converted_conditions[0])

        columns = []
        result_list = []
        table_name = ''

        for config in fields_override:
            field = config.get('feild', config.get('columnname', ''))
            if not field:
                continue

            columns.append({
                'data': field, 'name': '',
                'searchable': True, 'orderable': True,
                'search': {'value': '', 'regex': False}
            })

            table_name = config.get('table', table_name)
            datatype_str = config.get('datatype', 'character varying')
            datatype_code = datatype_to_code(datatype_str)
            result_list.append({
                'feildtype': config.get('feildtype', fieldtype),
                'table': config.get('table', table_name),
                'tableName': config.get('tableName', ''),
                'datatype': datatype_code,
                'columntype': config.get('columntype', 1),
                'feildName': config.get('feildName', field),
                'feild': field,
                'poly': '无', 'anyWay': '无', 'chart': '无', 'chartpoly': '无'
            })

        result = {
            'result': result_list,
            'tableParams': {
                'supporteddimension': None if not supporteddimension else supporteddimension,
                'supportedtimedimension': supportedtimedimension if supportedtimedimension else ''
            },
            'columnname': ''
        }

        payload = {
            'draw': 1, 'start': 0, 'length': 200, 'total': 0,
            'geographicdimension': geographicdimension,
            'timedimension': timedimension,
            'enodebField': enodeb_field, 'cgiField': cgi_field,
            'timeField': time_field, 'cellField': cell_field, 'cityField': city_field,
            'columns': columns,
            'order': [{'column': 0, 'dir': 'desc'}],
            'search': {'value': '', 'regex': False},
            'result': result,
            'where': converted_conditions,
            'indexcount': indexcount
        }

        logger.info("使用字段配置构建payload，包含 %d 个字段", len(columns))
        return payload

    def build_4g_voice_payload(self, volte_fields, epsfb_fields, where_conditions,
                               volte_dimension, epsfb_dimension):
        """构建4G语音小区的VoLTE和EPSFB的payload

        Args:
            volte_fields: VoLTE表的字段配置列表
            epsfb_fields: EPSFB表的字段配置列表
            where_conditions: 查询条件列表
            volte_dimension: VoLTE表的维度参数
            epsfb_dimension: EPSFB表的维度参数

        Returns:
            dict: {'volte': volte_payload, 'epsfb': epsfb_payload}
        """
        volte_payload = self._build_payload_with_field_configs(
            'VoLTE小区监控预警数据表-天',
            'VoLTE小区监控预警数据表-天',
            where_conditions,
            'table',
            volte_dimension.get('geographicdimension', '小区'),
            volte_dimension.get('timedimension', '天'),
            volte_dimension.get('enodebField', 'enodeb_id'),
            volte_dimension.get('cgiField', 'cgi'),
            volte_dimension.get('timeField', 'starttime'),
            volte_dimension.get('cellField', 'cell'),
            volte_dimension.get('cityField', 'city'),
            volte_fields
        )

        epsfb_payload = self._build_payload_with_field_configs(
            'EPSFB小区监控预警数据表-天',
            'EPSFB小区监控预警数据表-天',
            where_conditions,
            'table',
            epsfb_dimension.get('geographicdimension', '小区'),
            epsfb_dimension.get('timedimension', '天'),
            epsfb_dimension.get('enodebField', '---'),
            epsfb_dimension.get('cgiField', 'cgi'),
            epsfb_dimension.get('timeField', 'starttime'),
            epsfb_dimension.get('cellField', 'cell'),
            epsfb_dimension.get('cityField', 'city'),
            epsfb_fields
        )

        logger.info("4G语音小区payload构建完成: VoLTE=%d字段, EPSFB=%d字段",
                   len(volte_fields), len(epsfb_fields))
        return {'volte': volte_payload, 'epsfb': epsfb_payload}