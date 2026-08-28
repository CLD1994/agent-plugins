# 迁移与原子切换

## 行为合同

迁移按 `discovered → classified → extracted → reconciled → drafted → verified → signed → cut-over` 推进。迁移单位是可独立验证的行为合同，不是整份文件。

Commit A 保存 provenance 与分类证据；Commit B 建立 staged 规格页；Commit C 同时切换路由、消费者、tracker 并退役旧 authority。A、B、C 必须作为不可拆分 series 获 signer 批准，禁止 squash、rebase 或部分合入。

## 失败与恢复

任一来源冲突、未定义的可观察行为、未闭合消费者或不可解析链接都阻止 cut-over。已批准 diff 发生变化时必须重新验证并签署。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
