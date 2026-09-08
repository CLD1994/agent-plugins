# 01: 公共请求在任何副作用前被拒绝

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 所有公开命令和公共调用先检查请求的类型、字段、值和组合关系；请求不合法时，在选择或读取 Draft 根目录、创建计划、维护索引、调用外部协作者之前返回统一的请求错误。

- [ ] 普通操作、回收区操作（Trash）、恢复（Restore）、查询、通知、恢复和初始化的非法输入都在第一次持久副作用前被拒绝。
- [ ] 命令行接口（CLI）与公共调用对同一无效语义给出相同的机器分类和逻辑字段定位；命令行专属语法仍遵守接口契约。
- [ ] 同时存在无效请求和无效根目录时，结果仍是请求错误，且根目录读取、计划、自动阶段、维护、恢复和外部调用均未发生。
- [ ] 合法请求保留正向路径；测试验证结果和零副作用边界，而不是通过拒绝所有输入来通过测试。
- [ ] 核心与适配层的单元、集成和静态检查分别通过。

**停止条件:** 需要改变领域资格、增加公开字段或由本票据猜测未定义的业务语义时停止并报告。
