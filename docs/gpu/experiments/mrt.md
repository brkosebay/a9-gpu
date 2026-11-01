# Several colour targets (MRT): 2 and 4 targets, end to end (offline, 2026-11-01)

GLES 3.0 needs `GL_MAX_DRAW_BUFFERS >= 4`. This lane makes the A9 Vulkan driver write 2 or 4 colour
targets in one pass, the way Apple's driver does, and adds the one kernel field that was missing. It was
built and tested offline only. Nothing has run on hardware.

## 1. Apple's N-target pass (capture 20261101T145640Z, `mrt2`/`mrt4` against `pass`)

Source: `build/gpu/ios-capture-bundle/20261101T145640Z/parsed-v2/mrt{2,4}.json`, the raw regions in
`device/loadmrt/`, and Apple's A9 compiles `build/gpu-userspace/loadop-re/split_g5p/01{1,2}-*`. Every
driver-written word that changes with N:

| What | 1 target (our template) | 2 targets | 4 targets |
|---|---|---|---|
| Fragment program | `o0,o1` + emitpix `1c21 0008` | `o0..o3` + `1c41 0008` | `o0..o7` + `1c01 0008` (field 0 = 8) |
| Clear USC (BGRND0's pixel program) | 4 × `8a01 h …` (f32 common h → output half h), `1c21 0008`, END | 8 halves, `1c41` | 16 halves, `1c01` |
| Clear DOUTD (BGRND2) | 4 f32 → common 0..3 (`0x90000004`) | 8 (`0x90000008`) | 16 (`0x90000010`) |
| BGRND3, texunicode loader, EOT PDS program | `800000/800000`, `f4000800 d3000000`, `f0000800 f4000012 d3000000` | same | same |
| EOT DOUTD (PBE state → common 0..) | 7 dwords (`0x90000007`) | 15 (`0x9000000f`) | 31 (`0x9000001f`) |
| PBE block | target 0's 3 qwords + `0` | per target: 3 qwords + output-half offset `4t`, `0xffffffff` between targets | same |
| EOT USC | 66 B | 66 B | 130 B |
| `eot[2]` = EVENT_PIXEL_PDS_INFO | `0x402` | `0x402` | `0x802` (usc_sr_size 4) |
| `eot_resume` | = eot | = eot | = eot |
| `restore[1]` (record+0x51c) | `0x800000` | `0x1000000` | `0x2000000` |
| Pixel output width (record+0x6dc, our `pixel_output_width`) | 2 | **4** | **8** |

- The EOT USC is regular. Per target, in reverse order, it has one state load `88 01 2t 01 (99+0x20k) 01 00 41 28 01 39 00`.
  Then one PBE write per target, `1c05 2t 71|51 2k 08` (with `4c04` between writes). Then a branch to an END
  on the next 64-byte boundary. `a9/g5p_mrt_programs.h` generates the programs and the PBE block.
  For 2 and 4 targets the output equals Apple's byte for byte. For 1 target it equals our template.
- The RT parameter block (`allocator-*-params.bin`, 1 KiB) is identical for pass, mrt2 and mrt4, so the
  render-target geometry does not depend on N. Region 1's `0x106 → 0x506` words are GPU-written (absent
  pre-run), and region 0 is a read-only IOKit accounting page. Neither is driver input.
- Not reproduced: Apple's partial-render restore loader (DOUTD 8N image + 2 sampler + 6N transform dwords,
  record+0x500/0x504). Our kernel builds no PR restore background for one target either. That is a
  pre-existing gap, and only a parameter-buffer overflow reaches it. `restore[1]` follows Apple.

## 2. What was built

**Kernel** `a9/gpu-mrt1` @ **`96fa98d406`** (worktree `/src/wt/mrt1`), one commit on
`a9/gpu-multiclient2` @ `995d684abd`:
- `drm_g5p_render_pass_v3.pixel_output_width` and `pad` are carved from `reserved` (offset 504; the size
  stays 536). `DRM_G5P_CAP_RENDER_PIXEL_OUTPUT_WIDTH` is bit 29 and is reported with TYPED_RENDER_V3.
