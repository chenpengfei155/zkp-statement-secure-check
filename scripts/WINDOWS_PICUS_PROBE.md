# 路线一：原版 Picus 在 Windows 原生环境中的验证

> 这是接入网页前的实验记录。现在已完成原生后端和离线安装包，
> 使用方法见 [中文 README](../README.zh-CN.md)，分发与构建说明见
> [Windows 运行包](../vendor/picus/windows-x64/README.md)。下文保留当时的验证过程。

## 结论

**本机验证已跑通。** 保留原版 Picus 的推理算法，配合带 CoCoA 的 Windows cvc5，
能够原生执行当前六项 R1CS 检查。分析过程没有调用 WSL。

这比路线二的直接 Z3 编码更接近现有后端：BN254 下的 `out² = 1` 能检出欠约束，
Poseidon 的输出与全信号唯一性也能验证。路线二在这些唯一性检查中返回超时。

本次是独立验证，网页服务仍使用原有 WSL 后端。要正式切换，还需要接入原生任务执行器、
环境检测和安装流程，并回归网页停止、替换上传、清空及任务并发。

## 实测结果

测试日期：2026-09-23。原始记录位于 `build/windows-picus-probe/results.json`。

- 27 个样例：25 个有明确预期的小样例全部符合预期；两个大电路存在无法确定的项目。
- 15 个样例有现有 WSL 后端的对照记录，六项状态全部一致，包括超时/跳过状态。
- 所有 27 个文件的 SHA-256 与路线二实验使用的文件一致。
- 24 组唯一性反例重新代入原始 R1CS 核对，全部满足约束且目标变量确实不同。
  比较和代入均使用模数运算，不把两个等价余数误认为不同值。
- 12 个模 5 的样例沿用路线二的独立穷举预期，每个穷举 125 组赋值。
- 非线性有限域自检：`x² = 1 (mod 17)` 返回 `sat`，`x² = 3 (mod 17)` 返回 `unsat`，
  BN254 下 `x² = 1` 返回 `sat`。不是只检查程序能否启动或能否导入库。

| 样例 | Windows 原生结果 | 本次耗时 |
|---|---|---:|
| `demo.r1cs`：1 条约束、4 个变量 | 三项求解通过，发现 1 个未使用变量；六项状态与现有后端一致 | 1.92 秒 |
| `demo1.r1cs`：6 条约束、7 个变量 | 六项通过 | 1.94 秒 |
| `out² = 1`，BN254 | 输出和全信号不唯一，反例核对通过 | 2.02 秒 |
| 输出唯一、内部变量满足 `t² = 1`，模 17 | 输出唯一；全信号检查发现歧义 | 2.00 秒 |
| `out = 0` 与 `out = 1` | 无解；跳过两项唯一性检查 | 约 0.1 秒 |
| `Poseidon(2)`：765 条约束 | 输出/全信号唯一性通过，可满足性无法确定 | 9.06 秒 |
| `Num2Bits_strict`：1285 条约束 | 三项求解无法确定，三项结构检查完成 | 40.06 秒 |

还覆盖零约束、没有公开输出、同时有公开/私有输入、不同区段顺序、模运算回绕、
非线性无解，以及未使用变量、恒真约束、重复约束。

每次查询限时 5 秒，原生测试每文件上限 40 秒；此前 WSL 对照记录的每文件上限为 30 秒。
耗时来自单次顺序运行，包含 Python/Racket 启动，不能视为严格性能基准。
本次状态一致也不构成对所有电路、所有 Windows 机器的等价性证明。

## 原生环境与改动

运行目录：`build/windows-picus-probe/`，与网页环境隔离。

| 部件 | 实际版本/来源 |
|---|---|
| Windows Python | 项目 `.venv/Scripts/python.exe` |
| Racket | 官方 Windows x64 CS 8.16，安装于实验目录 |
| Picus | `138b151d3a388e5b6c040c163e0a1db04f2ceda6`；检测源码无修改 |
| cvc5 | `de62429fa7c03a46d5d75f9d78fc8888792a0798`，版本输出 1.0.8 |
| CoCoALib | 0.99800，原版 cvc5 的 trace 补丁，加本目录中的 Windows 兼容补丁 |
| 编译方式 | WSL Ubuntu-22.04 中的 MinGW-w64 x64 POSIX 交叉编译，静态链接依赖 |

