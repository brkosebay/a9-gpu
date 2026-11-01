# iOS capture bundle: LOAD, MRT2/MRT4 and firmware power words in one iOS boot

2026-11-01. Tools and runbook for the orchestrator. Every iOS boot ends the
live Linux desktop, so the three captures that only jailbroken iOS can give us
are bundled into one boot:

1. **LOAD**, i.e. the background object for `MTLLoadActionLoad`
   (gles3-continued107.md (`gles3-continued107.md`, not published) §3.1). It yields the PDS
   programs and background descriptor (BGRND0..3), the DOUTD descriptors with
   their source dwords (image state, sampler), the USC texture program, and the
   ISP/PPP descriptor words. A CLEAR reference (`pass`) and a repeat run of the
   reference serve as the noise baseline.
2. **MRT2 / MRT4** (gles3-continued107 §4). It yields the N×PBE state dwords
   that the EOT DOUTD moves, Apple's N-target EOT USC program, the EOT PDS
   program, and the descriptor words that change with N.
3. **Firmware power words, live** ([power-words-init-re.md](../firmware-re/power-words-init-re.md)
   §9). It yields the whole policy, meta and control records, including
   `meta+0x6d4`, `policy+4`, the pstate table `meta+0x710..0x728`, the limits
   `ctl+0x1028/102c/1030` and `ctl+0x04`. They are read at idle, under a Metal
   stress load and after it, with a 1 ms series in between. It also yields the
   accelerator limit fields and IOReport GPU pstate and Fender residency.

Nothing here writes to the device beyond its own directories (listed in §6).
Nothing reads MMIO.

## Status after run 20261101T145640Z, and the next boot

Write-up: logs/ios-capture-20261101T145640Z.md (`logs/ios-capture-20261101T145640Z.md`, not published).
Re-checked offline with the fixed parsers:
`build/gpu/ios-capture-bundle/20261101T145640Z/{parsed-v2/,check-v2.txt}`. The original `parsed/` is
unchanged.

* **LOAD: done.** load and load-linear decode exactly as on the device: `bgrnd0=['0x28c0','0x2840']`,
  4-word texunicode, 116-byte USC. The fixed parser only adds keys.
* **MRT2/MRT4: done, with no new capture.** The write-up's "uncaptured bytes" was a parser bug, not a
  capture gap. mrt2 has 32 regions and mrt4 has 34 (one per extra attachment), so the fixed 31-region
  record slot did not apply. The record search then took a decoy: region 6 +0x21e4 holds a table of the
  code heap's launch offsets. Reading record+0x2b8 there gave garbage, hence `0xa78f9941e`. The real
  record is a region base +0x288 (mrt2 region 10, mrt4 region 12). Its EOT is fully inside the captured
  regions:

  | | PBE dwords | layout | DOUTU | EOT USC | EOT PDS |
  |---|---|---|---|---|---|
  | mrt2 | 15 | 2 × 7, separated by `0xffffffff` | `0x1000002b80` | 66 B | `f0000800 f4000012 d3000000` |
  | mrt4 | 31 | 4 × 7, separated by `0xffffffff` | `0x1000002cc0` | 130 B | same |

  Each target set is the one-target template `fb05050a 00007e01 <8001c000|80020000|80024000|80028000>
  80000000 0 0 <0|4|8|c>`. `parsed-v2/mrt{2,4}.json` `mrt_eot.pbe_targets` has them.
  `check` now also requires the target count to match the scene (2 or 4).
* **Power: not captured yet, so it is the only phase for the next boot.** `fw+0x2f0` was the right
  slot, and control is entry 0 of packed group 2. The probe refused the group for two reasons, both
  correct for META translation but not for a CPU read. Its resource is 0x50000 bytes, above the old
  0x40000 packed-group bound. Its GPU VA is `0xe1c0210000`, outside the 16 MiB META window, so its META
  alias would be fictitious. run16 hit the same refusal. `AGXFirmware_allocateSharedRingGroups`
  (0x6c95da0) packs 18 sub-buffers into it: control, …, `fw+0x278` (0x36000 live), …, and the TA/3D/CL
  ring ctl/ent the dry plan already showed at +0x4c200..+0x4eac0. The last ring ends at 0x4fec0, which
  rounds up to 0x50000.
  * `fw-blocks` now resolves group 2 as a **CPU-only window**. It accepts the window only when the map
    class matches group 0, the map VA equals `fw+0x2f8`, and the resource length is the kext's own
    length. All 18 CPU and GPU slots must also sit at identical offsets in the exact packed layout, with
    the last one ending in the resource's last page. The layout proof passes on all 13 run16/run18
    `fw_before` dumps (two boots).
  * The window never enters the META/GPU lookup table, so every other probe mode is unchanged
    (`test-run18.sh` passes).
  * If group 2 still refuses, `device-capture.sh` runs fw-blocks with `--skip-record control`, so the
    six group-0 records are dumped anyway. If tfp0, kslide or the dry plan fail, no block is read at
    all. IOReport, ioreg and the Metal stress run in every mode. `power/fw-blocks-mode.txt` records the
    mode: full, skip-control or none.
* **loadmrt's 6 s is expected.** The run was complete: each scene is one 64×64 submit with ~5 MB of
  regions, about 0.6 s, and all 8 had rc=0. `192` is CAP_MB, not a repeat count. The "~2 min" in the
  old table was only a planning estimate.

### Next: gles3b (same iOS session, right after gles3)

New phase `gles3b` (2026-11-01 evening): 45 `g5pdump regions` captures for Vulkan 1.0 core and
GLES 3.0 state beyond batch 1. They are the same kind of capture as gles3 (one 64×64 submit per scene).
LIB = `gles3b.metallib` (gles3.metal + gles3b.metal). The scenes are listed in the header of
`tools/ios-capture-bundle/g5pdump-gles3b.inc.m`, and the hook is in `g5pdump-gles3b-scenes.patch`
(applied after the gles3 patch). In order:

