# 综合 PlatformAdapter 可观察合同

Type: prototype

Blocked by: 03 建立 durable authority 与恢复所有权模型；04 研究 Linux 平台能力证据；05 研究 macOS 平台能力证据；06 研究 Windows 平台能力证据

## Question

综合三平台证据和 root-lock 所有权模型后，`PlatformAdapter` 的最小内部方法协议是什么？怎样表达 capability probe、qualified filesystem、稳定 root-lock resource、non-blocking probe、no-follow path walk、identity/digest token、create-only publish、exact replace、file/directory durability 与 worker termination，并把不确定结果映射到统一且可诊断的错误分类而不把 native 算法变成公共协议？
