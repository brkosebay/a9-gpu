# Partial redraw: loadOp LOAD and render areas smaller than the target (offline, 2026-11-01)

Goal: labwc redraws only damage instead of the whole 1536×2048 every frame, so
`WLR_SCENE_DEBUG_DAMAGE=rerender` can go. Built and tested offline only; nothing has run on hardware.

## 1. Apple's background LOAD object (decoded)

Source: iOS capture `build/gpu/ios-capture-bundle/20261101T145640Z` (scenes `load`, `load-linear`, against
`pass`; `docs/gpu/experiments/logs/ios-capture-20261101T145640Z.md`). LOAD is the background object, like
CLEAR. Only the background words, its programs and four record words change.

| Word | CLEAR (template) | LOAD (Apple) | Meaning (Rogue `cr.xml`) |
|---|---|---|---|
| BGRND0 | `28c0 / 2840` | same layout, new programs | shader_addr (pixel-shader PDS data) / texunicode_addr |
| BGRND1 | 0 | 0 | unused |
| BGRND2 | `30800` → 1 DOUTD | `3c000` → 3 DOUTDs | uniformdata_addr: the loader's DOUTD table |
| BGRND3 | `800000 / 800000` | `1800000 / 800000` | SIZEINFO: pds_uniformsize 1 → 3 (×128 bit), usc_sharedsize 1 (×16 dwords) |

- **Pixel-shader PDS** (at BGRND0.shader): DOUTU qword `0x10_xxxxxxxx` (temps 8, the USC offset), launch
  `f4000002` at +0x20. This is the same shape as every fragment launch.
- **Uniform loader** (at BGRND0.texunicode): `f0001800 f0002008 f4002810 d3000000` = `loader_program(3)`.
- **DOUTD table.** It holds 3 source qwords, then 3 `{control, 0}` qwords: the driver's `loader_data` layout.
  The parsed JSON slices it as 16-byte records, so its "dest 50, 32 dwords" entry is misaligned. Re-sliced:

  | Source | Control | Contents |
  |---|---|---|
  | +0x00 | `10000008` → common0..7 | the attachment's image state (q0, q1, q2 = 0, q3 = q0) |
  | +0x20 | `10010002` → common8..9 | sampler `000e0000 00000040` |
  | +0x30 | `90014006` → common10..15 (last) | raster→texel 2×3 transform `1 0 0 1 0 0` |

- **Image state.** It is exactly `g5p_pbe_encode(pbe=false)` for the attachment, except Apple writes no q2 for
  64-byte rows. `check-partial.py` reproduces the capture's 64×64 linear words from the formula.
- **Sampler.** The low word is the captured fragment nearest/clamp word. The high word `0x40` replaces the
  fragment sampler's `00c00780`; bit 38 is presumably unnormalized coordinates, because the transform is the
  identity in pixels. Our sampler encoder refuses unnormalized coordinates, so the words are copied verbatim.
- **USC** (116 B, sha `f9f03daf…`): raster XY (`1c21 0068`) → u2f, then the common-store transform (`alu`/
  `9e`/`9a` forms), then the texture sample `18f5 0001 0880` (the `f_load_sample` form), then `emitpix`, then END.
- **Record words the ICD cannot set.** Our TYPED_V1/V3 kernel builds these, and the kernel r22 importer
  says (record→W perturbation):
  - record+0x4dc: 0 → `0x100`, which drives **3D W+0x7fc = 1**. No UAPI field reaches it.
  - record+0x500/0x504: the partial-render background pair, which LOAD sets to the main BGRND0. It reaches
    no W word.
  - record+0x600: 1 → `0x10000`. It reaches no W word.

## 2. What was built

Mesa fork branch **`g5p-partial`** (worktree `build/third_party/mesa-a9-partial-src`), 3 commits on `5c2eb7a6d4b`:

1. `40b0ef60399` **Scissor table to pool8.** TYPED_V1 already passes pool8 as ISP_SCISSOR_BASE (3D W+0x60;
   V2 names it `scissor_va`), and the hardware never writes pool8 (r66 `pool8_nz=0`). The recorder used to
   write its IPF pairs at scissor+0x2000, which no transport passed on, so typed jobs with a scissor were
   refused. Now the pairs go to pool8, and that refusal is gone. V3 builds W from the same base pass
   (`drm-render.inc` `g5p_ru_fill_3d`), so no kernel change is needed.