- V1 dialect (all-zero ext): 0 or 2 is today's pass, byte for byte. 4 and 8 write the width words (W 3c8,
  3cc, 108..130, 6b0) through the same 6ca7ae0 formula as the V2 dialect, now `g5p_ru_fill_3d_width()`.
  Any other width, or a non-zero pad, is `-EINVAL`.
- In the V2 dialect the field must be 0, because ext carries the width there.
- Width > 2 requires `eot_resume == eot`, so a partial render stores every target.
- No register access and no change to scheduling, binding or the kexec quiesce: W lives in RAM.

**Mesa** fork branch `g5p-mrt` @ **`7f4508599d4`** (worktree `build/third_party/mesa-a9-mrt-src`), 6 commits
on `g5p-partial@1ae79db1d8a`. That is the pair labwc runs; stack XFB (`g5p-xfb-fix`) later.
1. `11e0e7ed153`, `e7cc132d9ea`: mrt107's 0001/0002 (output plan, `g5p_usc_emit_mrt`, contiguous `o0..o(2N-1)`).
   mrt107's 0003 (V2-transport gate) and 0004 (XFB refusal, the XFB lane's) are not taken.
2. `fee05cde061`: a value read by two output halves (`o1 = v.yxzw` after `o0 = v`) no longer dies at its
   first read. Several targets hit this with any varying, and so did one target with `v.xxzw`. Only shapes
   that were refused before change. `g5p-shaderc --targets N`.
3. `56730378a01`: the recorder takes targets 1..N-1 from BeginRendering and render passes, lists them
   READ|WRITE, and builds §1's blocks fresh. The template keeps its layout: the clear DOUTD at state+0x800
   and the EOT data at +0x810 are rewritten in place, and BGRND0 names the N-target clear.
   `vkCmdClearAttachments` takes the attachment index.
   - Refused by name: 3 or more than 4 targets; LOAD, a partial area or profile1 with several targets; aliased
     targets; blend or a write mask other than 0xf with several targets; a pipeline whose target count
     differs from the pass's.
4. `3fc88602cb2`: the V3 pass carries `pixel_output_width = 2N` (0 for one target) and `restore[1] = 0x800000·N`.
   - `maxColorAttachments = maxFragmentOutputAttachments = 4` with TYPED_V3 + bit 29, else 1.
   - `independentBlend` stays true. Zink derives GLES 2.0's `blend_equation_separate` from it, so turning it
     off would cost labwc GLES 2.0. Every N > 1 blend is refused, so no attachment state is ignored.
5. `7f4508599d4`: gfx-test scenes and rejections.

