# 迁移参考

本参考描述把旧布局讨论迁移为 draft 自包含草稿的规则。迁移是独立用例：程序不提供
来源格式迁移器，也不接受来源路径；Agent 在固定候选根中读取并重写来源内容，复用
普通 Draft CLI 完成已经确定的机械变换，最后 `adopt` 把同一个根推进为正式草稿。
通用格式规则见 [draft-format.md](draft-format.md)。

## 迁移流程

1. `draft init --candidate TARGET`——创建固定候选根（`.draft/draft.json` 标记为
   `root_state=candidate`）。
2. Agent 读取只读来源，把内容逐页建立到候选根；可直接编辑标题、正文和正文链接。
3. 候选根中的批量导入或重写稳定后，可选运行
   `draft notify files-changed --root TARGET`；notification 不接收路径、变更类型、
   rename map 或 stdin 清单，也不是 fresh 或 recovery 的强制前置。
4. 连续调用普通结构命令（`create`、`move`、`promote`、`collapse`、`status set`、
   `relation add/remove`、`trash`/`restore`、`generate-conclusions`），命令间可继续
   手工编辑；候选命令只在自身操作所需事实缺失或自有不变量被违反时拒绝，不要求
   整个候选根无错。
5. `draft check --count-only` 或 `draft check --code <CODE>` 选择检查点，按诊断类别
   批量修复。
6. 完整候选检查 `errors` 为空后才可登记；扫描可以机会式维护内部 link-index journal、
   制品和 flag，但不修改迁移内容、marker 或回收生命周期。结构写命令的 `--dry-run`
   使用同一事实与计划边界，不产生 journal、revision、索引制品或 flag。
7. `draft adopt` 原子推进为 `root_state=registered`。索引性能降级 warning 不阻止登记；
   页面 errors、journal/索引制品矛盾、耐久失败或 recovery pending 等内部状态错误会
   阻止登记。
8. `draft check`（registered 模式）确认最终可观察状态。
9. 源目录保持原样，不修改；迁移前后可用来源摘要确认字节一致。

### 注意事项

- **有子页的旧平级页面必须升格为枢纽目录**
- 迁移期间只有一个工作根（同一候选根），不存在第二工作根、发布根或临时候选副本。
- 路径、机器块、正式关系和父导航由 CLI 维护；Agent 不手工修改这些部分。
- 候选根可以暂时不满足完整 Draft 协议；`adopt` 之前必须通过完整候选检查。
- 建立候选页面前把非 UTF-8 旧稿转为 UTF-8；现成手段示例 `iconv -f GB18030 -t UTF-8`；CLI 不检测、不转换，未转码文件会以 `structure.dirty` 点名阻碍。
- `draft recover --root TARGET` 只按 journal 证据收敛 recovery pending operation，不推进 retained trash 的到期、移交或清理；stale 或索引降级由 notification、`check` 或相关链接查询机会式维护。

## 树形迁移示例

把整棵旧稿目录复制进候选根后，一条 `move` 命令完成整树提升与全部可证明链接改写：

`cp -r` 进根 → `draft move legacy-notes pages` → `draft check` 按类修复 → `draft adopt`

## 机器区域改写

旧页面机器块中的注解行必须移出机器块，保留为正文引用句或普通段落；机器块只保留被允许的标签。

## 链接重写

- 机器区域：`父主题` 指向新物理父；`产出` 指向新位置的根级 `conclusions.md`；`依赖` 按新路径重算。
- 正文：指向受管理页面的链接按新相对路径重算；代码块内的链接不参与检查，无需改写。

## 通配与平台展开

- **通配注意**：根部 `*.md` 展开会包含 `index.md` 并命中 `structure.protected`，命令点名拒绝整批；排除后重试（显式枚举或 shell extglob）。
- **平台展开**：bash/PowerShell 原生展开通配；不展开的环境（如 cmd.exe）收到字面模式的 `structure.unknown_page`，改用显式枚举。
