# 采用 `active lifecycle catalog` 作为发现权威

`active lifecycle catalog` 是正常生命周期发现的架构权威，不是 Draft 领域对象或某个文件目录；终态审计记录继续保留用于审计，但不参与正常扫描，`schedule` 由其派生并可重建。该决定固定了正常性能与完整审计之间的真实取舍，也使发现边界难以逆转且不直观，因而记录为架构决策。
