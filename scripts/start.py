"""跨平台启动脚本（替代 start_local.bat，推荐用这个）。

为什么不用 .bat：
1. cmd.exe 默认按 GBK 解析批处理，文件存 UTF-8 会整段错位
2. 引号在 Git Bash / PowerShell / cmd 之间传递时会被吃掉
3. `pause` / `del` 等命令在非交互环境下可能阻塞

本脚本只依赖 Python，行为可预测，且能给出清晰的中文错误提示。

用法：
    python scripts/start.py                 # 启动全部
    python scripts/start.py --restart       # 先停掉旧进程再启动（推荐）
    python scripts/start.py --stop          # 只停止，不启动
    python scripts/start.py --backend       # 只启动后端
    python scripts/start.py --frontend      # 只启动前端
    python scripts/start.py --check         # 只做环境自检

**为什么默认行为不是 --restart**：
--restart 会杀掉正在运行的服务。如果用户只是想启动补充服务，
默认就杀进程会造成意外。所以提供显式选项，让意图清晰。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from services import (  # noqa: E402
    clear_service,
    record_service,
    stop_all,
    wait_ports_free,
)

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
IS_WINDOWS = os.name == "nt"

# 依赖检测：(导入名, pip 包名, 是否必需)
REQUIRED = [
    ("fastapi", "fastapi", True),
    ("uvicorn", "uvicorn", True),
    ("sqlalchemy", "SQLAlchemy", True),
    ("pydantic", "pydantic", True),
    ("pydantic_settings", "pydantic-settings", True),
    ("jose", "python-jose", True),
    ("alembic", "alembic", True),
    ("redis", "redis", True),
    ("numpy", "numpy", True),
    ("loguru", "loguru", True),
]

OPTIONAL = [
    ("bcrypt", "bcrypt", "密码哈希必需"),
    ("pypdf", "pypdf", "PDF 解析"),
    ("pdfplumber", "pdfplumber", "版面还原"),
    ("mcp", "mcp", "MCP 工具"),
    ("langgraph", "langgraph", "多智能体"),
]


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if IS_WINDOWS else text


def ok(msg: str) -> None:
    print(color(f"  [OK] {msg}", "32"))


def fail(msg: str) -> None:
    print(color(f"  [ERROR] {msg}", "31"))


def warn(msg: str) -> None:
    print(color(f"  [WARN] {msg}", "33"))


def info(msg: str) -> None:
    print(f"       {msg}")


def step(n: int, total: int, msg: str) -> None:
    print()
    print(color(f"Step {n}/{total}  {msg}", "36"))


def is_port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pick_port(preferred: int, alternates: list[int]) -> int:
    if not is_port_busy(preferred):
        return preferred
    for p in alternates:
        if not is_port_busy(p):
            return p
    return preferred


# ---------------------------------------------------------------- 步骤


def check_python() -> bool:
    if not VENV_PYTHON.exists():
        fail("虚拟环境不存在，请先运行：python -m venv .venv")
        return False
    ok("虚拟环境就绪")
    return True


def _missing_modules(modules: list[str]) -> list[str]:
    """在 venv 解释器里导入模块，返回导入失败的模块名。

    必须显式用 VENV_PYTHON 起子进程：直接在本进程 import 会用到
    运行脚本的解释器（可能是系统 Python），导致误报依赖缺失。
    """
    if not modules:
        return []
    code = (
        "import importlib,json,sys\n"
        f"mods={modules!r}\n"
        "bad=[]\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception:\n"
        "        bad.append(m)\n"
        "print(json.dumps(bad))\n"
    )
    try:
        r = subprocess.run(
            [str(VENV_PYTHON), "-c", code],
            capture_output=True,
            timeout=120,
            text=True,
        )
    except Exception:
        return list(modules)
    if r.returncode != 0:
        return list(modules)
    try:
        return json.loads((r.stdout or "[]").strip().splitlines()[-1])
    except Exception:
        return list(modules)


def check_deps() -> bool:
    pkg_by_module = {module: pkg for module, pkg, _ in REQUIRED}
    missing_pkgs = [
        pkg_by_module[m] for m in _missing_modules([m for m, _, _ in REQUIRED])
    ]

    if missing_pkgs:
        fail(f"缺少必需依赖：{', '.join(missing_pkgs)}")
        info("安装命令：")
        info(f"{VENV_PYTHON} -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend/requirements.txt")
        return False
    ok("核心依赖齐全")

    absent = [
        f"{pkg}（{desc}）"
        for pkg, desc in [
            (pkg, desc)
            for mod, pkg, desc in OPTIONAL
            if mod in _missing_modules([mod])
        ]
    ]
    if absent:
        warn("可选依赖缺失：" + "、".join(absent))
    else:
        ok("可选依赖齐全")
    return True


def check_docker() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=15, text=True
        )
    except FileNotFoundError:
        fail("未安装 Docker")
        return False
    except subprocess.TimeoutExpired:
        fail("Docker 响应超时")
        return False

    if r.returncode != 0:
        fail("Docker 未运行")
        info("请先启动 Docker Desktop：")
        info(r"C:\Users\21710\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe")
        return False
    ok("Docker 引擎运行中")
    return True


def start_containers() -> bool:
    print("       启动 PostgreSQL + Redis ...")
    r = subprocess.run(
        ["docker", "compose", "up", "-d", "postgres", "redis"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        fail("容器启动失败")
        info(r.stderr.strip()[:300])
        return False

    # 等待数据库健康
    for i in range(30):
        c = subprocess.run(
            ["docker", "exec", "hazardguard-postgres", "pg_isready", "-U", "postgres"],
            capture_output=True,
            text=True,
        )
        if c.returncode == 0:
            ok("数据库就绪")
            break
        time.sleep(1)
    else:
        warn("数据库等待超时，继续尝试")

    # pgvector 扩展
    subprocess.run(
        [
            "docker", "exec", "hazardguard-postgres",
            "psql", "-U", "postgres", "-d", "hazardguard",
            "-c", "CREATE EXTENSION IF NOT EXISTS vector;",
        ],
        capture_output=True,
        text=True,
    )
    ok("pgvector 扩展已启用")
    return True


def run_migrations() -> bool:
    print("       执行数据库迁移 ...")
    r = subprocess.run(
        [str(VENV_PYTHON), "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode == 0:
        ok("表结构已同步")
        return True
    warn("迁移未执行（首次运行或无新迁移属正常）")
    return False


def _detach_kwargs() -> dict:
    """让子进程脱离当前进程树。

    否则父脚本退出时会把子进程一起回收，
    表现为「脚本说启动成功，但服务实际没起来」。

    注意：DETACHED_PROCESS 不能与 CREATE_NEW_CONSOLE 同用（WinError 87），
    因此改用 CREATE_NEW_PROCESS_GROUP，在新控制台运行且不随父进程退出。
    """
    if IS_WINDOWS:
        return {
            "creationflags": (
                getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        }
    return {"start_new_session": True}


def spawn_backend(port: int) -> None:
    cmd = [
        str(VENV_PYTHON), "-m", "uvicorn", "app.main:app",
        "--reload", "--port", str(port),
    ]
    proc = subprocess.Popen(cmd, cwd=ROOT / "backend", **_detach_kwargs())
    record_service("backend", proc.pid, port)
    ok(f"后端已启动（端口 {port}，PID {proc.pid}）")


def spawn_frontend(port: int, backend_port: int) -> None:
    """启动前端。

    port         —— 前端自己的端口
    backend_port —— 后端端口，用于同步 vite 代理目标

    两者不能混用：曾因把前端端口写进代理 target，
    导致前端请求打到自己身上，登录永远失败。
    """
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    if not (ROOT / "frontend" / "node_modules").exists():
        warn("首次运行，正在安装前端依赖（可能需要几分钟）...")
        subprocess.run(
            [npm, "install", "--registry=https://registry.npmmirror.com"],
            cwd=ROOT / "frontend",
            check=False,
        )

    # 同步代理目标为「后端」端口
    _sync_proxy_port(backend_port)

    cmd = [npm, "run", "dev", "--", "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=ROOT / "frontend", **_detach_kwargs())
    record_service("frontend", proc.pid, port)
    ok(f"前端已启动（端口 {port}，PID {proc.pid}，代理后端 {backend_port}）")


def _sync_proxy_port(backend_port: int) -> None:
    """把 vite 代理目标同步为实际的后端端口。"""
    cfg_path = ROOT / "frontend" / "vite.config.ts"
    if not cfg_path.exists():
        return
    try:
        cfg = cfg_path.read_text(encoding="utf-8")
        # 只替换 proxy 段里的 target，避免误改 server.port
        new = re.sub(
            r'(proxy:\s*\{[^}]*?target:\s*"http://127\.0\.0\.1:)\d+',
            rf'\g<1>{backend_port}',
            cfg,
            flags=re.S,
        )
        if new != cfg:
            cfg_path.write_text(new, encoding="utf-8")
            ok(f"已同步前端代理目标到后端端口 {backend_port}")
    except Exception as e:  # noqa: BLE001
        warn(f"同步代理端口失败：{e}")


def spawn_worker() -> None:
    """启动 Celery Worker（处理异步审查任务）。

    **Worker 必须单独起**，它不是后端的一部分：
    后端只负责投递任务，真正执行审查的是 Worker 进程。
    不起Worker 的话，`async_mode=true` 投出去的任务会一直
    挂在 Redis 队列里，前端轮询到的进度永远是 5%。
    """
    cmd = [
        str(VENV_PYTHON), "-m", "celery",
        "-A", "app.celery_app.celery_app",
        "worker", "--loglevel=info", "--pool=solo",
        "--concurrency=1", "-Q", "celery",
    ]
    # Windows 上只能用 solo 池：prefork 会因 signal 处理失败而崩溃
    proc = subprocess.Popen(cmd, cwd=ROOT / "backend", **_detach_kwargs())
    record_service("worker", proc.pid, 0)
    ok(f"Celery Worker 已启动（PID {proc.pid}）")


def wait_healthy(port: int, path: str = "/health", timeout: float = 40) -> bool:
    """等后端健康检查通过。

    **不能只靠 sleep**：固定睡 3 秒在慢机器上不够，
    用户会看到「启动成功」但接口 502；睡太久又白等。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=2
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


