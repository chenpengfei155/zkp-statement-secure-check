# CirVerify

[English](README.md) | **简体中文**

CirVerify 是一个用于检查 Circom 源代码的静态分析工具，帮助发现零知识证明（ZKP）
电路中可能存在的安全问题。它通过分析代码，提示开发者检查电路设计中的潜在缺陷。

## 功能

CirVerify 可以检查 Circom 电路中的多类潜在问题，包括：

- **输出信号缺少约束**：检查输出信号是否没有受到任何约束限制。
- **组件输入缺少约束**：识别可能接受未经约束限制的值的组件输入信号。
- **计算依赖与约束依赖不一致**：发现计算使用了某个信号，但约束没有相应地限制它的情况。
- **组件输出未使用**：提示组件的输出没有被使用或检查。
- **信号未使用**：识别声明后没有参与任何计算或约束的信号。
- **类型不匹配**：检查潜在的类型问题，例如信号进入 `Num2Bits` 等模板前缺少合适的范围检查。
- **赋值使用不当**：检查赋值运算符使用不当等问题。
- **除零风险**：提示计算中可能存在的除以零问题。
- **非确定性数据流**：检查依赖信号值的条件赋值等可能导致非确定性数据流的情况。

<a id="installation"></a>

## 安装

先安装 Python 3 和 Git。项目的命令行依赖已经声明在 `setup.py` 中；
安装项目时，会同时安装这些依赖和 `cirverify` 命令，不需要单独的依赖清单文件。

如果还没有下载项目，可以先克隆仓库：

```bash
git clone https://github.com/ZJU-Automated-Reasoning-Group/cirverify
cd cirverify
```

下面的命令都在项目根目录执行，也就是包含 `setup.py` 的文件夹。
如果这台电脑上已经有可用的 `.venv`，可以跳过创建环境的那一行。

### Windows（PowerShell）

```powershell
py -3 -m venv .venv
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install -e .
```

UTF-8 设置用于避免输出警告符号时出现编码错误。每次打开新的 PowerShell 终端
运行 CirVerify 时，都需要重新执行这一行设置。

### macOS / Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e .
```

请在实际运行 CirVerify 的电脑上创建环境，不要把 Windows 的 `.venv` 直接复制到
macOS 或 Linux 使用。下面的命令直接指定环境里的程序路径，因此无需先激活环境。

如果需要通过浏览器使用工具，包括安装额外的 Flask 依赖，请查看
[网页版使用说明](#web-ui-usage)。

<a id="usage"></a>

## 命令行使用

可以通过命令行检查 Circom 代码并生成报告。如果希望通过浏览器操作，
请查看[网页版使用说明](#web-ui-usage)。

### 命令行参数

- `input`：**必填**，要检查的 Circom 文件路径。
- `--json`：**可选**，指定保存 JSON 报告的文件路径，文件名必须以 `.json` 结尾。

<a id="example-usage"></a>

### 使用示例

1. **基本检查：**
   检查 Circom 文件，并直接在终端中显示结果。

   Windows（PowerShell）：

   ```powershell
   .\.venv\Scripts\cirverify.exe .\demo.circom
   ```

   macOS / Linux：

   ```bash
   ./.venv/bin/cirverify ./demo.circom
   ```

   将 `demo.circom` 替换成你自己的 Circom 文件路径，即可检查其他文件。

2. **生成 JSON 报告：**
   检查 Circom 文件，并把结果保存为 JSON 文件。

   Windows（PowerShell）：

   ```powershell
   .\.venv\Scripts\cirverify.exe .\demo.circom --json .\demo_report.json
   ```

   macOS / Linux：

   ```bash
   ./.venv/bin/cirverify ./demo.circom --json ./demo_report.json
   ```

   检查完成后，报告会保存到指定文件中，可以用 VS Code 打开查看。

### 终端输出示例

运行工具时，终端会显示检查进度，例如下面这些信息。
程序的实际输出为英文，下面保留原文，方便与终端内容对照。

```bash
> .\.venv\Scripts\cirverify.exe .\demo.circom --json .\demo_report.json
[Info]       Generating AST for: .\demo.circom
[Success]    AST generated successfully.
[Success]    Type checking passed.
[Info]       Creating CDG: SingleAssignment0, in .\demo.circom
[Info]       Building conditional dependency edges of SingleAssignment0...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 5/5 [00:00<?, ?it/s]
[Info]       Building condition constraint edges of SingleAssignment0...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 5/5 [00:00<?, ?it/s]
[Success]    CDG created successfully.
[Info]       Starting the analysis process of graph SingleAssignment0.
[Info]       Detecting unconstrainted output...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 1/1 [00:00<?, ?it/s]
[Info]       Detecting unconstrained component input...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 5/5 [00:00<?, ?it/s]
[Info]       Detecting data flow constraint discrepancy...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████| 3/3 [00:00<?, ?it/s]
[Info]       Detecting unused component output...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 5/5 [00:00<?, ?it/s]
[Info]       Detecting type mismatch...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 5/5 [00:00<?, ?it/s]
[Info]       Detecting assignment misuse...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 7/7 [00:00<?, ?it/s]
[Info]       Detecting unused signals...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 7/7 [00:00<?, ?it/s]
[Info]       Detecting nondeterministic data flow...
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 7/7 [00:00<?, ?it/s]
[Success]    Detection completed successfully.
[Timeit]     Analysis completed in 0.30 s
```

如果发现可疑问题，工具会显示警告，例如：

```bash
[Warning]    In .\demo.circom:4:4
             Signal 'out' depends on 'a' via dataflow, but there is no corresponding constraint dependency.
