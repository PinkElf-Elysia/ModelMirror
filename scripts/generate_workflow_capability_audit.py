from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path


DIRECT_UPDATES = {
    "errorTrigger": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "failure_event_entry",
        "判断说明": "自研失败处置入口显式订阅 1–50 个独立工作流项目，只接收激活后的脱敏失败摘要；原子派发、occurrence key 去重并抑制处理器递归触发。",
    },
    "scheduleTrigger": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "scheduled_start",
        "判断说明": "自研定时启动支持单次、30 秒以上间隔、五段 Cron、IANA 时区、latest misfire 与 skip overlap；画布以日期、时长单位和常用日历规则配置。",
    },
    "webhook": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "http_event_entry",
        "判断说明": "自研私有 HTTP 事件入口仅支持 POST、JSON/纯文本、哈希密钥、幂等键与限流；可收紧正文格式和大小，并将完整事件与正文登记为全局变量。",
    },
    "wait": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "suspend_wait",
        "判断说明": "自研挂起等待使用 durable continuation，支持时长单位或带 IANA 时区的日期时间，最长 30 天；HTTP 无回执链路可返回 202 后持久挂起，恢复后原始请求正文不可用。",
    },
    "respondToWebhook": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "http_event_reply",
        "判断说明": "自研 HTTP 事件回执支持常用语义状态或 200-599 自定义状态、文本/JSON 模板正文，必须是私有 HTTP 工作流终端节点。",
    },
    "dataTable": {
        "模镜当前状态": "部分实现",
        "模镜对应节点": "data_table_query / data_table_insert / data_table_update / data_table_delete",
        "判断说明": "模镜已有自研 Agent Table 四类操作，但 Schema、过滤与产品语义不宣称和参考项等价。",
    },
    "stickyNote": {
        "模镜当前状态": "已实现",
        "模镜对应节点": "annotation",
        "判断说明": "模镜已有画布注释节点；它只承载编辑元数据，不进入执行语义。",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def status_bucket(value: str) -> str:
    if value == "已实现":
        return "已实现"
    if value == "部分实现":
        return "部分实现"
    if value == "通用节点可覆盖":
        return "通用覆盖"
    if value in {"仅目录声明", "仅运行目录声明"}:
        return "目录声明"
    return "未实现"


def main() -> None:
    args = parse_args()
    with args.source_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if len(rows) != 563:
        raise SystemExit(f"Expected 563 reference rows, found {len(rows)}")
    for row in rows:
        update = DIRECT_UPDATES.get(row.get("n8n内部标识", ""))
        if update:
            row.update(update)
        row["许可证边界"] = (
            "仅名称/节点类型能力参考；不复制代码、参数 Schema、文案、图标、测试或 UI"
            if ".ee" not in row.get("来源条目标识", "")
            else "企业条目仅保留名称审计；排除实现参考"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "n8n-node-capability-matrix.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    statuses = Counter(status_bucket(row["模镜当前状态"]) for row in rows)
    domains: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        domains[row["能力域"]][status_bucket(row["模镜当前状态"])] += 1
        domains[row["能力域"]]["总数"] += 1
    ee_count = sum(".ee" in row.get("来源条目标识", "") for row in rows)
    direct_rows = [row for row in rows if row.get("n8n内部标识") in DIRECT_UPDATES]

    domain_lines = []
    for domain, counts in domains.items():
        domain_lines.append(
            f"| {domain} | {counts['总数']} | {counts['已实现']} | "
            f"{counts['部分实现']} | {counts['通用覆盖']} | "
            f"{counts['目录声明']} | {counts['未实现']} |"
        )
    direct_lines = [
        f"| {row['能力域']} | {row['模镜建议节点名']} | {row['模镜对应节点']} | "
        f"{row['n8n原名参考']} | {row['模镜当前状态']} |"
        for row in direct_rows
    ]
    markdown = f"""# 工作流能力域与节点类型对照审计（#213 + R0/R1/R1.5）

- 审计日期：2026-08-20
- 唯一基线：PR #213 合并提交 `911593f505b05b01037769f578e21f22d2a1c9af`
- R0 基线事实：NodeContract V3、37 个 `NativeNodeKind`、35 个画布目录项、20 个冻结 compatibility 合同
- R1 结果：新增 4 个完整合同，并将既有 `llm` 提升为完整合同；自研节点总数 41、画布目录项 39、当前 19 个冻结 compatibility 合同；四节点与 `llm` Planner 均关闭
- R1.5 PR1 结果：新增完整合同 `failure_event_entry`；自研节点总数 42、画布目录项 40、compatibility 白名单不增长；Planner 关闭且 Xpert 内嵌入口禁止
- 参考清单：563 条节点名称/类型，其中 `.ee` {ee_count} 条仅保留名称审计

## 结论与许可证边界

本表只把节点名称和粗粒度能力类型作为事实输入，最终分类使用模镜自己的能力域、节点名、合同和运行语义。括号列仅保留参考原名。未复制或改写 n8n 代码、参数 Schema、文案、图标、测试或 UI；`.ee` 条目排除实现参考。此工程边界降低但不能替代正式法律意见。

R1 为单实例、原子文件持久化版本，不宣称多 Worker、HA 或多租户就绪。私有 HTTP 原始入站载荷不进入触发记录或运行事件；进入 timer continuation 前，事件和正文变量会替换为大小、哈希与“恢复后不可用”标记。无同步回执的 HTTP 链路可先返回 202 再持久挂起；HTTP 回执上游仍禁止挂起，HTTP 发布版本仍禁止运行时中间件和其他交互式 continuation。为支持幂等重复返回，用户显式配置的回执正文会作为回执保存，因此回显入站数据属于用户可见的持久化选择。

## 状态汇总

- 已实现：{statuses['已实现']}
- 部分实现：{statuses['部分实现']}
- 通用节点可覆盖：{statuses['通用覆盖']}（不等于已有专用连接器）
- 目录声明：{statuses['目录声明']}
- 未实现：{statuses['未实现']}

| 能力域 | 总数 | 已实现 | 部分实现 | 通用覆盖 | 目录声明 | 未实现 |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(domain_lines)}

## 本轮直接闭环

| 模镜能力域 | 模镜自主节点名 | 内部 ID | 原名仅供参考 | 当前状态 |
|---|---|---|---|---|
{chr(10).join(direct_lines)}

完整逐条对照见 [n8n-node-capability-matrix.csv](./n8n-node-capability-matrix.csv)。

## 门禁

- `/api/workflow/node-registry` 是新增节点的唯一权威目录；Registry 故障时本地目录全部只读。
- 前端 `WorkflowNodeKind`、后端 `NativeNodeKind`、NodeContract Registry 必须完全一致。
- Palette 必须是 NodeContract 合法子集；每个启用项必须有默认数据和配置入口。
- compatibility 合同不得超过 #213 冻结白名单；新节点必须直接提供完整合同。
- Planner 只接受完整合同、匹配 checksum 且显式启用的节点；R1 四节点和 R1.5 失败处置入口均禁止 Planner 自动生成。
"""
    (args.output_dir / "N8N_NODE_CAPABILITY_MATRIX.md").write_text(
        markdown,
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
