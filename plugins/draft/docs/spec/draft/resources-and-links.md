# 资源与链接

## 资源身份与链接

`D-RESOURCES` 定义结构操作和查询可以认证的链接事实。它不把链接观察变成正式关系，
也不为 runtime、CLI 或 `ReadOutcome` 发明字段 ABI。

### 输入、输出与状态维度

- 输入是 UTF-8 Markdown 字节、CommonMark 链接 span、链接所在 source、相对根路径和
  可认证的 before-to-after mapping。
- 输出是 `ResourceIdentity`、独立的存在性 observation、源类型和可证明的入站/出站
  链接事实，或可诊断失败。identity 与 observation 不能折叠：词法身份可认证时，目标
  缺失不自动成为闭包错误。
- `ResourceIdentity` 的领域类别为 `page`、`local`、`external`；observation 的状态为
  `present`、`missing`、`unavailable`、`unchecked`、`invalid`。公开枚举拼写和输出字段
  尚未获批，以上名称仅表述领域分类，不构成 ABI。

| 独立维度 | 允许状态 | 对行为的影响 |
| --- | --- | --- |
| 编码与语法 | UTF-8 且 CommonMark span 可提取、不能提取 | 不能提取时需要该事实的结构写零写。 |
| source kind | 受管理页面、未受管理 Markdown、其他 Markdown 来源 | 查询和闭包保留 source kind，不把未受管理来源伪装成页面。 |
| 目标身份 | `page`、`local`、`external`、未决 | 只有可证明身份可参与对应的改写或观察。 |
| 目标 observation | present、missing、unavailable、unchecked、invalid | 只描述观察结果，不反向改变 identity。 |
| fragment | 无、可解析 fragment、不可认证 fragment | mapping 改写路径时保留可认证 fragment；不能认证时停止相关写。 |

### 链接闭包、转换与副作用

结构操作的链接事实闭包覆盖根内全部可判定 Markdown，而不是仅覆盖受管理页面。对每个
纳入闭包的 CommonMark span，实施者必须能将 source bytes、source kind、原目标、
fragment 和 mapping 后目标一一对应。可证明 mapping 时，结构操作同一逻辑提交中更新
出站、入站和同批交叉链接；绝对本地链接的原文保留规则、具体 span 序列化及公开
`changed_paths` ABI 不在本页补定。

| 链接事实 | mapping / 认证结果 | 允许结果 |
| --- | --- | --- |
| UTF-8、CommonMark span、source kind 与 target identity 均可认证 | before-to-after mapping 可证明 | 将该 span 的路径目标按 mapping 更新，并保留 fragment；同批事实同时提交。 |
| identity 可认证，observation 为 missing | mapping 不依赖目标存在性 | 保留可认证 identity；不得把 missing 单独变成闭包失败。 |
| span、UTF-8、source 对齐、路径分类、fragment 或 mapping 任一项不可认证 | 任意 | 在任何结构写前失败，零写。 |
| 根内 Markdown 无法判定 | 闭包不完整 | 阻止结构写；不得以 lower-bound 集合继续。 |

正文中指向 `.draft/` 的链接 identity 分类是 `unresolved`：尚无唯一签署合同决定它是否
属于 `local/draft`、独立受保护事实或其他分类。本页既不把它定为 `local/draft`，也不把它
定为 no-identity；任何操作若必须依赖该分类，必须停止并零写。直接把 `.draft` 内路径作为
结构操作 operand 的保留路径拒绝规则，与正文链接的 identity 分类是两个问题，不能相互替代。

### 失败、零写、恢复与验证

- 结构写在完整闭包、mapping 和所有需改写 span 均认证前保持零写；不能按 basename、
  文本相似度或未受管理文件的名称猜目标。
- 提交后耐久不确定时，由 runtime 保留 recovery 现场；重试前重新读取所有闭包事实，
  不以缓存、索引或旧 observation 代替认证。
