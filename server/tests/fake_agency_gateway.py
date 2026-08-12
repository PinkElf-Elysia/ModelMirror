from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WORKFLOW_YAML = """```yaml
name: R3 专家团验收计划
description: 使用设计研究、性能审计和产品整合完成首页改版方案
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
  temperature: 0.3
  max_tokens: 4096
  timeout: 180000
  retry: 0
concurrency: 2
steps:
  - id: ux_research
    name: 用户体验研究
    role: design-ux-researcher
    task: 分析用户目标中的 SaaS 首页改版需求，给出转化与移动端证据框架
    acceptance: 至少列出三项有优先级的用户体验发现
    output: ux_research_output
  - id: performance_audit
    name: 性能风险审计
    role: testing-performance-benchmarker
    task: 分析用户目标中的 SaaS 首页性能风险，给出可量化的性能预算
    acceptance: 包含移动端核心指标、阈值和验证方式
    output: performance_audit_output
  - id: launch_plan
    name: 整合落地方案
    role: product-manager
    depends_on: [ux_research, performance_audit]
    task: 基于 {{ux_research_output}} 和 {{performance_audit_output}} 整合首页改版方案
    acceptance: 方案必须覆盖转化、性能、移动端、负责人和验收指标
    output: final_output
```"""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages") if isinstance(payload, dict) else []
        system = ""
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            system = str(messages[0].get("content") or "")
        if "工作流编排专家" in system or "AI workflow orchestration expert" in system:
            content = WORKFLOW_YAML
        elif "验收员" in system or "acceptance reviewer" in system.lower():
            content = '{"pass":true,"failed":[]}'
        else:
            content = (
                "这是 Fake Gateway 的受控验收产出。\n\n"
                "- 已覆盖用户目标与当前步骤职责。\n"
                "- 已给出可执行行动、负责人和量化验收指标。\n"
                "- 本响应不访问外网，也不会产生模型费用。"
            )
        response = json.dumps(
            {
                "id": "fake-agency-completion",
                "model": str(payload.get("model") or "fake-model"),
                "choices": [
                    {"message": {"role": "assistant", "content": content}}
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 80,
                    "total_tokens": 200,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    port = int(os.getenv("FAKE_AGENCY_GATEWAY_PORT", "8000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
