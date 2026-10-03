import os
import yaml
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from loguru import logger

from db.models import Base

class DatabaseManager:
    """数据库连接与生命周期管理器 (仅 SQLite 本地模式)"""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()
        self.active_db = "sqlite"
        self.engine = self._create_engine()
        self.SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self._init_db()

    def _load_config(self) -> dict:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _create_engine(self):
        db_conf = self.config.get("database", {})
        sqlite_conf = db_conf.get("sqlite", {})
        db_path = sqlite_conf.get("db_path", "data/monitor.db")
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        url = f"sqlite:///{os.path.abspath(db_path)}"
        logger.info(f"正在连接 SQLite 数据库: {db_path}")

        engine = create_engine(url, echo=False)

        # 开启 SQLite WAL 模式及优化参数
        if sqlite_conf.get("wal_mode", True):
            @event.listens_for(engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA cache_size=-64000")  # 64MB 缓存
                cursor.execute("PRAGMA temp_store=MEMORY")
                cursor.close()
        return engine

    def _init_db(self):
        """自动建表、增量补列与初始化系统检查点"""
        try:
            Base.metadata.create_all(bind=self.engine)
            self._migrate_add_columns()
            logger.info("数据库表结构校验/初始化完成 (SQLite)")
            self._init_default_checkpoints()
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise

    def _migrate_add_columns(self):
        """
        轻量列迁移: 逐列检查 ORM 模型与真实表结构差异，
        对新增的标量列执行 ALTER TABLE ADD COLUMN。
        """
        from db.models import Base

        def _existing_cols(table):
            with self.engine.connect() as conn:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                return {r[1] for r in rows}

        for table_name, table in Base.metadata.tables.items():
            existing = _existing_cols(table_name)
            if not existing:
                continue
            for col in table.columns:
                if col.name in existing:
                    continue
                col_type = col.type.compile(self.engine.dialect)
                ddl = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"

                # 依据 ORM 默认值给出安全的新列默认值(仅标量字面量；callable 如 datetime.utcnow 跳过)
                default = None
                if col.default is not None and not getattr(col.default, "is_callable", False):
                    default = col.default.arg
                if isinstance(default, bool):
                    ddl += f" DEFAULT {1 if default else 0}"
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    safe = default.replace("'", "''")
                    ddl += f" DEFAULT '{safe}'"

                with self.engine.begin() as conn:
                    conn.execute(text(ddl))
                logger.info(f"[迁移] 表 {table_name} 新增列 {col.name} ({col_type})")

    def _init_default_checkpoints(self):
        """初始化检查点状态"""
        with self.get_session() as session:
            from db.models import ETLCheckpoint
            defaults = [
                ("GLOBAL_STATUS", "STANDBY_AUTH", "调度器运行状态: ACTIVE / STANDBY_AUTH"),
                ("COOKIE_STATUS", "UNKNOWN", "NQI Cookie会话状态: VALID / EXPIRED / UNKNOWN"),
                ("COOKIE_LAST_CHECK", "", "最后一次检测Cookie时间"),
            ]
            for key, val, desc in defaults:
                exists = session.query(ETLCheckpoint).filter_by(checkpoint_key=key).first()
                if not exists:
                    session.add(ETLCheckpoint(checkpoint_key=key, checkpoint_value=val, description=desc))
            session.commit()

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """提供安全的上下文 Session 管理"""
        session: Session = self.SessionFactory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

# 单例实例
db_manager = DatabaseManager()
