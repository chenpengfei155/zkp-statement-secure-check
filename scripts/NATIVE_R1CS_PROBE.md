# 路线二：Windows 原生 R1CS 检测验证

## 结论

已实现并实测一个 **Windows Python + Z3** 原型，能够执行现有六项检查。
两个 demo 的六项状态与现有 Picus 后端一致；但这个直接求解原型还不能替代
Picus：大素数下仅有一条 `out² = 1` 约束时，两项唯一性检查就会超时。
这一结果说明“数学检查定义相同”不等于“限时内能够得出相同结论”。

这是独立实验脚本。网页的生产后端仍使用原有 Picus 实现。

## 环境与方法

- Windows x64，Python 3.11.5；原生子进程运行平台为 `os.name == 'nt'`。
- 独立环境：`.venv/native-probe`；不修改网页 Python 环境的依赖。
- `z3-solver==4.15.4.0`；`cvc5==1.3.3` 用于测试原生有限域支持。
- 本机 Circom 2.2.3，两个 circomlib 电路以 `--O0 --r1cs` 编译。
- 原生检查路径不调用 WSL。只有对照实验调用现有 WSL/Picus 后端。
- Picus 提交 `138b151d3a388e5b6c040c163e0a1db04f2ceda6`，
  参考后端 cvc5 提交 `de62429fa7c03a46d5d75f9d78fc8888792a0798`。
- 两边配置单次求解查询 5 秒、任务 30 秒。Picus 内部可以进行多次查询。
  原生子进程有 30 秒外部硬超时；求解器自身的 5 秒超时可能因预处理等原因超出。
- 表中时间为一次顺序运行的墙钟时间，包含 Python/WSL 启动；不是稳定性能基准。

前三项检查使用精确整数编码，不使用浮点数或随机采样：

1. 所有变量满足 `0 <= wi < p`，`w0 = 1`，`p` 从文件读取。
2. 每条约束编码为 `(A * B - C) mod p == 0`。
3. 可满足性：判断这一系统是否存在解。
4. 输出唯一性：构造两份约束，共享全部公开和私有输入，要求至少一个输出不同。
5. 全信号唯一性：同样共享全部输入，要求至少一个非输入信号不同。
6. 唯一性查询的 `sat` 表示发现反例，`unsat` 表示性质成立；超时保留 `unknown`。
   约束已证无解时跳过唯一性检查，避免把空集上的唯一性当成通过。

后三项结构检查复用 `web_ui/r1cs_checks.py` 的实际实现，包括原有工作量上限。
所有原生求解器返回的赋值和反例，都用 Python 大整数重新代入原始 R1CS 核对。
另外生成 12 个固定随机种子的模 5 电路，每个电路穷举全部 125 组变量赋值，
独立计算前三项性质，核对 SMT 结果。

## 测试结果

总计 **27 个样例**：24 个得出与人工预期或穷举结果一致的确定结论，3 个为
`unknown`。本次没有发现与独立预期矛盾的确定结论；这不构成普遍正确性证明。
其中 15 个与现有后端对照，13 个的六项状态完全相同（包括两边都超时的样例）。

| 样例 | 约束/变量数 | Windows Z3 原型 | 现有 Picus 后端 | 墙钟时间：原生 / 对照 |
|---|---:|---|---|---:|
| `demo.r1cs` | 1 / 4 | 三项求解通过，发现 1 个未使用输入 | 六项状态相同 | 0.13 / 2.19 秒 |
| `demo1.r1cs` | 6 / 7 | 六项通过 | 六项通过 | 0.13 / 2.03 秒 |
| `out² = 1`，模 17 | 1 / 2 | 输出不唯一，有经核对的反例 | 输出不唯一 | 0.12 / 2.10 秒 |
| `out² = 1`，BN254 模数 | 1 / 2 | 可满足；输出和全信号唯一性超时 | 找到不唯一反例 | 10.13 / 2.04 秒 |
| `out = 0` 且 `out = 1`，模 17 | 2 / 2 | 无解，跳过唯一性检查 | 相同 | 0.12 / 0.80 秒 |
| 输出唯一、内部变量满足 `t² = 1`，模 17 | 2 / 4 | 输出唯一；内部变量不唯一 | 相同 | 0.14 / 2.25 秒 |
| `Poseidon(2)` | 765 / 768 | 三项求解全部超时；结构检查完成 | 可满足性超时，两项唯一性通过 | 16.42 / 19.08 秒 |
| `Num2Bits_strict` | 1285 / 1284 | 三项求解全部超时；结构检查完成 | 三项求解全部超时；结构检查完成 | 25.46 / 33.39 秒 |

