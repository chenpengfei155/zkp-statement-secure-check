#!/usr/bin/env bash
set -euo pipefail
PICUS_REV=138b151d3a388e5b6c040c163e0a1db04f2ceda6
CVC5_REV=de62429fa7c03a46d5d75f9d78fc8888792a0798
if [[ ${1:-} == --system-deps ]]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends git ca-certificates build-essential cmake ninja-build libgmp-dev libssl-dev python3 python3-pip curl
    exit 0
fi
jobs=4
if [[ ${1:-} == --jobs ]]; then jobs=$2; fi
[[ $jobs =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid job count'; exit 1; }
prefix=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "${CIRVERIFY_PICUS_HOME:-$HOME/.local/share/cirverify-picus}")
export CIRVERIFY_PICUS_HOME="$prefix"
mkdir -p "$prefix"
exec 9>"$prefix/install.lock"
flock -n 9 || { echo 'Another Picus installation is running.'; exit 1; }
checkout() {
    local url=$1 directory=$2 revision=$3
    if [[ ! -d $directory/.git ]]; then
        git init "$directory"
        git -C "$directory" remote add origin "$url"
    fi
    if [[ -n $(git -C "$directory" status --porcelain --untracked-files=no) ]]; then
        echo "Refusing to replace modified source files in $directory" >&2; exit 1
    fi
    git -C "$directory" fetch --depth 1 origin "$revision"
    git -C "$directory" checkout --detach FETCH_HEAD
}
checkout https://github.com/Veridise/Picus.git "$prefix/Picus" "$PICUS_REV"
python3 -m pip install --user 'tomli==2.0.1'
if [[ ! -x $prefix/bin/cvc5 || ! -f $prefix/cvc5-revision || $(cat "$prefix/cvc5-revision") != "$CVC5_REV" ]]; then
    checkout https://github.com/cvc5/cvc5.git "$prefix/cvc5-src" "$CVC5_REV"
    (
        cd "$prefix/cvc5-src"
        ./configure.sh production --cocoa --auto-download --ninja --prefix="$prefix"
        # The historical HTTP URL now redirects to an unrelated page. Pre-seed
        # CMake's archive cache from CoCoA's current site, using the pinned hash.
        cocoa=build/deps/src/CoCoALib-0.99800.tgz
        cocoa_hash=f8bb227e2e1729e171cf7ac2008af71df25914607712c35db7bcb5a044a928c6
        if ! printf '%s  %s\n' "$cocoa_hash" "$cocoa" | sha256sum --check --status; then
            mkdir -p "$(dirname "$cocoa")"
            curl --fail --location --retry 3 https://cocoa.altervista.org/cocoalib/tgz/CoCoALib-0.99800.tgz -o "$cocoa"
            printf '%s  %s\n' "$cocoa_hash" "$cocoa" | sha256sum --check
        fi
        export PYTHONDONTWRITEBYTECODE=1
        cmake --build build -j "$jobs"
        cmake --install build
    )
    printf '%s\n' "$CVC5_REV" > "$prefix/cvc5-revision"
fi
export PATH="$prefix/racket-8.16/bin:$prefix/bin:$PATH"
bash "$(dirname "${BASH_SOURCE[0]}")/setup_picus_racket.sh"
"$prefix/bin/cvc5" --version | head -n 2
printf '%s\n' '(set-logic QF_FF)' '(declare-fun x () (_ FiniteField 17))' '(assert (= x #f1m17))' '(check-sat)' | "$prefix/bin/cvc5" --lang smt2 | grep -x sat
printf '%s\n' "$PICUS_REV" > "$prefix/picus-revision"
python3 "$(dirname "${BASH_SOURCE[0]}")/check_picus.py"
echo "Installed Picus in $prefix"
