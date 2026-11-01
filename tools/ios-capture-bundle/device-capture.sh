#!/bin/sh
# ON THE IPAD (iOS 16.7.10, palera1n rootless), as mobile.  Started by
# scripts/gpu/ios-capture-bundle.sh under nohup, so an SSH drop does not
# kill it; RUN/<phase>.done is written last.  No retries, no MMIO, no kernel
# write: g5pdump reads its own process's VM, ios-gpu-probe fw-blocks reads
# validated firmware RAM, ios-ioreport is userspace IOReport.
#   device-capture.sh loadmrt RUN     8 g5pdump region captures (~10 s)
#   device-capture.sh power RUN       IOReport + fw-blocks idle/load/after (~1 min)
#   device-capture.sh gles3 RUN       23 g5pdump GLES 3.0 feature captures (~30 s)
#   device-capture.sh gles3b RUN      45 g5pdump Vulkan 1.0 / GLES 3.0 batch-2 captures (~1 min)
# docs/gpu/experiments/ios-capture-bundle.md
set -u
# ICB_DEVICE_ROOT is unset on the iPad; test-runner.sh points it at a fake device.
B=${ICB_DEVICE_ROOT:-}/var/mobile/linux-a9/icb
D=${ICB_DEVICE_ROOT:-}/var/jb/usr/local/linux-a9-gpu
P1CTL=${ICB_DEVICE_ROOT:-}/cores/binpack/usr/sbin/p1ctl
PHASE=${1:-}
RUN=${2:-}
case $RUN in "$B"/run-*) ;; *) echo "RUN must be $B/run-<stamp>" >&2; exit 2 ;; esac
now() { date -u +%H:%M:%S; }

# gles3-continued107.md 3.1: one submit per scene, 64x64, ~30 MiB of regions
# (~0.6 s per scene on the device; 2026-11-01 took 6 s for all 8).
# `pass` is the CLEAR reference for load and mrt*, `pass-linear` for
# load-linear; the repeat dir re-runs both references (noise baseline).
loadmrt() {
	O=$RUN/loadmrt
	O2=$RUN/loadmrt-repeat
	mkdir -p "$O" "$O2" "$B/tmp"
	export TMPDIR=$B/tmp/ G5P_CAPTURE_SUBMIT=1 G5P_READ_NONRESIDENT_USER=1
	bad=0
	for spec in "$O basic pass" "$O basic pass-linear" "$O basic load" \
		"$O basic load-linear" "$O probes mrt2" "$O probes mrt4" \
		"$O2 basic pass" "$O2 basic pass-linear"; do
		set -- $spec
		echo "$(now) $3 -> $1"
		(cd "$B" && bin/g5pdump-gpu regions "$1" "bin/$2.metallib" "$3" 192) \
			> "$1/stdout-$3.txt" 2>&1
		rc=$?
		echo "$rc" > "$1/rc-$3.txt"
		[ "$rc" = 0 ] || bad=$((bad + 1))
	done
	echo "$(now) loadmrt bad=$bad"
	return "$bad"
}

# GLES 3.0 features the offline compiler cannot show (MSAA, depth/stencil
# store+load, render formats, array/3D/cube sampling, occlusion, instancing).
# Same `regions` capture as loadmrt, one submit per scene, priority order
# (a later scene failing does not stop the rest).  g3-base is the CLEAR
# baseline of the gles3.metallib pipeline; basic `pass` ties the run to
# loadmrt; the repeat dir re-runs g3-base (noise baseline).
gles3() {
	O=$RUN/gles3
	O2=$RUN/gles3-repeat
	mkdir -p "$O" "$O2" "$B/tmp"
	export TMPDIR=$B/tmp/ G5P_CAPTURE_SUBMIT=1 G5P_READ_NONRESIDENT_USER=1
	bad=0
	for spec in "$O gles3 g3-base" "$O gles3 msaa4-resolve" "$O gles3 msaa4-store" \
		"$O gles3 depth-store" "$O gles3 depth-load" "$O gles3 depth16-load" "$O gles3 stencil" \
		"$O gles3 fmt-srgb8" "$O gles3 fmt-rgba16f" "$O gles3 fmt-r32f" "$O gles3 fmt-rg16f" \
		"$O gles3 fmt-rgba8ui" "$O gles3 fmt-r32ui" "$O gles3 fmt-rgb10a2" "$O gles3 fmt-r8" \
		"$O gles3 fmt-rg8" "$O gles3 tex-array" "$O gles3 tex-3d" "$O gles3 tex-cube" \
		"$O gles3 occlusion" "$O gles3 instanced" "$O basic pass" "$O2 gles3 g3-base"; do
		set -- $spec
		echo "$(now) $3 -> $1"
		(cd "$B" && bin/g5pdump-gpu regions "$1" "bin/$2.metallib" "$3" 192) \
			> "$1/stdout-$3.txt" 2>&1
		rc=$?
		echo "$rc" > "$1/rc-$3.txt"
		[ "$rc" = 0 ] || bad=$((bad + 1))
	done
	echo "$(now) gles3 bad=$bad"
	return "$bad"
}

