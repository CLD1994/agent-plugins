# AGENTS.md

本文件适用于 `plugins/draft/` 子树。执行本子树任务前，先读取仓库根目录
`AGENTS.md`，再读取本文件。

## 测试布局

- 核心测试位于 `skills/draft/scripts/runtime/tests/`，插件适配层测试位于
  `scripts/tests/`；两侧都只使用 `unit/` 和 `integration/` 两个顶层分类。
- `network` 是集成测试的执行属性，不是第三类测试。它只标记依赖外部索引、
  依赖解析或下载、冷 uv 缓存的测试。
- 除解释器导入被测源码外，单元测试不得访问真实文件系统、启动子进程或修改
  进程级环境；涉及这些边界的用例必须放入 `integration/`。
- 核心和适配层测试必须分开运行，不得在一条 pytest 命令中同时收集两个测试树。

## 测试替身

- 本子树采用偏模拟派策略。测试替身必须受真实接口约束，并放在具有独立语义的
  协作者边界；不要把私有调用图复制为交互断言。
- 适配层单元测试可以替换适配层拥有的验证、持久化、输出和非确定性协作者；真实
  所有权、权限、符号链接、锁、原子写和清理失败必须保留集成测试。
- 适配层生产代码只依赖标准库且不得导入核心 `draft` 包；核心不得导入插件适配层。

## 测试命令

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime

uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q

uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'

uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```
