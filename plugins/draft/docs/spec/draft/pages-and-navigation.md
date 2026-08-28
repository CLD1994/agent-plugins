# 页面与导航

## 行为合同

根唯一为 `index.md`；普通叶页面位于 `pages/**/*.md`，枢纽页面为 `pages/<topic>/index.md`。`pages/index.md` 非法，不能由 create 或页面纳管产生。根无子页时仍保留唯一空 `## 子主题`。

父关系与根导航必须一致；直接子页面在导航中恰出现一次。结构操作由同一 before-to-after mapping 更新路径、父关系和导航。

## 失败与恢复

重复、错指、双表示、空非根枢纽或不可解析导航使相关检查失败；结构写在任何事实不可认证时零写。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