| scenes | what |
|---|---|
| `g3-base` | the gles3 baseline again (ties gles3b to gles3); repeated in `gles3b-repeat/` |
| `g3b-sample-a` | 16 sampled formats: R8/RG8/RGBA8 Snorm, R16/RG16/RGBA16 Unorm+Snorm, R16/RG16/RGBA16 Float, RG32/RGBA32 Float, RG11B10F, RGB9E5 |
| `g3b-sample-b` | BGRA8 sRGB, B5G6R5, A1BGR5, ABGR4, R32F (sampled) + R8/R16/RG16/RGBA16/R32/RG32/RGBA32 Uint and the Sint set (read()) |
| `g3b-compressed` | ETC2 RGB8/RGB8A1/RGB8 sRGB, EAC RGBA8/R11/RG11 (+R11/RG11 Snorm), ASTC 4×4 LDR/sRGB (void-extent solid block) |
| `g3b-rfmt-*` (13) | render into RGBA16 Unorm/Uint/Sint, RG16 Unorm/Snorm/Uint/Sint, RGBA32 Float/Uint, RG11B10F, R16F, BGRA8 sRGB, B5G6R5; pass 2 samples/reads it into rt |
| `g3b-points lines linestrip tristrip idx16-restart idx32 basevertex` | primitive types; [[point_size]] 8; u16 strip with 0xffff restart; u32 list; baseVertex 1 + baseInstance 2 |
| `g3b-discard g3b-fragdepth` | discard_fragment (right half) / [[depth(any)]] = uv.x, both with D32F Less write |
| `g3b-a2c g3b-samplemask` | 4× MSAA alphaToCoverage (alpha .5) / [[sample_mask]] 0x5, resolved into rt |
| `g3b-depthbias` | setDepthBias(0.01, slope 2, clamp .02), LessEqual |
| `g3b-samp-{mip,addr,border,shadow,swizzle,aniso,fetch}` | linear-mip-linear + level/bias/grad; repeat/mirror/clamp-to-zero; API clamp-to-border opaque white; compare LessEqual on D32F; swizzle (B,1,R,G); maxAnisotropy 4; read() at level 1 |
| `g3b-rt-mip2 g3b-rt-layer1` | render into level 2 of a 64×64 mip chain / slice 1 of a 2D array; pass 2 samples it |
| `g3b-blend-const g3b-wmask-rgb g3b-wmask-ra` | BlendColor/OneMinusBlendColor with constant (.25,.5,.75,1); write masks RGB and R\|A |
| `g3b-cs-image g3b-cs-atomic` | compute: storage-image write; device atomic add/max + threadgroup memory + barrier (texture atomics SKIPped: MSL 3.1 / Apple6+) |
| `g3b-vtx16` | vertex descriptor with 16 mixed attributes (float, half, char/short normalized, int, uint, 1010102 u/s normalized, char2, short4, uchar4) |

Unsupported things are logged inside the scene as `SKIP <what>: <reason>` (a stand-in texture or
sampler is bound, so the scene still submits). They never fail the phase. On iOS without the validation
layer, a refused format or sampler may instead abort that one scene (rc≠0, no SKIP line). Each scene is its own
process, so continue. `g3b-samp-border` (clamp-to-border on Apple3) is the most likely to do this.
Mac check (`build/gpu-userspace/hosttest/gles3b/out-{plain,env}`, `MTL_DEBUG_LAYER=1`, M4 Pro): 44/44 rc=0 in both variants, `submit ok=1` 44/44, no validation messages, pixels as designed. Only texture atomics are SKIPped. The M4 Pro is Apple9, so this proves the API usage only, not anything about A9. Fake device: `test-runner.sh` PASS (11 upload hash gates, 45 gles3b rc files).

```sh
# in the SAME iOS session, after the gles3 phase (no new preflight: same run dir)
(cd build/ios-capture-bundle/bin && shasum -a 256 -c ../SHA256SUMS)
scripts/gpu/ios-capture-bundle.sh upload      # 11 "ok" lines (re-sends all; new: g5pdump-gpu, gles3b.metallib, device-capture.sh)
scripts/gpu/ios-capture-bundle.sh gles3b      # ~1 min; last log line "gles3b bad=0", then rc=0
scripts/gpu/ios-capture-bundle.sh pull
scripts/gpu/ios-capture-bundle.sh health
```

**Sanity check** (`OUT=build/gpu/ios-capture-bundle/$(cat build/gpu/ios-capture-bundle/LATEST)`):

```sh
cat $OUT/gles3b.done.txt                                              # rc=0
grep -h 'submit ok=1' $OUT/device/gles3b*/regions-*.txt | wc -l       # 45
cat $OUT/device/gles3b/rc-*.txt $OUT/device/gles3b-repeat/rc-*.txt | sort | uniq -c   # 45 × 0
grep -h 'SKIP\|no pipeline\|UNSUPPORTED' $OUT/device/gles3b/regions-*.txt | sort | uniq -c
grep -h '^# rt ' $OUT/device/gles3b/regions-{g3b-depthbias,g3b-rt-mip2,g3b-samp-shadow}.txt
```

Expected rt on the Mac: `g3b-depthbias` px(32,40)=00ff00ff; `g3b-rt-mip2`/`g3b-rt-layer1`
px(0,0)=4d331aff, px(32,40)=0000ffff; `g3b-cs-atomic` out-cnt = 00000100 0000003f, out-tg = 4 × 000007e0.
A non-zero rc for one scene is not a reason to stay: pull and leave. Files land in
`$OUT/device/{gles3b,gles3b-repeat}/` and `$OUT/gles3b.log`. On the device they are in
`/var/mobile/linux-a9/icb/run-<stamp>/gles3b*`. The pull is about 45 × ~5 MB.

