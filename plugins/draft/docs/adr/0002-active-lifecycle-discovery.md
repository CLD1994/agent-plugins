# 采用 active lifecycle catalog 作为发现权威

正常生命周期发现只读取 active catalog；schedule、head、cache 和审计记录是派生或审计资料。这样可以维持正常路径的确定性，同时把完整终态审计与业务发现分开。具体生命周期行为见 [运行时生命周期规格](../spec/runtime/lifecycle-and-handoff.md)。

## 放弃的方向

不扫描 terminal operation progress 来猜测新的生命周期候选，也不让派生索引取代 active catalog。
