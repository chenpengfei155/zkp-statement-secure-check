#!/usr/bin/env bash
set -euo pipefail
prefix=${CIRVERIFY_PICUS_HOME:-"$HOME/.local/share/cirverify-picus"}
# Ubuntu 22.04's Racket 8.2 cannot use OpenSSL 3 with HTTPS proxies.
# A private runtime also keeps the user's other Racket installations untouched.
if [[ ! -x $prefix/racket-8.16/bin/racket ]]; then
    [[ $(uname -m) == x86_64 ]] || { echo 'This installer currently supports x86_64 WSL only.' >&2; exit 1; }
    archive="$prefix/racket-full-8.16.sh"
    hash=112e130000ab0b3cdddf907de2a357502a0643123af568fb20d4f118c96a4b8a
    if ! printf '%s  %s\n' "$hash" "$archive" | sha256sum --check --status; then
        curl --fail --location --retry 3 --retry-all-errors https://download.racket-lang.org/releases/8.16/installers/racket-8.16-x86_64-linux-cs.sh -o "$archive"
    fi
    printf '%s  %s\n' "$hash" "$archive" | sha256sum --check
    sh "$archive" --in-place --dest "$prefix/racket-8.16"
fi
export PATH="$prefix/racket-8.16/bin:$prefix/bin:$PATH"
# The full distribution includes the standard libraries and their build dependencies.
raco pkg install --auto --batch --skip-installed --no-docs --jobs 4 --name cirverify-picus "$prefix/Picus"
raco make "$prefix/Picus/picus.rkt"
