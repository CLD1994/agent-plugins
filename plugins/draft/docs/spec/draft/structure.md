# 结构操作

## 行为合同

`move SOURCE... TARGET` 支持多来源、文件来源和目录来源；以 `.md` 结尾的 target 是单文件精确目标，其他 target 是前缀目标。目录来源只移动普通 Markdown，非 Markdown 原地保留。未受管理 Markdown 只能在 candidate 根中页面纳管。

`promote` 只接受与叶页面同父的 sibling，保持原导航相对顺序并按保名子树映射；跨父重组先显式 `move`。`collapse` 只处理空的非根 hub。所有结构操作使用同一链接闭包和 mapping，整批成功或零写。

## 失败与恢复

重复枚举、目标碰撞、路径逃逸、父页面缺失、链接事实不可认证或目录清理无法证明时零写。自动 collapse 必须在 after-tree 中反复收敛，不能留下空 hub。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
