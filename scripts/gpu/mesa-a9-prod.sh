#!/usr/bin/env bash
# Offline only (no device). Builds and tests the A9 Mesa/EGL production bridge:
# the g5p Vulkan ICD (with patches/mesa-a9) + Zink + Mesa EGL/GBM/GLES2, in the
# existing arm64 build container, in an isolated checkout, with NO software
# rasterizer (gallium-drivers=zink only).
#
#   status        checkout, base, patch series, artifact tree
#   configure     meson setup (or reconfigure) of $OUT/out
#   build         ninja (all; libvulkan_g5p.so, libgallium, libEGL, libgbm, libGLESv2)
#   install       meson install --no-rebuild into $OUT/install
#   package       add the g5p ICD, its manifest and python backend, MANIFEST.sha256
#   test          host unit test + real stack against the LD_PRELOAD fake render node
#   vendor-check  the vendored contract headers vs tools/mesa-a9 and their pins
#   all           configure, build, install, package, test, package
#
# Checkout: third_party/mesa-a9-prod (a local clone of third_party/mesa-g5p-gfx,
# branch mesa-a9-prod = g5p-r19 + the patches of patches/mesa-a9). Never edits
# tools/mesa-a9 or third_party/mesa-g5p-gfx.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${MESA_A9_PROD_SRC:-$ROOT/third_party/mesa-a9-prod}"
OUT="${MESA_A9_PROD_OUT:-$ROOT/build/third_party/mesa-a9-prod}"
CONTAINER="${G5P_CONTAINER:-linux-a9-g5p-r5}"
JOBS="${JOBS:-3}" # 6 GB container: 4 jobs has been OOM-killed before
PATCHES="$ROOT/patches/mesa-a9"