[Warning]    In .\demo.circom:5:4
             The dataflow for signal 'out' does not mathematically match any of its constraints.
⚠ Total warnings: 2
```

### JSON 输出示例

指定 JSON 输出文件后，工具还会把结果保存到文件中，例如：

```bash
[Success] Saved report to path/to/output/report.json
```

JSON 文件按模板、问题类型和代码位置组织报告。下面是一个示例，
字段名和警告原文与工具的英文输出保持一致：

```json
{
  "SingleAssignment0": {
    "data flow constraint discrepancy": {
      ".\\demo.circom:4:4": [
        "Signal 'out' depends on 'a' via dataflow, but there is no corresponding constraint dependency."
      ]
    },
    "assignment missue": {
      ".\\demo.circom:5:4": [
        "The dataflow for signal 'out' does not mathematically match any of its constraints."
      ]
    }
  }
}
```

<a id="web-ui-usage"></a>

## 网页版使用说明

CirVerify 也提供网页版，可以在浏览器中检查 `.circom` 源码，或者查看编译后的
`.r1cs` 文件中的约束。网页服务运行在你自己的电脑上。

### 1. 启动网页服务

先完成上面的[安装步骤](#installation)，创建适合当前操作系统的 `.venv`
并安装 CirVerify。在 VS Code 中选择“终端 → 新建终端”，确保当前目录是项目根目录，
也就是包含 `setup.py`、`.venv` 和 `web_ui` 的文件夹。

网页额外需要 Flask。下面的 `pip install flask` 只需在首次使用时执行；
以后直接执行启动命令即可。Windows 新开终端时仍需设置 `PYTHONUTF8`。

**Windows（PowerShell）：**

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install flask
.\.venv\Scripts\python.exe .\web_ui\app.py
```

**macOS / Linux：**

```bash
./.venv/bin/python -m pip install flask
./.venv/bin/python ./web_ui/app.py
```

最后一行的意思是：用项目环境里的 Python 执行网页程序 `app.py`。
服务启动后，终端会持续运行、等待浏览器访问，这是正常现象。

### 2. 在浏览器中打开

看到终端提示 `Running on http://127.0.0.1:5000` 后，在浏览器地址栏输入：

