# CirVerify

**English** | [简体中文](README.zh-CN.md)

A static analysis tool for Circom source code, designed to detect security vulnerabilities in Zero-Knowledge Proof (ZKP) circuits written in the Circom language. This tool helps developers and users ensure the security and integrity of their ZKP projects by analyzing the source code and identifying potential issues during circuit design.


## Features

CirVerify detects a variety of potential issues in Circom circuits, including:

- **Unconstrained Output Signals**: Detects output signals that are not constrained by any constraints.
- **Unconstrained Component Inputs**: Identifies input signals to components that are not constrained and may accept unchecked values.
- **Data Flow Constraint Discrepancy**: Finds signals that depend on others via dataflow but lack corresponding constraint dependencies.
- **Unused Component Outputs**: Warns when outputs of components are not used or checked in the circuit.
- **Unused Signals**: Identifies signals that are declared but never used in any computation or constraint.
- **Type Mismatch**: Detects potential type mismatches, such as signals flowing into templates like `Num2Bits` without proper range checks.
- **Assignment Misuse**: Finds assignment misuse, where a variable is assigned using the wrong operator.
- **Divide by Zero**: Warns of potential divide-by-zero issues in the circuit.
- **Non-deterministic Data Flow**: Flags conditional assignments depending on signals, which may lead to non-deterministic data flows.

## Installation

Install Python 3 and Git first. CirVerify declares its command-line dependencies
in `setup.py`; installing the project also installs these dependencies and the
`cirverify` command. No separate requirements file is needed.

Clone the repository if you do not already have a local copy:

```bash
git clone https://github.com/ZJU-Automated-Reasoning-Group/cirverify
cd cirverify
```

Run the following commands from the project root (the folder containing
`setup.py`). If you already have a working `.venv` on this computer, skip the
environment creation command.

### Windows (PowerShell)

```powershell
py -3 -m venv .venv
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install -e .
```

The UTF-8 setting prevents encoding errors when printing warning symbols. Set it
again when opening a new PowerShell terminal to run CirVerify.

### macOS / Linux

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -e .
```

Create the environment on the computer where you will run CirVerify; do not copy
a Windows `.venv` to macOS or Linux. The commands below use the environment's
executables directly, so activating the environment is optional.

For the optional browser interface, including its additional Flask dependency,
see [Web UI Usage](#web-ui-usage).


## Usage

You can use it via the command line interface (CLI) to analyze Circom code and generate reports.
For browser-based operation, see [Web UI Usage](#web-ui-usage).

### Command Line Arguments

- `input`: **Required** - The path to the Circom file you want to analyze.
- `--json`: **Optional** - If specified, the tool will output a JSON report to the given file. The output file must end with `.json`.

### Example Usage

1. **Basic Analysis:**
   To analyze a Circom file and print the report to the console:

   Windows (PowerShell):

   ```powershell
   .\.venv\Scripts\cirverify.exe .\demo.circom
   ```

   macOS / Linux:

   ```bash
   ./.venv/bin/cirverify ./demo.circom
   ```

   Replace `demo.circom` with the path to your own Circom file. This runs the
   analysis and displays the results directly in the terminal.

2. **Generate JSON Report:**
   To analyze the Circom file and save the report in a JSON file:

   Windows (PowerShell):

   ```powershell
   .\.venv\Scripts\cirverify.exe .\demo.circom --json .\demo_report.json
   ```

   macOS / Linux:

   ```bash
   ./.venv/bin/cirverify ./demo.circom --json ./demo_report.json
   ```

   This will run the analysis and save the results in the specified JSON file.

### Example Output:

When you run the tool, you'll see progress information printed to the terminal, such as:

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
100%|██████████████████████████████████████████████████████████████████████████████████████████████████████| 3/3 [00:00<?, ?it/s]
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

If there are any warnings or issues detected, they will be printed like this:

```bash
[Warning]    In .\demo.circom:4:4
             Signal 'out' depends on 'a' via dataflow, but there is no corresponding constraint dependency.
[Warning]    In .\demo.circom:5:4
             The dataflow for signal 'out' does not mathematically match any of its constraints.
⚠ Total warnings: 2
```

### Example JSON Output:

If you specify a JSON output file, the results will also be saved to the file. For example:

```bash
[Success] Saved report to path/to/output/report.json
```

The JSON file will contain detailed information about the analysis, including detected vulnerabilities. A sample JSON output might look like this:

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

## Web UI Usage

CirVerify includes a web interface for analyzing `.circom` source and viewing
the constraints in compiled `.r1cs` files. The web server runs
on your own computer.

### 1. Start the Web Server

Complete [Installation](#installation) first to create `.venv` for your operating
system and install CirVerify. In VS Code, select **Terminal → New Terminal**.
Make sure the current directory is the project root: the folder containing
`setup.py`, `.venv`, and `web_ui`.

The web interface also requires Flask. Run the `pip install flask` command below
once when setting up the environment. On later runs, use the server startup
command directly. On Windows, set `PYTHONUTF8` in each new terminal as well.

**Windows (PowerShell):**

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install flask
.\.venv\Scripts\python.exe .\web_ui\app.py
```

**macOS / Linux:**

```bash
./.venv/bin/python -m pip install flask
./.venv/bin/python ./web_ui/app.py
```

The last command runs the web application, `app.py`, using the project's Python
environment. The terminal keeps running while the server waits for browser
requests; this is expected.

### 2. Open the Page in a Browser

After the terminal displays `Running on http://127.0.0.1:5000`, enter the following
address in your browser:

```text
http://127.0.0.1:5000
```

