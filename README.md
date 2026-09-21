# 异步设备联锁审计台

面向「输入零时刻成批切换」的无环整数延迟门网表，穷举所有门延迟选择与
事件交错，精确判定每个监测输出在**所有执行**中是否至多完成一次必要翻转。
不使用抽样、固定延迟或连续时间近似。

## 调度语义

- 所有主输入在 **t = 0** 同时从 `initial` 切换到 `target`。
- 每个门有**惯性整数延迟**闭区间 `[min, max]`。某网络变化后，先等同刻
  事件全部生效，再对受影响门**各求值一次**：
  - 输出不变：取消该门尚未到期的挂起事件；
  - 输出变化：为区间内**每个整数延迟**分别替换挂起事件（每个延迟是一条
    独立执行）。
- 同一时刻的事件成批生效后再求值；零延迟后续事件在同一时刻形成新的微批，
  按拓扑深度依次排空，时间不前进。
- 相同的未来构型（门输出 + 挂起事件 + 监测翻转计数）合并，时间线中的
  「可达状态数」是去重后的精确构型数。
- 监测输出的**必要翻转数** = 其初始稳态值与最终稳态值是否不同（0 或 1）。
  任一执行中超过该数即冒险，返回**最早违规时刻**，并给出按
  `(时刻, 门编号, 本批门事件选用延迟)` 字典序决胜的规范见证时间线。

## 快速开始（Docker Compose）

```bash
# 默认宿主机端口 8000
docker compose up --build

# 自定义宿主机端口
HOST_PORT=9000 docker compose up --build
# 或复制 .env.example 为 .env 后修改
```

打开 http://localhost:8000 ，编辑输入、网表、延迟区间与监测点，点击
「发起审计」。请求经真实 HTTP API `/api/audit` 提交，结果面板展示：

- 安全：逐时刻可达状态数条形图 + 规范执行回放；
- 冒险：最早违规时刻 / 违规门 / 监测点 + 可步进回放的见证时间线；
- 输入无效或网表成环：旧结果被清除，错误横幅显示错误码与定位（如
  `g1.inputs[0]`、环路径 `g1 -> g2 -> g1`）。

健康检查：`GET /health`（容器与 compose 均配置了 healthcheck）。

## 验收服务

Compose 内含一次性服务 `verify`：等待 `api` 健康后，通过真实 HTTP API
执行全部端到端验收（冒险检出、延迟区间穷举、精确构型计数、成环/未知网络
拒绝、错误信封、零延迟微批等），随后自行退出，并以退出码报告结果：

```bash
docker compose run --rm --build verify   # 退出码 0 = 全部通过
# 或让整套编排以验收结果为准：
docker compose up --build --abort-on-container-exit --exit-code-from verify
```

退出码：`0` 全部通过；`1` 有用例失败；`2` 服务不可达。

## 本地开发

后端：

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest            # 引擎与 API 单元测试
```

前端（Vite 开发服务器，代理到后端）：

```bash
cd frontend
npm install
npm run dev       # http://localhost:5173
```

## 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST_PORT` | `8000` | 宿主机映射端口 |
| `AUDIT_STATE_CAP` | `2000000` | 单层构型上限，超出返回 400 `state_limit` |
| `API_BASE` | `http://api:8000` | verify 服务访问的 API 基址 |
| `ACCEPTANCE_TIMEOUT` | `60` | verify 等待健康的秒数 |

## 目录

```
backend/app/        FastAPI 应用、Pydantic 模型、穷举引擎
backend/tests/      pytest 单元/API 测试
backend/acceptance/ 端到端验收脚本（verify 服务入口）
frontend/src/       React 控制台（编辑器 / 时间线 / 回放）
Dockerfile          多阶段构建：Vite 构建产物由 FastAPI 托管
docker-compose.yml  api（健康检查）+ 一次性 verify
```
