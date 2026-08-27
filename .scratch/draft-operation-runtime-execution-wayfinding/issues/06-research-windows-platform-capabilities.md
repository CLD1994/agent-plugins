# 研究 Windows 平台能力证据

Type: research

Blocked by: None

## Question

基于 Windows、Python 和本地文件系统的一手资料，哪些可用机制能够证明逐节点 no-follow、create-only/no-replace 发布、记录路径缺失时仍稳定的 live OS root lock、文件与目录 durability、`replace_exact_regular` 的 identity/digest 条件，以及可终止隔离调用的 hard timeout？哪些 NTFS/ReFS、卷、sharing/reparse-point 或错误结果必须归为 unsupported、external change、corrupt、durability failure 或 unknown，哪些结论仍需真实平台实验？