`127.0.0.1` refers to your own computer, and `5000` is the server's port. This
address connects to the service you just started. Opening `README.html` alone
does not start the analysis service.

### 3. Select Code and Analyze It

For your first analysis:

1. Choose **Single Assignment Issue** from the **Select Example** dropdown.
2. The example appears in the editor. Alternatively, use **Upload File** to open
   the project's `demo.circom` file.
3. Click **Analyze** and view the progress and result areas.
4. Read the messages following `[Warning]`. This example produces two warnings
   about the computation and constraints for `out`.
5. Edit the code and click **Analyze** again as needed. Browser edits are not
   automatically saved back to the original file.

The main controls are:

| Control | Usage |
|---|---|
| Select Example | Load a built-in example into the editor |
| Upload File | Open `.circom` source or a binary `.r1cs` file for viewing and analysis |
| Select Folder | Load the folder's `.circom` files into the file list, then select one to open |
| Analyze | Run source checks for the active `.circom` tab, or limited constraint checks for `.r1cs` |
| Stop | Request cancellation; background detection may continue until the current computation finishes |
| Clear | Close all file tabs, clear results, and release temporary R1CS uploads |

**Select Folder** does not automatically analyze the entire folder. For Circom, **Analyze**
submits only the current editor contents. If the code uses `include` to
reference other files, the submission may lack dependencies or lose the original
relative paths. In that case, use the [CLI](#usage) on the original `.circom` file
and preserve its directory structure and dependencies.

`Total warnings: 2` means two warnings were found. Reaching `100%` or showing an
analysis-complete message means the checks finished. Read the warning messages;
even an analysis with no warnings does not guarantee that the circuit is secure.

The web interface currently has no JSON report download button. To save a
Circom source report, follow the [CLI examples](#example-usage) and use `--json`.

### View and Analyze an R1CS File

1. Click **Upload File** and select `demo1.r1cs`, or another standard version 1
   `.r1cs` file. No `.sym` file or additional Node.js installation is needed.
2. The **R1CS · Constraint View** opens automatically. It shows the file size,
   constraint count, variable count, and public/private input and public output
   counts. Expand **Field modulus & reading guide** to see the full modulus and
   explanation. **Analyze** runs the constraint checks described below.
3. Read equations in the form `A × B − C = 0`, with the **← / →** page buttons
   showing 50 constraints per page. Variables are named `w1`, `w2`, etc.; `w0 = 1`
   is the constant. Original signal names and actual input values are unavailable.
4. **Signed / 简洁系数** is the default: for example, `p−1` appears as `−1`.
   Select **Original / 原始系数** to display the original nonnegative coefficients.
   Both views describe the same arithmetic modulo the modulus stored in the file.
5. Click **Analyze** and read the report below the workspace. It includes findings,
   wire/constraint numbers, and coverage of the original nine source check categories.
   **Stop** cancels an active check; results stay with their file tab.

For the project's `demo1.r1cs`, expect 6 constraints, 7 variables including the
constant, 2 public outputs, 0 public inputs and 4 private inputs. Switching file
tabs preserves source edits and the R1CS page/coefficient mode. Uploading the same
filename again replaces that tab's content.

R1CS analysis performs **limited constraint checks**: outputs and retained wires
absent from effective constraints, constraints equivalent to `0 = 0`, impossible
constant constraints, and contradictions found by bounded linear elimination.
Coefficients are normalized modulo the file's modulus before checking occurrence;
`x × 0 = 0`, for example, does not constrain `x`. Unused non-output wires and
tautologies are informational, not automatically security vulnerabilities.

Of the original nine check categories, unconstrained outputs and unused signals
have **partial coverage**. The remaining seven display **unavailable / 无法检查**:
component input/output checks, data-flow/constraint discrepancies, type mismatch,
assignment misuse, unsafe division, and nondeterministic data flow require source
information that R1CS does not retain. These are not reported as passing.
No findings does **not** prove safety: output uniqueness, general nonlinear
satisfiability and application logic are not verified. For example, `x² = 1` can
have multiple solutions even though `x` occurs in a constraint.

Analysis has a 20-second cooperative time budget, bounded polynomial expansion
and linear elimination, and displays at most 200 findings (counts include all
findings). Reaching a limit produces an explicitly partial report. One R1CS
analysis runs at a time. No solver, Node.js or new runtime dependency is needed.
It does not recover Circom source, accept `.sym` files, or generate proofs.
Custom-gate extensions are currently unsupported. Folder selection and the CLI
continue to support Circom source only.

The total upload request limit is **100 MiB**, including form data. R1CS uploads
are temporary: closing a file tab or clicking **Clear** releases them after any
active analysis finishes using the file. Files expire
after 30 minutes without a server request; restarting the server also removes access.
If a file has expired, upload it again. The original file on your computer is unchanged.

### 4. Stop the Server and Troubleshoot

Keep the server terminal running while you use the page. Press **Ctrl+C** in that
terminal to stop the service. Closing the browser tab does not stop the server.
After stopping it, run the startup command again before using the page.

| Problem | What to do |
|---|---|
| The browser cannot open the page | Check that the server is still running and use the `http://127.0.0.1:5000` address displayed in the terminal |
| `No module named 'flask'` | Run the `-m pip install flask` command above using your operating system's `.venv` Python |
| `python.exe`, `bin/python`, or `web_ui/app.py` cannot be found | Check that you are in the project root and have created `.venv` on the current operating system |
| An R1CS file has expired or was closed | Upload it again; temporary uploads expire after 30 minutes without access |
| An upload exceeds the limit | Keep the complete request below 100 MiB; `.r1cs` must be a supported binary file |

See also the [Web UI README](web_ui/README.md).