- 验证 UTF-8、CommonMark、fragment、managed/unmanaged source、missing identity、无法
  分类的 `.draft` 正文链接，以及任一闭包节点不可判定的零写反例。

## 所有权

`D-RESOURCE-OWNERSHIP` 定义页面子树、结构操作和 Trash/Restore 对物理资源的窄边界。

### 输入、输出与不变量

- 输入是受管理页面子树、相邻非 Markdown 节点、目录物理状态、操作的页面 mapping 与
  Trash/Restore 所需的已授权事实。
- 输出只说明受管理 Markdown 的领域所有权、可证明的目录清理决定或拒绝；不得借此输出
  附件清单、retained facts、计数、摘要或续读协议。
- 页面子树只拥有受管理 Markdown。图片、附件、普通文件、未知节点和目录不会因物理
  邻接而转移到页面所有权；已认证为 retained 的目录节点一律原位保留。这里的 retained 规则仅适用于
  `structure.md#目录来源` 已将目录节点认证为 retained 的分区；非 Markdown、附件或无法
  分类节点若作为 file source operand，属于 `D-MOVE` 矩阵外输入，必须写前拒绝、零写。

| 物理事实 | 操作 | 领域结果 / 副作用 |
| --- | --- | --- |
| 受管理 Markdown 且 mapping 可证明 | 结构移动、Trash、Restore | 只处理该 Markdown 和已授权的机器区、身份/摘要、关系/导航事实。 |
| 目录中已由 `D-MOVE` 认证为 retained 的非 Markdown、附件或 unknown 节点 | 目录 move 或页面所有权操作 | 原位保留；不得纳入页面包、取得所有权、mapping、`changed_paths` 或成功输出。 |
| 非 Markdown、附件或无法分类节点作为 file source，或目录节点事实不可信 | `D-MOVE` source 分类 | 写前拒绝、零写；不得将节点降级为 retained 或创建页面所有权。 |
| candidate 根中的 unmanaged Markdown | `D-MOVE` candidate 纳管分支，且 mapping/闭包/parent/nav 可证明 | 只按同一 `move` mapping 取得页面所有权；不由本节补齐页面协议字段。 |
| registered 根中的 unmanaged source（directory 的 `file`/`prefix` target 均包括） | `D-MOVE` | 拒绝、零写；不得创建或改写页面、导航、catalog/head 或日志副作用。 |
| root state 为 unknown、mixed 或不可认证，或 mapping/链接闭包不可认证 | `D-MOVE` 结构/所有权路径 | 写前拒绝、零写并保留诊断；不得把未知根或不完整闭包解释为 candidate、registered 或空集合。 |
| 目录可证明物理为空 | 结构或恢复后清理 | 可按已证明的结构清理规则移除目录；具体平台调用由 runtime 处理。 |
| 目录非空，或节点已认证为 retained/unknown | 目录清理 | 保留目录；不以“页面已移动”推断可删除。 |
| 目录边界、节点分类或物理空性无法认证 | `D-MOVE` 的目录 move 或其目录清理路径 | 写前拒绝、零写并保留诊断；不得先移动页面再决定是否保留目录。 |
| `.draft` 内部路径 | 任一页面所有权操作 | 不属于页面子树；不能作为页面 source/target 或被纳入页面所有权。 |

目录移除、Trash/Restore 的授权清单、handoff 和 durable cleanup 只有在对应 owner 证明后才
能发生；物理目录存在本身不证明 authority。任何不完整事实都在写前拒绝并保留现场。

### 转换、失败、恢复与验证

所有权成功转换的提交点与触发它的结构或 Trash/Restore 逻辑操作相同，不能独立创建第二
operation。若提交后耐久性不确定，保留现场并等待显式 recovery；重试重新证明物理空目录
和授权事实。

验证目录中混有 Markdown、图片、附件、未知节点、空目录和 `.draft` 路径的组合：只有受
管理 Markdown 可进入领域 after-state，且只有已证明物理为空的目录可被清理。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
