"""Bundled, network-free Python MCP adapters for catalog wave 1."""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


MAX_DATASETS = 8
MAX_DATA_ROWS = 1_000
MAX_DATA_COLUMNS = 64
MAX_DATA_BYTES = 96 * 1024
MAX_SPEC_BYTES = 32 * 1024
MAX_RESULT_BYTES = 128 * 1024
DATASET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
TIME_VALUE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _finite_number(value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1e100:
        raise ValueError("数值必须有限且绝对值不能超过 1e100。")
    return number


def _numeric_result(operation: str, result: float) -> dict[str, float | str]:
    clean = _finite_number(result)
    return {"operation": operation, "result": clean}


def calculate(
    operation: str,
    a: float,
    b: float | None = None,
) -> dict[str, float | str]:
    left = _finite_number(a)
    if operation == "sqrt":
        if left < 0:
            raise ValueError("平方根输入不能为负数。")
        return _numeric_result(operation, math.sqrt(left))
    if b is None:
        raise ValueError("该运算需要第二个数值。")
    right = _finite_number(b)
    if operation == "add":
        result = left + right
    elif operation == "sub":
        result = left - right
    elif operation == "mul":
        result = left * right
    elif operation in {"div", "mod"}:
        if right == 0:
            raise ValueError("除数不能为零。")
        result = left / right if operation == "div" else left % right
    else:
        raise ValueError("不支持的计算操作。")
    return _numeric_result(operation, result)


def build_calculator() -> FastMCP:
    mcp = FastMCP("ModelMirror Calculator")

    @mcp.tool(annotations=READ_ONLY)
    def add(a: float, b: float) -> dict[str, float | str]:
        """计算两个有限数值之和。"""

        return calculate("add", a, b)

    @mcp.tool(annotations=READ_ONLY)
    def sub(a: float, b: float) -> dict[str, float | str]:
        """计算两个有限数值之差。"""

        return calculate("sub", a, b)

    @mcp.tool(annotations=READ_ONLY)
    def mul(a: float, b: float) -> dict[str, float | str]:
        """计算两个有限数值之积。"""

        return calculate("mul", a, b)

    @mcp.tool(annotations=READ_ONLY)
    def div(a: float, b: float) -> dict[str, float | str]:
        """计算两个有限数值之商，除数不能为零。"""

        return calculate("div", a, b)

    @mcp.tool(annotations=READ_ONLY)
    def mod(a: float, b: float) -> dict[str, float | str]:
        """计算两个有限数值的模，除数不能为零。"""

        return calculate("mod", a, b)

    @mcp.tool(annotations=READ_ONLY)
    def sqrt(a: float) -> dict[str, float | str]:
        """计算非负有限数值的平方根。"""

        return calculate("sqrt", a)

    return mcp


def _timezone(value: str) -> ZoneInfo:
    clean = str(value or "").strip()
    if not clean or len(clean) > 128 or ".." in clean or clean.startswith("/"):
        raise ValueError("必须提供有效的 IANA 时区名称。")
    try:
        return ZoneInfo(clean)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知 IANA 时区：{clean}") from exc


def _time_payload(value: datetime) -> dict[str, Any]:
    offset = value.utcoffset()
    dst = value.dst()
    return {
        "timezone": str(value.tzinfo),
        "datetime": value.isoformat(timespec="seconds"),
        "utc_offset": offset.total_seconds() / 3600 if offset else 0.0,
        "is_dst": bool(dst and dst.total_seconds()),
    }


def current_time_payload(timezone: str) -> dict[str, Any]:
    return _time_payload(datetime.now(_timezone(timezone)))


def convert_time_payload(
    source_timezone: str,
    time: str,
    target_timezone: str,
) -> dict[str, Any]:
    clean_time = str(time or "").strip()
    if not TIME_VALUE.fullmatch(clean_time):
        raise ValueError("时间必须使用 24 小时制 HH:MM 格式。")
    source_zone = _timezone(source_timezone)
    target_zone = _timezone(target_timezone)
    hour, minute = (int(part) for part in clean_time.split(":"))
    source_value = datetime.now(source_zone).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    target_value = source_value.astimezone(target_zone)
    difference = target_value.utcoffset() - source_value.utcoffset()
    return {
        "source": _time_payload(source_value),
        "target": _time_payload(target_value),
        "time_difference_hours": difference.total_seconds() / 3600,
    }


def build_time() -> FastMCP:
    mcp = FastMCP("ModelMirror Time")

    @mcp.tool(annotations=READ_ONLY)
    def get_current_time(timezone: str) -> dict[str, Any]:
        """获取指定 IANA 时区的当前时间。"""

        return current_time_payload(timezone)

    @mcp.tool(annotations=READ_ONLY)
    def convert_time(
        source_timezone: str,
        time: str,
        target_timezone: str,
    ) -> dict[str, Any]:
        """把今天的 HH:MM 时间从源 IANA 时区转换到目标时区。"""

        return convert_time_payload(source_timezone, time, target_timezone)

    return mcp


def _json_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _validate_data(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(data, list) or len(data) > MAX_DATA_ROWS:
        raise ValueError(f"数据必须是数组且最多包含 {MAX_DATA_ROWS} 行。")
    cleaned: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict) or len(row) > MAX_DATA_COLUMNS:
            raise ValueError(f"每行必须是对象且最多包含 {MAX_DATA_COLUMNS} 列。")
        clean_row: dict[str, Any] = {}
        for key, value in row.items():
            clean_key = str(key)
            if not clean_key or len(clean_key) > 128:
                raise ValueError("列名不能为空且不能超过 128 个字符。")
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValueError("数据单元格只允许字符串、数值、布尔值或 null。")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("数据中不能包含 NaN 或 Infinity。")
            if isinstance(value, str) and len(value) > 4_096:
                raise ValueError("单个字符串单元格不能超过 4096 个字符。")
            clean_row[clean_key] = value
        cleaned.append(clean_row)
    if _json_size(cleaned) > MAX_DATA_BYTES:
        raise ValueError("数据序列化后不能超过 96 KiB。")
    return cleaned


def _reject_remote_references(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"url", "href"}:
                raise ValueError("Vega-Lite 规范不能包含远程 URL 或链接。")
            _reject_remote_references(child)
    elif isinstance(value, list):
        for child in value:
            _reject_remote_references(child)


class VegaLiteSession:
    def __init__(self) -> None:
        self.datasets: dict[str, list[dict[str, Any]]] = {}

    def save_data(self, name: str, data: list[dict[str, Any]]) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not DATASET_NAME.fullmatch(clean_name):
            raise ValueError("数据集名称只能包含字母、数字、下划线和连字符。")
        if clean_name not in self.datasets and len(self.datasets) >= MAX_DATASETS:
            raise ValueError(f"单个会话最多保存 {MAX_DATASETS} 个临时数据集。")
        cleaned = _validate_data(data)
        self.datasets[clean_name] = cleaned
        return {
            "name": clean_name,
            "rows": len(cleaned),
            "bytes": _json_size(cleaned),
            "storage": "ephemeral-memory",
        }

    def visualize_data(
        self,
        data_name: str,
        vegalite_specification: str,
    ) -> dict[str, Any]:
        clean_name = str(data_name or "").strip()
        data = self.datasets.get(clean_name)
        if data is None:
            raise ValueError("指定的临时数据集不存在。")
        raw_spec = str(vegalite_specification or "").strip()
        if not raw_spec or len(raw_spec.encode("utf-8")) > MAX_SPEC_BYTES:
            raise ValueError("Vega-Lite 规范不能为空且不能超过 32 KiB。")
        try:
            spec = json.loads(raw_spec)
        except json.JSONDecodeError as exc:
            raise ValueError("Vega-Lite 规范必须是有效 JSON。") from exc
        if not isinstance(spec, dict):
            raise ValueError("Vega-Lite 规范顶层必须是对象。")
        _reject_remote_references(spec)
        spec["data"] = {"values": data}
        result = {
            "data_name": clean_name,
            "rows": len(data),
            "output_type": "text",
            "artifact": spec,
        }
        if _json_size(result) > MAX_RESULT_BYTES:
            raise ValueError("Vega-Lite 返回结果不能超过 128 KiB。")
        return result


def build_vegalite() -> FastMCP:
    mcp = FastMCP("ModelMirror Vega-Lite")
    session = VegaLiteSession()

    @mcp.tool(annotations=READ_ONLY)
    def save_data(name: str, data: list[dict[str, Any]]) -> dict[str, Any]:
        """在当前临时会话内保存一份受限表格，供后续生成 Vega-Lite 规范。"""

        return session.save_data(name, data)

    @mcp.tool(annotations=READ_ONLY)
    def visualize_data(
        data_name: str,
        vegalite_specification: str,
    ) -> dict[str, Any]:
        """把临时数据注入受限 Vega-Lite JSON 规范并返回可复用产物。"""

        return session.visualize_data(data_name, vegalite_specification)

    return mcp


BUILDERS = {
    "calculator-mcp": build_calculator,
    "time-mcp": build_time,
    "vegalite-mcp": build_vegalite,
}
ADAPTER_TOOL_NAMES = {
    "calculator-mcp": ("add", "sub", "mul", "div", "mod", "sqrt"),
    "time-mcp": ("get_current_time", "convert_time"),
    "vegalite-mcp": ("save_data", "visualize_data"),
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    BUILDERS[args.adapter_id]().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