| part | time |
|---|---|
| upload | ~1 min |
| gles3b (45 × ~0.6–1 s) | ~1 min (ICB_WAIT allows 900 s) |
| pull + health + sanity | ~1–2 min |

Bundle (2026-11-01 19:05, rebuilt twice with identical sums). Changed: g5pdump-gpu, device-capture.sh; new: gles3b.metallib.
The other eight files are unchanged:

```
26eb8df90104311ae941f316ce5456cac66e75dbfff7328150b67defb8cd8bd0  g5pdump-gpu
7cf6fcdfe706cb85eedcbf53360efe60c1a1c9a9b963a79ed9ce09aa5149910c  gles3b.metallib
18baa4f2d6639b53646d1042c426fa123b6c63918addf63edc51dc609b8f100b  device-capture.sh
c9c3ce6c85519890688c6336a6e7f05a661c4180a8e9c191e174cd84f4bbc487  SHA256SUMS (file itself)
```

### Next boot: gles3 (~12–18 min away from the desktop)

New phase `gles3` (2026-11-01 evening): 23 `g5pdump regions` captures of GLES 3.0
features that the offline compiler can't show. Each is one 64×64 submit, CLEAR baseline +
feature, with the same region, submit and allocator capture as loadmrt. The scenes are
in `tools/ios-capture-bundle/g5pdump-gles3.inc.m` (header comment), with shaders in
`gles3.metal` and the hook in `g5pdump-gles3-scenes.patch`, applied after the LOAD/MRT patch.
They run in priority order:

| scene | what |
|---|---|
| `g3-base` | baseline: BGRA8 clear blue, `f_const` red triangle (z=.5) |
| `msaa4-resolve`, `msaa4-store` | 4× MSAA BGRA8 (private): `MultisampleResolve` into a 1× target / plain `Store` |
| `depth-store` | + Depth32Float (clear 1.0, Less, write), stored |
| `depth-load`, `depth16-load` | depth-store, then pass 2 loads colour+depth: full screen z=.75 green, Less (D32F / D16) |
| `stencil` | Depth32Float_Stencil8: pass 1 writes ref 1 under the triangle (Replace), pass 2 loads it, full-screen green with Equal 1 |
| `fmt-srgb8 fmt-rgba16f fmt-r32f fmt-rg16f fmt-rgba8ui fmt-r32ui fmt-rgb10a2 fmt-r8 fmt-rg8` | pass 1 renders into the format (clear + triangle), pass 2 samples it (float, nearest) or reads it (uint) full screen into BGRA8 |
| `tex-array`, `tex-3d`, `tex-cube` | a 4×4 RGBA8 texture, CPU filled, sampled at layer 1 / slice 1 (r=.75) / face 3 (−Y) |
| `occlusion` | visibility buffer, `Counting` at offset 0 around the triangle draw |
| `instanced` | `drawPrimitives … instanceCount:4`, `[[instance_id]]` offsets and colours |
| `pass` (basic.metallib), `gles3-repeat/g3-base` | tie to loadmrt; noise baseline |

Only raw data is captured: there are no decoders yet. Each scene has `regions-<scene>{,-buffers}.txt`, the
`<scene>-pre/-post-*.bin` regions, `submit-<scene>-*`, `allocator-<scene>-*`, and
`g3-<scene>-{rt,fmt}-after.raw` (shared 2D targets). Occlusion adds `g3-occlusion-vis-after.raw`.
MSAA and depth/stencil textures are private: they appear in the region dumps only if their pages are resident, so their absence is not a bug.

Every scene was validated offline on the Mac's Metal with `MTL_DEBUG_LAYER=1` (G5P_HOST_TEST build,
`build/gpu-userspace/hosttest/gles3/out2/`, run twice: plain, and with the device's `G5P_CAPTURE_SUBMIT=1 G5P_READ_NONRESIDENT_USER=1`): 21/21 rc=0 both times, no validation errors, pixels as designed.
That proves the API usage only, not anything about A9. `tools/ios-capture-bundle/test-runner.sh` covers the phase
(23 captures, 10 upload hash gates).

```sh
# still in Linux: the bundle is already built (sums below); re-verify without rebuilding
(cd build/ios-capture-bundle/bin && shasum -a 256 -c ../SHA256SUMS)
# DFU, palera1n -l, wait for SSH (step 1, §2). Then, with NO install, loadmrt or power:
scripts/gpu/ios-capture-bundle.sh preflight
scripts/gpu/ios-capture-bundle.sh upload              # 11 "ok" lines (gles3b bundle)
scripts/gpu/ios-capture-bundle.sh gles3               # ~30 s; last log line "gles3 bad=0", then rc=0
scripts/gpu/ios-capture-bundle.sh pull
scripts/gpu/ios-capture-bundle.sh health
# then step 4 (§2): DFU, cold 124e, warm kexec into the step-0 desktop kernel
```

`install` (sudo, ios-gpu-probe) is not needed: g5pdump reads only its own VM. Do not run `check`,
because check.py has no gles3 parser yet.

**Sanity check before leaving iOS** (host side, after `pull`; `OUT=build/gpu/ios-capture-bundle/$(cat build/gpu/ios-capture-bundle/LATEST)`):

```sh
cat $OUT/gles3.done.txt                                       # rc=0
grep -h 'submit ok=1' $OUT/device/gles3*/regions-*.txt | wc -l # 23
cat $OUT/device/gles3/rc-*.txt $OUT/device/gles3-repeat/rc-*.txt | sort | uniq -c   # 23 × 0
grep -h '^# rt ' $OUT/device/gles3/regions-{g3-base,depth-load,stencil,msaa4-resolve}.txt
ls $OUT/device/gles3/*-post-*.bin | wc -l                     # > 0 for every scene
```

