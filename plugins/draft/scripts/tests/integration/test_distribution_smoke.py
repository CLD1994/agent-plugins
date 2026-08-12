"""插件分发冒烟测试：宿主流程与宿主无关的运行时冷安装。

两个场景共享同一个模块作用域的只读插件分发副本（``distribution_copy``），
插件入口、技能目录和核心运行时路径在三个场景实例中完全相同，测试不得修改该副本：

- ``test_host_plugin_flow``：按 ``codex``/``claude`` 参数化，不标记 ``network``。
  每个宿主使用独立的数据目录、安全运行目录和草稿根，只验证 SessionStart、``bind``、
  ``unbind``、日志、桥接和数据目录隔离，不执行 uvx；冷运行时构建不按宿主重复。
- ``test_runtime_distribution_online_then_offline``：只运行一次，标记 ``network``。
  在同一来源路径下使用唯一缓存，先用 ``online-root`` 联网跑
  ``--version``/``init``/``check``，再用同一缓存以 ``--offline`` 和独立的
  ``offline-root`` 重复这些命令，避免第二次 ``init`` 命中已有草稿。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.paths import PLUGIN_ROOT

REPO_ROOT = PLUGIN_ROOT.parents[1]

HOSTS = ("codex", "claude")
HOST_ENV: dict[str, tuple[str, str]] = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}

UVX_TIMEOUT = 600


# ---------------------------------------------------------------------------
# 共享夹具：一次性分发副本与双宿主隔离目录
# ---------------------------------------------------------------------------


@dataclass
class HostCase:
    host: str
    plugin_copy: Path
    data_dir: Path
    safe_runtime_dir: Path
    draft_root: Path
    session_id: str

    @property
    def entry(self) -> Path:
        return self.plugin_copy / "scripts" / "draft.py"

    @property
    def skill_root(self) -> Path:
        return self.plugin_copy / "skills" / "draft"

    @property
    def skill_md(self) -> Path:
        return self.skill_root / "SKILL.md"

    @property
    def root_var(self) -> str:
        return HOST_ENV[self.host][0]

    @property
    def data_var(self) -> str:
        return HOST_ENV[self.host][1]


def _assert_no_git_ancestors(path: Path) -> None:
    """显式确认目录及其所有祖先都不含 .git（无仓库父目录语义）。"""
    cursor = path
    while cursor != cursor.parent:
        assert not (cursor / ".git").exists(), f".git found at {cursor}"
        cursor = cursor.parent


def _snapshot_tree(root: Path) -> dict[str, str]:
    """返回 {相对路径: 文件内容 SHA-256}，用于证明分发副本未被测试修改。"""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            snapshot[path.relative_to(root).as_posix()] = digest
    return snapshot


@pytest.fixture(scope="module")
def distribution_copy(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Path:
    """只复制一次普通插件目录；所有场景共享同一来源路径。

    不得为每个宿主分别复制核心运行时：来源路径不同会产生不同的 uv 工具环境，
    即使共享 ``UV_CACHE_DIR`` 也无法消除重复安装。

    副本是只读契约：基线快照在夹具返回前记录，模块测试全部结束后由 finalizer
    重新对比。配合 ``PYTHONDONTWRITEBYTECODE=1``，任何对副本的写入（含字节码）
    都会在收尾时显式失败，而不是静默污染共享来源。
    """
    parent = tmp_path_factory.mktemp("distribution")
    _assert_no_git_ancestors(parent)
    plugin_copy = parent / "plugin"
    shutil.copytree(
        PLUGIN_ROOT,
        plugin_copy,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
        ),
    )
    resolved = plugin_copy.resolve()
    baseline = _snapshot_tree(resolved)

    def _assert_copy_unchanged() -> None:
        assert _snapshot_tree(resolved) == baseline, (
            "distribution copy was modified by the test run"
        )

    request.addfinalizer(_assert_copy_unchanged)
    return resolved


@pytest.fixture(scope="module")
def host_cases(
    tmp_path_factory: pytest.TempPathFactory, distribution_copy: Path
) -> dict[str, HostCase]:
    """同时为两个宿主建立独立的 data_dir、安全运行目录和草稿根。

    会话标识按宿主区分，使跨宿主隔离断言可以精确识别本宿主产物。
    """
    cases: dict[str, HostCase] = {}
    for host in HOSTS:
        parent = tmp_path_factory.mktemp(f"host-{host}")
        _assert_no_git_ancestors(parent)
        data_dir = (parent / "data").resolve()
        data_dir.mkdir()
        safe_runtime_dir = (parent / "xdg").resolve()
        safe_runtime_dir.mkdir()
        safe_runtime_dir.chmod(0o700)
        draft_root = (parent / "draft-root").resolve()
        draft_root.mkdir()
        cases[host] = HostCase(
            host=host,
            plugin_copy=distribution_copy,
            data_dir=data_dir,
            safe_runtime_dir=safe_runtime_dir,
            draft_root=draft_root,
            session_id=f"smoke-session-{host}",
        )
    return cases


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _clean_smoke_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "PLUGIN_ROOT",
        "PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_DATA",
        "DRAFT_DATA",
        "DRAFT_SKILL_PATH",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "UV_CACHE_DIR",
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
        "UV_PYTHON",
        "UV_PYTHON_DOWNLOADS",
        "UV_OFFLINE",
        "UV_RESOLUTION",
    ):
        env.pop(name, None)
    # 分发副本是只读契约：禁止子进程向其写入 .pyc 字节码
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _session_stdin(session_id: str, source: str) -> str:
    return json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": session_id,
            "source": source,
            "transcript_path": "/not/read/transcript.jsonl",
            "cwd": "/workspace",
        },
        ensure_ascii=False,
    )


def _run_hook(
    case: HostCase, *, session_id: str | None = None, source: str = "startup"
) -> subprocess.CompletedProcess[str]:
    env = _clean_smoke_env()
    # 只设置当前宿主的 root/data 变量；另一宿主的变量已被清空
    env[case.root_var] = str(case.plugin_copy)
    env[case.data_var] = str(case.data_dir)
    env["XDG_RUNTIME_DIR"] = str(case.safe_runtime_dir)
    sid = case.session_id if session_id is None else session_id
    # 用 uv run 调用复制后的入口
    return subprocess.run(
        ["uv", "run", str(case.entry), "hook", "session-start", case.host],
        capture_output=True,
        text=True,
        env=env,
        input=_session_stdin(sid, source),
        timeout=120,
        check=False,
    )


def _run_entry(
    case: HostCase, args: list[str], *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = _clean_smoke_env()
    env[case.root_var] = str(case.plugin_copy)
    env[case.data_var] = str(case.data_dir)
    env["XDG_RUNTIME_DIR"] = str(case.safe_runtime_dir)
    return subprocess.run(
        ["uv", "run", str(case.entry), *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=120,
        check=False,
    )


def _run_uvx(
    runtime_root: Path,
    cache_dir: Path,
    cwd: Path,
    *draft_args: str,
    offline: bool = False,
    online_retries: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = _clean_smoke_env()
    extra: list[str] = []
    if offline:
        extra.append("--offline")
    cmd = [
        "uvx",
        "--isolated",
        "--cache-dir",
        str(cache_dir),
        *extra,
        "--from",
        str(runtime_root),
        "draft",
        *draft_args,
    ]
    # 联网冷缓存解析可能因瞬时网络抖动失败；仅对联网调用重试以吸收抖动，不掩盖持续失败
    # （重试后仍失败则原样返回，断言照常失败）。离线阶段不重试：必须使用联网阶段预热好的
    # 缓存，重试会掩盖真实的缓存未命中失败。
    attempts = 1 + (online_retries if not offline else 0)
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=UVX_TIMEOUT,
    )
    for _ in range(attempts - 1):
        if result.returncode == 0:
            break
        time.sleep(1)
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=UVX_TIMEOUT,
        )
    return result


def _parse_unbound_envelope(stdout: str) -> dict[str, str]:
    """解析未绑定信封，返回关键字段。"""
    lines = stdout.strip().splitlines()
    assert lines, f"empty stdout: {stdout!r}"
    assert lines[0] == "Draft 会话未绑定", stdout
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("- "):
            fields[line[2:].split("：", 1)[0]] = line.split("：", 1)[1]
    return fields


def _parse_rebind_envelope(stdout: str) -> dict[str, str]:
    lines = stdout.strip().splitlines()
    assert lines, f"empty stdout: {stdout!r}"
    assert lines[0] == "Draft 会话已绑定", stdout
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("- "):
            body = line[2:]
            if "：" in body:
                key, _, value = body.partition("：")
                fields[key] = value
            else:
                # "- 继续受管理讨论前..." 等无冒号行
                fields[body] = body
    return fields


# ---------------------------------------------------------------------------
# 场景一：宿主流程（不执行 uvx，不标记 network）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS)
def test_host_plugin_flow(host_cases: dict[str, HostCase], host: str) -> None:
    case = host_cases[host]
    other = host_cases["claude" if host == "codex" else "codex"]

    # 1. 调用 hook session-start HOST；应输出未绑定信封，退出 0
    result = _run_hook(case)
    assert result.returncode == 0, result.stderr
    assert "Draft 会话未绑定" in result.stdout
    assert case.session_id in result.stdout

    # 2. 解析未绑定信封：entry 和命令格式只指向共享分发副本
    envelope = _parse_unbound_envelope(result.stdout)
    entry_str = envelope["插件入口"]
    assert entry_str == str(case.entry), envelope
    # 绑定/解除格式中的入口路径也指向副本
    assert str(case.entry) in envelope["绑定格式"]
    assert str(case.entry) in envelope["解除格式"]
    # 不应指向仓库中的入口
    assert str(PLUGIN_ROOT) not in result.stdout
    assert str(REPO_ROOT) not in result.stdout

    # 3. bind 一个普通存在目录（draft_root）；重跑 Hook；断言已绑定信封
    bind_result = _run_entry(case, ["bind", str(case.draft_root), "--session", case.session_id])
    assert bind_result.returncode == 0, bind_result.stderr
    assert "绑定成功" in bind_result.stdout

    rebind_result = _run_hook(case, source="resume")
    assert rebind_result.returncode == 0, rebind_result.stderr
    assert "Draft 会话已绑定" in rebind_result.stdout
    rebind_fields = _parse_rebind_envelope(rebind_result.stdout)
    # root 指向本宿主独立的草稿根
    assert rebind_fields["草稿根"] == str(case.draft_root), rebind_fields
    # 当前 skill 指向共享副本中的 skill
    assert rebind_fields["当前 skill"] == str(case.skill_md), rebind_fields
    # 插件入口指向共享副本中的入口
    assert rebind_fields["插件入口"] == str(case.entry), rebind_fields
    # 核心检查命令指向共享副本中的 runtime
    expected_runtime = case.skill_root / "scripts" / "runtime"
    assert str(expected_runtime) in rebind_result.stdout, rebind_result.stdout
    assert "uvx --isolated --from" in rebind_result.stdout
    assert "draft check" in rebind_result.stdout
    # 不应指向仓库
    assert str(PLUGIN_ROOT) not in rebind_result.stdout
    assert str(REPO_ROOT) not in rebind_result.stdout

    # 4. unbind 后下一次 Hook 回到未绑定
    unbind_result = _run_entry(case, ["unbind", "--session", case.session_id])
    assert unbind_result.returncode == 0, unbind_result.stderr
    assert "解除成功" in unbind_result.stdout
    next_hook = _run_hook(case, source="clear")
    assert next_hook.returncode == 0, next_hook.stderr
    assert "Draft 会话未绑定" in next_hook.stdout

    # 5. 状态隔离：bindings/logs 只在所选宿主 data_dir，bridge 只在该宿主的安全运行目录，
    #    且不得合并为用户级全局 Draft 目录
    bindings_dir = case.data_dir / "bindings"
    logs_dir = case.data_dir / "logs"
    assert bindings_dir.exists()
    # 至少存在过 binding 文件（unbind 后可能仍存在空目录）
    assert logs_dir.exists()
    hook_log = logs_dir / "hook.jsonl"
    assert hook_log.exists()

    bridge_base = case.safe_runtime_dir / "draft" / "bridges"
    assert bridge_base.exists()
    # bridge 文件中引用的 data_dir 指向本宿主独立的 data_dir
    bridge_jsons = list(bridge_base.glob("*.json"))
    assert bridge_jsons, f"no bridge json in {bridge_base}"
    for bridge_file in bridge_jsons:
        payload = json.loads(bridge_file.read_text(encoding="utf-8"))
        assert payload["data_dir"] == str(case.data_dir), payload

    # 6. 宿主隔离：本宿主的产物不得出现在另一宿主的目录中
    other_bindings = other.data_dir / "bindings"
    if other_bindings.exists():
        for binding_file in other_bindings.glob("*.json"):
            body = binding_file.read_text(encoding="utf-8")
            assert case.session_id not in body, binding_file
            assert str(case.draft_root) not in body, binding_file
    other_log = other.data_dir / "logs" / "hook.jsonl"
    if other_log.exists():
        assert case.session_id not in other_log.read_text(encoding="utf-8"), other_log
    other_bridge_base = other.safe_runtime_dir / "draft" / "bridges"
    if other_bridge_base.exists():
        for bridge_file in other_bridge_base.glob("*.json"):
            payload = json.loads(bridge_file.read_text(encoding="utf-8"))
            assert payload["data_dir"] != str(case.data_dir), bridge_file
    # 本宿主的 Draft 业务文件不落入另一宿主的草稿根
    assert not (other.draft_root / ".draft").exists(), other.draft_root
    assert not (other.draft_root / "index.md").exists(), other.draft_root

    # 输出/状态中无路径解析回仓库检出（stdout + stderr）
    for output in (
        result.stdout,
        result.stderr,
        rebind_result.stdout,
        rebind_result.stderr,
        next_hook.stdout,
        next_hook.stderr,
    ):
        assert str(REPO_ROOT) not in output, f"repo path leaked: {output}"
        assert str(PLUGIN_ROOT) not in output, f"plugin path leaked: {output}"


# ---------------------------------------------------------------------------
# 场景二：宿主无关的运行时冷安装，只执行一次（network）
# ---------------------------------------------------------------------------


@pytest.mark.network
def test_runtime_distribution_online_then_offline(distribution_copy: Path, tmp_path: Path) -> None:
    """空缓存联网跑一次核心 smoke，再用同一缓存 + --offline 重跑。

    来源路径固定为共享分发副本；缓存是本测试独有的新建目录。第二次 ``init``
    使用独立的 ``offline-root``，避免命中在线阶段已初始化的草稿。
    """
    _assert_no_git_ancestors(tmp_path)
    runtime_root = distribution_copy / "skills" / "draft" / "scripts" / "runtime"
    cache_dir = (tmp_path / "cache").resolve()
    cache_dir.mkdir()
    online_root = (tmp_path / "online-root").resolve()
    online_root.mkdir()
    offline_root = (tmp_path / "offline-root").resolve()
    offline_root.mkdir()

    # 联网首次解析（空缓存 + 网络）
    for args in (("--version",), ("init", str(online_root)), ("check", "--root", str(online_root))):
        uvx_result = _run_uvx(runtime_root, cache_dir, tmp_path, *args, online_retries=1)
        assert uvx_result.returncode == 0, uvx_result.stderr + uvx_result.stdout
        if args == ("--version",):
            assert uvx_result.stdout.strip() == "0.3.0", uvx_result.stdout
        for output in (uvx_result.stdout, uvx_result.stderr):
            assert str(REPO_ROOT) not in output, f"repo path leaked: {output}"
            assert str(PLUGIN_ROOT) not in output, f"plugin path leaked: {output}"

    # 同一缓存 + --offline 重跑核心 smoke
    for args in (
        ("--version",),
        ("init", str(offline_root)),
        ("check", "--root", str(offline_root)),
    ):
        offline_result = _run_uvx(runtime_root, cache_dir, tmp_path, *args, offline=True)
        assert offline_result.returncode == 0, (
            f"offline {args} failed: {offline_result.stderr}{offline_result.stdout}"
        )
        if args == ("--version",):
            assert offline_result.stdout.strip() == "0.3.0", offline_result.stdout
        for output in (offline_result.stdout, offline_result.stderr):
            assert str(REPO_ROOT) not in output, f"repo path leaked: {output}"
            assert str(PLUGIN_ROOT) not in output, f"plugin path leaked: {output}"

    # Draft 业务文件只在各自所选 root；conclusions.md 改为按需生成，init 不再创建。
    for root in (online_root, offline_root):
        assert (root / "index.md").exists()
        assert not (root / "conclusions.md").exists()
        assert (root / ".draft").exists()

    # uv 缓存中没有 bindings/、hook.jsonl、.draft/
    cache_bindings = list(cache_dir.rglob("bindings"))
    cache_logs = list(cache_dir.rglob("hook.jsonl"))
    cache_draft = list(cache_dir.rglob(".draft"))
    assert not cache_bindings, f"bindings in cache: {cache_bindings}"
    assert not cache_logs, f"hook.jsonl in cache: {cache_logs}"
    assert not cache_draft, f".draft in cache: {cache_draft}"
