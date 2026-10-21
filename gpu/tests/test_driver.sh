#!/usr/bin/env bash
# Tests for gpu/driver.sh against FAKE host layouts built in a temp dir. No GPU, no root needed.
#   bash gpu/tests/test_driver.sh          exit 0 = all passed
# DRIVER_SH=/path overrides the script under test.
set -u
here=$(cd "$(dirname "$0")" && pwd)
DRIVER=${DRIVER_SH:-$here/../driver.sh}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1  [$2]"; fi; }

ELF64=$'\x7fELF\x02\x01\x01'   # magic + class 64: all driver.sh looks at
ELF32=$'\x7fELF\x01\x01\x01'
mklib() { # mklib DIR NAME [elf-header]
  mkdir -p "$1"; printf '%s' "${3:-$ELF64}" > "$1/$2"
}
snapshot() { # everything under the given dirs, excluding $EXCLUDE, with type/size/mtime/link target
  find "$@" -not -path "${EXCLUDE:-/nonexistent}*" -printf '%p %y %s %T@ %l\n' 2>/dev/null | LC_ALL=C sort
}

# --- a Debian-like host: driver and the host's libc live in the SAME directory
R=$T/debian
D=$R/usr/lib/x86_64-linux-gnu
for n in libcuda.so.1 libcuda.so.580.82 libnvidia-ml.so.1 libnvidia-ml.so.580.82 \
         libnvidia-ptxjitcompiler.so.1 libnvidia-nvvm.so.4 libnvidia-gpucomp.so.580.82 libcudadebugger.so.1 \
         libc.so.6 libm.so.6 libpthread.so.0 libGL.so.1 libnvidia-glcore.so.580.82; do mklib "$D" "$n"; done
ln -s libcuda.so.580.82 "$D/libcuda.so"            # a relative symlink in the host dir: must still resolve
SEARCH_DEBIAN=$D
W=$T/work1; mkdir -p "$W" "$T/cwd1"

# 1. Debian layout: libs found, stdout is the new LD_LIBRARY_PATH, stderr says where each came from
out=$(cd "$T/cwd1" && NIXSCI_DRIVER_SEARCH=$SEARCH_DEBIAN LD_LIBRARY_PATH=/prev bash "$DRIVER" "$W" 2>"$T/err1")
check "debian: stdout is workdir/driver/lib prepended to the old LD_LIBRARY_PATH" '[ "$out" = "$W/driver/lib:/prev" ]'
check "debian: libcuda.so.1 linked"            '[ -L "$W/driver/lib/libcuda.so.1" ]'
check "debian: libnvidia-ml, ptxjit, nvvm, gpucomp, cudadebugger linked" \
  'for n in libnvidia-ml.so.1 libnvidia-ptxjitcompiler.so.1 libnvidia-nvvm.so.4 libnvidia-gpucomp.so.580.82 libcudadebugger.so.1; do [ -L "$W/driver/lib/$n" ] || exit 1; done'
check "debian: libcuda.so (relative link in host dir) resolves" '[ -e "$W/driver/lib/libcuda.so" ]'
check "debian: stderr names the source directory of each lib" 'grep -qF "libcuda.so.1 <- $D" "$T/err1" && grep -qF "libnvidia-ml.so.1 <- $D" "$T/err1"'
check "debian: the host libc is NOT linked (that is the whole point)" \
  '[ ! -e "$W/driver/lib/libc.so.6" ] && [ ! -e "$W/driver/lib/libm.so.6" ] && [ ! -e "$W/driver/lib/libpthread.so.0" ]'
check "debian: unrelated GL / glcore libs not linked" '[ ! -e "$W/driver/lib/libGL.so.1" ] && [ ! -e "$W/driver/lib/libnvidia-glcore.so.580.82" ]'
check "debian: symlinks only, every one points into the chosen host dir" \
  'n=0; for f in "$W"/driver/lib/* "$W"/driver/lib/.[!.]*; do [ -e "$f" ] || [ -L "$f" ] || continue; [ -L "$f" ] || exit 1; case "$(readlink "$f")" in "$D"/*) ;; *) exit 1;; esac; n=$((n+1)); done; [ "$n" -ge 8 ]'