```text
http://127.0.0.1:5000
```

`127.0.0.1` 表示本机，`5000` 是程序使用的端口。这个地址连接的是你电脑上
刚刚启动的服务。仅打开 `README.html` 不会启动检测服务。

### 3. 选择代码并检查

第一次使用可以按下面的顺序操作：

1. 在 **Select Example** 下拉框中选择 **Single Assignment Issue**。
2. 编辑区会显示示例代码；也可以选择 **Upload File**，打开项目中的 `demo.circom`。
3. 点击 **Analyze**，查看进度和结果区域。
4. 阅读 `[Warning]` 后的问题说明。当前示例会产生两条与 `out` 的计算和约束有关的警告。
5. 如需修改，在编辑区修改代码后再次点击 **Analyze**；网页中的编辑不会自动保存回原文件。

页面上的主要功能：

| 控件 | 使用方法 |
|---|---|
| Select Example | 选择内置示例，并将代码载入编辑区 |
| Upload File | 打开 `.circom` 源码，或打开 `.r1cs` 二进制文件查看和检查约束 |
| Select Folder | 将文件夹中的 `.circom` 文件载入左侧列表，再点击具体文件打开 |
| Analyze | 检查当前标签页：Circom 运行源码检测，R1CS 使用 Picus 检查输出唯一性 |
| Stop | 请求取消当前分析；后台检测可能继续运行到本次计算结束 |
| Clear | 关闭所有文件标签页、清空结果，并释放临时上传的 R1CS 文件 |

