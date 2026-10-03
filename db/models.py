from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, DateTime, Text, Index, UniqueConstraint
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.compiler import compiles

Base = declarative_base()


# SQLite 不支持 BIGINT AUTOINCREMENT 主键 (建表会生成非自增 BIGINT, 插入报错)。
# 让 BigInteger 在 SQLite 方言下编译为 INTEGER, 获得 rowid 自增语义。
@compiles(BigInteger, "sqlite")
def _biginteger_to_integer(element, compiler, **kw):
    return "INTEGER"


class ETLCheckpoint(Base):
    """系统检查点与运行状态表 (会话状态感知)"""
    __tablename__ = "etl_checkpoint"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    checkpoint_key = Column(String(64), unique=True, nullable=False)  # GLOBAL_STATUS / COOKIE_STATUS 等
    checkpoint_value = Column(String(255), nullable=True)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SysUser(Base):
    """平台登录用户表 (普通用户/管理员)"""
    __tablename__ = "sys_user"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)   # 登录账号
    password = Column(String(128), nullable=False)               # 明文存储密码 (如需可改hash)
    role = Column(String(16), default="user")                    # 'admin' / 'user'
    display_name = Column(String(64), nullable=True)             # 显示名
    created_at = Column(DateTime, default=datetime.utcnow)


class WorkOrderRecord(Base):
    """工单记录表（统一存储集团工单与省内工单）"""
    __tablename__ = "work_order_record"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_type = Column(String(16), nullable=False)       # 'group' (集团工单) / 'province' (省内工单)
    order_code = Column(String(96), nullable=False)       # 工单唯一编号 (order_code / code)
    cluster_code = Column(String(64), nullable=True)      # 集团: 聚类工单序号
    city = Column(String(32), nullable=True, index=True)  # 地市中文名 (如 阳江, 广州)
    title = Column(String(512), nullable=True)            # 省内: title / 集团: 问题小区名
    cell_id = Column(String(64), nullable=True)           # 集团: 问题小区 CGI
    cell_name = Column(String(255), nullable=True)        # 小区名称
    status = Column(String(64), nullable=True)            # 状态中文 (集团: 当前状态 / 省内: woStatusName)
    is_finished = Column(Integer, default=0)              # 是否已办结: 0 未办结, 1 已办结
    is_ids = Column(Integer, default=0)                   # 是否IDS工单: 0 否, 1 是 (人工标记, 标记后不参与统计计算)
    is_manual_finished = Column(Integer, default=0)       # 是否人工标记已处理: 0 否, 1 是 (人工标记; 自动拉取证明已办结时重置为0)
    current_node = Column(String(64), nullable=True)      # 当前流程节点
    process_key = Column(String(64), nullable=True)       # 流程定义Key (如 proc_gtssxn)
    process_name = Column(String(128), nullable=True)     # 流程名称 (如 高铁实时性能工单流程)
    special_label = Column(String(128), nullable=True)    # 专项标签 (如 集团假日保障)
    create_time = Column(String(32), nullable=True)       # 工单生成/派发时间 'YYYY-MM-DD HH:MM:SS'
    finish_time = Column(String(32), nullable=True)       # 办结时间
    extra_json = Column(Text, nullable=True)              # 原始行数据 JSON 快照 (全量保留以便导出)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("order_type", "order_code", name="uq_wo_type_code"),
        Index("idx_wo_city_time", "city", "create_time"),
        Index("idx_wo_type_time", "order_type", "create_time"),
        Index("idx_wo_status", "order_type", "is_finished"),
    )


class WorkOrderCollectLog(Base):
    """工单采集任务日志表"""
    __tablename__ = "work_order_collect_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    scope = Column(String(16), nullable=False)            # 'all' / 'group' / 'province'
    start_date = Column(String(32), nullable=False)
    end_date = Column(String(32), nullable=False)
    city = Column(String(32), nullable=True)
    pages_fetched = Column(Integer, default=0)
    rows_fetched = Column(Integer, default=0)
    rows_inserted = Column(Integer, default=0)
    rows_updated = Column(Integer, default=0)
    status = Column(String(16), default="SUCCESS")        # SUCCESS / FAILED / PARTIAL
    error_message = Column(Text, nullable=True)
    duration_sec = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_wo_log_created", "created_at"),
    )