2. `f89f889e0b8` **LOAD and partial render areas.**
   - `load_background()` builds Apple's object above with `pdsc` and csbgen `cr.h`
     (`PDS_BGRND0/2/3`), with the image state pointing at the attachment itself.
   - Every tile runs the background and every tile is stored. So a render area smaller than the attachment
     uses the LOAD background (for LOAD and DONT_CARE), and draws are clipped to the area through the
     scissor table. Untouched tiles are loaded and stored back unchanged, so no store-op change is needed.
   - A full in-pass clear before any draw still swaps the clear background back in.
3. `1ae79db1d8a` **Offline scenes.** Also fixes the stale `rgba-scissor` pitch check (400 → 448), which had
   stopped that scene recording since the 64-byte row alignment.

**Still refused:**
- CLEAR, or any in-pass clear, in a partial render area (both need a clear quad).
- Partial or late `vkCmdClearAttachments`.
- LOAD for profile1 placement, or with rows not a multiple of 64 bytes or a base not 64-byte aligned.
- Depth LOAD/STORE (no ZLS).
- Everything else that was refused before.

**Why labwc needs LOAD only.** wlroots 0.20.2 GLES2 clips damage with per-rectangle triangles and never
calls `glScissor`. Only our 0001 patch's full-buffer opaque rectangle becomes a `glClear`. Zink keeps
`renderArea` at the full framebuffer for GBM/EGLImage FBOs (damage-sized areas only for kopper swapchains).
So without `rerender` the ICD sees LOAD, full area, and draws limited to damage. Partial areas and scissors
are for other clients.

**Offline results** (`scripts/gpu/partial.sh all`):
- Build control: the g5p-upload-fix tree reproduces device ICD `a2f00cda`.
- g5p-gfx-test: all 26 earlier scenes record byte-identically. The 6 new scenes record.
- `tools/mesa-a9-partial/check-partial.py`:
  - Each new scene's words match the capture: BGRND1..3, DOUTU temps, launch word, texunicode, USC bytes,
    DOUTD controls and offsets, sampler, transform, and the image-state formula.
  - A CPU model of each recorded job (LOAD from the decoded image state, clip from the pool8 table, store all)
    equals Vulkan's result pixel for pixel over the whole image. The model covers 64×64, 100×75 and
    1536×2048, partial areas off the tile grid, DONT_CARE, and LOAD then CLEAR.
- `test_check_partial.py`: 18/18 mutants caught.
- The round-17 decoder passes 11/11 scenes, `rgba-scissor` with the pool8 table included.
- `partial-vk --offline` over the typed-V3 fake node accepts all 10 passes, both on the ICD directly and
  through the trampoline (the blend pass is classified DST and patched TRANSLUCENT).
- uf-gl (GLES2 via Zink) through the new pair: PASS, 7 jobs, 0 refused.

The offline model checks our own encoding against itself and the capture. **Only the device can say
whether the GPU loads.**

## 3. Artifacts

Package `build/gpu/partial/package/partial-a9.tar.gz`, sha256 `56e7f7e74b6336bf0ccfa32d9a7f82c71d6afc696b6f1ae31d7ad26819a3e45a`.

| File | sha256 |
|---|---|
| `lib64/libvulkan_g5p.so` (ICD, `g5p-partial@1ae79db1d8a`) | `5b1d04ea13213e140224f036cffdfaf07ab1e696ae71168e472ff5560b7041b9` |
| `lib64/libvulkan_g5p117.so` (trampoline pinned to it, VM-edit shift 64) | `593fcd8c375d9e3b956206e54998557b2706a07d213a67aa8121912930357108` |
| `bin/partial-vk` (device fixture) | `c268d18aa4dc59be602dddbcdd42f7c1e0e792c682cab779078ddaefb327561f` |

The trampoline is the unchanged Mesa117 source (`-DG5P117_VM_EDIT_SHIFT=64`); the default build is still the
device's `081885cc`. Like the uploadfix pair, swap both libraries or neither. This ICD does **not** contain
`g5p-arena-fix`, so keep the eager-arena stopgap (`/tmp/deskcfg/arena.env`) the current desktop uses.

## 4. Device plan (orchestrator)