当前页面的 **Select Folder** 不会自动批量检查整个文件夹。对 Circom，**Analyze** 只发送
当前编辑区的代码。若代码通过 `include` 引用其他文件，网页提交的内容可能缺少依赖
或无法保留原来的相对路径；此时请使用[命令行方式](#usage)检查原始 `.circom` 文件，
并保持它与依赖文件的目录结构。

结果中的 `Total warnings: 2` 表示发现两条警告。进度达到 `100%` 或显示分析完成，
表示检查过程完成；需要继续阅读警告内容，没有警告也不能保证代码完全安全。

网页目前没有 JSON 报告下载按钮。需要保存 Circom 源码检测报告时，使用上面的
[命令行示例](#example-usage)，加上 `--json` 参数。

### 查看和检查 R1CS 文件

1. 点击 **Upload File**，选择 `demo1.r1cs` 或其他标准版本 1 的 `.r1cs` 文件。
   不需要 `.sym` 文件，也不需要额外安装 Node.js。
2. 页面自动进入 **R1CS · Constraint View（约束查看）**，顶部紧凑显示文件大小、
   约束数量、变量数量，以及公开输入、私有输入和公开输出的数量。点击
   **Field modulus & reading guide** 可展开完整模数与阅读说明。**Analyze** 用于运行下述约束检查。
3. 公式按 `A × B − C = 0` 展示，每页 50 条，点击 **← / →** 按钮翻页。
   `w1`、`w2` 等表示变量编号，`w0 = 1` 是常量；文件不能提供原始变量名称或实际输入值。
4. 默认选择 **Signed / 简洁系数**，例如把 `p−1` 显示为 `−1`；选择
   **Original / 原始系数** 可以查看原来的非负大整数。两种显示都按文件中的模数计算，
   数学含义相同，不会修改上传文件。
5. 完成下方的一次性 Picus 安装后，点击 **Analyze** 查看输出唯一性结论和可用反例。
   可以点击 **Stop** 取消；结果保存在对应文件标签页中。

项目里的 `demo1.r1cs` 应显示：6 条约束、7 个变量（含常量）、2 个公开输出、
0 个公开输入和 4 个私有输入。切换文件标签页时会保留源码编辑内容，以及 R1CS 的
当前页和系数显示模式；再次上传同名文件，会替换对应标签页的内容。

#### 一次性安装 Picus（Windows + WSL）

R1CS 查看器不需要额外软件；**Analyze** 需要在 WSL 的 `Ubuntu-22.04` 中安装
Picus、Racket 和带有限域支持的 cvc5。在项目根目录的 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_picus.ps1
```

安装会下载并编译依赖，首次可能需要较长时间。Picus 安装在 WSL 用户目录
`~/.local/share/cirverify-picus`，固定原版提交 `138b151d3a388e5b6c040c163e0a1db04f2ceda6`。
安装完成后重新启动网页服务；上传和分析时不会下载依赖。其他发行版可通过脚本的
`-Distro` 参数指定，同时在启动网页前设置 `$env:CIRVERIFY_PICUS_DISTRO`。
脚本使用独立的 Racket 8.16，并在结束前运行真实的正常、欠约束样例。
以后可用 `.\.venv\Scripts\python.exe .\scripts\check_picus.py` 重新检查环境。

**Picus 默认检查全部输入（公开和私有）相同时，公开输出是否唯一。**
例如，一个始终输出 `0` 的电路也可能满足“输出唯一”，但未必实现你想要的异或运算；
业务功能是否正确仍需结合设计要求验证。

| 结论 | 含义 |
|---|---|
| 输出唯一性已验证（safe） | Picus 验证了上述性质；不代表业务逻辑正确或所有安全问题均已排除 |
| 发现欠约束（unsafe） | 相同输入可对应不同合法输出；页面展示可用的反例和运行日志 |
| 无法确定（unknown） | 求解预算或时间不足，不能当作通过 |
| 分析未完成 / 已取消 | 环境、文件或运行错误 / 用户停止，不给出安全结论 |

没有公开输出时，不执行输出唯一性检查。变量使用 `w编号`，反例中的大整数保持
完整精度。Picus 不要求实际输入、`.sym` 或 `.circom`。推荐编译时使用 `--O0`；
若优化后的输入计数无法对应变量，需重新编译。本功能不还原源码、不生成证明、
不支持自定义门扩展，命令行和文件夹分析仍只处理 Circom。

默认单次求解查询 5 秒、整项分析 120 秒、Linux 子进程各限 4 GiB，同一时间一个任务。
**Stop**、关闭文件、替换同名文件或清空会终止该任务的 Picus/cvc5 子进程。
安装未就绪时会明确提示，不影响 Circom 检测和 R1CS 查看。
服务端配置与测试命令见 [网页说明](web_ui/README.md#picus-configuration)。

整个上传请求上限为 **100 MiB**（包含表单数据）。上传的 R1CS 只作临时保存：关闭文件
标签页或点击 **Clear** 会释放它（正在分析的文件会等分析结束后释放）。未在分析且
30 分钟没有向服务请求该文件时会过期，重启服务后也需
重新上传。出现过期提示时，再次上传原文件即可；电脑上的原文件不会被修改或删除。

### 4. 停止服务与常见问题

使用网页时保持启动服务的终端开着。用完后回到该终端，按 **Ctrl+C** 停止服务。
只关闭浏览器标签页不会停止服务；服务停止后，需要重新运行启动命令才能继续检查。

| 现象 | 处理方法 |
|---|---|
| 浏览器无法打开地址 | 确认终端里的服务仍在运行，并使用终端显示的 `http://127.0.0.1:5000` 地址 |
| 提示 `No module named 'flask'` | 使用对应系统的 `.venv` Python 执行上面的 `-m pip install flask` |
| 找不到 `python.exe`、`bin/python` 或 `web_ui/app.py` | 检查是否在项目根目录，以及是否已在当前系统创建 `.venv` |
| R1CS 提示已过期或已关闭 | 重新上传文件；临时文件在 30 分钟未访问后过期 |
| 上传提示超出限制 | 整个请求需小于 100 MiB；`.r1cs` 必须是支持的二进制格式 |

另见 [Web UI README（英文）](web_ui/README.md)。
