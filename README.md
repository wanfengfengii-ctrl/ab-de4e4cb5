# 异步设备联锁板 · 门延迟冒险审计台

面向**异步联锁板**输入切换的门延迟冒险（hazard）全栈审计工具：工程师在浏览器中
编辑初始/目标输入、无环逻辑门网表、各门的**整数延迟区间**与监测输出，经由真实
FastAPI 发起审计，后端**穷举**每一次调度可选的延迟取值与事件交错，判定所有可能
执行中的监测输出是否**至多完成一次必要翻转**，并在页面回放规范执行时间线。

## 语义保证（非近似）

- 外部输入在 **t = 0 时刻成批切换**，之后外部不再干预。
- 每个门拥有整数延迟区间 `[delay_min, delay_max]`；每当门输出需要变化时，区间内
  **每一个整数延迟**都作为独立调度分支被探索（笛卡尔积展开全部交错）。
- **惯性延迟**：尚未到期的事件在后续求值中若被推翻则被**取消**；若新值与当前值
  不同则以新事件**替换**，短于门延迟的毛刺被惯性吸收。
- **同一时刻的事件成批生效后再求值**：同刻事件先整体写入，再统一组合求值；
  选择延迟 0 的连锁反应在同一时刻以额外微批（micro-batch）完成（网表无环，
  微批轮次必然终止）。
- 状态 = 门电平向量 + 各门将到期事件 + 监测翻转计数，按完整状态**去重**，因此
  遍历是对全部可行延迟选择/事件交错的**完整有限探索**——无抽样、无固定延迟、
  无连续时间近似。
- 判定：监测输出在任一执行中翻转次数超过“必要翻转”（初始→目标是否要求该点
  变化）即冒险；静止时终值与目标向量所蕴含的电平不一致同样判冒险。
- 发现冒险时给出**最早违规时刻**，并按（门编号、所选延迟序列）字典序选出
  **规范见证**；安全时给出逐时刻可达状态数与规范执行时间线。
- 输入无效（电平越界、悬空连线、延迟区间反转、缺字段等）或**网表成环**时，
  返回 `valid=false` 与**定位到字段/环路径**的原因，前端清除旧结果。

> 若可达状态数超过引擎上限（默认 500,000），后端拒绝给出“安全”结论并返回错误，
> 以避免穷举被截断而产生假阴性。

## 目录结构

```
backend/            FastAPI + 穷举调度引擎
  app/main.py         路由、响应装配、静态前端托管
  app/engine.py       穷举调度（惯性延迟 / 同刻成批 / 零延迟微批 / 去重 / 见证）
  app/validator.py    输入校验与成环路径定位
  app/models.py       响应模型
  acceptance.py       一次性验收脚本（真实 HTTP，退出码报告）
frontend/           React + Vite 审计台
Dockerfile          多阶段构建（runtime / verify）
docker-compose.yml  web（健康检查、可配置宿主端口）+ verify（一次性服务）
```

## 一键运行

```bash
docker compose up --build
# 浏览器打开 http://localhost:8080
```

自定义宿主机端口（容器内固定 8000）：

```bash
HOST_PORT=9000 docker compose up --build
# 或复制 .env.example 为 .env 后修改 HOST_PORT
```

## 验收（一次性 verify 服务）

`verify` 服务等待 `web` 通过健康检查后，通过**真实 HTTP**执行验收集（健康检查、
穷举安全审计、同刻成批、惯性取消、冒险最早时刻与规范见证、无效输入定位、成环
定位、非法 JSON 兜底），打印报告并**自行退出**，以退出码报告结果：

```bash
docker compose build
docker compose run --rm verify
echo "exit code: $?"     # 0 = 全部通过；非零 = 失败项数量
```

也可在已运行的栈上执行：

```bash
docker compose up -d web
docker compose up verify   # verify 退出码即验收结论
```

## 本地开发

```bash
# 后端
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（dev server 代理 /api 与 /health 到 8000）
cd frontend
npm install
npm run dev
```

## 审计 JSON 形态

```json
{
  "inputs": ["a"],
  "initial": { "a": 0 },
  "target":  { "a": 1 },
  "gates": {
    "g1": { "type": "NOT", "inputs": ["a"],
            "delay_min": 1, "delay_max": 2 },
    "g2": { "type": "AND", "inputs": ["a", "g1"],
            "delay_min": 1, "delay_max": 1 }
  },
  "monitors": ["g2"]
}
```

支持门类型：`AND OR NOT BUF NAND NOR XOR XNOR`（`NOT/BUF` 单输入）。