生成的 `cvc5.exe` 是 Windows PE32+ x64 程序，`--show-config` 显示 `cocoa: yes`。
导入的 DLL 只有 `KERNEL32.dll`、`msvcrt.dll`、`PSAPI.DLL`；执行时不依赖 Linux 动态库。

本次二进制 SHA-256：

```text
8314b6fc64f9857850e052703ce6565d861e1a74c57e96a3fdf11c9e1360a47d
```

构建时解决的问题：

1. CoCoA 的配置脚本使用 Bash 语法，需要显式使用 Bash。
2. 明确传入 MinGW C++ 编译器和交叉编译的 GMP。
3. CoCoA 配置需要执行目标程序。通过本地文件队列，让 Windows 执行编译出的 PE 探针，
   保留真实 Windows 平台检测结果，并处理 `.exe` 后缀和 CRLF；没有将 Linux 检测值冒充 Windows 值。
4. 用 Windows CRT `signal()` 适配 CoCoA 的信号处理；调试输出用 `%p` 打印指针，
   避免 Windows 64 位指针转为 32 位 `long`。补丁见 `windows_picus_cocoa.patch`。

**WSL 参与这次构建；构建出的分析程序在 Windows 中直接运行。**
目前保留的是本机实验环境，还不是可直接分发到其他电脑的一键安装包。

完整构建脚本、辅助程序、配置日志、编译日志和 Racket 包清单位于
`build/windows-picus-probe/`；WSL 编译工作树位于 `~/.cache/cirverify-windows-probe/`。
这些构建产物被 Git 忽略，未改动原先的 WSL Picus 安装文件。

## 六项检查由谁执行

| 检查 | Windows 实现 |
|---|---|
| 可满足性 | cvc5 有限域求解；沿用项目 SMT 查询生成代码 |
| 输出唯一性 | 原版 Picus 默认模式 + cvc5 |
| 全信号唯一性 | 原版 Picus `--strong` + cvc5 |
| 未使用变量、恒真约束、重复约束 | 复用现有 Python 结构检查代码 |

验证器使用 Windows Job Object 管理进程树，每进程提交内存上限为 4 GiB；
子进程启动前先加入 Job，超时结束整个进程树后再清理临时目录。
实际的 R1CS 和 SMT 文件均放在含中文及空格的路径中运行。
强制超时测试确认孙进程被终止；整个测试结束后没有遗留 Racket/cvc5 进程或任务目录。
详情见 `lifecycle-smoke.json`。Windows 提交内存限制与 Linux `RLIMIT_AS` 的统计口径不同。

## 在这台电脑上复跑

在项目根目录 PowerShell 中运行，不需要启动 WSL：

```powershell
.\.venv\Scripts\python.exe scripts\validate_windows_picus.py demo.r1cs demo1.r1cs --output build\windows-picus-probe\rerun.json
```

复跑完整实验矩阵，需要保留路线二的输入文件及 `combined-results.json`：

```powershell
.\.venv\Scripts\python.exe scripts\validate_windows_picus.py --output build\windows-picus-probe\rerun-all.json
```

该脚本只读取历史 WSL 对照结果，不调用 WSL。原始记录的 `initial-results.json` 和
`remaining-results.json` 分别保存四个初始样例及其余 23 个样例；`results.json` 为合并并重新核对反例的记录。

两个大电路沿用路线二的 `.sorted.r1cs` 副本：只调整线性组合内加数的顺序，以适配现有
Python 读取器的排序要求，原始编译文件仍保留。这个读取器限制独立于 Windows/Picus 移植。

依据：[固定版本 Picus](https://github.com/Veridise/Picus/tree/138b151d3a388e5b6c040c163e0a1db04f2ceda6)、
[Picus 安装说明](https://github.com/Veridise/Picus/blob/138b151d3a388e5b6c040c163e0a1db04f2ceda6/NOTES.md)、
[Racket 8.16 官方发布](https://download.racket-lang.org/releases/8.16/)。