还覆盖了：BN254 无解、零约束自由输出、无输出、同时有公开和私有输入、模运算
回绕、不同区段顺序、小素数非线性无解、未使用变量/恒真/重复约束，以及上述
12 个穷举对照样例。原型共独立验证了 21 个可满足性赋值和 22 对不唯一反例。

“Picus 后端”列包含项目自带的 cvc5 可满足性和 Python 结构检查，
不能把其可满足性超时归因于 Picus 的唯一性算法。Z3 原始日志中的 `canceled`
在本实验中来自内部限时中断，结果仍是 `unknown`，不是用户主动取消。

## 发现的两个限制

### 原生 cvc5 包的有限域功能

安装 `cvc5==1.3.3` 的 Windows wheel 成功，线性等式查询也能运行。
但对 `x² = 3 (mod 17)` 调用真正的非线性有限域求解时返回：

```text
cvc5 can't solve field problems since it was not configured with --cocoa
```

因此，本次原型使用 Z3 的精确模整数编码。不能把“能 import cvc5”或“一个简单
等式返回 sat”当成有限域功能完整可用的证据。本结论仅针对本次测试的包，
不代表自行编译的原生 cvc5 也不能工作。

### 现有 R1CS 读取器对项顺序的限制

现有读取器要求每个线性组合的项按变量编号递增。Circom 2.2.3 编译出的两个
较大电路含有未排序的项，会被读取器拒绝。因此实验保留编译器原始文件，另建
`.sorted.r1cs` 副本，仅排序加数；不改系数、变量编号、模数、约束数量或方程。
双方后端分析同一个副本；原始文件哈希和排序数量保存在结果里。

这个兼容性问题独立于 Windows/WSL。本次未修改生产读取器。
格式参考：[R1CS 二进制规范](https://github.com/iden3/r1csfile/blob/master/doc/r1cs_bin_format.md)。

## 复现

在项目根目录的 PowerShell 中安装独立实验环境：

```powershell
.\.venv\Scripts\python.exe -m venv .venv\native-probe
.\.venv\native-probe\Scripts\python.exe -m pip install --only-binary=:all: z3-solver==4.15.4.0 cvc5==1.3.3
```

只运行原生检测，不需要 WSL：

```powershell
.\.venv\native-probe\Scripts\python.exe scripts\native_r1cs_z3.py demo1.r1cs --timeout-ms 5000
.\.venv\Scripts\python.exe scripts\validate_native_r1cs.py
```

有现有 Picus 环境时，运行完整对照：

```powershell
.\.venv\Scripts\python.exe scripts\validate_native_r1cs.py --compare-picus
```

`--case <名称>` 可以重复指定来只运行某几个样例。较大的电路需要项目中的
`.tools/circom.exe` 和相应 circomlib 源码；缺少编译器及编译产物时跳过这两项。

代码：`scripts/native_r1cs_z3.py` 和 `scripts/validate_native_r1cs.py`。
本次小样例结果：`build/native-r1cs-probe/results.json`。
本次较大电路结果：`build/native-r1cs-probe/benchmarks.json`。
本次合并结果：`build/native-r1cs-probe/combined-results.json`。
这些实验产物位于 Git 忽略的 `build` 目录。

若继续路线二，优先解决原生有限域求解能力或增加经过验证的代数推理与化简，
再扩大测试集。当前直接 Z3 原型适合探索和简单电路，还没有达到等价替换 Picus
的验收条件，也没有实现网页接入、任务取消 API 或生产级内存限制。