Use the current desktop kernel and boot route; this is userspace only.
`SSH=scripts/omarchy/ssh.sh`, `R=/tmp/mesa102-desk`, under `caffeinate -i`.
Echo 1 to `/sys/module/a9_gpu_drm/parameters/lat123_log` before each step.

**A. LOAD + sub-area fixture (T1 style, next to the running desktop, as root).** It runs on its own DRM
file; labwc keeps the old pair. That makes it a multi-client boot: STATUS rules apply afterwards (no warm
kexec unless the kernel has the mcfold2 quiesce).

```
$SSH "cat > /tmp/partial-a9.tar.gz" < build/gpu/partial/package/partial-a9.tar.gz
$SSH 'cd /tmp && rm -rf partial-a9 && tar xzf partial-a9.tar.gz && cd partial-a9 && sha256sum -c SHA256SUMS && mkdir -p out &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/partial-a9/lib64/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json'
$SSH "cd /tmp/partial-a9 && sudo -n env LD_LIBRARY_PATH=$R/a9-mesa/lib64 VK_DRIVER_FILES=/tmp/partial-a9/icd.json PVR_G5P_EXPERIMENTAL=1 PVR_G5P_DEBUG=refuse \
  timeout -s KILL 400 ./bin/partial-vk --out /tmp/partial-a9/out > run.txt 2>&1; echo rc=\$?; grep -E '^case|pass=|^result=|G5P117_INIT' run.txt"
$SSH 'sudo -n dmesg | grep -E "RENDER-V3 (bind|terminal|refused|3d-refused)|RENDER-USER job=" | tail -20; sudo -n grep "LABWC-A9 FRAME" /run/desk/labwc.log | tail -1'
mkdir -p build/gpu/partial/device && $SSH 'cd /tmp/partial-a9 && tar cf - run.txt out' | tar -C build/gpu/partial/device -xf -
```

PASS requires all of these:
- `rc=0`, `result=PASS`, and `G5P117_INIT … result=ok`.
- Every `pass=` line is `result=EXACT` over the whole image. That covers chained LOADs at 64×64, 100×75 and
  1536×2048, a partial area with a scissor, DONT_CARE, CLEAR then LOAD, and the premultiplied blend over
  loaded pixels. The blend pass may report `near=` (±1 per channel) but never `bad`.
- One bind line, all jobs `complete=0`, and labwc's FRAME `n` still advancing.

Each pass line has `ms=`, the submit-to-fence time. Compare `clear-load` pass 0 (CLEAR) with pass 1
(LOAD + small draw) at 1536×2048: the difference is the cost of the full-screen load.