die() { echo "mesa-a9-prod: $*" >&2; exit 1; }
rel() { echo "/work/${1#"$ROOT"/}"; }
dx() { docker exec -w /work "$CONTAINER" "$@"; }
need_container() {
    docker info >/dev/null 2>&1 || die "docker is not running"
    docker container inspect "$CONTAINER" >/dev/null 2>&1 || die "build container $CONTAINER not found (set G5P_CONTAINER)"
    docker start "$CONTAINER" >/dev/null
    case "$SRC$OUT" in "$ROOT"/*"$ROOT"/*) ;; *) die "source and output must live under $ROOT (mounted at /work)" ;; esac
    [ -d "$SRC/.git" ] || die "no checkout at $SRC (git clone --local third_party/mesa-g5p-gfx $SRC; git checkout -b mesa-a9-prod eeb62027b71; git am patches/mesa-a9/0*.patch)"
}

CS="$(rel "$SRC")"
CO="$(rel "$OUT")"

MESON_OPTS=(
    "-Dprefix=$CO/install" -Dbuildtype=release -Ddebug=false -Db_ndebug=true -Dstrip=true
    -Dvulkan-drivers=imagination -Dimagination-g5p-experimental=true
    # one Gallium driver, Zink: no llvmpipe/softpipe/swrast, so no silent software fallback.
    # llvm=enabled only because the Imagination build needs CLC; no Gallium driver uses it.
    -Dgallium-drivers=zink -Dllvm=enabled
    -Dplatforms= -Dglx=disabled -Degl=enabled -Dgbm=enabled -Dgles1=disabled -Dgles2=enabled
    -Dopengl=true -Dglvnd=disabled -Dshared-glapi=enabled
    -Dtools= -Dvulkan-layers= -Dvideo-codecs= -Dlmsensors=disabled -Dvalgrind=disabled
    -Dlibunwind=disabled -Dgallium-va=disabled -Dgallium-rusticl=false -Dxlib-lease=disabled -Dteflon=false
)

cmd_status() {
    echo "source:   $SRC"
    [ -d "$SRC/.git" ] && git -C "$SRC" log --oneline -3 | sed 's/^/          /'
    echo "series:   $(grep -vc '^#' "$PATCHES/SERIES" || true) patch(es) in $PATCHES/SERIES"
    echo "output:   $OUT"
    [ -f "$OUT/out/build.ninja" ] && echo "          configured" || echo "          not configured"
    [ -f "$OUT/out/src/imagination/vulkan/g5p/libvulkan_g5p.so" ] && echo "          libvulkan_g5p.so built" || true
    [ -f "$OUT/install/share/vulkan/icd.d/g5p_icd.json" ] && echo "          packaged" || true
}

cmd_configure() {
    need_container
    mkdir -p "$OUT"
    if [ -f "$OUT/out/build.ninja" ]; then
        dx meson configure "$CO/out" "${MESON_OPTS[@]}"
    else
        dx meson setup "$CO/out" "$CS" "${MESON_OPTS[@]}"
    fi
}

cmd_build() { need_container; dx ninja -C "$CO/out" -j "$JOBS"; }

cmd_install() { need_container; dx meson install -C "$CO/out" --no-rebuild >"$OUT/meson-install.log" 2>&1 && echo "installed: $OUT/install"; }

cmd_package() {
    need_container
    local I="$OUT/install" G="$SRC/src/imagination/vulkan/g5p" B="$OUT/out/src/imagination/vulkan/g5p"
    [ -f "$B/libvulkan_g5p.so" ] || die "libvulkan_g5p.so not built (run build)"
    [ -d "$I/lib64" ] || die "not installed (run install)"
    mkdir -p "$I/share/vulkan/icd.d" "$I/share/g5p" "$I/test" "$I/lib64/dri" "$I/share/glvnd/egl_vendor.d"
    cp "$B/libvulkan_g5p.so" "$I/lib64/libvulkan_g5p.so"
    # The Omarchy harness (scripts/omarchy/gpu-a9/lib.sh) looks for <prefix>/lib and
    # <prefix>/share/vulkan/icd.d/g5p_icd*.json: give it exactly that.
    [ -e "$I/lib" ] || ln -s lib64 "$I/lib"
    # relative library_path: resolved against the manifest's directory (Vulkan loader spec)
    cat >"$I/share/vulkan/icd.d/g5p_icd.json" <<'EOF2'
{"file_format_version":"1.0.0","ICD":{"library_path":"../../../lib64/libvulkan_g5p.so","api_version":"1.0.0"}}
EOF2
    rm -f "$I/share/vulkan/icd.d/a9_g5p_icd.json" # old name of the manifest
    cp "$G/g5p_compile.py" "$G/g5p_isa.py" "$I/share/g5p/"
    cp "$PATCHES/artifact.env.template" "$I/artifact.env"
    for f in liba9fakenode.so egl-smoke test-ta-contract test-typed-v3-abi; do [ -f "$OUT/test/$f" ] && cp "$OUT/test/$f" "$I/test/$f" || true; done
    cp "$PATCHES/test/"*.c "$PATCHES/test/run-smoke.sh" "$I/test/"
    (cd "$I" && find lib64 share test artifact.env -type f ! -name '*.symbols' | LC_ALL=C sort | xargs shasum -a 256 >MANIFEST.sha256)
    echo "packaged: $I ($(wc -l <"$I/MANIFEST.sha256" | tr -d ' ') files, MANIFEST.sha256)"
}

cmd_test() {
    need_container
    [ -f "$OUT/install/share/vulkan/icd.d/g5p_icd.json" ] || die "not packaged (run package)"
    # stream the test sources in: the bind mount can serve a stale copy of a just-edited file
    tar -C "$PATCHES/test" -cf - . | docker exec -i "$CONTAINER" sh -c 'rm -rf /tmp/a9test && mkdir -p /tmp/a9test && tar -C /tmp/a9test -xf -'
    dx bash /tmp/a9test/run-smoke.sh "$CO" "$CS"
}

cmd_vendor() {
    local D="$SRC/src/imagination/vulkan/g5p/a9" rc=0
    for f in a9_contracts.h a9ta_program.h a9ta_v3.h; do
        local pin have ws
        pin="$(awk -v f="$f" '$2 == f {print $1}' "$D/VENDORED")"
        have="$(shasum -a 256 "$D/$f" | cut -d' ' -f1)"
        ws="$(shasum -a 256 "$ROOT/tools/mesa-a9/$f" 2>/dev/null | cut -d' ' -f1 || true)"
        [ "$pin" = "$have" ] || { echo "MODIFIED  $f differs from its pin in VENDORED"; rc=1; }
        if [ -n "$ws" ] && [ "$ws" != "$have" ]; then echo "DRIFT     $f: tools/mesa-a9 has moved on (re-vendor deliberately, then update VENDORED)"; fi
        [ "$pin" = "$have" ] && [ "$ws" = "$have" ] && echo "ok        $f"
    done
    return $rc
}

case "${1:-}" in
    status) cmd_status ;;
    configure) cmd_configure ;;
    build) cmd_build ;;
    install) cmd_install ;;
    package) cmd_package ;;
    test) cmd_test ;;
    vendor-check) cmd_vendor ;;
    all) cmd_configure; cmd_build; cmd_install; cmd_package; cmd_test; cmd_package ;;
    *) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
