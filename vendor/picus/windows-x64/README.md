# Native Windows Picus runtime

This folder is part of the distributable CirVerify project. Keep `runtime.zip`,
`manifest.json`, `sources.zip`, this file, and `SOURCE_HASHES.md` together when
redistributing it. The runtime is an x64 Windows build, not a Linux executable
or a WSL launcher. No network access is required by the installer or analysis.

From the project root, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_picus.ps1
```

The installer checks SHA-256, extracts to `.tools/picus`, and tests real safe
and unsafe circuits before replacing an existing installation. Python must be
64-bit. An older installation is retained as `picus.backup-*` when upgrading.
The archive contains the Racket runtime and packages; installing Racket globally
is unnecessary. This packaging targets Windows x64; macOS is not supported by
this bundle. Linux can use `scripts/setup_picus.sh`.

## Components and corresponding sources

The original licenses and copyright notices are included in `runtime.zip` under
`licenses/`. The corresponding source materials accompany it in `sources.zip`.
Third-party licenses apply to their respective components, independently of
CirVerify's license.

| Component | Version / source | License |
|---|---|---|
| Picus | `138b151d3a388e5b6c040c163e0a1db04f2ceda6`, https://github.com/Veridise/Picus | MIT |
| cvc5 | `de62429fa7c03a46d5d75f9d78fc8888792a0798`, https://github.com/cvc5/cvc5 | BSD-3-Clause; this binary also links CoCoA (GPLv3) |
| CoCoALib | 0.99800, https://cocoa.altervista.org/ | GPLv3 |
| GMP | 6.2.1, https://gmplib.org/ | LGPLv3 / GPLv2 or later, per upstream |
| CaDiCaL | 1.7.4, https://github.com/arminbiere/cadical | MIT |
| SymFPU | `e6ac3af9c2c574498ea171c957425b407625448b`, https://github.com/martin-cs/symfpu | per bundled upstream license |
| Racket CS | 8.16, https://download.racket-lang.org/releases/8.16/ | MIT / Apache-2.0; see Racket notices for embedded components |
| Rosette and Racket packages | exact package sources and catalog checksums in `sources.zip` | per-package licenses included |
| libiconv | 1.15, https://www.gnu.org/software/libiconv/ | LGPLv3 or later |
| MPFR | 3.1.6, https://www.mpfr.org/ | LGPLv3 or later |
| Z3 | 4.8.8, https://github.com/Z3Prover/z3 | MIT; Rosette runtime dependency, not the analysis solver |

The finite-field cvc5 executable includes GPLv3 CoCoA; its corresponding build
sources, upstream dependency archives, modifications and build records are
provided. Racket DLLs remain separate and replaceable. Source hashes identify
the accompanying archives; they are integrity checks, not publisher signatures.

## Building and packaging (maintainers only)

End users do not run these steps. The bundled `cvc5.exe` was cross-compiled with
MinGW on Ubuntu 22.04 using `production --win64 --static --cocoa --no-poly
--auto-download --ninja`. CoCoA configuration probes were executed on Windows,
not on the Linux build host. The source bundle's `build-record/` preserves the
build scripts, package inventory and local probe helpers; their recorded paths
must be adjusted for another build machine. All platform patches are included.
The CoCoA patch adapts POSIX signals and pointer formatting; it does not replace
the solver with another algorithm. The Racket native library build instructions
are in the Racket source archive's `src/native-libs/` directory.

For Picus, install Windows Racket 8.16 and the dependencies from Picus `NOTES.md`,
then package a clean checkout of the pinned revision:

```powershell
.\.venv\Scripts\python.exe scripts\build_windows_picus_package.py `
  --source C:\build\Picus --raco C:\build\racket\raco.exe `
  --cvc5 C:\build\cvc5.exe --licenses C:\build\licenses
```

`picus-embedding.patch` changes only three `define-runtime-path` references to
`define-runtime-module-path`, so Racket's `raco exe` / `raco distribute` embeds
the dynamically selected modules. Picus's checking algorithm and solver options
are unchanged. The source checkout itself is not modified by the packager.
`collect_windows_picus_sources.py` collects the source bundle and license tree
from the exact build directories. Re-run it when updating any binary.

Validate the final archive with `scripts/setup_picus.ps1`, then run
`CIRVERIFY_TEST_PICUS=1` integration tests, including runtime isolation, large
finite-field counterexamples, direct Picus comparisons and process cleanup.
