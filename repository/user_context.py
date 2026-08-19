"""请求级用户上下文。

FastAPI 依赖把当前请求的 user_id 写入此 contextvar；
repository 层所有路径函数从这里派生数据目录，实现按用户隔离。
"""

from contextvars import ContextVar

DEFAULT_USER_ID = "default"

current_user_id: ContextVar[str] = ContextVar(
    "current_user_id", default=DEFAULT_USER_ID
)
