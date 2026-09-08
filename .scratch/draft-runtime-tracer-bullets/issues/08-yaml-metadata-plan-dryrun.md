# 08: YAML 元数据变更先形成可验证预演

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 对一个合法的页面元数据变更请求，系统先生成可比较的变更前/变更后事实和稳定序列化结果；预演模式只展示计划，不写页面、操作记录、索引或临时材料。

Blocked by:
  - [01: 公共请求在任何副作用前被拒绝](01-request-validation.md)：提供公共请求校验边界。
  - [05: 页面读取正确区分 YAML 头部和正文](05-yaml-page-read.md)：提供已认证的页面事实。

- [ ] 只有通过头部认证的状态、说明和关系事实才能进入计划；无效头部不会被当作默认值或空关系。
- [ ] 头部字段按稳定顺序写回，YAML 头部结束分隔行之后的正文按原字节逐字保留；前后事实比较不依赖引号或显示行号的偶然写法。
- [ ] 关系的新增、修改、移除和配对状态按设计规格的完整分类处理，不把未知状态静默当作成功。
- [ ] 私有计划只形成变更前后事实、稳定序列化和已认证的阻止事实，供运行时按现行结果合同处理；不自行发现运行时记录或探测根锁，不写页面、操作记录、权威资料、索引或临时材料。
- [ ] 如果实现触及尚未定义的“链接在原页面中的旧位置如何对应到移动后页面中的新位置”或诊断身份，停止并报告，不自行选择规则。

## 公开接入与关闭

本票按认证元数据上的纯计划、稳定序列化及上述验收关闭，不等待 12 的公开组合。公开预演使用同一私有计划，由 01、04 提供请求、准入、并发和错误边界，12 验证核心与命令行接口对其导出的已登记公开结果一致：成功使用既有 `OperationOutcome`，阻止事实使用对应的既有 `ErrorEnvelope`，字段按运行时结果设计限定，私有计划和变更前后事实不进入公开结果。

公开预演的锁行为按运行时设计区分记录状态：无已发现记录时不探测或取得根锁；已发现记录时最多一次非阻塞探测，仍然零写。这项完整分类由 04 负责，私有计划不承担探测或隐式恢复。

**停止条件:** 私有计划需要取得运行期根锁、创建操作记录或决定一个尚未确认的首条业务操作时停止；这些属于后续票据。
