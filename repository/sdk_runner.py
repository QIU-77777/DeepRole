"""统一的 Agent 运行层。"""

from __future__ import annotations

import asyncio
import types
import typing
from collections.abc import Callable
from typing import Any, TypeVar

from repository.log_config.routing import routing_logger
from repository.config import AGENT_RUN_MAX_ATTEMPTS, AGENT_RUN_TIMEOUT_SECONDS

T = TypeVar("T")


def _matches_output_type(value: Any, output_type: Any) -> bool:
    """兼容 UnionType (X | Y) 与参数化泛型 (dict[str, int]) 的 isinstance 检查。

    Python 内建 isinstance 第二参数不接受参数化泛型，会抛 TypeError；
    这里把 union 拆成各 arm 递归，把泛型退化到 origin 后再做常规 isinstance。
    """
    origin = typing.get_origin(output_type)
    if origin is types.UnionType or origin is typing.Union:
        return any(_matches_output_type(value, arg) for arg in typing.get_args(output_type))
    if origin is not None:
        return isinstance(value, origin)
    return isinstance(value, output_type)


def _build_run_metadata(
    workflow_name: str,
    usage_agent: str,
    usage_phase: str,
    model_name: str,
    trace_metadata: dict[str, str] | None,
) -> dict[str, str]:
    metadata = {
        "workflow_name": workflow_name,
        "usage_agent": usage_agent,
        "usage_phase": usage_phase,
        "model_name": model_name,
    }
    if trace_metadata:
        metadata.update(trace_metadata)
    return metadata


async def _run_agent_with_retries(
    *,
    agent,
    user_input: str,
    metadata: dict[str, str],
    timeout_seconds: float,
    label: str,
    on_result,
    max_attempts: int = AGENT_RUN_MAX_ATTEMPTS,
) -> Any:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                agent.run(user_input, metadata=metadata),
                timeout=timeout_seconds,
            )
            return on_result(result)
        except Exception as exc:
            exc_desc = (
                f"超时（>{timeout_seconds}s）"
                if isinstance(exc, asyncio.TimeoutError)
                else f"失败: {exc}"
            )
            if attempt >= max_attempts:
                routing_logger.error(
                    "[%s] LLM 调用第 %s/%s 次%s，停止重试",
                    label,
                    attempt,
                    max_attempts,
                    exc_desc,
                )
                raise
            routing_logger.warning(
                "[%s] LLM 调用第 %s/%s 次%s，准备重试",
                label,
                attempt,
                max_attempts,
                exc_desc,
            )

    raise RuntimeError("unreachable LLM retry state")


async def run_text_agent(
    *,
    agent,
    user_input: str,
    timeout_seconds: float,
    workflow_name: str,
    trace_metadata: dict[str, str] | None,
    usage_agent: str,
    usage_phase: str,
    model_name: str,
    error_label: str | None = None,
    max_attempts: int = AGENT_RUN_MAX_ATTEMPTS,
) -> str:
    """执行文本 Agent，返回原始字符串输出。"""
    label = error_label or usage_agent

    def _extract_text(result) -> str:
        output = result.output
        if not isinstance(output, str):
            routing_logger.error(f"[{label}] 文本输出类型异常: {type(output)!r}")
            raise TypeError(f"{label} expected str output, got {type(output)!r}")
        return output.strip()

    return await _run_agent_with_retries(
        agent=agent,
        user_input=user_input,
        metadata=_build_run_metadata(
            workflow_name, usage_agent, usage_phase, model_name, trace_metadata
        ),
        timeout_seconds=timeout_seconds,
        label=label,
        on_result=_extract_text,
        max_attempts=max_attempts,
    )


async def run_structured_agent(
    *,
    agent,
    user_input: str,
    output_type: type[T],
    timeout_seconds: float,
    workflow_name: str,
    trace_metadata: dict[str, str] | None,
    usage_agent: str,
    usage_phase: str,
    model_name: str,
    error_label: str | None = None,
    max_attempts: int = AGENT_RUN_MAX_ATTEMPTS,
    output_validator: Callable[[T], None] | None = None,
) -> T:
    """执行结构化 Agent，并统一处理超时、用量日志和 typed parse。"""
    label = error_label or usage_agent

    def _extract_structured(result) -> T:
        output = result.output
        if _matches_output_type(output, output_type):
            if output_validator is not None:
                output_validator(output)
            return output

        routing_logger.error(
            f"[{label}] structured output 类型异常: expected={output_type!r}, got={type(output)!r}, raw={result.response!r}"
        )
        raise TypeError(f"{label} expected {output_type!r}, got {type(output)!r}")

    return await _run_agent_with_retries(
        agent=agent,
        user_input=user_input,
        metadata=_build_run_metadata(
            workflow_name, usage_agent, usage_phase, model_name, trace_metadata
        ),
        timeout_seconds=timeout_seconds,
        label=label,
        on_result=_extract_structured,
        max_attempts=max_attempts,
    )


async def run_app_agent(
    agent,
    user_input: str,
    output_type: type[T],
    *,
    workflow_name: str,
    usage_agent: str,
    model_name: str,
    output_validator: Callable[[T], None] | None = None,
) -> T:
    """主流程结构化 Agent 调用的统一封装（套用应用层默认：超时 / usage_phase / trace）。"""
    return await run_structured_agent(
        agent=agent,
        user_input=user_input,
        output_type=output_type,
        timeout_seconds=AGENT_RUN_TIMEOUT_SECONDS,
        workflow_name=workflow_name,
        trace_metadata={"agent_name": usage_agent},
        usage_agent=usage_agent,
        usage_phase="agent_run",
        model_name=model_name,
        output_validator=output_validator,
    )
