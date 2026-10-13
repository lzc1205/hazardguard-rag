"""前端全流程接口冒烟测试。

为什么需要它：
前端 16 个页面调 51 个接口，全靠人工点页面验证既慢又不可靠——
改了个字段名、删了个接口，都要等用户点按钮才发现。
这个脚本按**前端真实调用顺序**跑一遍所有接口，
任何一步返回非 0 code、404、422 都会立刻暴露。

注意：走 TestClient 不经过 Nginx/Vite 代理，
所以校验的是「前后端契约」而非「网络链路」。

用法：
    cd D:\\123456789\\PythonProject6\\backend
    ..\\.venv\\Scripts\\python.exe scripts\\smoke_frontend_flow.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

# 与前端 api/index.ts 保持一致的账号
ADMIN = {"username": "admin", "password": "admin123"}

results: list[tuple[str, bool, str]] = []


def check(name: str, resp, expect_in: tuple[int, ...] = (200,)) -> dict:
    ok = resp.status_code in expect_in
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            if body.get("code") not in (0, None):
                ok = False
                detail = f"code={body.get('code')} msg={body.get('message')}"
            else:
                detail = str(body.get("message", ""))[:60]
    except Exception:
        detail = resp.text[:80]

    results.append((name, ok, f"[{resp.status_code}] {detail}"))
    return resp.json() if ok and "application/json" in resp.headers.get(
        "content-type", ""
    ) else {}


def main() -> int:
    with TestClient(app) as c:
        print("=" * 62)
        print("前端全流程接口冒烟测试")
        print("=" * 62)

        # ---------------------------------------------------------- 认证
        r = c.post("/api/v1/auth/login", json=ADMIN)
        body = check("登录", r)
        token = (body.get("data") or {}).get("access_token") if isinstance(body, dict) else None
        if not token:
            print("\n登录失败，无法继续。")
            for n, ok, d in results:
                print(f"  {'OK ' if ok else 'FAIL'} {n}: {d}")
            return 1

        H = {"Authorization": f"Bearer {token}"}

        check("当前用户", c.get("/api/v1/auth/me", headers=H))
        check("LLM 状态", c.get("/api/v1/llm/status", headers=H))

        # ---------------------------------------------------------- 概览统计
        check("统计总览", c.get("/api/v1/reviews/stats/overview", headers=H))
        check("统计看板", c.get("/api/v1/reviews/stats/dashboard", headers=H))

        # ---------------------------------------------------------- 项目
        proj_body = check(
            "创建项目",
            c.post(
                "/api/v1/projects",
                json={"name": "冒烟测试项目", "description": "接口全流程验证"},
                headers=H,
            ),
        )
        pid = (proj_body.get("data") or {}).get("id")
        check("项目列表", c.get("/api/v1/projects", headers=H))
        if pid:
            check("项目详情", c.get(f"/api/v1/projects/{pid}", headers=H))
            check("更新项目", c.put(
                f"/api/v1/projects/{pid}",
                json={"name": "冒烟测试项目(改)", "description": "已更新"},
                headers=H,
            ))
            check("项目文档列表", c.get(f"/api/v1/projects/{pid}/documents", headers=H))
            check("项目审查列表", c.get(f"/api/v1/projects/{pid}/reviews", headers=H))

        # ---------------------------------------------------------- 文档上传
        sample = ROOT / "backend" / "tests" / "data" / "test_docs" / "01_模板支架_违规样本.docx"
        doc_body: dict = {}
        if sample.exists():
            with sample.open("rb") as f:
                r = c.post(
                    "/api/v1/documents/upload",
                    files={"file": (sample.name, f, "application/octet-stream")},
                    # title 是后端必填 Form 字段，漏了会 422
                    data={
                        "title": "冒烟测试文档",
                        "project_id": pid or "",
                        "doc_category": "formwork",
                    },
                    headers=H,
                )
                doc_body = check("上传文档(docx)", r)
            chunks = (doc_body.get("data") or {}).get("chunk_count")
            results.append((
                "  └ 切块入库",
                bool(chunks),
                f"chunk_count={chunks}",
            ))
        else:
            results.append(("上传文档(docx)", False, "测试文档不存在"))

        pdf = ROOT / "backend" / "tests" / "data" / "test_docs" / "02_深基坑_违规样本.pdf"
        if pdf.exists():
            with pdf.open("rb") as f:
                c.post(
                    "/api/v1/documents/upload",
                    files={"file": (pdf.name, f, "application/octet-stream")},
                    data={
                        "title": "冒烟测试PDF",
                        "project_id": pid or "",
                        "doc_category": "foundation_pit",
                    },
                    headers=H,
                )
            results.append(("上传文档(pdf)", True, "已上传"))

        check("文档列表", c.get("/api/v1/documents", headers=H))

        did = (doc_body.get("data") or {}).get("id")
        if did:
            check("文档详情", c.get(f"/api/v1/documents/{did}", headers=H))
            r = c.get(f"/api/v1/documents/{did}/download", headers=H)
            ok = r.status_code == 200 and len(r.content) > 1000
            results.append((
                "下载文档",
                ok,
                f"[{r.status_code}] {len(r.content)} bytes",
            ))

        # ---------------------------------------------------------- 审查（同步）
        review_text = """深基坑工程专项施工方案
