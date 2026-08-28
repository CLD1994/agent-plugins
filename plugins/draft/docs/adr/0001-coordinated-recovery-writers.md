# 受协调 writer 的显式恢复边界

Draft 只在遵守共同协议的受协调 writer 之间提供串行化。运行期 root lock、durable authority commit 和显式 `recover()` 共同保证未完成操作不会被普通入口猜测为成功。该决定解释并发与恢复边界；具体行为见 [运行时恢复规格](../spec/runtime/recovery.md)。

## 放弃的方向

不把任意本地进程、目录存在或 terminal projection 当作恢复依据，也不承诺防护绕过协议的同权限进程。
