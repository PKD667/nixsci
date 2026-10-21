#!/usr/bin/env bash
# Bind a Nix-built CUDA program to the HOST's NVIDIA userspace driver.
#
#   ld=$(driver.sh [WORKDIR])        # run it
#   LD_LIBRARY_PATH="$ld" probe      # use the result for ONE command, not for the whole shell
#
# stdout: the value LD_LIBRARY_PATH should have for the program (nothing else is ever written there).
# stderr: which directory every library came from, or `no NVIDIA driver library found on this host`.
# Exit: 0 always (a missing driver is the program's own error to report, not a hang here).
#
# Why a directory of symlinks and not LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu: that directory
# also holds the host's libc, and a Nix binary must keep its own. The directory built here holds
# libcuda and its companions only, so nothing but the driver can be found through it.
#
# The directory is WORKDIR/driver/lib, WORKDIR = $1, else $NIX_DEPLOY_WORKDIR, else $PWD. Nothing
# outside it is created or modified. The libraries all come from ONE host directory (the first in
# the search list that has libcuda.so.1) so two driver versions are never mixed.
#
# NIXSCI_DRIVER_SEARCH (colon separated) replaces the default search list; used by the tests.
set -u

workdir=${1:-${NIX_DEPLOY_WORKDIR:-$PWD}}
libdir=$workdir/driver/lib
search=${NIXSCI_DRIVER_SEARCH:-/run/opengl-driver/lib:/usr/lib/x86_64-linux-gnu:/usr/lib/aarch64-linux-gnu:/usr/lib64:/usr/lib:/usr/lib/nvidia}
orig=${LD_LIBRARY_PATH:-}

say() { printf 'driver.sh: %s\n' "$*" >&2; }

# 64-bit ELF? (a /usr/lib or /usr/lib64 may hold 32-bit libraries)
is_elf64() {
  local magic
  magic=$(od -An -tx1 -N5 -- "$1" 2>/dev/null | tr -d ' \n') || return 1
  [ "$magic" = 7f454c4602 ]
}

# Patterns of the libraries that make up the userspace driver as a CUDA program uses it.
# libcuda dlopens the PTX JIT compiler and friends, so NEEDED alone does not list them.
patterns=(
  'libcuda.so*'
  'libcudadebugger.so*'
  'libnvidia-ml.so*'
  'libnvidia-ptxjitcompiler.so*'
  'libnvidia-nvvm*'
  'libnvidia-gpucomp.so*'
  'libnvidia-fatbinaryloader.so*'
)

found=
for d in ${search//:/ }; do
  [ -e "$d/libcuda.so.1" ] || continue
  is_elf64 "$d/libcuda.so.1" || { say "skip $d: libcuda.so.1 is not a 64-bit ELF"; continue; }
  found=$d
  break
done

if [ -z "$found" ]; then
  say "no NVIDIA driver library found on this host (searched: $search)"
  printf '%s\n' "$orig"
  exit 0
fi

mkdir -p -- "$libdir" || { say "cannot create $libdir"; printf '%s\n' "$orig"; exit 0; }
# a previous run's links, never anything that is not a symlink
for old in "$libdir"/* "$libdir"/.[!.]*; do
  [ -L "$old" ] && rm -f -- "$old"
done

declare -A linked=()
link() { # link NAME
  local name=$1
  [ -n "${linked[$name]:-}" ] && return
  [ -e "$found/$name" ] || return
  ln -s -- "$found/$name" "$libdir/$name" || return
  linked[$name]=1
  say "$name <- $found"
}

for p in "${patterns[@]}"; do
  for f in "$found"/$p; do
    [ -e "$f" ] || continue
    link "${f##*/}"
  done
done

# whatever else libcuda (and what it pulls in) NEEDs from the same vendor family, when readelf exists
if command -v readelf >/dev/null 2>&1; then
  queue=(libcuda.so.1)
  seen=" "
  while [ "${#queue[@]}" -gt 0 ]; do
    cur=${queue[0]}; queue=("${queue[@]:1}")
    case "$seen" in *" $cur "*) continue ;; esac
    seen="$seen$cur "
    [ -e "$found/$cur" ] || continue
    while IFS= read -r need; do
      case "$need" in
        libnvidia-*|libcuda*) link "$need"; queue+=("$need") ;;
      esac
    done < <(readelf -d -- "$found/$cur" 2>/dev/null | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p')
  done
fi

# the new directory goes first, so the host driver wins over anything the closure might carry
printf '%s\n' "${libdir}${orig:+:$orig}"
