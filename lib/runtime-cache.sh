#!/usr/bin/env bash
# Runtime-only cache. Keep this target selection aligned with https://bun.sh/install.
bun_runtime_target() {
  local target
  case "$(uname -ms)" in
    'Linux x86_64') target=linux-x64 ;;
    'Linux aarch64' | 'Linux arm64') target=linux-aarch64 ;;
    'Darwin x86_64')
      target=darwin-x64
      if [[ $(sysctl -n sysctl.proc_translated 2>/dev/null) == 1 ]]; then
        target=darwin-aarch64
      fi
      ;;
    'Darwin arm64') target=darwin-aarch64 ;;
    *) return 1 ;; # Unknown installer targets must not share a cached runtime.
  esac
  if [[ $target == linux-* && -f /etc/alpine-release ]]; then
    target="$target-musl"
  fi
  case "$target" in
    linux-x64*)
      if ! cat /proc/cpuinfo | grep avx2 >/dev/null; then target="$target-baseline"; fi
      ;;
    darwin-x64*)
      if ! sysctl -a | grep machdep.cpu | grep AVX2 >/dev/null; then target="$target-baseline"; fi
      ;;
  esac
  printf '%s\n' "$target"
}

bun_runtime_digest() {
  if command -v sha256sum >/dev/null; then
    sha256sum | cut -d ' ' -f 1
  else
    shasum -a 256 | cut -d ' ' -f 1
  fi
}

bun_runtime_cache_init() {
  local target key
  BUN_RUNTIME_CACHE_ENTRY=
  [[ -n $CACHE_DIR && -n ${STACK:-} && $BUN_VERSION =~ ^v([0-9]+\.[0-9]+\.[0-9]+)$ ]] || return 0
  BUN_RUNTIME_VERSION="${BASH_REMATCH[1]}"
  target=$(bun_runtime_target) || return 0
  BUN_RUNTIME_ID=$(printf 'format=1\nversion=%s\nstack=%s\ntarget=%s\n' \
    "$BUN_RUNTIME_VERSION" "$STACK" "$target")
  key=$(printf '%s\n' "$BUN_RUNTIME_ID" | bun_runtime_digest) || return 0
  BUN_RUNTIME_CACHE_ROOT="$CACHE_DIR/heroku-buildpack-bun-runtime"
  # Validation always invokes an absolute executable, never a bun found on PATH.
  if [[ $BUN_RUNTIME_CACHE_ROOT != /* ]]; then
    BUN_RUNTIME_CACHE_ROOT="$PWD/$BUN_RUNTIME_CACHE_ROOT"
  fi
  [[ ! -L $BUN_RUNTIME_CACHE_ROOT ]] || return 0
  BUN_RUNTIME_CACHE_ENTRY="$BUN_RUNTIME_CACHE_ROOT/v$BUN_RUNTIME_VERSION-$target-$key"
}

bun_runtime_cache_valid() {
  local entry="$1" digest version
  [[ -d $entry && ! -L $entry && ! -L $entry/bin ]] || return 1
  [[ -f $entry/identity && ! -L $entry/identity && $(cat "$entry/identity") == "$BUN_RUNTIME_ID" ]] || return 1
  [[ -f $entry/sha256 && ! -L $entry/sha256 ]] || return 1
  [[ -f $entry/bin/bun && ! -L $entry/bin/bun && -x $entry/bin/bun ]] || return 1
  [[ -L $entry/bin/bunx && $(readlink "$entry/bin/bunx") == bun ]] || return 1
  digest=$(bun_runtime_digest < "$entry/bin/bun") || return 1
  [[ $digest == "$(cat "$entry/sha256")" ]] || return 1
  version=$("$entry/bin/bun" --version 2>/dev/null) || return 1
  [[ $version == "$BUN_RUNTIME_VERSION" ]]
}

bun_runtime_cache_restore() {
  [[ -n $BUN_RUNTIME_CACHE_ENTRY ]] || return 1
  bun_runtime_cache_valid "$BUN_RUNTIME_CACHE_ENTRY" || return 1
  # Only Bun's two runtime names are owned; leave other buildpacks' bin files alone.
  rm -f "$BIN_DIR/bun" "$BIN_DIR/bunx" || return 1
  cp -p "$BUN_RUNTIME_CACHE_ENTRY/bin/bun" "$BIN_DIR/bun" || return 1
  ln -s bun "$BIN_DIR/bunx" || return 1
  echo "       Using cached Bun v$BUN_RUNTIME_VERSION"
}

bun_runtime_cache_publish() (
  [[ -n $BUN_RUNTIME_CACHE_ENTRY ]] || exit 0
  local stage
  mkdir -p "$BUN_RUNTIME_CACHE_ROOT" || exit 1
  # A concurrent writer (or stale lock) may skip this optional optimization.
  mkdir "$BUN_RUNTIME_CACHE_ENTRY.lock" || exit 1
  stage=
  trap 'if [[ -n $stage ]]; then rm -rf "$stage"; fi; rmdir "$BUN_RUNTIME_CACHE_ENTRY.lock"' EXIT
  if bun_runtime_cache_valid "$BUN_RUNTIME_CACHE_ENTRY"; then exit 0; fi
  stage=$(mktemp -d "$BUN_RUNTIME_CACHE_ROOT/.tmp.XXXXXX") || exit 1
  mkdir "$stage/bin" || exit 1
  cp -p "$BIN_DIR/bun" "$stage/bin/bun" || exit 1
  ln -s bun "$stage/bin/bunx" || exit 1
  printf '%s\n' "$BUN_RUNTIME_ID" > "$stage/identity" || exit 1
  bun_runtime_digest < "$stage/bin/bun" > "$stage/sha256" || exit 1
  bun_runtime_cache_valid "$stage" || exit 1
  # Publish only complete, validated content by rename on the same filesystem.
  rm -rf "$BUN_RUNTIME_CACHE_ENTRY" || exit 1
  mv "$stage" "$BUN_RUNTIME_CACHE_ENTRY" || exit 1
)
