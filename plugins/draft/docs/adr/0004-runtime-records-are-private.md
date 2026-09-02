# 运行时耐久记录只供 Draft 使用

操作记录、进度、内部执行阶段和恢复现场只由 Draft 读取和写入，不构成供外部工具直接依赖的兼容接口；外部工具只能通过公开命令观察和改变 Draft。这样可以在不破坏外部调用方的前提下演进内部记录，并避免绕过恢复规则直接改写权威事实。恢复结果的公共投影由 [显式恢复与 RecoveryOutcome](../spec/runtime/recovery.md#显式恢复与-recoveryoutcome) owner 定义。