check "debian: find -type f under driver/ is empty" '[ -z "$(find "$W/driver" -type f)" ]'

# 2. nothing outside the workdir is touched (fake root, cwd and the host dirs are identical before/after)
W2=$T/work2; mkdir -p "$W2"
before=$(EXCLUDE= snapshot "$R" "$T/cwd1")
(cd "$T/cwd1" && NIXSCI_DRIVER_SEARCH=$SEARCH_DEBIAN bash "$DRIVER" "$W2" >/dev/null 2>&1)
after=$(EXCLUDE= snapshot "$R" "$T/cwd1")
check "outside workdir: fake root and cwd unchanged by a run" '[ "$before" = "$after" ]'
check "outside workdir: only workdir/driver was created in the workdir" '[ "$(ls -A "$W2")" = "driver" ] && [ "$(ls -A "$W2/driver")" = "lib" ]'

# 3. default workdir resolution: $1, then NIX_DEPLOY_WORKDIR, then $PWD
W3=$T/work3; mkdir -p "$W3" "$T/cwd3"
(cd "$T/cwd3" && NIX_DEPLOY_WORKDIR=$W3 NIXSCI_DRIVER_SEARCH=$SEARCH_DEBIAN bash "$DRIVER" >/dev/null 2>&1)
check "workdir: NIX_DEPLOY_WORKDIR is used" '[ -L "$W3/driver/lib/libcuda.so.1" ] && [ ! -e "$T/cwd3/driver" ]'
(cd "$T/cwd3" && env -u NIX_DEPLOY_WORKDIR NIXSCI_DRIVER_SEARCH=$SEARCH_DEBIAN bash "$DRIVER" >/dev/null 2>&1)
check "workdir: falls back to \$PWD" '[ -L "$T/cwd3/driver/lib/libcuda.so.1" ]'

# 4. search order: five candidate dirs all have a driver; remove the winner each round, the next must win
P=$T/multi
dirs=("$P/run/opengl-driver/lib" "$P/usr/lib/x86_64-linux-gnu" "$P/usr/lib64" "$P/usr/lib" "$P/usr/lib/nvidia")
for d in "${dirs[@]}"; do mklib "$d" libcuda.so.1; mklib "$d" libnvidia-ml.so.1; done
SEARCH_MULTI=$(IFS=:; echo "${dirs[*]}")
for i in 0 1 2 3 4; do
  W4=$T/order$i; mkdir -p "$W4"
  NIXSCI_DRIVER_SEARCH=$SEARCH_MULTI bash "$DRIVER" "$W4" >/dev/null 2>"$T/err4"
  check "order: round $i takes libcuda.so.1 from ${dirs[$i]#"$P"}" '[ "$(readlink "$W4/driver/lib/libcuda.so.1")" = "${dirs[$i]}/libcuda.so.1" ]'
  check "order: round $i takes EVERY lib from that one directory (no mixing)" \
    '[ "$(readlink "$W4/driver/lib/libnvidia-ml.so.1")" = "${dirs[$i]}/libnvidia-ml.so.1" ]'
  rm -f "${dirs[$i]}/libcuda.so.1" "${dirs[$i]}/libnvidia-ml.so.1"
done

# 4b. a directory that has only NVML (no libcuda), or a 32-bit libcuda, is not a driver directory
Q=$T/skip; mklib "$Q/a" libnvidia-ml.so.1; mklib "$Q/b" libcuda.so.1 "$ELF32"; mklib "$Q/c" libcuda.so.1; mklib "$Q/c" libnvidia-ml.so.1
W4b=$T/skipw; mkdir -p "$W4b"
NIXSCI_DRIVER_SEARCH=$Q/a:$Q/b:$Q/c bash "$DRIVER" "$W4b" >/dev/null 2>&1
check "skip: no-libcuda dir and 32-bit libcuda are passed over" '[ "$(readlink "$W4b/driver/lib/libcuda.so.1")" = "$Q/c/libcuda.so.1" ]'

# 4c. the DEFAULT search list is the documented one, in the documented order
default=$(sed -n 's/^search=\${NIXSCI_DRIVER_SEARCH:-\(.*\)}$/\1/p' "$DRIVER")
check "default search order" '[ "$default" = "/run/opengl-driver/lib:/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu:/usr/lib64:/usr/lib:/usr/lib/nvidia" ]'

