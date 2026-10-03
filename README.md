# 节假日监控工单监控（阉割版）

本系统为「节假日监控工单监控」的精简版本，仅保留以下能力：

1. **登录大数据平台（NQI / CAS 单点登录）**：图形验证码 + 短信验证码分阶段登录、Cookie 持久化与会话心跳续期。
2. **集团工单查询**：采集集团方案库中的「集团假日保障」类工单，支持地市/时间区间/关键词筛选、人工标记已处理、Excel 导出。
3. **省内优化工单查询**：采集省内优化流程（`proc_gtssxn` / `proc_jzfx_ssyhlc`）工单，支持地市/时间区间/IDS 标记筛选、人工标记已处理、Excel 导出。

数据库固定使用 **SQLite**（本地 WAL 模式，免安装开箱即用）。

> 原「性能超频监控」相关能力（KPI 小时级采集、4G/5G 质差规则判定、日/周超频看板、任务补采、数据管理等）已在本版本中移除。

---

## 快速上手

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `config/config.yaml`：

- `nqi.base_url / account / password / city`：大数据平台地址与账号。
- `workorder.*`：工单自动采集开关、间隔、回溯天数、集团标签、省内流程 Key 等。
- `database.sqlite.db_path`：SQLite 数据库文件路径（默认 `data/monitor.db`）。

### 3. 启动

```bash
python3 main.py
```

启动后访问：`http://127.0.0.1:9000`

平台登录默认账号：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| admin | admin123 | 管理员 |
| user | user123 | 普通用户 |

首次登录后请在页头「登录续期」中完成大数据平台认证，以激活工单自动采集。

---

## 目录结构

```text
节假日监控工单监控/
├── config/                  # 基础配置
├── core/
│   ├── nqi/                 # 内化封装的大数据平台鉴权（登录/验证码/ RSA 加密）
│   ├── nqi_client.py        # 大数据平台客户端（仅登录与会话管理）
│   ├── cache.py             # Redis 缓存（不可达自动降级直查）
│   └── scheduler.py         # 定时调度（Cookie 心跳 + 工单自动采集）
├── db/                      # 数据持久层（SQLAlchemy + 仅 SQLite）
├── services/
│   ├── auth_service.py      # 会话状态与登录续期
│   ├── workorder_collector.py  # 集团/省内工单采集
│   ├── workorder_service.py    # 工单查询与汇总统计
│   ├── user_service.py      # 平台账号登录与令牌
│   └── export_service.py    # 工单 Excel 导出
├── web/
│   ├── app.py               # FastAPI 入口与 API 路由
│   └── static/              # Vue3 + Element Plus 离线前端
├── main.py                  # 启动入口
└── requirements.txt
```