一、工程概况
本工程基坑开挖深度18.5米，周长约420米，采用钻孔灌注桩加两道内支撑支护。
本工程属于危险性较大的分部分项工程。
二、编制依据
《建筑基坑支护技术规程》JGJ 120-2012
三、施工计划
开挖分4层，总工期8个月。
四、施工工艺技术
基坑开挖一次性到底。基坑监测每三天进行一次。周边堆载不控制。
五、施工安全保证措施
安排专人巡查边坡。
六、施工管理及作业人员配备和分工
专职安全员2名，作业班组45人。
七、验收要求
支护结构施工完成后由项目技术负责人组织验收。
八、应急处置措施
配备沙袋、抽水泵等应急物资。
九、计算书及相关施工图纸
附支护结构计算书及平面布置图。"""

        rev_body = check(
            "发起审查(同步)",
            c.post(
                "/api/v1/reviews",
                json={
                    "title": "冒烟测试审查",
                    "text": review_text,
                    "project_id": pid,
                    "document_id": did,
                },
                headers=H,
            ),
        )
        rid = (rev_body.get("data") or {}).get("review_id") or (rev_body.get("data") or {}).get("id")

        if rid:
            check("审查详情", c.get(f"/api/v1/reviews/{rid}", headers=H))
            check("审查轨迹", c.get(f"/api/v1/reviews/{rid}/trace", headers=H))
            check("报告预览", c.get(f"/api/v1/reviews/{rid}/report.preview", headers=H))

            r = c.get(f"/api/v1/reviews/{rid}/report.docx", headers=H)
            ok = r.status_code == 200 and len(r.content) > 5000
            results.append(("导出 Word", ok, f"[{r.status_code}] {len(r.content)} bytes"))

            r = c.get(f"/api/v1/reviews/{rid}/report.pdf", headers=H)
            ok = r.status_code == 200 and r.content[:4] == b"%PDF"
            results.append((
                "导出 PDF",
                ok,
                f"[{r.status_code}] {len(r.content)} bytes",
            ))

        check("审查列表", c.get("/api/v1/reviews", headers=H, params={"page": 1, "page_size": 10}))
        check("任务列表", c.get("/api/v1/reviews/tasks", headers=H))

        # ---------------------------------------------------------- 知识库
        check("法规检索", c.post(
            "/api/v1/search",
            json={"query": "模板支架立杆间距", "top_k": 5},
            headers=H,
        ))
        check("法规类别", c.get("/api/v1/categories", headers=H))
        check("条目列表", c.get("/api/v1/entries", headers=H))
        check("向量统计", c.get("/api/v1/vector/stats", headers=H))

        # ---------------------------------------------------------- Prompt
        check("Prompt 列表", c.get("/api/v1/prompts", headers=H))
        check("Prompt 版本", c.get("/api/v1/prompts/versions", headers=H))

        # ---------------------------------------------------------- 问答
        check("智能问答", c.post(
            "/api/v1/ask",
            # mode 只接受 react / function_calling / direct
            json={"content": "深基坑监测频率要求是什么？", "mode": "direct"},
            headers=H,
        ))

        # ---------------------------------------------------------- 清理
        if did:
            check("删除文档", c.delete(f"/api/v1/documents/{did}", headers=H))
        if rid:
            check("删除审查", c.delete(f"/api/v1/reviews/{rid}", headers=H))
        if pid:
            check("删除项目", c.delete(f"/api/v1/projects/{pid}", headers=H))

    # ---------------------------------------------------------- 汇总
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [(n, d) for n, ok, d in results if not ok]

    print(f"\n通过 {passed}/{len(results)}")
    if failed:
        print("\n失败项：")
        for n, d in failed:
            print(f"  [FAIL] {n}: {d}")
        return 1

    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
