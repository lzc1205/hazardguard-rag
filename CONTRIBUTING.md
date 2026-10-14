# 贡献指南

感谢你对 HazardGuard 的关注！欢迎通过以下方式参与贡献。

## 行为准则

本项目采用[贡献者契约行为准则](https://www.contributor-covenant.org/zh-cn/version/2/1/code_of_conduct/)。参与即表示你同意遵守其条款。

## 如何贡献

### 报告 Bug

1. 在 [Issues](https://github.com/lzc1205/hazardguard-rag/issues) 中搜索是否已有相同问题
2. 如果没有，新建 Issue，包含以下信息：
   - 操作系统和 Python 版本
   - 重现步骤
   - 期望行为 vs 实际行为
   - 相关日志（如有）

### 提交功能请求

1. 先搜索是否已有类似请求
2. 新建 Issue，描述：
   - 你希望解决什么问题
   - 建议的解决方案
   - 替代方案（如有）

### 提交代码

1. **Fork** 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature-name`
3. 编写代码，遵循项目规范：
   - Python 代码使用 ruff 格式化（`ruff check .`）
   - 行宽 120 字符
   - 新增功能需包含测试
4. 运行测试确保通过：`cd backend && pytest tests/`
5. 提交前运行密钥安全检查：`pytest tests/test_api/test_secret_safety.py`
6. 提交代码：`git commit -m "feat: 描述你的改动"`
7. 推送到你的 Fork：`git push origin feature/your-feature-name`
8. 创建 Pull Request

### Commit 规范

使用[约定式提交](https://www.conventionalcommits.org/zh-hans/)格式：

- `feat:` 新功能
- `fix:` 修复 Bug
- `docs:` 文档变更
- `refactor:` 代码重构
- `test:` 测试相关
- `chore:` 构建/工具链变更

## 开发环境搭建

```bash
# 克隆仓库
git clone https://github.com/lzc1205/hazardguard-rag.git
cd hazardguard-rag

# 配置环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入你的 LLM_API_KEY

# 启动服务（需要 Docker Desktop）
docker compose up -d --build

# 运行测试
cd backend && pytest tests/
```

## 项目结构

```
backend/
├── app/
│   ├── agents/        # 多智能体 Agent
│   ├── api/           # FastAPI 路由与依赖注入
│   ├── models/        # SQLAlchemy 数据模型
│   ├── rag/           # 检索增强生成
│   ├── schemas/       # Pydantic 请求/响应模型
│   ├── services/      # 业务逻辑层
│   ├── tasks/         # Celery 异步任务
│   └── utils/         # 工具函数
├── alembic/           # 数据库迁移
├── eval/              # 评测框架与数据集
├── mcp/               # MCP 工具服务
├── scripts/           # 运维脚本
└── tests/             # 测试套件
```

## 审查流程

1. 至少一位维护者审查通过后合并
2. CI 必须全部通过（lint + test + secret-safety）
3. 破坏性变更需在 PR 描述中明确标注

## 联系方式

如有问题，请通过 [GitHub Issues](https://github.com/lzc1205/hazardguard-rag/issues) 联系。