Reading a failure. Each pass is judged against the previous pass's readback. `--out` keeps only the
mismatching passes, as W×H×4 raw BGRA. The `bad` pixels are split three ways:
- **`leak` / `miss`** (the draw's colour outside its clip, or the clip left undrawn) is a **clip** fault:
  the pool8 scissor table or the render-area clamp. The table has never run on hardware before.
- **`other`** is a **LOAD/store** fault (garbage, zero, or stale contents).

The scissor-free LOAD verdicts are `a64` pass 1, `desk` pass 1 and `clear-load` pass 1. labwc (B) uses
no scissor, so a clip-only failure does not block B.
- `other` ≈ every pixel outside the drawn rectangle: the background does not load. First suspect is 3D
  **W+0x7fc**, which Apple sets for LOAD and our kernel cannot (§1). Next would be a kernel field for it;
  the ICD side has nothing left to change.
- `other` only where the previous pass wrote: a stale texture cache between jobs.
- `desk`/`a100` `other` only outside the render area: partial-area tiles are not stored back unchanged.
- `REFUSED` with VkResult -8: an ICD refusal, whose reason is in `run.txt` (`PVR_G5P_DEBUG=refuse`).
  -4 (device lost) or 2 (fence timeout after 30 s) is a GPU failure: stop, collect dmesg, then DFU per the
  STATUS rules.

**B. labwc without `rerender` (only after A passes).** Restart labwc on the same boot with the new pair and
one env change. The restart ends the live session, and the old context stays leaked until reboot.

```
$SSH 'sudo -n systemctl stop desk-window.service desk-read.service desk-labwc.service; sleep 1'
$SSH "cd /tmp/partial-a9 && cp --remove-destination lib64/libvulkan_g5p.so lib64/libvulkan_g5p117.so $R/a9-mesa/lib64/ && sha256sum $R/a9-mesa/lib64/libvulkan_g5p*.so"
# B1: drop rerender only (visibility culling stays off)
$SSH "sudo -n sed -i '/^WLR_SCENE_DEBUG_DAMAGE=/d' $R/a9-mesa/share/labwc119/gpu.env"
$SSH "sudo -n sh -s -- desk tramp" < /tmp/desk/device-launch.sh 2>&1 | cut -c1-240   # prints damage= (empty)
```

Then repeat tahang1's S3 measurement unchanged:
- focus foot, run `soak-type.py 900 4`, then a 60 s burst at 15 keys/s;
- the owner drags windows, taps the menu, and opens and closes foot;
- collect dmesg and run `scripts/gpu/tahang1/soak-stats.py dmesg.txt`.

PASS requires all of these:
- At least 1000 frames, every `complete=0`, and no `refuse` line in `/run/desk/labwc.log`.
- **Visual check:** foot's text updates in place, nothing is left behind where windows moved or the menu
  closed, waybar's clock ticks, and the fbprobe window box is unchanged. A stale rectangle means damage or
  LOAD is wrong. Note whether it follows a window or stays put.
- Report frame gap median/p90, peak fps and `fin` median/p90 against:
  - the task baseline: 0.254 s / 17 fps / 66 ms (tahang1a);
  - the current one: 0.113 s / 20 fps / 52 ms (tahang1c).

The gain is not known in advance. A damage frame still loads and stores the whole screen (about the cost of
the wallpaper draw it replaces). What it drops is every draw outside the damage, plus the CPU work in
wlroots, Zink and the ICD for them.

B2 (optional, the next restart): also drop `WLR_SCENE_DISABLE_VISIBILITY=1`. Its only reason was to keep the
rerender clear full-size; enabling culling removes the black background under the opaque wallpaper and the
occluded window parts. Run the same measurement.

**Config change after B passes.** Apply it to `configs/labwc-a9/gpu.env` and the staged labwc119 copy, and
update `/tmp/desk/device-launch.sh`'s "keep rerender" comment and STATUS's rule:

```diff
-# full redraw every frame (the background becomes one full-buffer clear: loadOp CLEAR, never LOAD),
-# no visibility culling (it would clip that clear), no direct scanout of client buffers
+# damage-only redraws (loadOp LOAD: docs/gpu/experiments/partial-redraw.md), no direct scanout of client buffers
 WLR_RENDERER=gles2
 WLR_DRM_DEVICES=@CARD@
 WLR_NO_HARDWARE_CURSORS=1
-WLR_SCENE_DEBUG_DAMAGE=rerender
-WLR_SCENE_DISABLE_VISIBILITY=1
 WLR_SCENE_DISABLE_DIRECT_SCANOUT=1
```

The second removed line is B2's. Keep it if only B1 passed.

## 5. Open

- 3D W+0x7fc (record+0x4dc bit 8) for LOAD jobs: its meaning is unknown, and the device decides whether it is
  needed. If it is, add a TYPED_V3 flag that the kernel copies to W+0x7fc.
- Clear quads for partial and late in-pass clears. With them, CLEAR could also run in a partial area.
- Skipping the tiles outside a partial area (a TERMINATE region / ISP render origin) instead of
  loading and storing them. This is untested on A9.

## 6. Files

- Mesa fork, branch `g5p-partial`: `g5p_record.c`, `g5p_job.h`, `g5p_gfx.h`, `g5p_submit.c`,
  `g5p_drm_render.c` and `g5p_gfx_test.c`.
- Workspace (not committed; the orchestrator commits):
  - `scripts/gpu/partial.sh`;
  - `tools/mesa-a9-partial/` (`check-partial.py`, `test_check_partial.py`, `partial-vk.c`,
    `partial.vert`, `partial.frag`);
  - `tools/gpu-userspace/check-round17.py` (`rgba-scissor` stride 400 → 448);
  - this file.
- `build/gpu/partial/` keeps only the new ICD, the trampolines, the clients, the package and the test logs.
  `partial.sh tramp` and `repro` need `partial.sh build` first (it recreates `bin/old`).
