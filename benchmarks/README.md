# Benchmarks 实验入口说明

本目录包含用于检测的 Circom 电路入口、依赖库、辅助文件和额外的已知漏洞样例。
下表列出的 **17 个文件夹，共包含 258 个实验入口**，各项目的入口数量与论文
[Practical Security Analysis of Zero-Knowledge Proof Circuits](https://www.usenix.org/system/files/usenixsecurity24-wen_1.pdf#page=12)
第 5.1 节、表 4 的统计一致。

## 文件夹与入口数量

以下路径均相对于 `benchmarks/`。只统计各文件夹**直接包含的 `.circom` 文件**，
不递归统计其子文件夹中的文件。

| 文件夹 | 实验入口数量 |
|---|---:|
| [aes-circom](aes-circom/) | 8 |
| [circom-ecdsa](circom-ecdsa/) | 16 |
| [circom-matrix](circom-matrix/) | 12 |
| [circom-ml](circom-ml/) | 12 |
| [circom-pairing](circom-pairing/) | 2 |
| [circomlib-cff5ab6](circomlib-cff5ab6/) | 67 |
| [darkforest-eth-9033eaf](darkforest-eth-9033eaf/) | 9 |
| [ed25519-099d19c](ed25519-099d19c/) | 27 |
| [hermez-network-9a696e3-fixed](hermez-network-9a696e3-fixed/) | 11 |
| [hydra-2010a65](hydra-2010a65/) | 3 |
| [iden3-core-56a08f9](iden3-core-56a08f9/) | 28 |
| [internal](internal/) | 7 |
| [keccak256-circom-af3e898](keccak256-circom-af3e898/) | 14 |
| [maci-9b1b1a6-fixed](maci-9b1b1a6-fixed/) | 34 |
| [semaphore-0f0fc95](semaphore-0f0fc95/) | 1 |
| [zk-group-sigs-1337689-fixed](zk-group-sigs-1337689-fixed/) | 3 |
| [zk-SQL-4c3626d](zk-SQL-4c3626d/) | 4 |
| **合计** | **258** |

统计核对日期：2026-09-28。新增或删除实验入口后，请同步更新本表。

## 什么是实验入口

上表统计的每个入口文件都包含 `component main`，用于指定要实例化和检测的电路。
一个入口可以通过 `include` 引用多个库文件，库文件还可以继续引用其他文件。
因此，一个实验电路可能涉及多个 `.circom` 源码文件；递归统计所有源码文件，
得到的数量会大于 258。

## 不计入这 258 个入口的文件

- `libs/`：依赖库，以及库附带的测试和示例文件。**请保留此目录**，很多入口需要引用其中的代码。
- `knownbug/`：额外的 11 个已知漏洞样例入口。
- `zk-SQL-4c3626d/utils/`：`hashTable.circom` 和 `utils.circom` 两个辅助文件；该项目只统计其顶层的 4 个入口。
- `cirverify_*/`：临时目录中的文件。

选择实验对象时，以表中的文件夹和统计范围为准。依赖库内也可能包含
`component main` 测试入口，不能把所有含 `main` 的文件都算入这套 258 个实验电路。

## Circom 检测能力与准确率

CirVerify 当前对 Circom **源代码**进行静态分析，不是对所有电路功能做形式化证明。
检测器目前包含 9 类规则：未约束输出、未约束组件输入、数据流与约束不一致、
未使用组件输出、未使用信号、类型不匹配、赋值误用、除零风险和非确定性数据流。
一条规则没有发现问题时记为 `Success`，发现至少一条问题时记为 `Warning`。

### 评测数据和标注

- 正常 benchmark 包含上表的 258 个入口；`knownbug/` 另有 11 个已知漏洞样例，
  因此 ZKAP 对照表共包含 269 条记录。
- 对照数据位于 [`src/test/ZKAP.csv`](../src/test/ZKAP.csv)。该文件有 269 条记录，
  其中总体 `safe/unsafe` 标注为 82/69，另有 118 条总体标注为空；ZKAP 对照列为
  `safe/unsafe/timeout`，数量为 85/172/12。空标注和 `timeout` 不能当作正常的
  `safe` 或 `unsafe` 样本计入准确率。
- `libs/` 中的依赖源码不作为独立样本。一个入口可能递归引用多个依赖文件，
  所以“入口数量”与“源码文件数量”不是同一个统计口径。

### 已完成的小规模核对

2026-10-09 在当前开发环境中运行了 `knownbug/` 下的 11 个样例，并将每个样例的
“预期漏洞类别”与 CirVerify 实际报告的规则类别逐项比较。这个结果只表示规则级
命中情况，不表示 269 条数据集的最终准确率。

| 指标 | 数值 |
|---|---:|
| True Positive (TP) | 16 |
| False Positive (FP) | 4 |
| False Negative (FN) | 0 |
| True Negative (TN) | 0 |
| Precision | 80.00% |
| Recall | 100.00% |
| F1-score | 88.89% |
| Accuracy | 80.00%* |

`Accuracy` 一栏是按 `(TP + TN) / (TP + TN + FP + FN)` 得出的机械值。由于这 11 个
样例只包含漏洞类别，没有构造真负例，`TN=0`，因此这个 80% **不能作为总体准确率**
或项目验收数据。正式报告应同时包含安全样例，才能解释 Accuracy、误报率和漏报率。

这次核对的总耗时约为 2.590 秒，平均每个入口约 0.235 秒，最慢入口约 2.407 秒。
这是小规模样例的单次开发机测量，没有记录完整硬件和软件环境，也不能替代 269 个
入口的平均验证时间验收。

### 全量准确率状态

当前仓库**没有提交 269 个入口的完整检测结果和正式准确率表**，因此暂不声称
CirVerify 在完整 benchmark 上的 Accuracy、Precision、Recall 或 F1-score。现有
评测脚本 [`src/test/evaluate.py`](../src/test/evaluate.py) 仍读取相对路径
`zkap.txt`，而仓库实际提供的是 `ZKAP.csv`；同时批量脚本
[`src/test/test_cases.py`](../src/test/test_cases.py) 的 `done_dir` 会跳过主要
benchmark 目录，`file_not_check` 还会排除部分超时或递归较深的样例。因此，直接
运行现有脚本不能形成完整、可复现的准确率结论。

正式全量评测需要固定数据集版本和运行环境，逐个记录每个入口的成功、失败、超时、
耗时及 9 类规则结果，然后分别报告：

1. **文件级结果**：是否发现至少一项问题，与明确的 `safe/unsafe` 标注比较；
2. **规则级结果**：每一种检测规则分别统计 TP、TN、FP、FN、Precision、Recall 和 F1；
3. **不可判定样本**：将解析失败、超时和缺少标注单独列出，不能默认算作安全或正确；
4. **性能结果**：给出样本数量、平均值、最大值、超时数量以及硬件和软件版本。

即使全量评测没有 Warning，也只能说明这些规则在该数据集上没有报告问题，不能证明
电路满足任意业务规格或不存在规则覆盖范围之外的漏洞。
