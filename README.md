# 埠灯公服·抄表回传归并

把现场拆成多条回传的抄表任务，按「任务编号 + 计划日」归并为可复核结果：已核验记录优先且结论稳定，迟到或跨日记录只在同一计划日与时间窗内补齐，时间倒退或关键字段缺失的记录隔离并说明原因。面向演示数据和本地复现，场景、规则与业务边界见 [BUSINESS.md](BUSINESS.md)。

## 业务模块

- 回传批次
- 录入回传
- 归并执行
- 归并结果

## 本地运行

```sh
cp -n .env.example .env
docker compose up -d --build --wait
```

- 工作台 `http://127.0.0.1:18294`
- FastAPI 文档 `http://127.0.0.1:19294/docs`
- PostgreSQL 16 端口 `127.0.0.1:17794`，账号 `app`，演示库 `workflow`
- `docker compose exec -T backend python -m scripts.seed_demo` 手动重跑演示数据
- `docker compose logs -f backend worker` 查看运行情况
- `docker compose down` 停容器并保留演示卷

### 演示账号

统一密码 `Demo-Walkthrough-2026`。

| 账号 | 角色 | 能执行的操作 |
|---|---|---|
| `operator` | 经办员 | 读取批次、录入现场回传、提交归并、导出结果 |
| `reviewer` | 复核员 | 读取、导出和签署结果 |
| `viewer` | 观察员 | 读取、导出已有结果 |

`SEED_DEMO=true` 启动时生成账号与一个业务样例。工作台新建批次会预填样例。可编辑表单中的数值、文本和结构化 JSON 后提交。

相同输入指纹再次提交不会新建批次；同一批次重复提交执行会复用排队中/执行中的执行或最近一次成功结果，失败只新增一条失败执行，不覆盖旧版结果。角色枚举位于后端 `src/constants/workflow.py` 与前端 `src/constants/workflow.js`。执行状态枚举也位于这两个文件，使用于队列、列表和执行详情。证据校验码是报告 JSON 按 UTF-8、键排序且省略多余空格序列化后的 SHA-256。

## 工程结构

| 服务 | 实现 |
|---|---|
| frontend | Nginx 与原生 HTML、CSS、JavaScript |
| backend | Python 3.11、FastAPI、SQLAlchemy |
| worker | Python 持久队列执行与 Redis 唤醒信号 |
| db | PostgreSQL 16，持久卷保存批次、执行、证据及审计 |
| redis | Redis 7，追加日志卷保存队列唤醒信号 |

编辑 `backend/src/services/domain.py` 可修改归并规则与业务表单说明；该模块只依赖标准库，可直接测试。API 路由、数据定义和页面位于 `backend/src/routes/`、`backend/src/models/` 与 `frontend/src/pages/`。开发时更新源文件后重新构建对应服务。旧库升级时 `src/models/tables.py` 的 `upgrade` 会补齐批次输入指纹列与唯一索引。

`.env.example` 提供 `FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`DB_PASSWORD`、`AUTH_KEY`、`SEED_DEMO` 与 `NETWORK_SUBNET`。修改 `NETWORK_SUBNET` 可避开本机现有容器网段。所有发布的端口绑定本机回环地址。接入独立部署前需配置自己的凭据并按业务范围做独立验收。

[MIT License](LICENSE)
