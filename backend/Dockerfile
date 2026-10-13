# ---------------------------------------------------------------------------
# 国内镜像源说明
#
# Dockerfile 里的 apt 与 pip 都指定了清华源。实测：apt update 走官方源
# 超过 30 分钟仍未完成（疑似超时重试），换清华源后只要 3 秒。
# 这是面向国内使用者的必要优化——若部署到境外环境，把这两处的
# `-i` / sed 替换掉即可换回官方源。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# builder 阶段：装编译工具链，把依赖装进 /usr/local
#
# 为什么要分两阶段：gcc/g++ 装一次要 5 分钟以上，而运行时完全不需要编译器。
# 单阶段的话这些工具会一直留在最终镜像里——既让镜像大出一百多 MB，
# 也扩大了攻击面。标准做法是「在 builder 里编译，运行阶段只拷贝产物」。
#
# 为什么是 3.12 而不是 3.11：
# requirements.txt 里 numpy>=2.5.3 **要求 Python 3.12+**，
# 在 3.11 上会直接安装失败：
#   ERROR: Could not find a version that satisfies the requirement numpy>=2.5.3
#   （2.5.x 的 Requires-Python 均为 >=3.12）
# 本地 venv 是 3.13，与依赖要求一致；镜像保持 3.12 是兼容下限。
# 保持 pyproject.toml 的 requires-python 与此同步。
#
# 曾装 libgl1-mesa-glx + libglib2.0-0（OpenCV 那一套），已删除：
# 1. 本项目不用 OpenCV/图形处理（requirements 与代码均无 cv2 依赖）；
# 2. Debian trixie 已移除 libgl1-mesa-glx，保留会让构建直接失败：
#    E: Package 'libgl1-mesa-glx' has no installation candidate
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# 指定清华镜像源：官方 PyPI 在国内网络下极慢（实测安装 30 分钟仍未完成，
# 疑似超时重试）。国内项目应默认走镜像源，需要官方源时改这一行即可。
RUN pip install --prefix=/install -r requirements.txt \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn

# ---------------------------------------------------------------------------
# 运行阶段：不带编译器，只保留 Python + 已装好的包
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# 中文文件名与 PDF 报告需要；时区设为东八区，日志时间才对得上
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY --from=builder /install /usr/local

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]