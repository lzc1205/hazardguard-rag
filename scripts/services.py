"""服务进程管理：启动 / 停止 / 重启。

从 start.py 拆出来的原因：
停止逻辑要可靠地「只杀自己的进程」，涉及进程识别、PID 记录、
端口归属判断，和启动逻辑混在一起会让 start.py 过长且难维护。

**为什么要专门做停止功能**

原来的做法是「端口被占用就换端口」，实测问题很严重：
用户每次重启，端口都会顺延 8000 → 8001 → 8002，
前端同理 5173 → 5174 → 5175，浏览器书签、代理配置、
之前记下来的地址全部失效。重启三次就找不着服务在哪了。

正确做法是：**先把上一个自己启的进程停掉，再用固定端口启动**。

**如何避免误杀别人的进程**

不能只按端口 kill —— 8000 端口上可能是别的项目。
所以三重识别，缺一不可：
1. PID 记录文件（本脚本启动时写入）
2. 端口 + 进程命令行特征匹配（uvicorn app.main / vite / celery hazardguard）
3. 全部命中才杀
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run"
PID_FILE = RUN_DIR / "services.json"

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------- PID 记录


def _read_pids() -> dict:
    if not PID_FILE.exists():
        return {}
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_pids(data: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def record_service(name: str, pid: int, port: int) -> None:
    data = _read_pids()
    data[name] = {"pid": pid, "port": port, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_pids(data)


def clear_service(name: str) -> None:
    data = _read_pids()
    if data.pop(name, None) is not None:
        _write_pids(data)


# ---------------------------------------------------------------- 进程识别


def _run_capture(cmd: list[str], timeout: float = 30) -> str:
    """执行命令并拿 stdout，自动处理中文编码。

    **不能用 text=True**：Windows 上 PowerShell 输出是 GBK，
    `text=True` 会按 UTF-8 解码，直接抛 UnicodeDecodeError
    （实测在这台机器上必崩，因为进程列表里含中文命令行）。
    """
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""

    data = r.stdout or b""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _ps_output() -> str:
    """跨平台获取进程命令行列表。

    Windows 用 PowerShell CIM，Linux 用 ps。
    这里不能用 psutil —— 那是新增依赖，而标准库够用。
    """
    if IS_WINDOWS:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
        ]
    else:
        cmd = ["ps", "-eo", "pid,args"]

    return _run_capture(cmd)


def find_project_pids() -> dict[str, list[int]]:
    """找出属于本项目的进程。

    特征匹配（必须同时满足「命令行含特征」且「路径指向本项目」）：
    - 后端：uvicorn + app.main:app
    - Worker：celery + hazardguard
    - 前端：vite 或 node + frontend 目录

    **双重条件的作用**：只有命令行特征而不校验路径，
    会把别的项目的 uvicorn/vite 也杀掉。
    """
    out = _ps_output()
    if not out:
        return {"backend": [], "worker": [], "frontend": []}

    found: dict[str, list[int]] = {"backend": [], "worker": [], "frontend": []}
    root_lower = str(ROOT).lower().replace("\\", "/")

    # 解析成 (pid, cmdline) 列表
    entries: list[tuple[int, str]] = []
    if IS_WINDOWS:
        try:
            data = json.loads(out) if out.strip() else []
            if isinstance(data, dict):
                data = [data]
            for item in data:
                pid = item.get("ProcessId")
                cl = item.get("CommandLine") or ""
                if pid and cl:
                    entries.append((int(pid), cl))
        except json.JSONDecodeError:
            pass
    else:
        for line in out.splitlines()[1:]:
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                entries.append((int(parts[0]), parts[1]))

    for pid, cl in entries:
        cl_lower = cl.lower().replace("\\", "/")
        is_ours = root_lower in cl_lower

        if "uvicorn" in cl_lower and "app.main:app" in cl_lower:
            if is_ours:
                found["backend"].append(pid)
        elif "celery" in cl_lower:
            if is_ours or "hazardguard" in cl_lower:
                found["worker"].append(pid)
        elif "vite" in cl_lower or ("node" in cl_lower and "frontend" in cl_lower):
            if is_ours:
                found["frontend"].append(pid)

    return found


# ---------------------------------------------------------------- 停止


def _kill_pid(pid: int, timeout: float = 5.0) -> bool:
    """结束进程，Windows 下要连子进程一起杀。

    uvicorn --reload / vite / celery 都会派生子进程，
    只杀父进程的话子进程会继续占着端口。
    """
    try:
        if IS_WINDOWS:
            # /T = 连子进程树一起杀
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=timeout + 3,
            )
        else:
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                time.sleep(0.2)
            os.kill(pid, signal.SIGKILL)
        return True
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError, OSError):
        return False


def stop_all(verbose: bool = True) -> int:
    """停止本项目的所有服务。

    返回被停掉的进程数。
    """
    killed = 0

    # 1) 先用 PID 记录文件停（最可靠 —— 那是本脚本自己写的）
    recorded = _read_pids()
    for name, info in recorded.items():
        pid = info.get("pid")
        if not pid:
            continue
        if _pid_alive(pid):
            if _kill_pid(pid):
                killed += 1
                if verbose:
                    print(f"       已停止 {name}（PID {pid}）")

    # 2) 再按特征扫描，覆盖「手动起的 / 记录丢失的」进程
    time.sleep(1)
    for name, pids in find_project_pids().items():
        for pid in pids:
            if pid in (record_info.get("pid") for record_info in recorded.values()):
                continue  # 已在上面处理过
            if _pid_alive(pid):
                if _kill_pid(pid):
                    killed += 1
                    if verbose:
                        print(f"       已停止 {name}（PID {pid}）")

    if killed == 0:
        if verbose:
            print("       没有需要停止的进程")
    else:
        if verbose:
            print(f"       共停止 {killed} 个进程")
        # 清掉 PID 记录，避免下次误判
        _write_pids({})

    return killed


def _pid_alive(pid: int) -> bool:
    try:
        if IS_WINDOWS:
            out = _run_capture(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=10
            )
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except (subprocess.TimeoutExpired, ProcessLookupError, OSError):
        return False


def wait_ports_free(ports: list[int], timeout: float = 10.0) -> bool:
    """等端口释放完。

    Windows 上进程被杀后端口释放有延迟，
    立刻重启会撞 'address already in use'。
    """
    import socket

    deadline = time.time() + timeout
    for port in ports:
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    break  # 端口已释放
            time.sleep(0.3)
    return True