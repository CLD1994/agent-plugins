#!/bin/sh
# draft 插件平台 setup：按显式平台参数读取宿主数据目录并生成规范化启动器。
# Codex 只读取 PLUGIN_DATA；Claude Code 只读取 CLAUDE_PLUGIN_DATA。
set -eu

sq() {
    # 把参数安全地包进 shell 单引号
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

json_escape() {
    # 把参数编码为 JSON 字符串（含外层引号）
    printf '%s' "$1" | python3 -c 'import json,sys; sys.stdout.write(json.dumps(sys.stdin.read()))' 2>/dev/null \
        || printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/^/"/; s/$/"/'
}

PLATFORM=${1-}
case "$PLATFORM" in
    codex) DATA=${PLUGIN_DATA-} ;;
    claude) DATA=${CLAUDE_PLUGIN_DATA-} ;;
    *) echo "用法：setup.sh codex|claude" >&2; exit 2 ;;
esac

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
PLUGIN_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd -P)
RUNTIME="$PLUGIN_ROOT/runtime"
SKILL="$PLUGIN_ROOT/skills/draft/SKILL.md"

if [ -z "$DATA" ] || [ ! -d "$DATA" ] || [ ! -w "$DATA" ]; then
    echo "平台数据目录不可用" >&2
    exit 1
fi
DATA=$(CDPATH='' cd -- "$DATA" && pwd -P)

if ! uv --version >/dev/null 2>&1; then
    echo "未找到 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

mkdir -p "$DATA/logs" "$DATA/bin"

echo "==> 同步隔离环境（Python 3.14，按锁文件）"
UV_PROJECT_ENVIRONMENT="$DATA/venv" uv sync --project "$RUNTIME" --locked --python 3.14

VERSION=$(UV_PROJECT_ENVIRONMENT="$DATA/venv" uv run --project "$RUNTIME" --frozen --no-sync draft --version)
LOCK_DIGEST=$(sha256sum "$RUNTIME/uv.lock" | cut -d' ' -f1)
PYTHON_VERSION=$(UV_PROJECT_ENVIRONMENT="$DATA/venv" uv run --project "$RUNTIME" --frozen --no-sync python --version 2>&1 | awk '{print $2}')

cat > "$DATA/installed-runtime-manifest.json" <<EOF
{
  "runtime_version": "$VERSION",
  "lock_digest": "$LOCK_DIGEST",
  "python_version": "$PYTHON_VERSION",
  "runtime_path": $(json_escape "$RUNTIME")
}
EOF

cat > "$DATA/bin/draft" <<EOF
#!/bin/sh
export DRAFT_DATA=$(sq "$DATA")
export DRAFT_SKILL_PATH=$(sq "$SKILL")
export UV_PROJECT_ENVIRONMENT=$(sq "$DATA/venv")
exec uv run --project $(sq "$RUNTIME") --frozen --no-sync draft "\$@"
EOF
chmod +x "$DATA/bin/draft"

cat > "$DATA/bin/draft-hook-claude" <<EOF
#!/bin/sh
export DRAFT_DATA=$(sq "$DATA")
export DRAFT_SKILL_PATH=$(sq "$SKILL")
export UV_PROJECT_ENVIRONMENT=$(sq "$DATA/venv")
exec uv run --project $(sq "$RUNTIME") --frozen --no-sync \
  draft-hook-claude --data-dir $(sq "$DATA") "\$@"
EOF
chmod +x "$DATA/bin/draft-hook-claude"

"$DATA/bin/draft" --version >/dev/null

cat > "$DATA/setup-status.json" <<EOF
{
  "status": "ok",
  "runtime_version": "$VERSION",
  "platform": "$PLATFORM",
  "data_dir": $(json_escape "$DATA")
}
EOF

echo "环境就绪：$DATA（平台 $PLATFORM）"
