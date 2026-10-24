# HazardGuard

[![CI](https://github.com/lzc1205/hazardguard-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/lzc1205/hazardguard-rag/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-✓-2496ED.svg)](https://www.docker.com/)

> 危大工程专项方案合规审查系统 —— 基于 RAG 的多智能体 Agent
>
> 上传专项施工方案，系统自动完成「方案识别 → 多维度审查 → 出具可溯源审查报告」全流程，
> 每个结论强制绑定规范编号 + 条款原文，无可靠依据自动拒答。

---

## 30 秒看懂这个项目

普通 RAG 拿到 top-k 就开始生成答案。但在工程合规领域，**一次幻觉的代价是真实的安全事故责任** —— 所以这个项目真正在解决的问题不是「能不能答」，而是「凭什么敢答」。

| 维度 | 做法 |
|---|---|
| **证据闸门** | 检索与生成之间加一道校验：得分过低 / 证据不足 / 引用无法定位原文 → 一律拒答或转人工；**绝不因"查不到"就判违规** |
| **混合检索** | BM25 + 向量双路召回 + RRF 融合 + 确定性规则重排。纯向量检索会把「规范编号」类 token 抹平导致召回失效 —— 这正是现场人员直接报条文号的真实场景 |
| **多智能体** | 6 个 Agent 串成「拆解 → 初审 → 复核 → 报告」流水线，并行子任务扇出后汇聚；初审 / 复核双 Agent 结论一致才输出，冲突自动升级人工 |
| **可溯源** | 每条结论绑定规范编号 + 条款原文 + 页码，配版本元数据与废止条款黑名单，拦截引用旧版规范 |
| **可量化** | 27 条回归评测集驱动迭代，Recall@5 从 37.5% 迭代到 91.67%，每次改动都有据可查 |

**关键数字**：15 张表 · 55 个 API · 27 条回归评测集 · 6 个 Agent · 8 个 Prompt 模板 · 6 个 MCP 工具 · 16 个前端页面 · PostgreSQL 16 + PGVector + Celery + Docker Compose

**端到端实测**（6.5m 深基坑专项方案）：

```
识别: 危大=True  超规模=True  类别=['foundation_pit']
评分=77  结论=pass_with_modification
审查意见 9 条（7 条有法规依据 / 2 条拒答）+ 专家论证 8 条
Trace 6 环节 | token 7781 | 成本 ¥0.0156
```

---

## 目录

- [项目截图](#项目截图)
- [核心设计原则](#核心设计原则)
- [快速开始](#快速开始)
- [当前进度](#当前进度)
- [评测指标](#评测指标)
- [检索指标优化过程](#检索指标优化过程有据可查可用于面试)
- [踩坑记录](#踩坑记录面试可讲)
- [检索层实现说明](#检索层实现说明)
- [数据模型](#数据模型15-张表)
- [项目结构](#项目结构)
- [常见问题排查](#常见问题排查)
- [Docker 在本项目中的作用](#docker-在本项目中的作用)
- [环境要求](#环境要求)
- [测试](#测试)

---

## 项目截图

> 截图占位 —— 把图片放到 `docs/images/` 下，文件名按下方标注替换即可。
> 这个项目最值得截图的是**审查报告页**（条款高亮 + 风险分级）与 **Trace 页**（6 个 Agent 的耗时 / Token 成本），直接体现「可溯源」和「可观测」。

| 审查报告（条款高亮 + 风险分级） | 编排 Trace（6 Agent 耗时 / Token） |
|---|---|
| ![审查报告](docs/images/report.png) | ![Trace](docs/images/trace.png) |
| 审查任务与进度 | 知识库与 Prompt 管理 |
| ![审查任务](docs/images/review-task.png) | ![知识库](docs/images/knowledge.png) |

---

## ⚠️ 部署前必读：默认配置不可用于生产

本项目为了开箱即用提供了若干默认值，**公网部署前必须全部替换**：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| 管理员账号 | `admin` / `admin123` | **必须修改**，否则任何人可登录并删除数据 |
| `JWT_SECRET_KEY` | `change-me-in-production` | **必须改为随机串**，否则可伪造登录令牌 |
| `LLM_API_KEY` | 空（读 `.env`） | **绝不要提交真实密钥**，仓库只提供 `.env.example` |

```bash
# 首次运行：复制环境变量模板
cp backend/.env.example backend/.env
# 然后务必修改 backend/.env 里的上述三项
```

> 仓库的 `.gitignore` 已排除 `backend/.env`，且测试套件包含密钥泄露检测
> （`tests/test_api/test_secret_safety.py`）——**如果你把自己的 `.env` 提交上来，
> CI 会直接失败**。密钥一旦进入 git 历史，事后删除文件也无法清除。

---

## 📌 项目文档

**[`docs/需求文档.md`](docs/需求文档.md)（2890 行，v2.0）** —— 完整的功能规格、数据模型、
接口契约与算法说明。

v2.0 依据 **100 份真实 JD 样本**（校招 64 / 社招 31 / 实习 5，本科可投 67%）改版，
补齐了四項高频工程能力（评测 34% / 上下文工程 21% / MCP 20% / 成本可观测 10%+11%）。

| 我要做... | 看第几章 |
|---|---|
| 加数据表 | 4.2 |
| 加接口 | 4.3 |
| 改检索逻辑 | 5 |
| 改 Agent | 6 |
| 改前端 | 7 |
| 改危大判定阈值 | 8.1 |
| **建评测集** | **9** |
| **做上下文工程** | **10** |
| **写 MCP 工具** | **11** |
| **做成本/Trace** | **12** |
| 交付前验收 | 15.3 |

---

## 核心设计原则

**不编造，是合规审查系统的底线，不是加分项。**

普通 RAG 拿到top-k 就开始生成答案。但在工程合规领域，一次幻觉的代价是真实的
安全事故责任。所以本项目在检索与生成之间加了一道**证据闸门**（`app/rag/evidence.py`）：

| 校验项 | 不通过时的行为 |
|---|---|
| 检索得分过低 | 拒答，提示规范可能未收录 |
| 证据数量不足 | 拒答，不给判定 |
| 引用无法定位原文 | 拦截判定，标记人工复核 |
| 拒答态的风险映射 | 归为 `compliant`，**绝不因"查不到"就判违规** |

---

## 快速开始

### 方式一：Docker 一键部署（推荐给「拿到代码就跑」的人）

只需要 Docker Desktop，不需要装 Python 和 Node。

```bash
# 1. 配置（填入自己的大模型 API Key）
cp .env.example .env

# 2. 启动（首次约 3~5 分钟构建镜像）
docker compose up -d --build

# 3. 访问
#    http://localhost:3000     前端
#    http://localhost:8000/docs 接口文档（Swagger UI）
```

登录账号：`admin / admin123`（在 `.env` 里改）。

启动的 5 个容器：

| 服务 | 端口 | 作用 |
|---|---|---|
| `postgres` | 5432 | PostgreSQL 16 + pgvector（向量库） |
| `redis` | 6379 | 缓存 + Celery 队列 |
| `backend` | 8000 | FastAPI 服务 |
| `worker` | — | Celery Worker，执行异步审查任务 |
| `frontend` | 3000 | nginx 托管的前端静态资源 |

**数据库初始化是自动的**：`backend` 容器的启动命令会依次执行
`alembic upgrade head` → `scripts/init_db.py`（建表 + 创建管理员 + 导入
23 条法规种子 + 构建向量索引）→ 启动 uvicorn。

也就是说**不需要手动创建用户或初始化数据**，直接登录即可。

常用命令：

```bash
docker compose ps                      # 查看状态
docker compose logs -f backend         # 看后端日志
docker compose restart backend         # 重启后端
docker compose down                    # 停止（数据保留在 volume）
docker compose down -v                 # 连数据一起删除（慎用）
```

### 方式二：本地开发（能看实时日志）

需要本机有 Python 3.11+ 和 Node 18+。

**A. 双击脚本**
```
D:\123456789\PythonProject6\start_local.bat
```

**B. PyCharm 终端（推荐，可看到实时日志）**

```bash
cd D:\123456789\PythonProject6
python scripts\start.py --restart
```

`--restart` = **先停掉旧进程，再按固定端口启动**。
这样反复重启，端口永远稳定在 8000 / 5173 ——浏览器书签、
代理配置、之前记下的地址全部不会失效。

**参数**：

| 命令 | 用途 |
|---|---|
| `--restart` | 停止旧进程后重启（**日常最常用**）|
| `--stop` | 停止全部服务（数据库容器保留，数据不丢）|
| `--check` | 只做环境自检，不启动 |
| `--backend` | 只起后端 + Worker |
| `--frontend` | 只起前端 |
| `--port 8001` | 指定后端端口 |
| `--no-worker` | 不启动 Celery Worker |

脚本会自动完成：检查虚拟环境 → 检查依赖 → 停旧进程 →
启 Docker → 数据库迁移 → 启动后端 + Worker（等健康检查通过）→ 启动前端。

**手动分步启动**（想逐个排查时）：

```bash
# 1. 只起数据库
docker compose up -d postgres redis

# 2. 初始化数据（建表 + 管理员 + 法规种子 + 向量索引，幂等可重复跑）
cd backend
../.venv/Scripts/python.exe scripts/init_db.py

# 3. 起后端 + Worker
cd ..
python scripts\start.py --restart
```

> `init_db.py` 是幂等的，重复执行安全。已存在的数据会跳过，
> 不会覆盖你已有的密码。

**为什么不用固定 sleep 等启动**
改成轮询 `GET /health` 直到返回 200。固定睡 3 秒在慢机器上不够，
会出现「显示启动成功但接口 502」；睡太久又白等。

**进程识别不会误杀其他项目**
停止时用双重条件：命令行特征（`uvicorn app.main` / `celery` / `vite`）
**且**路径指向本项目。实测在 `/tmp/otherproj` 起的同名进程不会被计入。

启动后访问 **http://127.0.0.1:5173**，账号 `admin` / `admin123`。

### 异步审查（Celery Worker）

审查支持两种模式：

| 模式 | 请求参数 | 适用 |
|---|---|---|
| 同步（默认） | `async_mode=false` | 几百字的小方案，1.5 秒返回完整结果 |
| 异步 | `async_mode=true` | 几百页大文档，立即返回 task_id |

**异步模式必须启动 Worker**，否则任务会一直挂在 Redis 队列里没人处理：

```bash
cd D:\123456789\PythonProject6\backend
../.venv/Scripts/python.exe -m celery -A app.celery_app.celery_app worker ^
  --loglevel=info --pool=solo --concurrency=1 -Q celery
```

启动后应能在日志里看到 `[tasks]` 列表包含两个任务——
**如果这个列表是空的，说明任务没注册，异步链路不会工作**。

异步任务的进度存放在 `async_tasks` 表，前端轮询接口获取：
```
GET /api/v1/reviews/tasks/{task_id}
```
返回 `status` / `progress` / `current_stage`，
`current_stage` 是中文环节名（如「正在执行合规审查」），可直接显示。

---

## 常见问题排查

### 登录一直失败 / 提交后无响应

**症状**：点 Login 后按钮卡住或控制台报 502，页面无跳转。

**原因**：前端代理（`vite.config.ts` 的 `target`）指向了错误的端口，
请求打到了自己或一个已停掉的服务上。

**排查步骤**：
```bash
# 1. 确认后端活着没
curl http://127.0.0.1:8000/health

# 2. 确认代理指向哪里（应与后端实际端口一致）
grep target frontend/vite.config.ts

# 3. 直接绕过前端测登录
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

若第 2 步的端口与第 1 步不一致，改 `vite.config.ts` 后**必须重启前端**
（Vite 不会热重载 proxy 配置）。

> 已用 `python scripts\start.py` 启动时，代理端口会自动同步，无需手动改。

### 端口被占用

`netstat -ano | findstr :8000` 查 PID，`taskkill /F /PID <PID>` 释放。
`scripts/start.py` 会自动顺延到 8001 / 5174。

### Vite 启动报缺少 @rollup 或 @esbuild 的平台包

Windows 上 npm 的 optional dependencies 已知 bug。
本项目已在 `package.json` 的 `devDependencies` 中显式声明：
```
@rollup/rollup-win32-x64-msvc
@esbuild/win32-x64
```
若仍报错：
```bash
cd frontend
npm install @rollup/rollup-win32-x64-msvc @esbuild/win32-x64 --save-dev --registry=https://registry.npmmirror.com
```

### pip install 卡住不动

版本冲突导致 pip 反复解析依赖。务必加镜像：
```bash
.venv/Scripts/python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend/requirements.txt
```
`requirements.txt` 的版本约束需与 `.venv` 实际安装的一致。

### 数据库连不上

```bash
docker compose ps                    # 确认容器 healthy
docker compose restart postgres     # 重启数据库
docker logs hazardguard-postgres --tail 30
```

---

## Docker 在本项目中的作用

Docker 有**两种用法**，取决于你要干什么：

### 用法一：只把数据库放进 Docker（本地开发，当前在用）

**只负责运行两个基础设施服务，业务代码在宿主机上跑**：

| 服务 | 镜像 | 用途 | 端口 |
|---|---|---|---|
| PostgreSQL | `pgvector/pgvector:pg16` | 16 张业务表 + 向量存储 | 5432 |
| Redis | `redis:7-alpine` | Celery 队列 + 缓存 | 6379 |

```
├── Docker    → PostgreSQL + Redis
├── PyCharm   → 后端 FastAPI（--reload 热重载）
└── 终端      → 前端 Vite（热更新）
```

**为什么数据库用 Docker 而不是本地安装**：
- PostgreSQL 要装服务、配密码、注册开机启动，还得手动装 pgvector 扩展
- Docker 一条命令搞定，镜像自带 pgvector
- 数据存于 volume，删容器不丢数据；换机器 `docker compose up` 即可复现

### 用法二：全部容器化（给别人部署用）

```bash
cp .env.example .env      # 填 API Key
docker compose up -d --build
```

5 个容器全跑：`postgres` + `redis` + `backend` + `worker` + `frontend`。
对方机器**只需要装 Docker**，不需要 Python / Node。

这种方式面向「拿到代码就想跑起来」的场景，
也是本项目对外分发的主路径（详见「快速开始」）。

> 两种模式数据是同一份（同一个 volume），可以随时切换。

### Docker 常用命令

```bash
docker compose up -d postgres redis    # 启动数据库
docker compose ps                      # 查看容器状态
docker compose stop                    # 停止（保留数据）
docker compose down                    # 停止并删除容器
docker exec -it hazardguard-postgres psql -U postgres -d hazardguard   # 进数据库
```

> Docker Desktop 重启电脑后需手动启动，容器会自动恢复（已配 `restart: unless-stopped`）。

---

## 环境要求

- Python 3.11+（当前 3.13.5）
- Node.js 20+（当前 22.22）
- Docker Desktop
- 磁盘：D 盘建议留 30GB 以上（当前剩 75GB）

### 依赖安装（手动时）

```bash
# 后端 —— 必须加镜像，否则官方源会卡死
.venv/Scripts/python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend/requirements.txt

# 前端 —— 必须加镜像
cd frontend && npm install --registry=https://registry.npmmirror.com
```

> **注意**：`requirements.txt` 的版本约束必须与 `.venv` 实际安装的一致，
> 否则 pip 会反复解析依赖（实测卡住数分钟）。

---

## PyCharm 运行配置

`.idea/runConfigurations/HazardGuard.xml` 已配置好，重启 PyCharm 后在右上角下拉可见：

| 配置名 | 用途 |
|---|---|
| 后端 API | 启动 uvicorn（8000 端口，带 `--reload`） |
| 后端 API (8040) | 同上，8040 端口 |
| 前端 Vite | 启动前端 dev server |
| 评测 | 跑检索质量评测 |
| 全部测试 | pytest 跑全部测试 |

日常开发建议：PyCharm 点「后端 API」，终端跑 `npm run dev`。

---

## 项目结构

```
PythonProject6/
├── docs/
│   └── 需求文档.md            # ★ 开发唯一基准
├── backend/
│   ├── app/
│   │   ├── main.py                     # FastAPI 入口
│   │   ├── config.py                # pydantic-settings 配置
│   │   ├── database.py                 # 异步引擎 + 会话
│   │   ├── redis_client.py
│   │   ├── celery_app.py
│   │   ├── models/                     # ★ 15 张表
│   │   │   ├── base.py                 #   UUID 主键基类
│   │   │   ├── enums.py                #   枚举与常量
│   │   │   ├── user.py
│   │   │   ├── project.py# projects/documents/document_chunks
│   │   │   ├── review.py               # reviews/review_items/expert_points/reports
│   │   │   ├── knowledge.py            # knowledge_entries/chunks/build_tasks
│   │   │   └── chat.py                 # chat_sessions/messages/async_tasks/prompt_versions
│   │   ├── schemas/                    # Pydantic 出入参
│   │   ├── api/
│   │   │   ├── deps.py                 # 鉴权依赖、角色校验
│   │   │   └── v1/                     # auth / users 路由
│   │   ├── services/                   # 业务逻辑层
│   │   ├── rag/                        # RAG 引擎
│   │   │   ├── parser.py               #   文档结构化解析（双栏 PDF/表格）
│   │   │   ├── chunker.py              #   条款级分块
│   │   │   ├── retriever.py            #   BM25 + 向量混合检索 + RRF
│   │   │   └── evidence.py             #   证据闸门与拒答
│   │   ├── agents/                     # 多智能体（待实现）
│   │   │   └── prompts/
│   │   └── utils/                      # 日志、异常、安全
│   ├── alembic/versions/               # 迁移
│   ├── tests/
│   │   └── test_services/test_rag.py   # 17 项测试
│   └── requirements.txt
├── frontend/                           # Vue3 + TypeScript + Element Plus
├── docker-compose.yml
└── start_local.bat
```

---

## 当前进度

| 模块 | 状态 | JD 频次 |
|---|---|---|
| 数据模型 15 张表 | ✅ 已落库 PostgreSQL 16 + pgvector | — |
| Alembic 迁移 | ✅ 5 个迁移已执行 | — |
| **后端 API 49 个接口** | ✅ 全部注册并实测通过 | — |
| 鉴权 RBAC | ✅ JWT + 角色校验 | 6% |
| 条款级分块 | ✅ 双格式兼容（GB/JGJ） | — |
| **混合检索 + RRF** | ✅ BM25 + 向量 + PGVector，Recall@5 **95.83%** | RAG 63% |
| 证据校验与拒答 | ✅ 拒答准确率 100% | 差异化 |
| 文档结构化解析 | ✅ 双栏 PDF / 表格 / 降级处理 | — |
| **评测框架** | ✅ 27 条评测集 + 自动化流水线 | **34%** |
| **MCP 工具服务器** | ✅ 6 个工具，自测 22/22 | **20%** |
| **上下文工程** | ✅ Token 预算 / 压缩 / 记忆 / 隔离 | **21%** |
| **多智能体编排** | ✅ 6 Agent 端到端跑通（含 ReAct + Function Calling） | 简历核心 |
| **Prompt 工程化** | ✅ 8 模板 + 版本管理 + 硬约束可视化 | **47%** |
| **LangChain 接入** | ✅ LCEL 链 + Tool Calling | **31%** |
| **向量数据库** | ✅ PGVector 512 维 + HNSW 索引 | **21%** |
| **LLM 说理** | ✅ DeepSeek 接入，单次 ¥0.0054 | — |
| **成本与可观测** | ✅ 全链路 Trace + 精确成本核算 | 10%/11% |
| **报告导出** | ✅ Word + PDF 双格式，拒答均显式披露 | — |
| **异步任务** | ✅ Celery Worker + 步骤级进度 | 11% |
| 前端 16 个页面 | ✅ 登录/概览/项目/文档/审查/审查记录/报告/问答/法规速查/知识库管理/统计分析/编排记录/Prompt/用户/设置 | — |

### 端到端演示结果（6.5m 深基坑）

```
识别: 危大=True  超规模=True  类别=['foundation_pit']
评分=77  结论=pass_with_modification
审查意见 9 条（7 条有法规依据 / 2 条拒答）+ 专家论证 8 条
Trace 6 环节 | token 7781 | 成本 ¥0.0156
```

**拒答机制实战验证**：方案未写监测内容且知识库无对应条款时，
系统输出 `refused` 而非硬凑结论——这是本项目最核心的差异化能力。

### 评测指标

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| Recall@5 | **91.67%** | ≥95% | ⚠️ 差 1 条 |
| 命中率 Hit@5 | **95.83%** | ≥98% | ⚠️ 差 1 条 |
| 引用正确率 | **100%** | 100% | ✅ |
| 拒答准确率 | **100%** | ≥95% | ✅ |
| 误拒率 | **0%** | ≤3% | ✅ |

**已知 Bad Case**：`ud_002`（暗挖工程专家论证情形）召回未命中。

### 检索指标优化过程（有据可查，可用于面试）

| 版本 | 改动 | Recall@5 |
|---|---|---|
| v1 | 纯 BM25 | 37.5% |
| v2 | 修条款号归一化 bug | 66.67% |
| v3 | 接入向量路 | 70.83% |
| v4 | 修评测集标注 + 中文数字条款 | 87.5% |
| v5 | 领域同义词 + RRF 权重调整 | **91.67%** |

## 踩坑记录（面试可讲）

1. **LangGraph 1.2.14 节点不调度** — `add_node()` 传实例方法时，
   第二个及之后的节点函数从未被调用且不报错，流程静默停住。
   改为显式顺序编排，图定义保留但不依赖框架调度。
2. **passlib 1.7.4 + bcrypt 5.0.0 不兼容** — 连短密码都抛
   `ValueError: password cannot be longer than 72 bytes`，登录全挂。改用原生 bcrypt。
3. **VARCHAR(20) 截断** — `pass_with_modification` 22 字符超限导致审查接口 500。
4. **鉴权返回 422 而非 401** — `Header(...)` 必填声明导致缺 token 时在进入函数前就报参数错误。
5. **char n-gram 检索硬伤** — JGJ 规范用 `4.2.1` 裸编号、GB 用 `第4.2.1条`，
   正则只认一种会让整篇文档退化成单块、检索完全失效。两种必须都支持。

### 数据模型（15 张表）

| 分域 | 表 |
|---|---|
| 用户 | `users` |
| 项目文档 | `projects` `documents` `document_chunks` |
| 审查域 | `reviews` `review_items` `expert_points` `reports` |
| 知识库 | `knowledge_entries` `knowledge_chunks` `knowledge_build_tasks` |
| 会话任务 | `chat_sessions` `chat_messages` `async_tasks` `prompt_versions` |

**按 JD 要求额外增加的字段**（超出原需求文档 4.2 节）：

- `reviews.agent_trace` / `token_summary` / `cost_estimate` — 全链路 Trace 与成本
- `reviews.hazard_params` — 危大识别 Agent 提取的关键参数
- `review_items.citation_verified` — 引用是否通过 evidence 校验
- `knowledge_entries.is_mandatory` — 强制性条文标记
- `knowledge_entries.regulation_code` / `page_number` / `deprecated_date` — 溯源与时效性
- `knowledge_chunks.embedding_model` — 换模型后区分重建
- `chat_sessions.context_strategy` / `token_budget` — 上下文工程策略
- `chat_messages.refused` / `refuse_reason` — 拒答记录
- `prompt_versions` — Prompt 版本管理（JD 可观测性 11%）

---

## 检索层实现说明

### 条款级分块（`rag/chunker.py`）

工程规范有明确条文结构，不能按固定长度硬切——否则检索片段无法绑定条款号，溯源无从谈起。

**已处理的两种编号格式**（JGJ 与 GB 写法不同，只认一种会导致整篇退化成单块）：

- `第3.1.2 条`（GB 系列）
- `3.1.2 立杆间距不应大于900mm`（JGJ 系列裸编号）

### 混合检索（`rag/retriever.py`）

| 检索路 | 擅长 | 不擅长 |
|---|---|---|
| BM25 关键词 | 精确条款号、专有名词 | 口语化提问 |
| 向量语义 | 同义表达、近似描述 | 数字/编号易漂移 |

两路通过 **RRF 融合**（只看排名，消除分数量纲差异），再经 `rule_based_boost` 施加确定性规则：
提问含条款号 → 该条款直接置顶；点名规范名 → 同文档加权；强制性条文 → 加权（漏检成本高于误报）。

### 拒答机制（`rag/evidence.py`）

置信度分三档，高置信度需同时满足高分 + 多条依据。

---

## 测试

```bash
cd backend
../.venv/Scripts/python.exe -m pytest tests/ -v
```

当前 17 项测试全部通过，覆盖：分块边界、强制性条文识别、BM25 排序、RRF 融合、
规则加权、拒答触发、风险等级映射。

---

## 开发环境备注

以下是**本机开发时的环境约束**（不影响代码运行，仅供参考）：

- **解释器**：项目根目录下的 `.venv`，PyCharm 里已配置好
- **装包建议加国内镜像**：
  ```bash
  pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```
  官方源在部分网络环境下很慢。
- **磁盘占用提示**：本项目刻意避免了 torch / sentence-transformers 等
  大型依赖（向量化用字符 n-gram，零外部依赖），
  Docker 镜像 + venv + node_modules 合计约 300MB，
  比同类的 RAG 项目轻很多。

### 常见环境问题

**PyCharm 里找不到解释器 / 报找不到模块**
Settings → Project → Python Interpreter → 选 `.venv\Scripts\python.exe`。

**D 盘空间不足（Docker 数据盘）**
Docker Desktop → Settings → Resources → Disk image location 改到大容量盘。

**`wmic` 命令不可用**
部分环境会禁用 wmic，查进程信息用 `tasklist` 替代。