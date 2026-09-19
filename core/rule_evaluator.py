from typing import List, Dict, Any
from loguru import logger

class RuleEvaluator:
    """
    10 大类性能问题小区判定引擎
    根据小区类型判定规则实现门限比对
    """

    @classmethod
    def evaluate_4g_records(cls, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        评估 4G 小区记录，产出单时段问题小区事件
        涉及规则：
        1. VoLTE高掉线小区: volte_drop_rate > 2% 且 volte_traffic > 1 (ERL)
        3. LTE 掉线高小区: wireless_drop_rate > 2% 且 rrc_max_conn > 100
        5. LTE 接通低小区: wireless_setup_rate < 98% 且 rrc_max_conn > 100
        8. VoLTE 无线接通低小区: volte_setup_rate < 95% 且 volte_traffic > 1 (ERL)
        9. 4G 下行低速率小区: dl_prb_util > 50%(0.5) 且 dl_perceived_rate < 1 (Mbps)
        """
        issue_records = []
        for r in records:
            cell_id = r["cgi"]
            cell_name = r.get("cell_name", "")
            city = r.get("city", "")
            start_time = r["start_time"]

            drop_rate = r.get("wireless_drop_rate", 0.0)
            setup_rate = r.get("wireless_setup_rate", 1.0)
            rrc_conn = r.get("rrc_max_conn", 0.0)
            volte_drop = r.get("volte_drop_rate", 0.0)
            volte_traffic = r.get("volte_traffic", 0.0)
            volte_setup = r.get("volte_setup_rate", 1.0)
            dl_prb = r.get("dl_prb_util", 0.0)
            dl_rate = r.get("dl_perceived_rate", 0.0)

            # 规则 1: VoLTE高掉线小区
            if volte_drop > 0.02 and volte_traffic > 1.0:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "4G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "VoLTE高掉线小区",
                    "metric_value_1": round(volte_drop * 100, 2),
                    "metric_value_2": round(volte_traffic, 2)
                })

            # 规则 3: LTE 掉线高小区
            if drop_rate > 0.02 and rrc_conn > 100:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "4G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "LTE 掉线高小区",
                    "metric_value_1": round(drop_rate * 100, 2),
                    "metric_value_2": round(rrc_conn, 0)
                })

            # 规则 5: LTE 接通低小区
            if setup_rate < 0.98 and rrc_conn > 100:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "4G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "LTE 接通低小区",
                    "metric_value_1": round(setup_rate * 100, 2),
                    "metric_value_2": round(rrc_conn, 0)
                })

            # 规则 8: VoLTE 无线接通低小区
            if volte_setup < 0.95 and volte_traffic > 1.0:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "4G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "VoLTE 无线接通低小区",
                    "metric_value_1": round(volte_setup * 100, 2),
                    "metric_value_2": round(volte_traffic, 2)
                })

            # 规则 9: 4G 下行低速率小区
            if dl_prb > 0.5 and dl_rate < 1.0:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "4G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "4G 下行低速率小区",
                    "metric_value_1": round(dl_prb * 100, 2),
                    "metric_value_2": round(dl_rate, 2)
                })

        return issue_records

    @classmethod
    def evaluate_5g_records(cls, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        评估 5G 小区记录，产出单时段问题小区事件
        涉及规则：
        2. VoNR业务Flow掉线高小区: vonr_flow_drop_rate > 2% 且 vonr_traffic > 0.5 (ERL)
        4. SA 高掉线小区: sa_drop_rate > 2% 且 rrc_max_conn > 100
        6. SA 低接通小区: sa_setup_rate < 98% 且 rrc_max_conn > 100
        7. VoNR 无线接通率低: vonr_setup_rate < 95% 且 vonr_traffic > 0.5 (ERL)
        10. 5G 下行低速率小区: dl_prb_util > 80%(0.8) 且 dl_user_rate < 5 (Mbps) 且 rrc_max_conn > 100
        """
        issue_records = []
        for r in records:
            cell_id = r["ncgi"]
            cell_name = r.get("cell_name", "")
            city = r.get("city", "")
            start_time = r["start_time"]

            sa_drop = r.get("sa_drop_rate", 0.0)
            sa_setup = r.get("sa_setup_rate", 1.0)
            rrc_conn = r.get("rrc_max_conn", 0.0)
            vonr_drop = r.get("vonr_flow_drop_rate", 0.0)
            vonr_traffic = r.get("vonr_traffic", 0.0)
            vonr_setup = r.get("vonr_setup_rate", 1.0)
            dl_prb = r.get("dl_prb_util", 0.0)
            dl_rate = r.get("dl_user_rate", 0.0)

            # 规则 2: VoNR业务Flow掉线高小区
            if vonr_drop > 0.02 and vonr_traffic > 0.5:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "5G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "VoNR业务Flow掉线高小区",
                    "metric_value_1": round(vonr_drop * 100, 2),
                    "metric_value_2": round(vonr_traffic, 2)
                })

            # 规则 4: SA 高掉线小区
            if sa_drop > 0.02 and rrc_conn > 100:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "5G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "SA 高掉线小区",
                    "metric_value_1": round(sa_drop * 100, 2),
                    "metric_value_2": round(rrc_conn, 0)
                })

            # 规则 6: SA 低接通小区
            if sa_setup < 0.98 and rrc_conn > 100:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "5G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "SA 低接通小区",
                    "metric_value_1": round(sa_setup * 100, 2),
                    "metric_value_2": round(rrc_conn, 0)
                })

            # 规则 7: VoNR 无线接通率低
            if vonr_setup < 0.95 and vonr_traffic > 0.5:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "5G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "VoNR 无线接通率低",
                    "metric_value_1": round(vonr_setup * 100, 2),
                    "metric_value_2": round(vonr_traffic, 2)
                })

            # 规则 10: 5G 下行低速率小区
            if dl_prb > 0.8 and dl_rate < 5.0 and rrc_conn > 100:
                issue_records.append({
                    "start_time": start_time,
                    "network_type": "5G",
                    "cell_id": cell_id,
                    "cell_name": cell_name,
                    "city": city,
                    "issue_type": "5G 下行低速率小区",
                    "metric_value_1": round(dl_prb * 100, 2),
                    "metric_value_2": round(dl_rate, 2)
                })

        return issue_records
