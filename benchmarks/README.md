# Benchmarks 实验入口说明

本目录包含用于检测的 Circom 电路入口、依赖库、辅助文件和额外的已知漏洞样例。
下表列出的 **17 个文件夹，共包含 258 个实验入口**，各项目的入口数量与论文
[Practical Security Analysis of Zero-Knowledge Proof Circuits](https://www.usenix.org/system/files/usenixsecurity24-wen_1.pdf#page=12)
第 5.1 节、表 4 的统计一致。

## 集中检测入口

[`entries/`](entries/) 原有 258 个入口，按本机检测耗时筛选后保留 **234 个 `.circom` 入口**，已移除 24 个耗时超过 150 秒的入口。批量检测时使用此目录。
文件命名为 `项目名__原文件名.circom`，避免不同项目的同名文件相互覆盖。
这些文件是下表原始入口的副本，已调整 `include` 的相对路径；原项目目录保留以兼容现有脚本。
[`entries/manifest.json`](entries/manifest.json) 记录每个集中入口与原始文件的对应关系。

运行时请保留整个 `benchmarks/` 目录结构：入口仍引用 `libs/` 和原项目中的辅助源码，
单独复制或上传 `entries/` 不能携带全部依赖。原始入口更新后，需要同步更新对应副本。
`entries/` 是原始样本经耗时筛选后的集中副本，不应与下表原始目录重复计数。

逐项耗时、超时删除记录和检测失败信息见 [`entries/timing_results.json`](entries/timing_results.json)。
检测失败的入口保留；超时仅表示本机运行超过 150 秒，不代表电路有漏洞。

R1CS 的唯一性检测需要保留输入线，重新编译请使用 `--r1cs --O0`。
`scripts/compile_benchmark_r1cs.py` 已默认使用此参数；29 个因 `--O1` 优化而丢失
输入布局的现有文件已从原入口重新生成。`r1cs/manifest.json` 中逐条 `flags`
优先于顶层默认参数，其余文件仍保留原编译产物。本次原文件备份位于
`build/benchmark-audit/original-o1/`，完整检测记录位于 `build/benchmark-audit/`。

## 可重复的全目录检测

当前 `entries/` 的 234 个 `.circom` 和 `r1cs/` 的 207 个 `.r1cs` 共计 **441 个检测输入**，
源码与编译产物分别计数。这两个目录另外的 9 个清单、统计等元数据文件不作为电路输入检测。
以下命令在包含 `.venv/`、`scripts/` 和 `web_ui/` 的项目根目录运行，使用已安装依赖的 Windows Python：

```powershell
.\.venv\Scripts\python.exe .\scripts\audit_sources.py
.\.venv\Scripts\python.exe .\scripts\audit_r1cs.py
```

两个脚本各自串行处理目录中的全部匹配文件。源码默认读取 `benchmarks/entries/`，输出到
`build/source-audit/`，每个入口预算为 150 秒。R1CS 默认读取 `benchmarks/r1cs/`，输出到
`build/r1cs-audit/`；原生引擎预算为 120 秒，单次求解查询预算为 5000 毫秒，完整校验加分析的
外层预算为 420 秒，Windows 内存限制为每进程 4096 MiB。R1CS 检测需要已安装的 Picus/cvc5 原生运行时。

输出目录中的 `progress.json` 表示当前进度，`summary.json` 汇总逐文件状态，`results/` 保存详细报告。
再次运行相同命令会复用输入、实现及配置指纹匹配的报告；需要重新尝试未完成项时添加
`--retry-incomplete`。R1CS 还核对实际运行时文件指纹，并在 `attempts/` 保留每次运行的证据及被替换的旧报告。
输入目录可分别用 `--entries` 或 `--r1cs` 指定，输出目录均使用 `--output` 指定；完整参数见各脚本的 `--help`。

在对应输出目录创建 `stop.request` 会请求取消当前子进程并停止该批次。恢复前删除此标记，再运行原命令。
例如，停止并随后恢复 R1CS 批次：

```powershell
New-Item -ItemType File -Path .\build\r1cs-audit\stop.request -Force
# 等待 progress.json 的 state 变为 stopped 后，准备恢复时执行：
Remove-Item -LiteralPath .\build\r1cs-audit\stop.request
.\.venv\Scripts\python.exe .\scripts\audit_r1cs.py
```

**全部文件已尝试（all attempted）不等于所有检查都有确定结论（all checks conclusive）。**
`state: complete` 和退出码 0 表示本轮遍历结束；仍须查看逐项状态、原因，以及 R1CS 汇总中的
`all_checks_complete`。只有所有适用检查完成，单项文件才记为 `completed`；发现真实问题也属于已完成检测，
不等于安全通过。无输出或已证明不可满足时跳过不适用的唯一性检查，会明确记录原因。
原始语法错误、格式错误、资源限制和求解器未决必须保留诊断；没有确定证明的检查继续显示 `UNKNOWN`，
不能把超时、失败或部分完成的结果改写为通过，也不能通过删除样本来宣称全量通过。

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
检测器目前包含 5 类规则：未约束输出、未约束组件输入、数据流与约束不一致、
未使用组件输出、未使用信号。
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
| True Positive (TP) | 9 |
| False Positive (FP) | 1 |
| False Negative (FN) | 0 |
| True Negative (TN) | 45 |
| Precision | 90.00% |
| Recall | 100.00% |
| F1-score | 94.74% |
| Accuracy | 98.18%* |

`Accuracy` 一栏是按 `(TP + TN) / (TP + TN + FP + FN)` 得出的机械值。由于这 11 个
样例主要是漏洞样例，真负例数量有限，因此这个 98.18% **不能作为总体准确率**
或项目验收数据。正式报告应同时包含安全样例，才能解释 Accuracy、误报率和漏报率。

这次核对的总耗时约为 2.599 秒，平均每个入口约 0.236 秒，最慢入口约 2.114 秒。
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
耗时及 5 类规则结果，然后分别报告：

1. **文件级结果**：是否发现至少一项问题，与明确的 `safe/unsafe` 标注比较；
2. **规则级结果**：每一种检测规则分别统计 TP、TN、FP、FN、Precision、Recall 和 F1；
3. **不可判定样本**：将解析失败、超时和缺少标注单独列出，不能默认算作安全或正确；
4. **性能结果**：给出样本数量、平均值、最大值、超时数量以及硬件和软件版本。

即使全量评测没有 Warning，也只能说明这些规则在该数据集上没有报告问题，不能证明
电路满足任意业务规格或不存在规则覆盖范围之外的漏洞。
