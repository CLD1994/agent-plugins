# 受协调 writer 的显式恢复边界

Draft 只在遵守共同协议的受协调 writer 之间提供串行化。运行期 root lock、durable authority commit 和显式 `recover()` 共同保证未完成操作不会被普通入口猜测为成功。

恢复动作由已认证证据决定：完整 after authority 只缺匹配进度时补写进度；临时材料只有在能够证明不属于任何当前、待处理或已认证操作时才清理；证据不足时保留现场并报错。该决定解释并发与恢复边界；具体状态分区见 [运行时恢复规格](../spec/runtime/recovery.md)。

## 放弃的方向

不把任意本地进程、目录存在、修改时间或 terminal projection 当作恢复依据，也不承诺防护绕过协议的同权限进程。
