# CirVerify Web UI

[English project guide](../README.md) | [简体中文项目说明](../README.zh-CN.md)

A web interface for analyzing Circom source, viewing binary R1CS constraints and running limited constraint checks.

## Features

- 📝 **Type Code**: Write or paste Circom code directly in the editor
- 📚 **Examples**: Select from pre-loaded example circuits with common issues
- 📁 **Upload**: Open `.circom` files for source analysis or `.r1cs` files for viewing and constraint checks
- 🔢 **R1CS Viewer**: Metadata, 50 constraints per page, and signed/original coefficient display
- ⚡ **Quick Analysis**: Instant security vulnerability detection

## Setup

First follow the [project installation instructions](../README.md#installation)
to create `.venv` and install CirVerify. Run all commands below from the project
root (the folder containing `setup.py`), not from inside `web_ui`.

The web interface also requires Flask. Install it once in the same environment.

### Windows (PowerShell)

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install flask
.\.venv\Scripts\python.exe .\web_ui\app.py
```

### macOS / Linux

```bash
./.venv/bin/python -m pip install flask
./.venv/bin/python ./web_ui/app.py
```

Open http://127.0.0.1:5000 in your browser after the server starts. This address
refers to your own computer. Keep the terminal running while using the page;
press `Ctrl+C` in that terminal to stop the server.

## Usage

For a step-by-step guide in Chinese, including startup commands and
troubleshooting, see [the Chinese web UI guide](../README.zh-CN.md#web-ui-usage).

1. Choose **Single Assignment Issue** from **Select Example**, type your own
   code, or use **Upload File** to open a `.circom` file.
2. Click **Analyze** to check the code currently displayed in the editor.
3. Read the warnings in the result area. The built-in example reports two
   warnings; analysis completion does not mean the circuit has no issues.
4. Edit the code and run **Analyze** again as needed. Edits in the browser are
   not automatically saved back to the original file.

**Select Folder** loads `.circom` files into the file browser. Select a file to
open it in the editor; the current **Analyze** button does not run a batch
analysis of the folder. For circuits with relative `include` dependencies, use
the CLI on the original file so the dependency paths are preserved.

The web interface has no JSON download button. Use the
[CLI examples](../README.md#example-usage) with `--json` to save a report.

### R1CS Constraint Viewer and Analysis

Use **Upload File** to open `demo1.r1cs`. The viewer opens automatically and offers
**Analyze** for limited constraint checks. Its compact overview shows the file size, constraint/variable
counts and public/private input and public output counts. Expand **Field modulus
& reading guide** for the full modulus and explanation. The **← / →** buttons
page through 50 equations at a time in the form `A × B − C = 0`.

Variables appear as `w1`, `w2`, etc.; `w0 = 1` is the constant. The file does not
provide original names or actual input values. **Signed / 简洁系数** displays
equivalent small negative coefficients by default (`p−1` becomes `−1`);
**Original / 原始系数** displays the original nonnegative coefficients. Both use
arithmetic modulo the file's prime, without changing the file.

Source edits and R1CS view settings survive tab switching. Re-uploading a filename
replaces its tab. Closing a file tab or using **Clear** releases its temporary R1CS
upload; 30 minutes without a server request also expires it. Upload again after
expiry or a server restart. Original files on your computer are unchanged.

The viewer uses Python's standard library and does not need Node.js or `snarkjs`.
It supports standard version 1 files, but not custom-gate extensions or `.sym`
files. It does not restore source or generate proofs.
Folder selection and the CLI remain Circom-only.

Click **Analyze** to check retained wires absent from effective constraints,
tautologies (`0 = 0`), impossible constant constraints and contradictions found
by bounded linear elimination. The report appears below the workspace and stays
with its file tab. Occurrence is checked after polynomial normalization modulo
the file's modulus; mentioning a wire in `x × 0 = 0` does not constrain it.
Unused non-output wires and tautologies are informational, not proof of a vulnerability.

The report lists all nine original source check categories: unconstrained outputs
and unused signals have partial coverage; the other seven explicitly say
**unavailable / 无法检查**, because assignments, types, component boundaries and
conditional computation are missing from R1CS. No findings does not establish
safety. Output uniqueness, general nonlinear satisfiability and application logic
are not checked. See the [full scope](../README.md#view-and-analyze-an-r1cs-file).

The analysis uses a 20-second cooperative time budget, at most 4,096 products
per constraint / 1,000,000 overall, and linear elimination limited to 256 basis
rows, 128 terms per row and 100,000 reduction operations. Exceeded limits produce
a partial report. At most 200 findings are displayed; counts include the remainder.
Only one R1CS analysis runs at a time; a second request receives HTTP 409.
**Stop** requests cancellation. Closed uploads in use by a worker are deleted
when that worker releases them; active analyses prevent expiry.

API: `POST /r1cs/<id>/analyze` returns a `session_id`. The existing
`GET /progress/<session_id>` stream delivers progress and a structured R1CS report
in its `complete` event. `POST /stop/<session_id>` cancels the analysis. Expired
or closed uploads return HTTP 404. Circom `/analyze` retains its text report format.

## Known Limitations

- **Stop** requests cancellation, but the background detection may continue
  until its current computation finishes. Use `Ctrl+C` in the server terminal
  to stop the service.

## Upload Limits

- Maximum upload request size: 100 MiB, including all files and form data.
- Single-file upload accepts UTF-8 `.circom` source and binary `.r1cs` files.

## Development Checks

From the repository root, run `python -m unittest discover -s tests -v` with the
project environment's Python. With Node.js available, run
`node --test tests/r1cs_format.test.cjs tests/r1cs_workspace.test.cjs` for coefficient
display, file-switching, analysis lifecycle and asynchronous response checks. Node.js is needed
only for these JavaScript development tests, not to run the viewer.

