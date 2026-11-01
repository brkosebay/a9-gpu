#!/usr/bin/env bash
# Offline end-to-end test of scripts/gpu/ios-capture-bundle.sh and the real
# device-capture.sh against a fake iPad: a stub scripts/ipad-ssh.sh runs each
# command locally with /var/mobile, /var/jb, /private/var and /cores/binpack
# rewritten under a temp dir; the bundle's binaries are shell fakes whose
# hashes go into a fake SHA256SUMS, so the real hash gates run.  No device.
#   tools/ios-capture-bundle/test-runner.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
T=$(mktemp -d /tmp/icbrun.XXXXXX)
trap 'rm -rf "$T"' EXIT
R=$T/root DEV=$T/dev
mkdir -p "$R/scripts/gpu" "$R/build/ios-capture-bundle/bin" "$DEV/bin" "$DEV/private/var" \
    "$DEV/cores/binpack/usr/sbin" "$DEV/var/mobile" "$DEV/var/jb/usr/local"
cp "$REPO/scripts/gpu/ios-capture-bundle.sh" "$R/scripts/gpu/"
cp "$REPO/tools/ios-capture-bundle/device-capture.sh" "$R/build/ios-capture-bundle/bin/"

cat > "$R/scripts/ipad-ssh.sh" <<'EOF'
#!/usr/bin/env bash
DEV=${FAKE_DEV:?}
if [ "${IPAD_SCP:-0}" = 1 ]; then
    args=("$@"); dst=${args[${#args[@]}-1]}; unset 'args[${#args[@]}-1]'
    exec cp "${args[@]}" "$DEV${dst#mobile@127.0.0.1:}"
fi
cmd=$(printf '%s' "$*" | sed -E "s#(^|[ '\"=:(])(/private/var|/var/mobile|/var/jb|/cores/binpack)#\1$DEV\2#g")
ICB_DEVICE_ROOT=$DEV PATH=$DEV/bin:$PATH exec bash -c "$cmd"
EOF
chmod 755 "$R/scripts/ipad-ssh.sh"
fake() { printf '#!/bin/sh\n%s\n' "$2" > "$1"; chmod 755 "$1"; }
fake "$DEV/bin/sw_vers" 'printf "ProductName:\tiPhone OS\nProductVersion:\t16.7.10\nBuildVersion:\t20H350\n"'
fake "$DEV/bin/sysctl" '[ "$*" = "-n kern.osversion" ] && echo "${FAKE_OSVERSION:-20H350}"'
fake "$DEV/bin/sha256sum" 'exec shasum -a 256 "$@"'
fake "$DEV/bin/ioreg" 'echo "+-o AGXAcceleratorG5P_C0"'
fake "$DEV/bin/sudo" 'while [ $# -gt 0 ]; do case $1 in -n) shift;; -S) read -r _pw; shift;; -p) shift 2;; *) break;; esac; done; exec "$@"'
fake "$DEV/cores/binpack/usr/sbin/p1ctl" 'echo 0x1234000'
FB=$R/build/ios-capture-bundle/bin
fake "$FB/g5pdump-gpu" '[ "$1" = regions ] && [ -f "$3" ] || exit 9
echo p > "$2/$4-post-0000000100000000.bin"; echo k > "$2/submit-$4-000-kernel.bin"; echo a > "$2/allocator-$4-encoded.txt"'
fake "$FB/ios-gpu-probe" 'case $1 in
probe) echo "tfp0=valid page=0x4000" ;;
fw-blocks) shift; dir=; tag=; dry=0; args="$*"
    while [ $# -gt 0 ]; do case $1 in --dir) dir=$2; shift ;; --tag) tag=$2; shift ;; --dry-plan) dry=1 ;; esac; shift; done
    if [ $dry = 1 ]; then for r in root ta 3d cl policy meta; do echo "record $r fw+0x000 -> 0x0 ok"; done
        case ${FAKE_DRYPLAN:-} in
        control-unresolved) echo "record control  fw+0x2f0 -> 0xfffffff6ec210000 group window unresolved" ;;
        *) echo "record control fw+0x2f0 -> 0x0 ok" ;; esac; exit 0; fi
    [ -d "$dir" ] || exit 2; echo "$args" > "$dir/$tag-args.txt"
    echo "end bad=0" > "$dir/$tag-index.txt"; echo "# stop=complete" > "$dir/$tag-series.csv" ;;