# Batch 2 (Vulkan 1.0 core / GLES 3.0): sampled formats, compressed,
# render formats, primitive types and index forms, discard/fragdepth,
# a2c/sample mask, depth bias, samplers, mip/layer targets, blend constant
# and write masks, compute (image write, atomics, threadgroup), 16 vertex
# attributes.  Same capture as gles3; LIB = gles3b.metallib (gles3.metal +
# gles3b.metal), so g3-base runs first and again in gles3b-repeat.
# Scenes are listed in g5pdump-gles3b.inc.m; unsupported features are logged
# as SKIP inside the scene and do not stop the phase.
gles3b() {
	O=$RUN/gles3b
	O2=$RUN/gles3b-repeat
	mkdir -p "$O" "$O2" "$B/tmp"
	export TMPDIR=$B/tmp/ G5P_CAPTURE_SUBMIT=1 G5P_READ_NONRESIDENT_USER=1
	bad=0
	for m in g3-base g3b-sample-a g3b-sample-b g3b-compressed \
		g3b-rfmt-rgba16unorm g3b-rfmt-rgba16uint g3b-rfmt-rgba16sint g3b-rfmt-rg16unorm \
		g3b-rfmt-rg16snorm g3b-rfmt-rg16uint g3b-rfmt-rg16sint g3b-rfmt-rgba32float \
		g3b-rfmt-rgba32uint g3b-rfmt-rg11b10f g3b-rfmt-r16f g3b-rfmt-bgra8srgb g3b-rfmt-b5g6r5 \
		g3b-points g3b-lines g3b-linestrip g3b-tristrip g3b-idx16-restart g3b-idx32 g3b-basevertex \
		g3b-discard g3b-fragdepth g3b-a2c g3b-samplemask g3b-depthbias \
		g3b-samp-mip g3b-samp-addr g3b-samp-border g3b-samp-shadow g3b-samp-swizzle \
		g3b-samp-aniso g3b-samp-fetch g3b-rt-mip2 g3b-rt-layer1 \
		g3b-blend-const g3b-wmask-rgb g3b-wmask-ra g3b-cs-image g3b-cs-atomic g3b-vtx16 \
		repeat:g3-base; do
		o=$O
		case $m in repeat:*) o=$O2 m=${m#repeat:} ;; esac
		echo "$(now) $m -> $o"
		(cd "$B" && bin/g5pdump-gpu regions "$o" bin/gles3b.metallib "$m" 192) \
			> "$o/stdout-$m.txt" 2>&1
		rc=$?
		echo "$rc" > "$o/rc-$m.txt"
		[ "$rc" = 0 ] || bad=$((bad + 1))
	done
	echo "$(now) gles3b bad=$bad"
	return "$bad"
}

