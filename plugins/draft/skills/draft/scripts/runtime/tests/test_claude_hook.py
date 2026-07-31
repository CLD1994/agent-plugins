import io
import json
from pathlib import Path

import pytest

from draft.bindings import bind
from draft.claude_hook import main
from draft.lifecycle import initialize
from draft.models import Binding


@pytest.fixture
def skill_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill = tmp_path / "SKILL.md"
    skill.write_text("测试 skill 内容", encoding="utf-8")
    monkeypatch.setenv("DRAFT_SKILL_PATH", str(skill))
    return skill


@pytest.fixture
def bound_data_dir(tmp_path: Path, skill_file: Path) -> Path:
    from draft.bindings import skill_digest

    root = tmp_path / "topic"
    initialize(root)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bind(
        data_dir,
        Binding(
            session_id="abc123",
            root=root,
            last_page_hint=None,
            skill_name="draft",
            skill_digest=skill_digest(skill_file),
        ),
    )
    return data_dir


def run_hook(
    payload: dict[str, object],
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str]:
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = main(["--data-dir", str(data_dir)])
    return code, capsys.readouterr().out


def session_payload(source: str, session: str = "abc123") -> dict[str, object]:
    return {
        "session_id": session,
        "transcript_path": "/not/read/transcript.jsonl",
        "cwd": "/workspace",
        "hook_event_name": "SessionStart",
        "source": source,
    }


def test_compact_injects_short_rebind_envelope(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)

    assert code == 0
    envelope = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert json.loads(output)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "活动草稿根" in envelope
    assert "draft" in envelope
    assert "compact" in envelope
    assert "transcript" not in envelope.lower()


def test_unbound_session_is_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output = run_hook(session_payload("startup", "new"), tmp_path, monkeypatch, capsys)

    assert code == 0
    assert output == ""


def test_resume_and_clear_also_rebind(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for source in ("resume", "clear", "startup"):
        code, output = run_hook(session_payload(source), bound_data_dir, monkeypatch, capsys)
        assert code == 0
        envelope = json.loads(output)["hookSpecificOutput"]["additionalContext"]
        assert f"触发原因：{source}" in envelope


def test_repeated_invocation_is_idempotent(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _code, first = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)
    code, second = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)
    assert code == 0
    assert first == second


def test_invalid_json_is_silent(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert main(["--data-dir", str(bound_data_dir)]) == 0
    assert capsys.readouterr().out == ""


def test_wrong_event_name_is_silent(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = session_payload("compact")
    payload["hook_event_name"] = "PreCompact"
    code, output = run_hook(payload, bound_data_dir, monkeypatch, capsys)
    assert code == 0
    assert output == ""


def test_missing_session_id_is_silent(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = session_payload("compact")
    del payload["session_id"]
    code, output = run_hook(payload, bound_data_dir, monkeypatch, capsys)
    assert code == 0
    assert output == ""


def test_missing_bound_root_injects_diagnostic(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import shutil

    shutil.rmtree(bound_data_dir.parent / "topic")
    code, output = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)
    assert code == 0
    envelope = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "draft" in envelope
    assert "活动草稿根" not in envelope


def test_stale_page_hint_is_omitted(
    bound_data_dir: Path,
    skill_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from draft.bindings import skill_digest

    root = bound_data_dir.parent / "topic"
    (root / "pages/a.md").write_text(
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n", encoding="utf-8")
    bind(
        bound_data_dir,
        Binding(
            session_id="abc123",
            root=root,
            last_page_hint=Path("pages/a.md"),
            skill_name="draft",
            skill_digest=skill_digest(skill_file),
        ),
    )
    code, output = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)
    assert code == 0
    envelope = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "最后页面提示：pages/a.md" in envelope

    (root / "pages/a.md").unlink()
    code, output = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)
    assert code == 0
    envelope = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "最后页面提示" not in envelope


def test_digest_mismatch_injects_diagnostic(
    bound_data_dir: Path,
    skill_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    skill_file.write_text("已被替换的 skill 内容", encoding="utf-8")
    code, output = run_hook(session_payload("compact"), bound_data_dir, monkeypatch, capsys)
    assert code == 0
    envelope = json.loads(output)["hookSpecificOutput"]["additionalContext"]
    assert "draft" in envelope
    assert "活动草稿根" not in envelope


def test_oversized_stdin_is_rejected(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(" " * (64 * 1024 + 1)))
    assert main(["--data-dir", str(bound_data_dir)]) == 0
    assert capsys.readouterr().out == ""


def test_trailing_second_json_value_is_rejected(
    bound_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sys

    payload = json.dumps(session_payload("compact")) + "\n{}"
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert main(["--data-dir", str(bound_data_dir)]) == 0
    assert capsys.readouterr().out == ""