Expected rt bytes (BGRA order, the same as on the Mac): `g3-base` and `msaa4-resolve`
px(0,0)=ff0000ff (blue clear), px(32,40)=0000ffff (red). `depth-load` px(0,0)=00ff00ff
(green passes Less outside) and px(32,40)=0000ffff (the triangle wins). `stencil` px(0,0)=ff0000ff and
px(32,40)=00ff00ff. `msaa4-store` rt stays 00000000 (never a target). A non-zero rc for a single scene is
not a reason to stay: the other scenes still ran, so pull and leave. If the A9 refuses a format or
sample count, `regions-<scene>-buffers.txt` says so (`no pipeline`/`UNSUPPORTED`).

Pulled files land in `build/gpu/ios-capture-bundle/<stamp>/device/{gles3,gles3-repeat}/` and
`gles3.log`. On the device they are in `/var/mobile/linux-a9/icb/run-<stamp>/`. The pull is a few
hundred MB at most: 23 × pre+post regions under CAP_MB 192, and loadmrt was ~5 MB per scene.

| part | time |
|---|---|
| DFU + palera1n + SSH | 3–6 min |
| preflight + upload | ~1 min |
| gles3 (23 × ~0.6–1 s, plus process start) | ~30 s (ICB_WAIT allows 900 s) |
| pull + health + sanity | ~1–2 min |
| step 4, back to the desktop | 6–10 min |

(Superseded by the gles3b rebuild above: g5pdump-gpu and device-capture.sh now have the gles3b sums. That bundle
runs gles3 the same way and `upload` prints 11 "ok" lines.) Bundle (`scripts/gpu/ios-capture-bundle-build.sh`, 2026-11-01). Only g5pdump-gpu, gles3.metallib and
device-capture.sh changed. The other seven files have the same sums as the power-boot bundle:

```
6d942feda30fd033bb48a6c570e3818f08e2697e2edf201a0d86b0e36b91aba6  g5pdump-gpu
382575bcc952969b71c608de6ce6234bde3506eb4c9602fce90fc939392e6fc8  gles3.metallib
fdfe55e64f07230c6368404f9ca95f28ded3e53254f850c8ef900be48d92b200  device-capture.sh
0efc9472b951ae5454517aa55de3fb0baf9563aea0f7e92f230b0bef7c27a6eb  SHA256SUMS (file itself)
```

### Previous boot: power only (superseded by the gles3 section above)

```sh
# still in Linux: step 0 (§2), then build and test; the build must print these sums
scripts/gpu/ios-capture-bundle-build.sh
tools/ios-capture-bundle/test-runner.sh && python3 -I tools/ios-capture-bundle/test_parsers.py
# step 1 (§2): DFU, palera1n -l, wait for SSH. Then:
scripts/gpu/ios-capture-bundle.sh preflight
scripts/gpu/ios-capture-bundle.sh upload
IPAD_PW=… scripts/gpu/ios-capture-bundle.sh install
scripts/gpu/ios-capture-bundle.sh power               # ~1 min; prints "power bad=0 fw-blocks=full"
scripts/gpu/ios-capture-bundle.sh pull
ICB_PHASES=power scripts/gpu/ios-capture-bundle.sh check
scripts/gpu/ios-capture-bundle.sh health
# then step 4 (§2): DFU, cold 124e, warm kexec into the step-0 desktop kernel
```

`power/1-dry-plan.txt` still shows `fw window group2 shared-control: implausible group map length
(0x50000); not registered`. That line is the META-side refusal, which is expected and unchanged. It is
followed by `win group2 shared-control … (CPU-only, no META alias)` and `record control … ok`.

Do not run `loadmrt`: LOAD and MRT are done. A good `check` prints `power fw-blocks mode mode=full`,
`power dry-plan records 7/7`, `power control record … ok`, fw-blocks idle/load/after, both identity
checks, and IOReport GPU Performance States.

If `power` prints `STOP: dry plan: record control … fw-blocks mode=skip-control, IOReport continues`, the
group-0 records and IOReport are still captured. The line `fw window group2 (CPU-only): <reason>` in
`power/1-dry-plan.txt` names the refusal. Pull, check and leave iOS. A repeat would refuse the same way;
the fix is offline. One check has no device evidence yet: the map VA (`map+0x28`) equal to `fw+0x2f8`.
It holds by construction, because `allocatePackedSharedGroup` sets each entry's GPU address to the map VA
plus its offset. If it fails anyway (`fw+0x2f8 differs from the map VA`), that only means skip-control.
It is not a safety issue, because nothing is read from the refused window.

| part | time |
|---|---|
| DFU + palera1n + SSH | 3–6 min |
| preflight + upload + install | ~1 min |
| power (warmup 4 s, dry plan ≤10 s, idle 3 s, IOReport 34 s with 25 s stress and the 15 s load series, after 5 s) | ~1 min |
| pull (~3 MB) + check | ~1 min |
| step 4, back to the desktop | 6–10 min |

## 0. Is iOS still bootable?

Yes, as of the last evidence. Jailbroken iOS 16.7.10 (20H350) was booted
with `palera1n -l` on 2026-10-29 at 09:41 for the Mesa FDR calibration pull
(device-run-20261028.md (`../../release/device-run-20261028.md`, not published) §35). That
was a week after Omarchy moved to the NVMe on 2026-10-22. The "removed the
iOS boot partitions" line in `docs/sep/README.md` refers to the SEP GPT
partition; it does not mean iOS is gone. `preflight` re-checks this at the
start of the run: if `scripts/ipad-ssh.sh true` fails, or the device does not
report 16.7.10, stop there.

## 1. Build (host, before leaving Linux)

```sh
scripts/gpu/ios-capture-bundle-build.sh      # ~1 min; build/ios-capture-bundle/{bin,SHA256SUMS}
tools/ios-capture-bundle/test-runner.sh      # fake-device run of every phase (~25 s)
python3 -I tools/ios-capture-bundle/test_parsers.py
```

