"""分层依赖方向的机械守卫：依赖只能向内/向下指。

四层：server → app → repository → models（无 shared 内核——原 shared/ 已拆散到各层）。

这条规则以前靠纪律维持，现在靠这个测试 + AST 静态扫描强制成立。
新增一个反向 import 会让对应用例直接失败。
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _imported_modules(py: Path) -> set[str]:
    """返回该文件所有绝对 import 的完整模块路径（忽略相对 import）。"""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
    return mods


def _files(pkg: str) -> list[Path]:
    return [p for p in (_ROOT / pkg).rglob("*.py") if "__pycache__" not in p.parts]


def _roots(mods: set[str]) -> set[str]:
    return {m.split(".")[0] for m in mods}


def test_models_depends_only_inward():
    """领域层不得依赖任何外层（repository / app / server）。"""
    for py in _files("models"):
        assert not (_roots(_imported_modules(py)) & {"app", "repository", "server"}), py


def test_repository_does_not_depend_on_app_or_server():
    """仓储/基础设施层不得反向依赖用例层或交付层。"""
    for py in _files("repository"):
        assert not (_roots(_imported_modules(py)) & {"app", "server"}), py


def test_app_does_not_depend_on_server():
    """用例层不得依赖交付层。"""
    for py in _files("app"):
        assert "server" not in _roots(_imported_modules(py)), py
