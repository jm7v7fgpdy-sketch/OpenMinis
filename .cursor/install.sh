#!/usr/bin/env bash
#
# Cloud Agent install script for OpenMinis (Android target).
#
# The base image/snapshot already carries the stable, slow toolchain:
# JDK 17, the Android SDK (platform 36/35, build-tools, platform-tools,
# NDK r28, CMake 3.22.1), a recent Go toolchain, gomobile/gobind, gawk and
# ninja. This script performs the repository-dependent bootstrap that must run
# against the checked-out source: init the proot submodule, materialize the
# build-time customization file, build the native sandbox (proot + Alpine
# rootfs), build the rclone AAR, and stage them where Gradle expects them.
#
# It is idempotent: every step is a no-op (or a cheap re-verify) when its
# artifact already exists, so it is safe to run repeatedly against cached state.
#
# iOS is intentionally out of scope here: it requires macOS + Xcode and cannot
# be built on this Linux VM. See BUILDING.md for the iOS instructions.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Toolchain env (kept in sync with .cursor/env.sh / /etc/profile.d) --------
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
export ANDROID_HOME="${ANDROID_HOME:-/opt/android-sdk}"
export ANDROID_NDK_HOME="${ANDROID_NDK_HOME:-/opt/android-sdk/ndk/28.0.12433566}"
export PATH="$JAVA_HOME/bin:/usr/local/go/bin:$HOME/go/bin:$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$PATH"

log() { printf '\033[0;34m[install]\033[0m %s\n' "$*"; }

# --- 1. proot submodule (Android sandbox source) ------------------------------
# deps/ish is iOS-only and deliberately left uninitialized on Linux.
if [ ! -f deps/proot/src/GNUmakefile ]; then
  log "Initializing deps/proot submodule..."
  git submodule update --init deps/proot
else
  log "deps/proot submodule already present."
fi

# --- 2. Build-time customization (empty values are fine for building) ---------
CUSTOM="src/android/app/provider-customization.properties"
if [ ! -f "$CUSTOM" ]; then
  log "Creating $CUSTOM from example."
  cp "${CUSTOM}.example" "$CUSTOM"
else
  log "provider-customization.properties already present."
fi

# --- 3. Native sandbox: proot binary + ELF loaders ----------------------------
# build_proot.sh re-installs and sha256-verifies the vendored loaders every run;
# it is safe and cheap to invoke repeatedly.
log "Building/verifying native proot sandbox..."
bash deps/build_proot.sh

# --- 4. Alpine rootfs + proot asset (downloads only when missing) -------------
log "Preparing Android sandbox assets (Alpine rootfs)..."
bash scripts/prepare_android_sandbox.sh

# --- 5. rclone AAR (gomobile build artifact -> app/libs) ----------------------
RCLONE_AAR="src/android/app/libs/rclone.aar"
if [ ! -f "$RCLONE_AAR" ]; then
  log "Building rclone.aar via gomobile (this can take a few minutes)..."
  # gomobile init is a no-op once the NDK stdlib cache exists.
  gomobile init
  bash deps/build_rclone_android.sh
  mkdir -p "$(dirname "$RCLONE_AAR")"
  cp deps/build/rclone/rclone.aar "$RCLONE_AAR"
else
  log "rclone.aar already staged at $RCLONE_AAR."
fi

# --- 6. Point Gradle at the SDK -----------------------------------------------
printf 'sdk.dir=%s\n' "$ANDROID_SDK_ROOT" > src/android/local.properties
log "Wrote src/android/local.properties (sdk.dir=$ANDROID_SDK_ROOT)."

# --- 7. Warm the Gradle dependency cache --------------------------------------
# Resolves the full dependency graph so the first agent build is fast and to
# surface any resolution problem here rather than mid-task.
log "Warming Gradle dependency cache..."
( cd src/android && ./gradlew :app:help --no-daemon -q ) || \
  log "Gradle warm-up reported a non-zero exit (non-fatal); continuing."

log "Install complete. Build the app with:"
log "  cd src/android && ./gradlew :app:assembleDebug"