The build applies `tools/mesa-a9-loadop-re/g5pdump-load-mrt-scenes.patch` to a
temporary copy of `g5pdump.m`, so the tree's copy is untouched. It uses the
same compiler flags and `ldid` entitlements as the existing build scripts
(`tools/gpu-userspace/build.sh`, `tools/ios-gpu-probe/build.sh`,
`scripts/power/build-ios-ioreport.sh`). It does not touch
`build/ios-gpu-probe/`, `build/gpu-userspace/bin/` or `build/power/`.

Artifacts of this build (all arm64 or data; the build is reproducible: two rebuilds on 2026-11-01 gave
identical sums; only `ios-gpu-probe` and `device-capture.sh` changed after run 20261101T145640Z):

| file | bytes | sha256 | what |
|---|---|---|---|
| `g5pdump-gpu` | 119040 | `c0697d7e3fea9966af136c5c704073b3c1c841d7ff080c3f9b3405e3d81e4da2` | g5pdump + `load`/`load-linear`/`mrt2`/`mrt4` scenes, `ent-gpu.plist` |
| `basic.metallib` | 126441 | `888aa090caf8213325be1757d6da4a19e75157029057cd9b446258e8a5676bd4` | `v_passthrough`/`f_solid` (AIR, iOS 16) |
| `probes.metallib` | 26689 | `fc709c0f14d6ca2c18e68011c360e7bf64b8f6fcccf22d16873c42f3f6cec6a3` | `v_pos`/`f_mrt2`/`f_mrt4` |
| `ios-gpu-probe` | 314224 | `35410254d419027c1bfbc62abb2bfd79db457a7b07b8bd7a02685b9f52255418` | the probe with the `fw-blocks` mode (CPU-only group 2, `--skip-record`), `probe.entitlements.plist` |
| `metal-triangle` | 73936 | `60219ff25d66bcce21a7747e5d29826e9b2c6f672208254859baa2f49a320b8a` | stress workload, `metal.entitlements.plist` |
| `triangle.metallib` | 6712 | `6635e4bde457cc8583755f08eb5b1d99a3484b34eb02eb89d042e9853e13c25a` | its shaders |
| `ios-ioreport` | 54000 | `8953f52c36800f489faad9da1fc42fb3aec25a98ddfd44537cc3323488ba0a03` | IOReport sampler, `configs/power/ios-ioreport.plist` (readback checked) |
| `allow-sudo.sh` | 1495 | `8ee5ebd749d8aa12dbdb507e4c31e444623b67a0a89bb524901ababa8a372e53` | unchanged installer |
| `device-capture.sh` | 5227 | `91dbbeac574200de9c54b26f9ea2cba4546c22c8c1035730ef0983375e3fbf97` | the on-iPad sequence (fw-blocks modes; IOReport always) |

If a rebuild prints different hashes, the runner still works: it checks
against whatever `SHA256SUMS` the build wrote.

## 2. Runbook

### Step 0: record the Linux state you will return to (still in Linux)

```sh
mkdir -p build/gpu/ios-capture-bundle
scripts/omarchy/ssh.sh 'uname -r; cat /proc/cmdline; findmnt -no SOURCE /; cat /proc/sys/kernel/random/boot_id' \
  > build/gpu/ios-capture-bundle/pre-ios-linux.txt
ls build/boot/autoboot.disabled && scripts/boot/install-autoboot.sh --status   # must be paused / not loaded
```

* Autoboot is paused: `build/boot/autoboot.disabled` has existed since
  2026-10-30 16:22, and the LaunchAgent was not loaded when checked on
  2026-11-01. If it is active, the DFU in step 1 boots Linux instead of iOS.
* If `findmnt` shows an nbd root, leave `qemu-nbd` running. Never kill it by
  name.
* Kill any stale boot chains you started earlier before beginning this one.

### Step 1: Linux → jailbroken iOS (~3–6 min)

The owner puts the iPad into DFU (button hold), then:

```sh
palera1n -l
```

Use plain rootless palera1n, with no `-p`, no `-k` and no `-K` KPF; the probe
only needs its tfp0. If it hangs after "Booting PongoOS…" while PongoOS has
enumerated, Ctrl-C and run the identical command again (that happened on
10-29, §35). If palera1n refuses because of the passcode, use the same
`PALERA1N_BYPASS_PASSCODE_CHECK=1` that `scripts/boot.sh` uses. Do not use
`linux-a9-kexec ios`: it boots iOS without the jailbreak, so there is no SSH.
Then wait for SSH:

```sh
until scripts/ipad-ssh.sh true 2>/dev/null; do sleep 5; done
```

### Step 2: captures (~6 min)

Run each phase as its own command, in this order. Nothing retries. A
phase that fails prints `STOP: …` and exits non-zero.

```sh
scripts/gpu/ios-capture-bundle.sh preflight           # 16.7.10 (sw_vers, or build 20H350 / Darwin 22.6.0), kslide, >1 GiB free, bundle sha
scripts/gpu/ios-capture-bundle.sh upload              # 9 files; staged sha256 == bundle
IPAD_PW=… scripts/gpu/ios-capture-bundle.sh install   # allow-sudo.sh; installed sha256; sudo -n probe -> tfp0=valid
scripts/gpu/ios-capture-bundle.sh loadmrt             # ~10 s, 8 g5pdump captures (done 2026-11-01; skip)
scripts/gpu/ios-capture-bundle.sh power               # ~1 min, IOReport + fw-blocks idle/load/after
scripts/gpu/ios-capture-bundle.sh pull                # tar the device run dir -> build/gpu/ios-capture-bundle/<stamp>/device
scripts/gpu/ios-capture-bundle.sh check               # host: sanity table + parsed outputs (ICB_PHASES=power if only power ran)
scripts/gpu/ios-capture-bundle.sh health              # crash/panic files newer than the upload
```

