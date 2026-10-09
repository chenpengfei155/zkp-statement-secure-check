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
