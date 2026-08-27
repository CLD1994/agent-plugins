# 冻结 catalog 与 index records

Type: prototype

Blocked by: 03 建立 durable authority 与恢复所有权模型；10 冻结 canonical encoding 与共享标量

## Question

catalog transition、active authority、schedule projection、index transition intent、epoch、head authority、cache projection 与 scan token 的完整 schema 是什么？怎样编码 predecessor/next generation、owner、source operation、content digest、staging 和 commit proof，使 normal path 不扫描 terminal progress、authority 与 projection 永不混淆、同次最终 scan 和 matching epoch settlement 可机械验证？
