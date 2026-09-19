from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, Index, UniqueConstraint
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class ETLTaskLog(Base):
    """ETL任务流水日志表"""
    __tablename__ = "etl_task_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_name = Column(String(64), nullable=False)        # 4G_KPI / 5G_CU / 5G_DU
    data_hour = Column(String(32), nullable=False)        # '2026-09-19 08:00:00'
    row_count = Column(Integer, default=0)
    status = Column(String(32), nullable=False)           # SUCCESS / FAILED / PAUSED_NEED_AUTH
    error_message = Column(Text, nullable=True)
    duration_sec = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("task_name", "data_hour", name="uq_task_hour"),
        Index("idx_etl_hour_status", "data_hour", "status"),
    )


class ETLCheckpoint(Base):
    """系统检查点与运行状态表 (支持断点续采与会话感知)"""
    __tablename__ = "etl_checkpoint"

    id = Column(Integer, primary_key=True, autoincrement=True)
    checkpoint_key = Column(String(64), unique=True, nullable=False)  # LAST_SUCCESS_HOUR / GLOBAL_STATUS
    checkpoint_value = Column(String(255), nullable=True)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KPI4GHourly(Base):
    """4G小时级性能指标表"""
    __tablename__ = "kpi_4g_hourly"

    id = Column(Integer, primary_key=True, autoincrement=True)
    start_time = Column(String(32), nullable=False)       # '2026-09-19 08:00:00'
    cgi = Column(String(64), nullable=False)
    cell_name = Column(String(128), nullable=True)
    city = Column(String(64), nullable=True)
    vendor = Column(String(64), nullable=True)
    rrc_max_conn = Column(Float, default=0.0)             # RRC最大连接数
    wireless_drop_rate = Column(Float, default=0.0)       # 无线掉线率(%)
    wireless_setup_rate = Column(Float, default=0.0)      # 无线接通率(%)
    volte_drop_rate = Column(Float, default=0.0)          # VoLTE掉话率(%)
    volte_traffic = Column(Float, default=0.0)            # VoLTE话务量(ERL)
    volte_setup_rate = Column(Float, default=0.0)         # VoLTE接通率(%)
    dl_prb_util = Column(Float, default=0.0)              # 下行PRB利用率(0-1或0-100)
    dl_perceived_rate = Column(Float, default=0.0)        # 下行感知速率(Mbps)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("start_time", "cgi", name="uq_4g_time_cgi"),
        Index("idx_4g_time_cgi", "start_time", "cgi"),
        Index("idx_4g_cgi_time", "cgi", "start_time"),
    )


class KPI5GHourly(Base):
    """5G小时级性能指标表 (CU与DU关联对齐后)"""
    __tablename__ = "kpi_5g_hourly"

    id = Column(Integer, primary_key=True, autoincrement=True)
    start_time = Column(String(32), nullable=False)       # '2026-09-19 08:00:00'
    ncgi = Column(String(64), nullable=False)
    cell_name = Column(String(128), nullable=True)
    city = Column(String(64), nullable=True)
    vendor = Column(String(64), nullable=True)
    rrc_max_conn = Column(Float, default=0.0)             # RRC最大连接数
    sa_drop_rate = Column(Float, default=0.0)             # SA无线掉线率(%)
    sa_setup_rate = Column(Float, default=0.0)            # SA无线接通率(%)
    vonr_flow_drop_rate = Column(Float, default=0.0)      # VoNR业务Flow掉线率(%)
    vonr_traffic = Column(Float, default=0.0)             # VoNR话务量(ERL)
    vonr_setup_rate = Column(Float, default=0.0)          # VoNR无线接通率(%)
    dl_prb_util = Column(Float, default=0.0)              # 下行PRB利用率(来自DU)
    dl_user_rate = Column(Float, default=0.0)             # 下行平均感知速率(Mbps, 来自CU)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("start_time", "ncgi", name="uq_5g_time_ncgi"),
        Index("idx_5g_time_ncgi", "start_time", "ncgi"),
        Index("idx_5g_ncgi_time", "ncgi", "start_time"),
    )


class CellIssueRecord(Base):
    """性能问题小区明细记录表 (每次评估产生的单时段质差事件)"""
    __tablename__ = "cell_issue_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    start_time = Column(String(32), nullable=False)       # '2026-09-19 08:00:00'
    network_type = Column(String(16), nullable=False)     # 4G / 5G
    cell_id = Column(String(64), nullable=False)          # cgi / ncgi
    cell_name = Column(String(128), nullable=True)
    city = Column(String(64), nullable=True)
    issue_type = Column(String(64), nullable=False)       # 如 'VoLTE高掉线小区'
    metric_value_1 = Column(Float, nullable=True)         # 主指标值 (如掉线率)
    metric_value_2 = Column(Float, nullable=True)         # 门限/伴随值 (如话务量或最大连接数)
    is_repeated_8h = Column(Integer, default=0)           # 8小时内是否复现 (1:是, 0:否)
    repeat_count_8h = Column(Integer, default=1)          # 过去8小时内累计恶化次数
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("start_time", "cell_id", "issue_type", name="uq_issue_record"),
        Index("idx_issue_time_type", "start_time", "issue_type"),
        Index("idx_issue_cell_time", "cell_id", "start_time"),
        Index("idx_issue_repeated", "is_repeated_8h", "start_time"),
    )


class CellOverclockSummary(Base):
    """周期超频统计沉淀表 (本日/本周考核汇总)"""
    __tablename__ = "cell_overclock_summary"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_type = Column(String(16), nullable=False)       # 'DAILY' (本日) / 'WEEKLY' (上周五至本周四)
    cycle_key = Column(String(32), nullable=False)        # 日期 '2026-09-19' 或周标签 '2026-W38'
    network_type = Column(String(16), nullable=False)     # 4G / 5G
    cell_id = Column(String(64), nullable=False)
    cell_name = Column(String(128), nullable=True)
    city = Column(String(64), nullable=True)
    issue_type = Column(String(64), nullable=False)
    occurrences_count = Column(Integer, default=1)        # 恶化时段总数
    active_days_count = Column(Integer, default=1)        # 恶化天数
    max_repeat_in_8h = Column(Integer, default=1)         # 8小时窗口内最大复现数
    first_time = Column(String(32), nullable=True)
    last_time = Column(String(32), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("cycle_type", "cycle_key", "cell_id", "issue_type", name="uq_cycle_summary"),
        Index("idx_summary_cycle", "cycle_type", "cycle_key"),
    )