`IPAD_PW` is read only by `install`, is piped to `sudo -S`, and is never
written anywhere.

If `install` ends with `tfp0=unavailable`, the probe cannot reach the kernel
task port, which means the jailbreak is not active (for example, iOS restarted
without palera1n). Nothing can be read in that state. Go through DFU and
`palera1n -l` again, then start over from `preflight`. The probe has always
taken its tfp0/hsp4 from palera1n rootless (`tools/ios-gpu-probe/README.md`).
The `-K` KPFs in other docs are AGX-observer experiments and are not needed
here.

`loadmrt` and `power` start `device-capture.sh` under `nohup` on the iPad and
poll for `<phase>.done`. The wait is up to 900 s and 300 s respectively;
`ICB_WAIT=<s>` overrides it. Because the phase runs under `nohup`, an SSH drop
does not kill a capture. If a phase prints `no <phase>.done after N s`, do not
start it again: read `RUN/<phase>.log` on the device, and `pull` once it has
finished. A phase cannot run twice in one run dir.
To repeat one, run `preflight` again (new run dir), then that phase, `pull`
and `check`.

What runs on the iPad:

* `loadmrt`, as mobile, with `TMPDIR=$B/tmp/ G5P_CAPTURE_SUBMIT=1 G5P_READ_NONRESIDENT_USER=1`.
  It runs `bin/g5pdump-gpu regions OUT LIB SCENE 192` for `pass`,
  `pass-linear`, `load`, `load-linear` (basic.metallib) and `mrt2`, `mrt4`
  (probes.metallib) into `loadmrt/`, then `pass` and `pass-linear` again into
  `loadmrt-repeat/` as the noise baseline. These are the flags of
  gles3-continued107 §3.1 and of the round-16 source capture.
  `G5P_READ_NONRESIDENT_USER=1` writes the `allocator-<scene>-encoded.txt`
  pool maps that the DOUTD decode needs.
* `power`, as mobile, with `sudo -n` for the probe only:
  1. `ios-gpu-probe probe` (tfp0).
  2. The run15/run18 warmup (`metal-triangle --seconds 4`, 64×64).
  3. `fw-blocks --dry-plan`. 7/7 records `ok` → mode `full`. The six group-0
     records ok but control refused → mode `skip-control`: steps 5, 7 and 8
     pass `--skip-record control`. Anything else (or no kslide/tfp0) → mode
     `none`: no block read at all. The mode goes to `power/fw-blocks-mode.txt`,
     and any mode except `full` makes the phase exit non-zero. Steps 4 and 6
     (ioreg, IOReport, stress) run in every mode.
  4. `ioreg` of the accelerator.
  5. `fw-blocks --tag idle --samples 3000` (3 s).
  6. `ios-ioreport 'GPU Stats'` and `'SoC Stats'` (34 × 1 s) started together
     with `metal-triangle --seconds 25 --triangles 1000000 --width 1536 --height 2048`.
  7. 3 s into the stress, `fw-blocks --tag load --samples 15000` (15 s).
  8. After the stress ends, `fw-blocks --tag after --samples 5000` (5 s), then
     `ioreg` again.

### Step 3: sanity check before leaving iOS

`check` prints one line per check and exits 0 only if all of them pass. Do
not leave iOS on a FAIL: repeat the failed phase as described above.

| check | passes when | a FAIL means |
|---|---|---|
| `capture loadmrt/<scene>`, `capture loadmrt-repeat/<scene>` | `rc=0`, ≥1 `<scene>-post-*.bin` region, `submit-<scene>-000-kernel.bin`, `allocator-<scene>-encoded.txt` | read `stdout-<scene>.txt`; a missing allocator file means the env was lost |
| `oracle load`, `oracle load-linear` | `LOAD_PRESERVED` (and `linear_backing` too) | Metal did not preserve the prefill; the capture is not a LOAD |
| `oracle mrt2`, `oracle mrt4` | `MRT_EXACT` | an attachment was unwritten or aliased |
| `decode load*` | background decoded, no `diff_error` | the record or the code heap was not found |
| `decode mrt*` | EOT decoded, every DOUTD source mapped, 2 or 4 target sets, USC ends in `0080` | see `parsed/mrt*.json` `variant_eot.error` |
| `power rc-*` | all 0 | read `power/N-*.txt` |
| `power fw-blocks mode` | `mode=full` | `skip-control`: control refused, the 6 group-0 records were read; `none`: no block read (reason in the file) |
| `power dry-plan records` | 7/7 | a slot pointer differs from the packed layout, or a group window was refused |
| `power control record` | `record control … ok` | group 2 refused; the `fw window group2 (CPU-only): <reason>` line in `1-dry-plan.txt` names the check |
| `fw-blocks idle/load/after` | index `end bad=0`, series `stop=complete` | partial dump; the index says which piece |
| `identity policy+0x4`, `identity meta+0x71c` | 16 and 340 in every snapshot | the block is not the one power-words-init-re describes (§9.2); do not trust its offsets |
| `ioreport GPU Performance States` | ≥1 delta, residency > 0 s | the IOReport subscription failed; read `ioreport-gpu/log.txt` |

`check` also writes the parsed outputs (§4), so the numbers can be looked at
before leaving iOS.

### Step 4: back to the Linux desktop (~6–10 min)

Use the orchestrator's current desktop path (2026-11-01): DFU, cold 124e, then a warm kexec into
the current desktop kernel. That kernel is the one step 0 recorded, normally `tahang1a-ed4afeb59c`
(or mcfold1 until tahang1 has passed its soak).

