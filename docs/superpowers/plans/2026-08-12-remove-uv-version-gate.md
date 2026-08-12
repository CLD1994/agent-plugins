# 删除 uv 精确版本门禁实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让缓存隔离集成测试直接验证 uv/uvx 的真实行为，不再按版本字符串提前失败。

**Architecture:** 保留缓存复用、并发发布隔离和冲突工具隔离测试；删除只承担版本白名单职责的常量、辅助函数、自动 fixture 和测试。模块说明同步改为行为契约。

**Tech Stack:** Python 3.12、pytest、uv/uvx

## Global Constraints

- 不放宽任何缓存复用或隔离行为断言。
- 不把版本不匹配改成 skip、warning 或 xfail。
- 不修改生产代码。

---

### Task 1: 删除精确版本门禁

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cache_isolation.py`

**Interfaces:**
- Consumes: PATH 中可执行的 `uv` 和 `uvx`。
- Produces: 不受版本字符串限制、直接验证缓存行为的集成测试模块。

- [x] **Step 1: 确认 RED**

运行当前模块，确认它在 uv/uvx 非 0.12.1 时于模块 fixture 阶段失败，真实行为测试未执行。

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cache_isolation.py -q
```

- [x] **Step 2: 删除版本白名单**

删除 `SUPPORTED_UV_VERSION`、`parse_uv_version`、`assert_supported_uv_versions`、`uv_version_gate` 和 `test_version_gate_rejects_unsupported_versions`，并从模块说明移除精确版本声明。

- [x] **Step 3: 验证 GREEN**

重新运行该模块；预期所有缓存行为测试实际执行并通过，或以具体行为断言报告真实失败。

- [x] **Step 4: 运行相关质量门禁**

运行核心单元测试、核心非联网与联网集成测试、Pyright、Ruff 和 `uv lock --check`。