# 5. no driver anywhere: exit 0, exact message, LD_LIBRARY_PATH untouched, nothing created
W5=$T/work5; mkdir -p "$W5"; mklib "$T/nodrv/usr/lib" libc.so.6; mklib "$T/nodrv/usr/lib" libnvidia-ml.so.1
out=$(NIXSCI_DRIVER_SEARCH=$T/nodrv/usr/lib:$T/nodrv/missing LD_LIBRARY_PATH=/keep:/me bash "$DRIVER" "$W5" 2>"$T/err5"); rc=$?
check "no driver: exit 0" '[ "$rc" = 0 ]'
check "no driver: message on stderr" 'grep -q "no NVIDIA driver library found on this host" "$T/err5"'
check "no driver: LD_LIBRARY_PATH value returned unchanged" '[ "$out" = "/keep:/me" ]'
check "no driver: nothing created in the workdir" '[ -z "$(ls -A "$W5")" ]'
out=$(env -u LD_LIBRARY_PATH NIXSCI_DRIVER_SEARCH=$T/nodrv/missing bash "$DRIVER" "$W5" 2>/dev/null)
check "no driver: unset LD_LIBRARY_PATH stays empty (not ':' or the workdir)" '[ -z "$out" ]'

# 6. re-run: stale links go, regular files are never deleted
W6=$T/work6; mkdir -p "$W6/driver/lib"; ln -s /nonexistent "$W6/driver/lib/libstale.so"; echo keep > "$W6/driver/lib/keep.txt"
NIXSCI_DRIVER_SEARCH=$SEARCH_DEBIAN bash "$DRIVER" "$W6" >/dev/null 2>&1
check "rerun: stale symlink removed" '[ ! -L "$W6/driver/lib/libstale.so" ]'
check "rerun: a regular file in the directory is left alone" '[ "$(cat "$W6/driver/lib/keep.txt")" = keep ]'
check "rerun: links present" '[ -L "$W6/driver/lib/libcuda.so.1" ]'

# 7. NEEDED chain via readelf (stub): libcuda -> libnvidia-a -> libnvidia-b; libc.so.6 must not follow
S=$T/stub; mkdir -p "$S/bin"
cat > "$S/bin/readelf" <<'R'
#!/bin/sh
case "$3" in
  */libcuda.so.1)     echo ' 0x0000000000000001 (NEEDED)             Shared library: [libnvidia-chain-a.so.1]'; echo ' 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]' ;;
  */libnvidia-chain-a.so.1) echo ' 0x0000000000000001 (NEEDED)             Shared library: [libnvidia-chain-b.so.1]' ;;
esac
R
chmod +x "$S/bin/readelf"
mklib "$T/chain" libcuda.so.1; mklib "$T/chain" libnvidia-chain-a.so.1; mklib "$T/chain" libnvidia-chain-b.so.1; mklib "$T/chain" libc.so.6
W7=$T/work7; mkdir -p "$W7"
PATH="$S/bin:$PATH" NIXSCI_DRIVER_SEARCH=$T/chain bash "$DRIVER" "$W7" >/dev/null 2>&1
check "needed: transitive libnvidia-* dependencies of libcuda are linked" '[ -L "$W7/driver/lib/libnvidia-chain-a.so.1" ] && [ -L "$W7/driver/lib/libnvidia-chain-b.so.1" ]'
check "needed: non-NVIDIA dependencies (libc) are not" '[ ! -e "$W7/driver/lib/libc.so.6" ]'

# 8. no readelf at all: the fixed list is enough
B=$T/minbin; mkdir -p "$B"; for t in od tr ln rm mkdir sed cat dirname; do ln -s "$(command -v $t)" "$B/$t"; done
W8=$T/work8; mkdir -p "$W8"
PATH=$B NIXSCI_DRIVER_SEARCH=$SEARCH_DEBIAN "$(command -v bash)" "$DRIVER" "$W8" >/dev/null 2>&1
check "no readelf: fixed list still links libcuda and libnvidia-ml" '[ -L "$W8/driver/lib/libcuda.so.1" ] && [ -L "$W8/driver/lib/libnvidia-ml.so.1" ]'

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" = 0 ]
