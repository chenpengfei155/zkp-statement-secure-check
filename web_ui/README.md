# CirVerify Web UI

[English project guide](../README.md) | [简体中文项目说明](../README.zh-CN.md)

A web interface for analyzing Circom source, viewing binary R1CS constraints and checking output uniqueness with Picus.

## Features

- 📝 **Type Code**: Write or paste Circom code directly in the editor
- 📚 **Examples**: Select from pre-loaded example circuits with common issues
- 📁 **Upload**: Open `.circom` files for source analysis or `.r1cs` files for viewing and Picus analysis
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
**Analyze** for Picus output-uniqueness checks. Its compact overview shows the file size, constraint/variable
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

Click **Analyze** after the [one-time Picus installation](../README.md#install-picus-once-windows--wsl).
The report displays `safe`, `unsafe`, or `unknown`, plus elapsed time and logs.
Picus checks unique public outputs for identical public and private inputs;
it does not prove business correctness or general satisfiability. No public
outputs means `not_applicable`, not a passing result. Counterexamples show
wire numbers and two alternative outputs, with decimal strings preserving precision.

### Picus configuration

Set these **server-side environment variables** before starting Flask:

| Variable | Default |
|---|---|
| `CIRVERIFY_PICUS_DISTRO` | `Ubuntu-22.04` |
| `CIRVERIFY_PICUS_HOME` | `~/.local/share/cirverify-picus` (Linux path) |
| `CIRVERIFY_PICUS_TIMEOUT` | `120` seconds per task |
| `CIRVERIFY_PICUS_QUERY_TIMEOUT_MS` | `5000` milliseconds per solver query |
| `CIRVERIFY_PICUS_MEMORY_MIB` | `4096` per Linux child process |

The fixed Picus revision is `138b151d3a388e5b6c040c163e0a1db04f2ceda6`;
cvc5 is built at `de62429fa7c03a46d5d75f9d78fc8888792a0798` with CoCoA.
The installer uses the official Racket 8.16 x86_64 distribution, with SHA-256
verification. CoCoA's moved download URL is replaced by its current official
archive URL, checked against the hash required by the pinned cvc5 source.
The first installation needs network access and several GiB of disk space.
The installer uses root only for Ubuntu packages; the tools live in the WSL
user's directory. The backend uses argument arrays, a private temporary working
directory, and a Linux process group. Stop, timeout, or loss of the supervisor's
parent pipe kills/reaps the group; it never terminates the entire WSL distribution.
No automatic fallback to the former finite R1CS checks is performed.

API: `GET /r1cs/engine` returns readiness, pinned revision and a reason.
`POST /r1cs/<id>/analyze` returns a `session_id`; it returns 503 when unavailable,
422 for incompatible input counts, 404 for expired uploads and 409 when busy.
`GET /progress/<session_id>` emits stage/elapsed-time updates and a `complete`
event containing `kind`, `engine`, `revision`, `solver`, `scope`, `verdict`,
`reason`, `exit_code`, `elapsed_seconds`, `logs`, and optional `counterexample`.
Verdicts are `safe`, `unsafe`, `unknown`, `error`, `cancelled`, `not_applicable`.
`POST /stop/<session_id>` requests cancellation; the lock is retained until
cleanup finishes. Upload leases prevent expiry during analysis.
Circom `/analyze` retains its existing text format and behavior.

## Known Limitations

- For **Circom**, **Stop** requests cancellation, but background detection may continue
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

After installing Picus, test the actual WSL engine (no mocks):

```powershell
.\.venv\Scripts\python.exe .\scripts\check_picus.py
$env:CIRVERIFY_TEST_PICUS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The real-engine suite compares the demo's web result to the upstream command,
checks both counterexample outputs against `out² = 1` in two fields, and tests
zero constraints, reordered sections, Unicode paths, timeout, cancellation,
parent-pipe closure, process reaping and temporary-directory cleanup. The
installer also runs real `safe`/`unsafe` smoke tests before reporting success.