# power-words-init-re.md 9: the meta/policy/control records whole, at idle,
# under a Metal stress load and after it, with a change-compressed 1 ms
# series of the host-written words; IOReport GPU/SoC residency alongside.
# fw-blocks runs only on a validated dry plan:
#   full          all 7 records ok
#   skip-control  the 6 group-0 records ok, control refused: --skip-record control
#   none          anything else: no block read at all
# IOReport, ioreg and the Metal stress are userspace and run in every mode.
power() {
	P=$RUN/power
	mkdir -p "$P/ioreport-gpu" "$P/ioreport-soc"
	FWB=none WHY=
	probe() { sudo -n "$D/ios-gpu-probe" "$@"; }
	if SLIDE=$("$P1CTL" kslide); then
		echo "slide=$SLIDE" > "$P/slide.txt"
		probe probe > "$P/0-probe.txt" 2>&1
		if grep -q 'tfp0=valid' "$P/0-probe.txt"; then :; else WHY="tfp0 unavailable"; fi
	else
		WHY="no p1ctl kslide"
	fi
	# run15/run18 warmup: a finite Metal client that exits before any block read
	(cd "$D" && ./metal-triangle --seconds 4 --triangles 1 --width 64 --height 64 --mark-pid 0) \
		> "$P/metal-warmup.log" 2>&1
	if [ -z "$WHY" ]; then
		if probe fw-blocks --dry-plan --slide "$SLIDE" --wait 10 > "$P/1-dry-plan.txt" 2>&1; then
			n=$(grep -cE '^record (root|ta|3d|cl|policy|meta) .* ok$' "$P/1-dry-plan.txt")
			if [ "$n" != 6 ]; then
				WHY="$n/6 group-0 records validated in the dry plan"
			elif grep -qE '^record control .* ok$' "$P/1-dry-plan.txt"; then
				FWB=full
			else
				FWB=skip-control
				WHY="dry plan: $(grep '^record control' "$P/1-dry-plan.txt" | tr -s ' ')"
			fi
		else
			WHY="fw-blocks dry plan failed"
		fi
	fi
	echo "mode=$FWB${WHY:+ reason=$WHY}" > "$P/fw-blocks-mode.txt"
	[ -z "$WHY" ] || echo "STOP: $WHY; fw-blocks mode=$FWB, IOReport continues"
	FWARGS=
	[ "$FWB" = skip-control ] && FWARGS="--skip-record control"
	blocks() {  # tag samples stdout-file
		[ "$FWB" = none ] && return 0
		# shellcheck disable=SC2086 # FWARGS is empty or two words
		probe fw-blocks --slide "$SLIDE" --dir "$P" --tag "$1" --samples "$2" --interval-us 1000 \
			$FWARGS > "$P/$3" 2>&1
		echo $? > "$P/rc-$1.txt"
	}
	ioreg -c AGXAcceleratorG5P_C0 -r -l > "$P/registry-before.txt" 2>&1
	echo "$(now) idle"
	blocks idle 3000 2-idle.txt
	echo "$(now) ioreport + stress"
	"$B/bin/ios-ioreport" 'GPU Stats' "$P/ioreport-gpu" 34 1000 2> "$P/ioreport-gpu/log.txt" &
	IG=$!
	"$B/bin/ios-ioreport" 'SoC Stats' "$P/ioreport-soc" 34 1000 2> "$P/ioreport-soc/log.txt" &
	IS=$!
	sleep 2
	(cd "$D" && ./metal-triangle --seconds 25 --triangles 1000000 --width 1536 --height 2048 --mark-pid 0) \
		> "$P/metal-stress.log" 2>&1 &
	MT=$!
	sleep 3
	blocks load 15000 3-load.txt
	wait "$MT"
	echo $? > "$P/rc-metal-stress.txt"
	echo "$(now) after"
	blocks after 5000 4-after.txt
	wait "$IG"
	echo $? > "$P/rc-ioreport-gpu.txt"
	wait "$IS"
	echo $? > "$P/rc-ioreport-soc.txt"
	ioreg -c AGXAcceleratorG5P_C0 -r -l > "$P/registry-after.txt" 2>&1
	bad=0
	for f in "$P"/rc-*.txt; do [ "$(cat "$f")" = 0 ] || bad=$((bad + 1)); done
	[ "$FWB" = full ] || bad=$((bad + 1))
	echo "$(now) power bad=$bad fw-blocks=$FWB"
	return "$bad"
}

mkdir -p "$RUN"
case $PHASE in
loadmrt) loadmrt > "$RUN/loadmrt.log" 2>&1; rc=$? ;;
power) power > "$RUN/power.log" 2>&1; rc=$? ;;
gles3) gles3 > "$RUN/gles3.log" 2>&1; rc=$? ;;
gles3b) gles3b > "$RUN/gles3b.log" 2>&1; rc=$? ;;
*) echo "usage: device-capture.sh loadmrt|power|gles3|gles3b RUN" >&2; exit 2 ;;
esac
echo "rc=$rc end=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RUN/$PHASE.done"
exit "$rc"
