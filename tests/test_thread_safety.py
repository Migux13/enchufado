"""Static guard: dispatcher receivers that touch entity state must run in the event loop.

Home Assistant runs a plain (non-@callback, non-async) dispatcher receiver in the
executor, where async_write_ha_state() raises report_non_thread_safe_operation.
homeassistant is not a test dependency, so this inspects the source instead.
"""
import ast
from pathlib import Path

_BASE = Path(__file__).parent.parent / "custom_components" / "enchufado"


def _is_callback(fn: ast.AST) -> bool:
    if isinstance(fn, ast.AsyncFunctionDef):
        return True
    return any(isinstance(d, ast.Name) and d.id == "callback" for d in fn.decorator_list)


def test_dispatcher_receivers_run_in_the_loop():
    checked = 0
    for path in _BASE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = {
                n.name: n
                for n in ast.walk(func)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not func
            }
            for call in ast.walk(func):
                if not (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "async_dispatcher_connect"
                    and len(call.args) >= 3
                    and isinstance(call.args[2], ast.Name)
                ):
                    continue
                receiver = local.get(call.args[2].id)
                assert receiver is not None, f"{path.name}: receiver not found"
                assert _is_callback(receiver), (
                    f"{path.name}: {receiver.name} must be @callback or async"
                )
                checked += 1
    assert checked >= 1


def test_async_dispatcher_send_only_from_coroutines():
    for path in _BASE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for call in ast.walk(func):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "async_dispatcher_send"
                ):
                    assert any(
                        isinstance(d, ast.Name) and d.id == "callback" for d in func.decorator_list
                    ), f"{path.name}: async_dispatcher_send in sync {func.name}"
