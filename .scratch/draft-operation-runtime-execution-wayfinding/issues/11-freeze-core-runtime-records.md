# 冻结 init、root lock 与 operation records

Type: prototype

Blocked by: 03 建立 durable authority 与恢复所有权模型；10 冻结 canonical encoding 与共享标量

## Question

InitDescriptor、marker expectation、runtime lock record、operation manifest、progress event、owned artifact、cleanup authorization 与 completion proof 的完整 schema 是什么？字段怎样绑定 request、before/after closure、holder、predecessor、digest 和恢复方向，使 marker-ready cleanup、manifest-before-artifact、content-committed 后仅前向和最终 release 都可从持久事实机械证明？
