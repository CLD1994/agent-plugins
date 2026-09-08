# 04: 只读、预演和通知在并发写入下保持安全

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 读取、预演和文件变更通知在另一个进程写入同一根目录时仍遵守各自的只读边界。读取不因为无法写入而伪造结果；预演不写持久状态；通知在忙碌时只返回允许的最小成功结果，并正确映射机器错误和命令行接口退出状态。

Blocked by:
  - [02: 写入和恢复共享根目录排他锁](02-root-lock-admission.md)：提供根锁竞争与统一错误事实，供只读、预演和通知接入。

- [ ] 普通读取本身不创建操作记录、不清理现场；机会式索引维护取锁失败时跳过写入并继续投影稳定读取结果。
- [ ] 预演在没有待恢复记录时不探测或取得运行期锁；发现待恢复记录后最多进行规范允许的一次探测，并且始终零写。
- [ ] 文件变更通知在活动写入者存在时不进行维护写、不安排隐式恢复或重试，并返回通知契约允许的最小结果。
- [ ] 活动占用、待恢复、平台不支持、损坏和保存不确定分别映射到已登记的程序错误码；运行时错误的命令行接口（CLI）退出状态与核心调用一致。
- [ ] 通过另一进程持锁、读取期间变化和维护写失败的集成测试，并证明所有短暂取得的锁在返回前释放。

**停止条件:** 需要让只读入口隐式恢复、增加公开字段或改变通知业务语义时停止并报告。