```sh
# DFU (owner), then the usual tethered chain, cold-booting the single-client 124e base:
scripts/boot.sh pongo
scripts/boot.sh linux build/boot/m1n1-linux-desktop124e-3863b328f2.bin
until scripts/omarchy/ssh.sh true 2>/dev/null; do sleep 5; done
# warm kexec into the desktop kernel, with the uploadfix ICD pair, rerender, lazy arena, no preload
UPLOADFIX=1 /tmp/desk/boot-desk.sh build/boot/m1n1-linux-tahang1a-ed4afeb59c.bin tahang1a-ed4afeb59c
```

If step 0 recorded a different kernel or entry, return to that one instead.
`/tmp/desk/boot-desk.sh` is the orchestrator's script; the older
`scripts/gpu/latency123-boot-desk.sh` path still works for latency123 entries.

* A PongoOS softboot `e0004051` means stop and DFU again.
* If `boot.sh linux` reports "no upload" four times, the device drops back to
  DFU. Retry once from DFU.

The iOS-side files can stay on the device: they are small apart from the run
dirs, and iOS is expendable.

### Expected runtime

| part | time |
|---|---|
| step 1, DFU + palera1n + SSH | 3–6 min |
| preflight + upload + install | ~1 min |
| loadmrt (8 × one submit, 64×64) | ~10 s (6 s on 2026-11-01) |
| power | ~1 min (dry plan ≤10 s, warmup 4 s, idle 3 s, stress 27 s, after 5 s) |
| pull (~100–250 MB) + check | 1–3 min |
| step 4, back to the desktop | 6–10 min |
| **total away from the desktop** | **~15–25 min** |

## 3. What is captured, and what is not

* **LOAD**: the user render record and code heap (regions), which
  `extract_a9_load.py` decodes, and the kernel command buffer plus submit args
  as the kext received them (`submit-<scene>-000-{kernel,args}.bin`). That
  buffer carries the ISP/PPP/BGRND/EOT descriptor words before the kext copies
  them into its firmware record.
* **MRT**: the same files for `mrt2`/`mrt4`. The EOT object is followed all
  the way down: record+0x2b8 → data segment → DOUTD descriptors → their PBE
  source dwords → the DOUTU qword after the last descriptor → EOT USC bytes,
  plus the EOT PDS program until halt. On the round-16 capture (one target)
  this gives the template's 7 PBE dwords
  `fb05050a 00007e01 8001c000 80000000 0 0 0`, DOUTU `0x1000002b00`, a 66-byte
  EOT USC, and PDS `f0000800 f4000012 d3000000`. On 2026-11-01 (mrt2/mrt4)
  the one DOUTD moves 8N−1 dwords: N target sets of 7, separated by
  `0xffffffff` (see the status section at the top).
* **Not captured: the kext-built W3D firmware record** (the
  `W 0x3b4..0x3c8, 0x108..0x130, 0x6b0` words of gles3-continued107 §3.1).
  Reading it needs `ios-gpu-probe capture-job` to hold a live g5pdump command
  buffer, which is the run-12 panic class (reading a PM buffer after its owner
  released it), so it is kept out of this one-boot bundle. The kernel command
  buffer has the descriptor words the kext copies into W. If a lane needs W
  itself, that is a separate iOS run.
* **Power**, for each snapshot (`idle|load|after` × `-a` before and `-b` after
  its series): `group0..3` whole and the `AGXMetaFirmware` (0xcd8) and
  accelerator (0x1eb0) instances. Group 0 is the roots: TA/3D/CL blocks,
  policy at +0x3040, meta at +0x3a00. Group 2 is control. So power-words §9.2's
  `root + X` is `group0.bin` offset X, and `shared + X` is `group2.bin` offset
  X. There is also a 1 ms change-compressed series of the host-written words:
  meta `+0x6c0..0x78c`, policy `+0x00..0x40` (with the firmware's pstate
  `+0x34`), `+0x630..0x710` and `+0x7c0..0x7d0`, control `+0x00..0x40`,
  `+0x220..0x240` and `+0x1028..0x1040`. Finally there is IOReport `GPU Stats`
  (GPU Performance States, Fender State, CLTM) and `SoC Stats`, and `ioreg`
  before and after. power-words §9.1 asks for `ios-probe-run16.sh`; it is not
  needed, because these dumps contain everything that run reads.