*) exit 2 ;; esac'
fake "$FB/metal-triangle" 'exit 0'
fake "$FB/ios-ioreport" 'mkdir -p "$2"; echo l > "$2/channels.plist"; echo d > "$2/delta-000.plist"; echo READY >&2'
fake "$FB/allow-sudo.sh" 'D=$ICB_DEVICE_ROOT/var/jb/usr/local/linux-a9-gpu; mkdir -p "$D"
cp "$ICB_DEVICE_ROOT/var/mobile/linux-a9/ios-gpu-probe/"* "$D/"; echo installed'
for f in basic.metallib probes.metallib gles3.metallib gles3b.metallib triangle.metallib; do echo "$f" > "$FB/$f"; done
(cd "$FB" && shasum -a 256 * > ../SHA256SUMS)

export FAKE_DEV=$DEV
run() { local rc=0; "$R/scripts/gpu/ios-capture-bundle.sh" "$@" || rc=$?; sleep 0.3; return $rc; }  # let the tee drain
fail() { echo "FAIL: $*"; exit 1; }
run preflight > "$T/preflight.txt" || { cat "$T/preflight.txt"; fail preflight; }
STAMP=$(cat "$R/build/gpu/ios-capture-bundle/LATEST")
OUT=$R/build/gpu/ios-capture-bundle/$STAMP RUN=$DEV/var/mobile/linux-a9/icb/run-$STAMP
run upload > "$T/upload.txt" || { cat "$T/upload.txt"; fail upload; }
[ "$(grep -c '^ok ' "$T/upload.txt")" = 11 ] || fail "upload hash gates: $(cat "$T/upload.txt")"
IPAD_PW=not-a-password run install > "$T/install.txt" || { cat "$T/install.txt"; fail install; }
grep -q 'tfp0=valid' "$T/install.txt" || fail "install probe"
grep -rq not-a-password "$OUT" && fail "password written to the run dir"
run loadmrt > "$T/loadmrt.txt" || { cat "$T/loadmrt.txt"; fail loadmrt; }
[ "$(cat "$RUN"/loadmrt/rc-*.txt "$RUN"/loadmrt-repeat/rc-*.txt | grep -c '^0$')" = 8 ] || fail "8 loadmrt rc files"
grep -q 'loadmrt bad=0' "$T/loadmrt.txt" || fail "loadmrt log"
run loadmrt > "$T/again.txt" && fail "a second loadmrt in the same run was allowed"
grep -q 'already ran' "$T/again.txt" || fail "rerun guard message"
run gles3 > "$T/gles3.txt" || { cat "$T/gles3.txt"; fail gles3; }
[ "$(cat "$RUN"/gles3/rc-*.txt "$RUN"/gles3-repeat/rc-*.txt | grep -c '^0$')" = 23 ] || fail "23 gles3 rc files"
grep -q 'gles3 bad=0' "$T/gles3.txt" || fail "gles3 log"
run gles3b > "$T/gles3b.txt" || { cat "$T/gles3b.txt"; fail gles3b; }
[ "$(cat "$RUN"/gles3b/rc-*.txt "$RUN"/gles3b-repeat/rc-*.txt | grep -c '^0$')" = 45 ] || fail "45 gles3b rc files"
[ -f "$RUN/gles3b-repeat/rc-g3-base.txt" ] && [ -f "$RUN/gles3b/g3b-vtx16-post-0000000100000000.bin" ] || fail "gles3b tree"
grep -q 'gles3b bad=0' "$T/gles3b.txt" || fail "gles3b log"
run power > "$T/power.txt" || { cat "$T/power.txt"; fail power; }
for t in idle load after; do [ -f "$RUN/power/$t-index.txt" ] || fail "power $t"; done
grep -q 'power bad=0 fw-blocks=full' "$T/power.txt" || fail "power log"
grep -qx 'mode=full' "$RUN/power/fw-blocks-mode.txt" || fail "mode file: $(cat "$RUN/power/fw-blocks-mode.txt")"
grep -q -- '--skip-record' "$RUN"/power/*-args.txt && fail "full mode passed --skip-record"
[ -f "$RUN/power/ioreport-gpu/delta-000.plist" ] || fail "ioreport in full mode"
run pull > "$T/pull.txt" || { cat "$T/pull.txt"; fail pull; }
[ -f "$OUT/device/loadmrt/mrt4-post-0000000100000000.bin" ] && [ -f "$OUT/device/power/load-series.csv" ] &&
    [ -f "$OUT/device/loadmrt.done" ] && [ -f "$OUT/device/gles3/stencil-post-0000000100000000.bin" ] &&
    [ -f "$OUT/device/gles3.done" ] && [ -f "$OUT/device/gles3b.done" ] &&
    [ -f "$OUT/device/gles3b/g3b-cs-atomic-post-0000000100000000.bin" ] || fail "pulled tree"
run health > "$T/health.txt" || fail health
# no sw_vers on the iPad: the build number alone identifies 16.7.10; a
# different build stops preflight
rm "$DEV/bin/sw_vers"
FAKE_OSVERSION=20G75 run preflight > "$T/pre-bad.txt" && fail "preflight accepted another iOS build"
grep -q 'STOP: not iOS 16.7.10' "$T/pre-bad.txt" || fail "version stop: $(cat "$T/pre-bad.txt")"
# the 2026-11-01 dry plan (control: group window unresolved): the six
# group-0 records are still dumped with --skip-record control, IOReport runs,
# the phase reports non-zero and check.py names the skipped record
run preflight > "$T/pre3.txt" || fail "preflight 3: $(cat "$T/pre3.txt")"
RUN3=$DEV/var/mobile/linux-a9/icb/run-$(cat "$R/build/gpu/ios-capture-bundle/LATEST")
FAKE_DRYPLAN=control-unresolved run power > "$T/power3.txt" && fail "skip-control power reported success"
grep -q 'STOP: dry plan: record control fw+0x2f0 .* group window unresolved; fw-blocks mode=skip-control, IOReport continues' \
    "$T/power3.txt" || fail "skip-control stop line: $(cat "$T/power3.txt")"
grep -q 'power bad=1 fw-blocks=skip-control' "$T/power3.txt" || fail "skip-control log"
grep -q '^mode=skip-control reason=' "$RUN3/power/fw-blocks-mode.txt" || fail "skip-control mode file"
for t in idle load after; do
    [ -f "$RUN3/power/$t-index.txt" ] || fail "skip-control $t not dumped"
    grep -q -- '--skip-record control' "$RUN3/power/$t-args.txt" || fail "skip-control $t args"
done
[ -f "$RUN3/power/ioreport-gpu/delta-000.plist" ] && [ -f "$RUN3/power/ioreport-soc/delta-000.plist" ] ||
    fail "ioreport in skip-control mode"
# a stalled tfp0 skips every block read, but IOReport + stress still run
fake "$DEV/var/jb/usr/local/linux-a9-gpu/ios-gpu-probe" 'echo "tfp0=unavailable"; exit 1'
run preflight > "$T/pre2.txt" || fail "preflight via kern.osversion: $(cat "$T/pre2.txt")"
RUN2=$DEV/var/mobile/linux-a9/icb/run-$(cat "$R/build/gpu/ios-capture-bundle/LATEST")
run power > "$T/power2.txt" && fail "power without tfp0 succeeded"
grep -q 'STOP: tfp0 unavailable; fw-blocks mode=none, IOReport continues' "$T/power2.txt" ||
    fail "tfp0 stop: $(cat "$T/power2.txt")"
grep -q '^mode=none reason=tfp0 unavailable' "$RUN2/power/fw-blocks-mode.txt" || fail "none mode file"
[ -f "$RUN2/power/1-dry-plan.txt" ] && fail "dry plan ran without tfp0"
ls "$RUN2"/power/*-index.txt > /dev/null 2>&1 && fail "fw-blocks read without tfp0"
[ -f "$RUN2/power/ioreport-gpu/delta-000.plist" ] && [ -f "$RUN2/power/ioreport-soc/delta-000.plist" ] &&
    [ "$(cat "$RUN2/power/rc-ioreport-gpu.txt")" = 0 ] && [ -f "$RUN2/power/registry-after.txt" ] ||
    fail "ioreport without tfp0"
echo "PASS: ios-capture-bundle runner preflight (sw_vers or kern.osversion; wrong build stops)/upload(11 hash gates)/install/loadmrt(8 captures, rerun guard)/gles3(23 captures)/gles3b(45 captures)/power full/pull/health; power skip-control (6 records + IOReport); tfp0 stop (no block read, IOReport still sampled)"
