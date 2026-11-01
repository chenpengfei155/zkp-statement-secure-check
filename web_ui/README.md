# CirVerify Web UI

[English project guide](../README.md) | [简体中文项目说明](../README.zh-CN.md)

A web interface for analyzing Circom source, viewing binary R1CS constraints and running five R1CS checks with Picus, cvc5 and structural analysis.

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
analysis of the folder. The selected file is sent together with the other source
files in that folder, preserving relative `include` paths and current editor
changes. Select the project folder containing both the entry and its libraries.

Every accepted source analysis returns a report. Completed checks show findings
or no findings; syntax errors, missing dependencies, resource limits and analyzer
failures return `unknown`, with diagnostic details and no safety conclusion.
Library files without `component main` return `not_applicable`. Cancellation
returns `cancelled`. An incomplete result never means zero warnings or a pass.

Source analyses run in separate processes with a default 150-second time limit.
To change it, set `CIRVERIFY_SOURCE_TIMEOUT_SECONDS` before starting the server
(for example, `$env:CIRVERIFY_SOURCE_TIMEOUT_SECONDS = "300"` in PowerShell).
The `/analyze_folder` API returns one report per uploaded source file and continues
after an individual file fails or times out.

The web interface has no JSON download button. Use the
[CLI examples](../README.md#example-usage) with `--json` to save a report.

### R1CS Constraint Viewer and Analysis

Use **Upload File** to open `demo1.r1cs`. The viewer opens automatically and offers
**Analyze** for five R1CS checks. Its compact overview shows the file size, constraint/variable
counts and public/private input and public output counts. Expand **Field modulus
& reading guide** for the full modulus and explanation. The **← / →** buttons
page through 50 equations at a time in the form `A × B − C = 0`.

Variables appear as `w1`, `w2`, etc.; `w0 = 1` is the constant. The file does not
provide original names or actual input values. **Signed coefficients** displays
equivalent small negative coefficients by default (`p−1` becomes `−1`);
**Original coefficients** displays the original nonnegative coefficients. Both use
arithmetic modulo the file's prime, without changing the file.

Source edits and R1CS view settings survive tab switching. Re-uploading a filename
replaces its tab. Closing a file tab or using **Clear** releases its temporary R1CS
upload; 30 minutes without a server request also expires it. Upload again after
expiry or a server restart. Original files on your computer are unchanged.

The viewer uses Python's standard library and does not need Node.js or `snarkjs`.
It supports standard version 1 files, but not custom-gate extensions or `.sym`
files. It does not restore source or generate proofs.
Folder selection and the CLI remain Circom-only.

Click **Analyze** after the [one-time native Picus installation](../README.md#install-picus-once-native-windows).
The report lists satisfiability, output uniqueness, all-signal uniqueness
(including internal wires), unused wires and trivial constraints. Each has a
separate pass, finding, unknown, error or skipped status.
It uses Circom's **Analysis Result** heading, light result panel and English
`[Info]`, `[Success]` or `[Warning]` text output. Counterexamples appear below the
summary; **Run details and raw logs** retains the original Picus output.
Picus checks unique public outputs for identical public and private inputs and
then uses `--strong` to include internal wires. Satisfiability separately checks
whether at least one satisfying assignment exists. During the constraint scan,
it first verifies the candidate `w0 = 1`, all other wires `0`, against every
equation modulo the field prime. If all equations hold, the report records this
verified witness and skips the standalone cvc5 query. A failed candidate does
not imply unsatisfiability. It next tries input values of zero and one, propagates
single-unknown linear equations, and reconstructs binary values where explicit Boolean
constraints and distinct power-of-two weights permit it. This handles circuits
whose internal wires must be nonzero even when their inputs are zero, such as
`Sign`. Every resulting candidate is checked again against every original
constraint before it can count as a pass. The report identifies whether the
assignment was all-zero or constructed by propagation.
If fixed seeds fail, the search can derive up to eight inputs that occur once
in affine equations, individually or together, to satisfy commitment comparisons.
The report lists those input wires; every original equation must still hold.

The candidate search can also verify two complete assignments with identical
inputs. If their outputs differ, output uniqueness fails with a verified
counterexample; if only internal values differ, the all-signal check reports a
finding. Those negative conclusions need no further solver search. Identical
candidate outputs do not prove uniqueness: unresolved checks still use Picus.
Each input seed is searched independently. A pair from a later seed can expose
different outputs, but assignments with different inputs are never paired.

A separate symbolic proof propagates equality between any two valid assignments
with the same inputs. It uses nonzero constant linear coefficients and injective
Boolean weighted sums whose integer range is smaller than the field prime.
Unlike witness search, this proof never treats a sampled input as nonzero and
never treats a modular bit decomposition as unique when it can wrap. A successful
proof completes the corresponding uniqueness check; unproved checks retain Picus.

Candidate construction uses a one-second budget checked between constraint
processing steps, plus file-size, term-count, and work limits.
A conflict, unsupported equation, or exhausted search budget
falls back to the original unrestricted cvc5 query; none proves unsatisfiability.
These shortcuts apply to the known BN254 prime and exactly verified primes up
to 65,536; other moduli retain cvc5's field validation and solving. This existence check does not
prove that every input has a solution or that outputs are unique.
Unsatisfiable systems skip uniqueness
checks. No public outputs skips only output uniqueness; the remaining checks
run without a green overall pass. Counterexamples show wire numbers and two
alternative outputs/internal values, with decimal strings preserving precision.
Structural and strong-mode findings produce an advisory `warning`, whereas
non-unique outputs produce `unsafe`, and inconsistent constraints produce
`unsatisfiable`. `safe` requires all five checks to pass; `unknown` never counts
as passing. These properties do not establish intended business functionality.

Structural checks normalize A*B-C modulo the field, eliminating zero coefficients
and treating w0 as 1. For the known prime fields, they compute effective variables
and trivial constraints exactly in linear time without expanding every product.
Other moduli retain the polynomial expansion fallback, capped at 20,000 term
operations per row and 1,000,000 per file. Incomplete fallback scans are marked
partial/unknown. At most 100 findings per check are displayed; counts include all
findings discovered. Solver queries are written only when verified assignments
did not already establish satisfiability.

### Picus configuration

Set these **server-side environment variables** before starting Flask:

| Variable | Default |
|---|---|
| `CIRVERIFY_PICUS_HOME` | Windows: `<project>/.tools/picus`; Linux: `~/.local/share/cirverify-picus` |
| `CIRVERIFY_PICUS_TIMEOUT` | `120` seconds per task |
| `CIRVERIFY_PICUS_QUERY_TIMEOUT_MS` | `5000` milliseconds per solver query |
| `CIRVERIFY_PICUS_MEMORY_MIB` | `4096` per child process (Windows committed memory; Linux address space) |

The 120-second budget covers the entire five-check task. When needed, the standalone cvc5
satisfiability query uses the same 5-second query limit (plus a two-second
process watchdog allowance). Output uniqueness gets half the time remaining
after that stage; strong mode gets the rest. Each stage uses a Windows Job Object
or Linux process group so cancellation/timeout releases descendants and temporary files.
`Satisfiability check timed out` means no conclusion was reached within this
budget; it does not mean the constraints are inconsistent. Increasing the time
limit may still be insufficient for large systems or may encounter the memory limit.
Explicit solver out-of-memory diagnostics and worker `MemoryError` produce an
`unknown` check with reason `memory_limit`, preserving diagnostics, exit codes
and completed checks. They do not establish satisfiability or uniqueness.
Other process failures and invalid solver responses remain errors.

The fixed Picus revision is `138b151d3a388e5b6c040c163e0a1db04f2ceda6`;
cvc5 is built at `de62429fa7c03a46d5d75f9d78fc8888792a0798` with CoCoA.
On Windows x64, `scripts/setup_picus.ps1` installs the included standalone runtime
offline into `.tools/picus`, with archive and per-file SHA-256 verification.
Racket 8.16 and its packages are embedded, so users do not install Racket, cvc5,
WSL or a compiler. Run `scripts/check_picus.py` to repeat the environment and
real-circuit checks. Missing/damaged runtime files produce an installation error;
there is no fallback to WSL or another checker. Circom and the R1CS viewer remain usable.
See the [runtime package](../vendor/picus/windows-x64/README.md) for licenses,
corresponding source archives and maintainer build instructions.

Use `-HomeDirectory 'C:\Tools\CirVerify Picus'` to install elsewhere, and set
`CIRVERIFY_PICUS_HOME` to that Windows directory before starting Flask. Old
`CIRVERIFY_PICUS_DISTRO` settings are unused; remove Linux-valued home overrides.
Stop the web service before updating a runtime currently in use. Failed self-checks
leave the previous installation intact; successful updates retain a backup directory.

The backend uses argument arrays and a private temporary directory. Windows
children join a Job Object before any solver starts. Stop, timeout, parent-pipe
closure or supervisor termination kills the analysis descendants, and the parent
retains its concurrency lock and uploaded-file lease until cleanup completes.
Linux installations still use the native `scripts/setup_picus.sh` workflow.

API: `GET /r1cs/engine` returns readiness, pinned revision and a reason.
`POST /r1cs/<id>/analyze` returns a `session_id`; it returns 503 when unavailable,
422 for incompatible input counts, 404 for expired uploads and 409 when busy.
`GET /progress/<session_id>` emits stage/elapsed-time updates and a `complete`
event containing `kind`, `engine`, `revision`, `solver`, `platform`, `scope`, `verdict`,
`reason`, `exit_code`, `elapsed_seconds`, `logs`, and optional `counterexample`.
Verdicts are `safe`, `unsafe`, `warning`, `unsatisfiable`, `unknown`, `error`, `cancelled`, `not_applicable`.
`POST /stop/<session_id>` requests cancellation; the lock is retained until
cleanup finishes. Upload leases prevent expiry during analysis.
Circom `/analyze` returns a check-by-check summary. Each detector is marked
`[Success]` or `[Warning]`; warning findings can be expanded for their source
locations and messages. Parser, detector and tqdm output is kept out of the
browser result.

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

After installing Picus, test the actual native engine (no mocks):

```powershell
.\.venv\Scripts\python.exe .\scripts\check_picus.py
$env:CIRVERIFY_TEST_PICUS = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The real-engine suite compares the demo's web result to the upstream command,
checks both counterexample outputs against `out² = 1` in two fields, and tests
unique outputs with ambiguous internal wires, inconsistent systems, no-output
circuits, structural advisories, zero constraints, reordered sections, Unicode paths,
timeouts that preserve completed checks, cancellation in each solver stage,
parent-pipe closure, process reaping and temporary-directory cleanup. The
installer also runs real `safe`/`unsafe` smoke tests before reporting success.

