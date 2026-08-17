#!/usr/bin/env bash
# 适配层唯一权威质量门禁入口：cwd 无关。
#
# 从 git 解析绝对仓库根，构造绝对 RUNTIME，并固定到适配层目录运行 ruff 与 pyright
# （ruff.toml 的 src=["."] 相对该固定 cwd 自动发现，从仓库根/任意子目录调用结果一致）。
# 不在仓库、runtime 项目缺失、无 Python 文件或任一工具失败时非零退出；
# 绝不出现"未检查任何文件却 All checks passed"的假绿。
#
# 计划 Task 14、子树 AGENTS、CI 与完成报告都调用本入口，不得复制多份 shell 片段。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 从调用方 cwd 经 git 解析仓库根：仓库内任意子目录调用都得到同一绝对根；
# 仓库外调用稳定非零退出。
if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    echo "error: 不在 git 仓库内，无法解析仓库根" >&2
    exit 1
fi

RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
if [ ! -d "$RUNTIME" ]; then
    echo "error: runtime 项目缺失（$RUNTIME）" >&2
    exit 1
fi

if ! find "$SCRIPT_DIR/adapter" "$SCRIPT_DIR/tests" -name '*.py' 2>/dev/null | grep -q .; then
    echo "error: 未发现任何适配层 Python 文件（$SCRIPT_DIR/adapter, $SCRIPT_DIR/tests）" >&2
    exit 1
fi

cd "$SCRIPT_DIR"
uv run --project "$RUNTIME" --locked ruff format --check .
uv run --project "$RUNTIME" --locked ruff check .
uv run --project "$RUNTIME" --locked pyright -p "$SCRIPT_DIR/pyrightconfig.json"

echo "适配层质量门禁全部通过（cwd 无关入口）。"