The source change touches `src/imagination` (Mesa's AGENTS.md asks agents not to). This is on the
owner's instruction, as for the earlier lanes. Every commit carries `Generated-by: LLM`. Private fork only.

## 3. Offline results

`scripts/gpu/mrt.sh all`, `scripts/gpu/mrt1-build.sh check`:
- **Controls.**
  - The g5p-partial tree rebuilds the desktop ICD `5b1d04ea`.
  - The trampoline recipe gives `081885cc` (default) and, on the partial ICD, the desktop's `593fcd8c`.
- **Regression.** All 30 round17-19 shaders compile to identical bytes (old vs new compiler). All 405 files
  of every pre-existing g5p-gfx-test scene record identically. The round-17 decoder gives 11/11.
- **`tools/mesa-a9-mrt/check-mrt.py`: 133 checks, 0 failures.** Against Apple's mrt2/mrt4:
  - the clear USC, launch word, DOUTU temps, texunicode, BGRND2 control, clear floats (C/M/W/K) and BGRND3;
  - `eot[2]`, the EOT PDS program, DOUTD control, DOUTU temps and the EOT USC, all byte-exact;
  - the PBE block: each target is our one-target PBE of that image + `4t` + separators, and Apple's is its
    one-target PBE + `4t` + separators. Our dwords 0/3/5 differ from Apple's twiddled targets exactly as in
    the device-proven one-target pass (linear rows).
  - Our compiled `mrt2.frag`/`mrt4.frag` have the same instructions as Apple's `f_mrt2`/`f_mrt4` and the same
    `1c41/1c01 0008 0080` tail.
  - Then `oracles.py mrt` on images rebuilt from the recorded words (clear block → output-half offset `4t`
    → target; fragment constants likewise) gives **MRT_EXACT for mrt2, mrt4 and mrt4 with in-pass clears**.
    The all-offset-0 mutant is caught.
- **Rejections** (gfx-test, each with its named reason): 3/5 targets (pipeline and pass), blend and mask
  0x7 with 2 targets, LOAD, partial area, aliased targets, pipeline/pass count 1/2, 2/1 and 4/2, a clear of
  attachment 2 of 2. The mock reports max 4.
- **Repro over the typed-V3 fake node** (`A9_FAKE_MRT=1`: cap 29 and the kernel's width/eot rules), direct
  and through the trampoline:
  - `mrt-vk --offline`: 9 jobs accepted. The fake kernel saw 3 width-4 passes (`eot 30810/2bc0/402
    restore1 1000000`) and 3 width-8 passes (`…/802 2000000`).
  - Without cap 29: max 1, and the one-target case still runs. MRT passes are refused before any ioctl.
  - The trampoline classifies the MRT fragment programs OPAQUE.
- **Kernel host.**
  - `scripts/gpu/mrt1-host/test_render_mrt.c`: 168 checks, 0 failures. It covers widths in both dialects,
    the eot rule, and that width 0/2 leaves the 3D W byte-identical while 4/8 change exactly the nine
    width words, to the V2 dialect's values.
  - On this tree: `test_render_user_v3` 1558/0, the five multiclient2 suites, `audit_mc2` PASS, the tahang1
    gate (10767, 11 mutants), `test_kexec_quiesce` 107/0, bind/admit, `audit_kq`/RAM-only PASS.
  - checkpatch: 0 errors (2 warnings, the `Co-Authored-By` form).
- `tools/mesa-a9-mrt/check-mrt-device.py --self-test`: the model passes; swapped targets, a broadcast
  output, a stale previous-job clear and a leftover sentinel are all caught.

The offline checks prove that our words equal Apple's and agree with each other. Only the device can say
whether the GPU writes N targets.

## 4. Artifacts

| File | sha256 |
|---|---|
| `build/boot/m1n1-linux-mrt1-96fa98d406.bin` (entry `mrt1-96fa98d406`, release `7.3.0-rc1-g96fa98d40639`, config = combined118) | `b0f8012281fc9602d1cde76cec1c3816f881edf25871a7383a0c94f1732d9b19` |
| `build/gpu/mrt/package/mrt-a9.tar.gz` | `7e6cfa37d3ccb289b91689b2d5d924ca6efac47fcdb87c8979fb8618e3cb3358` |
| └ `lib64/libvulkan_g5p.so` (ICD, `g5p-mrt@7f4508599d4`) | `0db6fa5ff113b2a27bc877a738695f074241109dbb2b1a110c003963d8773043` |
| └ `lib64/libvulkan_g5p117.so` (trampoline pinned to it, VM-edit shift 384) | `98d03359bddcce9623b794849a870f3a85f6e47da97d6379184b4830b5678316` |
| └ `bin/mrt-vk` (device fixture) | `23b4de340359a941d2cd704a0e9c7f267f2672709e390d563908019bf242884b` |

The entry dir is `build/gpu/mrt1/mrt1-96fa98d406-entry/` (`entry.sha256`). labwc keeps its own pair.
This package is for the extra client only.

## 5. Device plan (orchestrator): **warm-safe**

Worst case: a lost client context, or a warm hop back to the base. There are no firmware power words and
no new GPU register access, and the kexec quiesce is unchanged (the same tree as multiclient2).

**Order.** Use its own boot on the mrt1 kernel, after the base boot's own tests. In it: M0, M1, M2, M3 (optional),
then hop back. If it must share a boot, it goes after nothing that can end the epoch and before anything
that can (F stall hook, XFB diag, recovery soak R). It never shares a boot with a power-word test (DFU-risk).

**What can wedge.**
- **The width words** at 4/8 (W 3c8/3cc/108..130/6b0). The formula comes from the kext and has never flown.
  A wrong partition gives garbage or a 3D that never completes. That is a timeout of a non-first job, so
  the context is lost (multiclient2 isolation, flown in Boot 1 F). Worst case `drain dead`: the epoch
  ends, then hop back.
- **A wrong PBE address/stride** would MMU-fault (`terminal kind=fault`). labwc stops, then hop back:
  deskfold-era kernels warm-recovered two client TA faults the same way (STATUS).
- **The EOT/clear USC programs** are Apple's bytes, so they are unlikely to loop. If one did, it is the same
  as the timeout case.
- **Every run's first job is the proven one-target pass** (`--case one,…`), so every MRT job is a non-first
  job of its context, the case loss isolation covers.

Setup (`SSH=scripts/omarchy/ssh.sh`, under `caffeinate -i`; `R=/tmp/mesa102-desk` as boot-desk.sh stages it):
```
# hop in from the running base (deskfold/multiclient2), as deskfold step 3:
$SSH 'sudo -n systemctl stop desk-window.service desk-read.service desk-labwc.service; sleep 1'
$SSH 'echo td-kexec | sudo -n tee /sys/bus/platform/devices/201000000.gpu/command; sudo -n dmesg | grep -E "GPU-KQ|RENDER-V3 unbind|POWER gfx-off|command td-kexec"'
<the desktop's usual flags> scripts/omarchy/desk/boot-desk.sh build/boot/m1n1-linux-mrt1-96fa98d406.bin mrt1-96fa98d406
$SSH 'uname -r; P=/sys/module/a9_gpu_drm/parameters; for p in render_user_drain_ms render_user_hard_ms render_user_recover render_user_test_stall_job kexec_quiesce render_user_fw_window; do printf "%s=%s " $p "$(sudo -n cat $P/$p)"; done; echo'
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (terminal|refused|bind)|RENDER-USER attached" | tail -4; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
$SSH "cat > /tmp/mrt-a9.tar.gz" < build/gpu/mrt/package/mrt-a9.tar.gz
$SSH 'cd /tmp && rm -rf mrt-a9 && tar xzf mrt-a9.tar.gz && cd mrt-a9 && sha256sum -c SHA256SUMS && mkdir -p out &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/mrt-a9/lib64/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json'
R=/tmp/mesa102-desk
mrt() { $SSH "cd /tmp/mrt-a9 && sudo -n env LD_LIBRARY_PATH=$R/a9-mesa/lib64 VK_DRIVER_FILES=/tmp/mrt-a9/icd.json PVR_G5P_EXPERIMENTAL=1 PVR_G5P_DEBUG=refuse \
  timeout -s KILL 300 ./bin/mrt-vk --case $1 --out /tmp/mrt-a9/out > $2.txt 2>&1; echo $2 rc=\$?; grep -E 'maxColor|^case|job=|^result=|G5P117_INIT' $2.txt"; }
mark() { $SSH "sudo -n dmesg | grep -E 'RENDER-V3 (bind|unbind|close|lost|drained|drain dead|terminal|refused|3d-refused)|G5P VM release' | tail -${1:-6}; sudo -n grep 'LABWC-A9 FRAME' /run/desk/labwc.log | tail -1"; }
```
Boot PASS: `uname -r` = `7.3.0-rc1-g96fa98d40639`, `render_user_drain_ms=45000 render_user_hard_ms=60000
render_user_recover=0 render_user_test_stall_job=0` (isolation on), labwc attached and FRAME advancing,
no `terminal`.

**M0. Control (one target, this kernel + this ICD):** `mrt one m0; mark`
- PASS: `maxColorAttachments=4` (the kernel reports bit 29 and the ICD sees it), `G5P117_INIT … result=ok`,
  3 jobs `result=EXACT` with every `guard=0`, `rc=0`; one `bind … ret=0`, then `unbind`/`close … plan=unbind`.
- `maxColorAttachments=1` means the wrong kernel or ICD: stop.

**M1. 2 targets (the 64×64 mrt2 scene, then 2 more jobs with other clears and quads, one context):**
`mrt one,mrt2 m1; mark`
- PASS: rc=0, 6 jobs EXACT (3 one-target, then 3 mrt2), every `rt*:bad=0,guard=0`, no `lost`/`terminal`,
  labwc FRAME still advancing.

**M2. 4 targets:** `mrt one,mrt4 m2; mark`. PASS as M1, with `rt0..rt3`.

**Host oracle on the readback:**
```
mkdir -p build/gpu/mrt/device && $SSH 'cd /tmp/mrt-a9 && tar cf - m*.txt out' | tar -C build/gpu/mrt/device -xf -
python3 -I tools/mesa-a9-loadop-re/oracles.py capture build/gpu/mrt/device/out mrt2   # Apple's scene check, unchanged
python3 -I tools/mesa-a9-loadop-re/oracles.py capture build/gpu/mrt/device/out mrt4
python3 -I tools/mesa-a9-mrt/check-mrt-device.py build/gpu/mrt/device/out              # every job, its own clears
```
PASS: both `MRT_EXACT` (covered 1024), and every line `result=PASS`.

**M3 (optional, last). 4 targets from one varying** (inferred register-form writes into output halves 4..15;
no Apple witness): `mrt one,mrt4-vary m3; mark`. PASS as M2. A mismatch here with M2 passing points at the
inferred fmul-result-half form, not at the pass.

**Reading a failure** (`m*.txt` has a line per target per job):
- `refused … (-8)` before submit: the ICD's reason is in `m*.txt` (`PVR_G5P_DEBUG=refuse`). A kernel
  `-EINVAL` shows as `RENDER-V3 refused` in dmesg (width/pad/eot rule).
- `rtN:bad=…,sentinel>0` (N ≥ 1): the EOT did not store target N (EOT USC or PBE block).
  `foreign>0`: target N got another target's clear or output (offset `4t` or the clear order). Every pixel
  bad, with target 0 right: suspect the width words.
- `guard>0`: the PBE wrote outside the image (address/stride). Stop the lane.
- VkResult -4 or 2 (fence timeout): expect `RENDER-V3 lost … drained` (only the context is lost; labwc keeps
  rendering). Collect dmesg and stop the MRT runs.
- `terminal kind=fault` or `drain dead`: labwc stops. Collect dmesg and `/run/linux-a9/pstore`, then hop back.

**Hop back** (always, also after a pass): the same three lines as the hop in, with the base payload
(`build/boot/m1n1-linux-multiclient2-995d684abd.bin multiclient2-995d684abd`, or the desktop kernel of the
day). PASS: `GPU-KQ … ret=0`, the new boot `origin=warm-cycled failed=0`, labwc attached. DFU only if the
hop itself fails (STATUS).

## 6. Not done, and why

- 3 targets, mixed formats (RGBA16F, R32F, …), MRT + blending, MRT + LOAD/partial areas: no Apple capture.
  They are refused by name. LOAD needs Apple's N-image background, the same loader as the PR restore in §1.
- The partial-render restore loader for N targets (§1), as for one target.
- Depth with several targets is untested: ZLS is still refused, so depth stays on chip.

## 7. Files

- Mesa fork `g5p-mrt`: `pco/{g5p_mrt_out.h,g5p_usc.[ch],pco_g5p_general.[ch]}`,
  `vulkan/g5p/{a9/g5p_mrt_programs.h,a9/g5p_typed_v3.h,g5p_job.h,g5p_gfx.h,g5p_record.c,g5p_cmd_gfx.c,g5p_pipeline.c,g5p_nir.c,g5p_device.c,g5p_drm_render.c,g5p_submit.c,g5p_shaderc.c,g5p_gfx_test.c}`.
- Kernel `a9/gpu-mrt1`: `include/uapi/drm/apple_g5p_drm.h`, `drivers/gpu/apple-g5p/render-user-layout.h`,
  `drivers/gpu/drm/apple-g5p/{render-user-validate.h,drm-render.inc,a9-gpu-drm.c}`.
- Workspace (not committed; the orchestrator commits):
  - `scripts/gpu/mrt.sh` and `scripts/gpu/mrt1-build.sh`;
  - `scripts/gpu/mrt1-host/test_render_mrt.c`;
  - `tools/mesa-a9-mrt/` (`check-mrt.py`, `check-mrt-device.py`, `mrt-vk.c`, `mrt-programs-dump.c`, `shaders/`);
  - `patches/mesa-a9/test/a9-fake-node.c` (`A9_FAKE_MRT=1`; unset, it behaves as before);
  - this file.