def do_stop() -> int:
    print()
    print(color("=" * 60, "36"))
    print(color("  停止 HazardGuard-RAG 服务", "36"))
    print(color("=" * 60, "36"))
    print()
    print("       正在查找本项目的进程 ...")
    killed = stop_all(verbose=True)
    wait_ports_free([8000, 8001, 8002, 5173, 5174, 5175])

    print()
    if killed:
        print(color(f"  已停止 {killed} 个进程", "32"))
    else:
        print(color("  没有运行中的服务", "32"))
    print()
    print("  数据库容器保持运行（数据不丢，下次启动更快）")
    print("  如需停止容器：docker compose down")
    print()
    return 0


# ---------------------------------------------------------------- 主流程


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HazardGuard-RAG 启动脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""常用场景：
  python scripts/start.py --restart      改完代码后重启（最常用）
  python scripts/start.py --stop         停止全部服务
  python scripts/start.py --check        检查环境但不启动
  python scripts/start.py --backend      只起后端 + Worker
""",
    )
    parser.add_argument("--backend", action="store_true", help="只启动后端")
    parser.add_argument("--frontend", action="store_true", help="只启动前端")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="先停掉本项目正在运行的进程再启动（推荐：端口不会被顺延）",
    )
    parser.add_argument("--stop", action="store_true", help="只停止服务，不启动")
    parser.add_argument(
        "--no-worker",
        action="store_true",
        help="不启动 Celery Worker（默认会启动，用于处理异步审查任务）",
    )
    parser.add_argument("--check", action="store_true", help="只做环境自检")
    parser.add_argument("--port", type=int, default=8000, help="后端端口")
    parser.add_argument("--frontend-port", type=int, default=5173, help="前端端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    # --stop 最短路径：不需要检查环境
    if args.stop:
        return do_stop()

    print()
    print(color("=" * 60, "36"))
    print(color("  HazardGuard-RAG  危大工程合规审查系统", "36"))
    print(color("=" * 60, "36"))

    total = 7 if not args.check else 5
    if args.restart:
        total += 1
    step(1, total, "检查 Python 环境")
    if not check_python():
        return 1

    step(2, total, "检查依赖")
    if not check_deps():
        return 1

    step(3, total, "检查 Docker")
    docker_ok = check_docker()
    if not docker_ok and not args.backend and not args.frontend:
        return 1

    # 用可变计数器而不是 offset：--restart 会在第 3 步后插一步，
    # 用 offset 偏移会让 --check（提前 return）与 --restart 组合时编号跳号
    seq = [3]

    def nxt(title: str) -> None:
        seq[0] += 1
        step(seq[0], total, title)

    # --restart：停掉旧进程，等端口释放
    if args.restart:
        nxt("停止旧进程")
        killed = stop_all(verbose=True)
        wait_ports_free(
            [args.port, args.frontend_port,
             args.port + 1, args.frontend_port + 1]
        )
        if killed:
            ok(f"已清理 {killed} 个旧进程，端口已释放")

    if args.check:
        step(4, total, "数据库容器")
        if docker_ok:
            start_containers()
        step(5, total, "完成")
        print()
        print(color("环境自检完成", "32"))
        return 0

    need_backend = args.backend or not args.frontend
    need_frontend = args.frontend or not args.backend

    backend_port = args.port
    frontend_port = args.frontend_port

    if need_backend:
        nxt("准备数据库")
        if docker_ok:
            start_containers()
            run_migrations()
        else:
            warn("Docker 未运行，数据库相关接口会报错")

        nxt("启动后端与 Worker")
        # --restart 时端口应已释放；仍被占用说明有外部进程占着
        if is_port_busy(backend_port) and not args.restart:
            warn(f"端口 {backend_port} 被占用（非本项目进程？），改用 {backend_port + 1}")
            backend_port += 1
        spawn_backend(backend_port)

        if not args.no_worker:
            spawn_worker()
        else:
            warn("未启动 Celery Worker —— async_mode=true 的审查任务不会被执行")

        # 等健康检查通过，而不是固定 sleep
        print("       等待后端就绪 ...")
        if wait_healthy(backend_port):
            ok(f"后端就绪（{backend_port}）")
        else:
            warn(f"后端健康检查超时，可访问 http://127.0.0.1:{backend_port}/docs 排查")

    if need_frontend:
        nxt("启动前端")
        if is_port_busy(frontend_port) and not args.restart:
            warn(f"端口 {frontend_port} 被占用，改用 {frontend_port + 1}")
            frontend_port += 1
        spawn_frontend(frontend_port, backend_port)
        # 等 Vite 就绪
        for _ in range(30):
            if is_port_busy(frontend_port):
                break
            time.sleep(1)
        else:
            warn("前端启动较慢，可稍后手动刷新浏览器")

    url = (
        f"http://127.0.0.1:{frontend_port}"
        if need_frontend
        else f"http://127.0.0.1:{backend_port}/docs"
    )
    print()
    print(color("=" * 60, "32"))
    print(color("  全部启动完成", "32"))
    print(color("=" * 60, "32"))
    print()
    if need_frontend:
        print(f"  前端界面:   {url}")
    if need_backend:
        print(f"  接口文档:   http://127.0.0.1:{backend_port}/docs")
    print(f"  测试账号:   admin / admin123")
    print()
    print("  使用流程:")
    if need_frontend:
        print("    1. 打开前端地址并登录")
        print("    2. 点击左侧「审查工作台」")
        print("    3. 点击「发起合规审查」，页面已预填示例方案")
    print()
    print("  停止服务: python scripts\\start.py --stop")
    print("  重启服务: python scripts\\start.py --restart")
    print()

    if not args.no_browser and need_frontend:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())