import os
import yaml
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from loguru import logger

from db.models import Base

class DatabaseManager:
    """数据库连接与生命周期管理器 (支持 MySQL 与 SQLite 平滑切换)"""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config = self._load_config()
        self.active_db = self.config.get("database", {}).get("active", "sqlite").lower()
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
        if self.active_db == "mysql":
            mysql_conf = db_conf.get("mysql", {})
            user = mysql_conf.get("user", "root")
            password = mysql_conf.get("password", "")
            host = mysql_conf.get("host", "127.0.0.1")
            port = mysql_conf.get("port", 3306)
            dbname = mysql_conf.get("database", "cell_overclock_monitor")
            charset = mysql_conf.get("charset", "utf8mb4")
            
            # 构建 MySQL 连接 URL
            url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{dbname}?charset={charset}"
            logger.info(f"正在连接 MySQL 数据库: {host}:{port}/{dbname}")
            
            engine = create_engine(
                url,
                pool_size=mysql_conf.get("pool_size", 10),
                max_overflow=mysql_conf.get("max_overflow", 20),
                pool_recycle=mysql_conf.get("pool_recycle", 3600),
                pool_pre_ping=True,
                echo=False,
            )
            return engine
        else:
            # SQLite 本地模式
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
                    cursor.execute("PRAGMA cache_size=-64000") # 64MB 缓存
                    cursor.execute("PRAGMA temp_store=MEMORY")
                    cursor.close()
            return engine

    def _init_db(self):
        """自动建表与初始化系统检查点"""
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info(f"数据库表结构校验/初始化完成 (当前引擎: {self.active_db})")
            self._init_default_checkpoints()
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise

    def _init_default_checkpoints(self):
        """初始化检查点状态"""
        with self.get_session() as session:
            from db.models import ETLCheckpoint
            defaults = [
                ("GLOBAL_STATUS", "ACTIVE", "调度器运行状态: ACTIVE / STANDBY_AUTH / BACKFILLING"),
                ("LAST_SUCCESS_HOUR", "", "最后一次拉取并计算成功的完整小时时段"),
                ("COOKIE_STATUS", "VALID", "NQI Cookie会话状态: VALID / EXPIRED / UNKNOWN"),
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