* **Not read: the GPU perf-state and voltage registers themselves.**
  * `Fender+0x1000/0x1004` and every PMGR register are MMIO. A userspace MMIO
    read panicked iOS on run 6, and `kread()` refuses device apertures.
  * The `AGXMetaFender` object's state shadow (`+0x08`) and ApplePMGR's
    per-domain records have no validated pointer path in the probe. Reaching
    them would be new RE, not part of this bundle.
  * Substitutes: IOReport `Fender State` and `GPU Performance States`
    residency (what the host and firmware actually selected), `policy+0x34`
    in the series (the firmware's pstate index), the accelerator limit fields
    and the live ADT `perf-states` already pulled to
    `build/firmware/private/IODeviceTree.txt`.
  * power-words-init-re §7 found that on A9 no OS writes a GPU voltage. The
    PMGR perf-state path is CPU-only, and the voltage follows the frequency
    in hardware. So there is no host-written iOS voltage word to capture.

## 4. Offline parsing (host)

`check` runs all of this. Each part can also be run on its own (D =
`build/gpu/ios-capture-bundle/<stamp>/device`):

```sh
P=tools/ios-capture-bundle
python3 -I $P/parse_load_mrt.py load $D/loadmrt load        --ref pass        --repeat $D/loadmrt-repeat > load.json
python3 -I $P/parse_load_mrt.py load $D/loadmrt load-linear --ref pass-linear --repeat $D/loadmrt-repeat > load-linear.json
python3 -I $P/parse_load_mrt.py mrt  $D/loadmrt mrt2        --ref pass        --repeat $D/loadmrt-repeat > mrt2.json
python3 -I $P/parse_load_mrt.py mrt  $D/loadmrt mrt4        --ref pass        --repeat $D/loadmrt-repeat > mrt4.json
python3 -I $P/parse_power.py $D/power --json power-words.json --md power-words.md
# the gles3-continued107 3.1 commands still apply to $D/loadmrt:
python3 -I tools/mesa-a9-loadop-re/oracles.py capture $D/loadmrt mrt4
```

| output | for | contents |
|---|---|---|
| `load.json` `load_program`, `load-linear.json` | Mesa LOAD lane | BGRND0..3, texunicode PDS words, DOUTU qword and launch word, USC bytes up to `0080`, BGRND1/2 DOUTD descriptors with source dwords |
| `*.json` `extract` | both | changed spans vs the CLEAR reference with the repeat's noise removed (`extract_a9_load` v2 schema), unpaired regions |
| `*.json` `submit.kernel`, `submit.args` | both | kernel command buffer word diff vs reference (`semantic`), noise offsets, the variant's full word list |
| `mrt2.json`/`mrt4.json` `mrt_eot` | Mesa MRT lane | EOT words, EOT PDS program and its DOUTD count, DOUTD list, `pbe_dwords` (and `pbe_sets_of_7` when the count is a multiple of 7), DOUTU qword, EOT USC bytes |
| `power-words.md` / `.json` | kernel power lane | per word: catalogue.py name/kind/apple_derived/linux_staged, the power-words doc's iOS expectation and its Linux arm A value, the iOS value in all six snapshots (policy `+0x6xx/+0x7xx` also as f32); identity checks; uncatalogued non-zero words; words that moved between snapshots; the 0x160-byte `policy+0x3c` table and its `acc+0x450` source; accelerator fields; series transitions; IOReport residency |

The power table shows catalogue.py and power-words-init-re side by side even
where they disagree. For example, catalogue.py's `meta+0x6d4` says "no Apple
write", while the doc says 1 while powered. The live column settles it.

## 5. Changes and tests

* `tools/ios-gpu-probe/ios-gpu-probe.c`, new mode `fw-blocks`. It dumps
  whole host-written records. Each record is read only after its
  `AGXMetaFirmware` slot pointer has been checked against the packed group-0
  layout inside the window that `fw_windows_resolve()` already validated. A
  layout surprise therefore refuses instead of guessing. Reads go through
  `safe_read_span` inside the windows, or `obj_read` inside the class-checked
  instances. There is no MMIO, no kernel write and no new pointer chase. The
  series writes the first sample, each changed one and the last one, with
  timestamps.
* `tools/ios-gpu-probe/test-fw-blocks.c`, a host test: slot checks, dumps,
  series compression, the refusal paths, and pool windows skipped. Its
  `--fixture DIR` mode writes the power-parser fixtures with the real code.
* `tools/ios-capture-bundle/`: `device-capture.sh`, `parse_load_mrt.py` (code
  heap found without the single-target EOT signature, EOT followed to PBE and
  USC, kernel-buffer diff), `parse_power.py`, `check.py`, `test_parsers.py`
  (pass6 EOT decode, a synthetic 2-target EOT, both code-heap fallbacks, the
  power table, check gates) and `test-runner.sh` (every runner phase against
  a fake iPad, the version gate with and without `sw_vers`, and the tfp0
  stop).
* `scripts/gpu/ios-capture-bundle-build.sh` and `scripts/gpu/ios-capture-bundle.sh`.
* `tools/ios-gpu-probe/test-run18.sh` still passes with the new mode in the
  file.

After run 20261101T145640Z:

* `ios-gpu-probe.c`:
  * `fw_group2_resolve()` and its pure check `fw_g2_layout_ok()` (status section). Only `fw-blocks`
    calls them, and the window is kept in `t->g2`, outside `t->win`.
  * New flag `fw-blocks --skip-record NAME`: the record is neither read nor counted as bad.
* `test-fw-blocks.c` adds three cases:
  * skip-control with group 2 unresolved: six records dumped, `end bad=0`;
  * the layout proof on the live offsets, plus six perturbations;
  * `fw_group2_resolve` through the mock: accept, the window is not reachable by META lookup, and six
    refusals (map class, map VA, resource length, resource class, one slot, overlap).
* `parse_load_mrt.py`:
  * the record finder outside the 31-region profile: a region base +0x288 with a DOUTD+DOUTU+halt EOT,
    exactly one, or refuse;
  * new `pbe_targets` and `pbe_target_count` fields.
* `check.py`:
  * the MRT target-count gate;
  * the `power fw-blocks mode` and `power control record` lines;
  * `--phases`.
* `device-capture.sh`: the fw-blocks modes, and IOReport independent of fw-blocks.
* `test_parsers.py`, 15 tests:
  * decoy record (pass6, off-profile);
  * `pbe_targets`;
  * the 2026-11-01 pulled data: mrt2 and mrt4 decode, load and load-linear unchanged;
  * check gates for skip-control and for IOReport without any block.
* `test-runner.sh` covers three power modes on the fake iPad:
  * full;
  * skip-control (`--skip-record control` on idle, load and after; IOReport present);
  * tfp0 gone (no dry plan, no block read, IOReport and registry still written).

## 6. Device footprint

The bundle writes only to `/var/mobile/linux-a9/icb/` (binaries, `tmp/` and
`run-<stamp>/`) and `/var/mobile/linux-a9/ios-gpu-probe/` (allow-sudo's staging
dir). `allow-sudo.sh` copies the bundle's probe, `metal-triangle` and
`triangle.metallib` into `/var/jb/usr/local/linux-a9-gpu/` and rewrites the
same sudoers rule that is already there. That replaces the run18 probe in that
dir, but `ios-probe-run18.sh` re-uploads and re-installs its own probe at the
start of every run. Nothing touches syscfg, NVRAM or the boot-firmware storage.
