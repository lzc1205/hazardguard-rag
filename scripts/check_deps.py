"""依赖检测脚本（供 start_local.bat 调用）。

为什么不用 `python -c "import fastapi"` 内联：
批处理里的引号在 Git Bash / PowerShell / cmd 之间层层传递时会被吃掉，
表现为 SyntaxError: unterminated string literal。
写成独立文件最稳妥，且能输出清晰的中文错误原因。

用法：
    python scripts/check_deps.py
退出码：0=依赖齐全，1=有缺失
"""

import importlib
import sys

# (模块名, pip 包名, 是否必需)
REQUIRED = [
    ("fastapi", "fastapi", True),
    ("uvicorn", "uvicorn", True),
    ("sqlalchemy", "sqlalchemy", True),
    ("pydantic", "pydantic", True),
    ("pydantic_settings", "pydantic-settings", True),
    ("jose", "python-jose", True),
    ("passlib", "passlib", True),
    ("loguru", "loguru", True),
    ("alembic", "alembic", True),
    ("redis", "redis", True),
    ("numpy", "numpy", True),
    ("httpx", "httpx", False),
    ("pypdf", "pypdf", False),
    ("mcp", "mcp", False),
    ("langgraph", "langgraph", False),
]


def main() -> int:
    missing_required = []
    missing_optional = []

    for module, pkg, required in REQUIRED:
        try:
            importlib.import_module(module)
        except ImportError:
            if required:
                missing_required.append(pkg)
            else:
                missing_optional.append(pkg)

    if missing_required:
        print("MISSING_REQUIRED:" + ",".join(missing_required), file=sys.stderr)
        return 1

    if missing_optional:
        # 缺可选依赖不算失败，只提示
        print("MISSING_OPTIONAL:" + ",".join(missing_optional), file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())