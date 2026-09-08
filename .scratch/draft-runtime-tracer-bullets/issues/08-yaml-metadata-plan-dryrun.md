# 08: YAML 元数据变更先形成可验证预演

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 对一个合法的页面元数据变更请求，系统先生成可比较的变更前/变更后事实和稳定序列化结果；预演模式只展示计划，不写页面、操作记录、索引或临时材料。

Blocked by:
  - [01: 公共请求在任何副作用前被拒绝](01-request-validation.md)：提供公共请求校验边界。
  - [05: 页面读取正确区分 YAML 头部和正文](05-yaml-page-read.md)：提供已认证的页面事实。

- [ ] 只有通过头部认证的状态、说明和关系事实才能进入计划；无效头部不会被当作默认值或空关系。
- [ ] 头部字段按稳定顺序写回，正文结束行之后的字节逐字保留；前后事实比较不依赖引号或显示行号的偶然写法。
- [ ] 关系的新增、修改、移除和配对状态按设计规格的完整分类处理，不把未知状态静默当作成功。
- [ ] 预演零持久写；核心调用和命令行接口输出都能说明将改变的事实与阻止原因。
- [ ] 如果实现触及尚未定义的“链接在原页面中的旧位置如何对应到移动后页面中的新位置”或诊断身份，停止并报告，不自行选择规则。

**停止条件:** 需要取得运行期根锁、创建操作记录或决定一个尚未确认的首条业务操作时停止；这些属于后续票据。
