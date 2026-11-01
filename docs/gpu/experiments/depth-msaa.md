# Depth/stencil attachments (built) and 4× MSAA (decoded, refused): offline, 2026-11-01

GLES 3.0 through Zink needs depth/stencil buffers that survive between passes, and 4× MSAA. This lane makes the
A9 Vulkan driver load and store depth and stencil the way Apple's driver does. **No kernel change is needed for
that.** MSAA needs words that only the kernel writes, so it stays refused, and §6 lists exactly what the kernel lane
has to add. Everything here is offline. Nothing has run on hardware.

## 1. Apple's words (capture 20261101T175626Z, `gles3`/`gles3b` against `g3-base`)

Each scene's `submit-<scene>-000-kernel.bin` holds one 0x820-byte render record per pass. Diffed against the
one-target base (noise taken from `gles3-repeat`), a depth pass changes **only** the six ZLS words and the
background values. Those are exactly the words the kernel's V2 extension carries (`docs/gpu/render-uapi.md`,
`zls[0..5]`, `bgobj_*`):

| Record | 3D W → CR | depth-store | depth-load pass 2 | D16 pass 1 / 2 | stencil pass 1 / 2 |
|---|---|---|---|---|---|
| +0x2f0 `zls[0]` | W80 → f48 ISP_ZLSCTL | `000a2004` | `000aa006` | `050a2004` / `050aa006` | `000e2004` / `00026002` |
| +0x310 `zls[1]` | W88 → f50 ZLOAD_BASE | depth VA | depth VA | depth VA | depth VA / **0** |
| +0x338 `zls[2]` | W90 → f58 ZSTORE_BASE | depth VA | depth VA | depth VA | depth VA |
| +0x360/+0x388 `zls[3,4]` | W98/Wa0 → f60/f68 STENCIL_LOAD/STORE | 0 | 0 | 0 | stencil VA \| 1 (depth + 0x4000) |
| +0x308 `zls[5]` | W138 → fd0 ISP_ZLS_PIXELS | `1f803f` | `1f803f` | `1f803f` | `1f803f` |
| +0x4d0 | W3a4 → f80 ISP_BGOBJDEPTH | `3f800000` | `3f800000` | **`0000ffff`** | `3f800000` |

The ZLSCTL fields decode cleanly against Rogue `cr.xml`:
- `0x22000` is load- and store-twiddled.
- D16 adds load/store format `16BITINT` (`0x5000000`); D32F uses format 0.
- `zstoreen` + `forcezstore` = `0x80004`, `zloaden` + `forcezload` = `0x8002`.
- Stencil uses `sstoreen` `0x40000` and `sloaden` `0x4000`, with its own plane and enable bit.
- The extents are always 0. ZLS_PIXELS = (w-1) | (h-1) << 15 sizes the surface.

Apple's userspace builds these words in `FramebufferGen2` (AGXMetalA9 `0x207d12cdc`, decompile in
`build/gpu-userspace/cs/round12/decomp/`):
- `0x22000`, with `| 0x5000000` for D16;
- ZLS_PIXELS 32-aligned when there is MSAA, a stencil plane, or a padded small texture;
- the bases as above.

Per-draw ISP words, from both VDM streams (`tools/mesa-a9-depth/check-depth.py`):
- Depth only: ISPCTL `03c00000` (`bpres` 0), front A = back A (`dfbztestenable`, `dcmpmode`, write).
- Stencil: `03cc0000` (`two_sided` + `bpres`), `sref` in A, `sop3=REPLACE`/`scmpmode` in B, masks 0xff, also with
  all ops KEEP.
- Depth bias: ISPCTL `dbenable`, `dbindex 0`, and table entry `{0.01, 2.0, 0.02}`: `setDepthBias` verbatim (D32F).
- Rogue's `pvr_setup_isp_faces_and_control` gives the same fields.

Record words that no UAPI field reaches. Both are known gaps:
- **+0x4dd → 3D W+0x7fc = 1 on every LOAD pass.** Colour LOAD already works on hardware without it (partial lane), so
  it is not required. It is the first suspect if depth reload fails.
- **+0x4e5 → ISP_CTL bit 20 `dbias_is_int` for D16.** Without it, D16 depth bias uses Rogue's float-equation
  scaling (pvr's non-ERN42307 path).

Discard and fragment-depth draws use ISPA passtype 4 and 5 (punch-through, depth feedback). Both need shader
feedback instructions, so they stay refused under a depth/stencil test.

## 2. Kernel: no change for depth/stencil

`a9/gpu-multiclient2@995d684abd` (and `mrt1`) treats a TYPED_V3 pass with a non-zero `ext` as the V2 dialect:
- validation: `g5p_ru_pass2_check`, flagged ZLS words inside RW resources;
- fill: `g5p_ru_fill_3d_v2` writes `zls[]` to W80/88/90/98/a0/138 (+ copies), plus `bgobj_*` and the tables;
- the module parameter `render_user_dialect2` gates only the old profile 3, not V3.

The ICD names the pools as the scissor/depth-bias tables at 65536 entries each. That restates V1 byte for byte, and
the kernel's own test proves it. So the rest of the 3D is the proven V1 pass. Render targets stay
`samples = 1` (`ensure_rt`), which is one reason MSAA needs the kernel (§6).

## 3. What was built

Mesa fork branch **`g5p-depth`** @ **`ed5f13dabed`** (worktree `build/third_party/mesa-a9-depth-src`), 3 commits on
`g5p-mrt@7f4508599d4`, each with `Generated-by: LLM`:

1. `1f07233bc57` adds depth/stencil attachments with load and store.
   - **Formats:** D16, D32F, S8, D32F_S8, attachment-only and optimal. Zink emulates D24S8/X8_D24 with D32F(_S8).
     There is no env gate any more: the formats follow the kernel (TYPED_V3, or the mock).
   - **Planes:** twiddled, page-aligned, each sized for a power-of-two square of 32-pixel tiles (an upper bound for
     the unknown twiddle order; §5 measures the real footprint). The stencil plane comes after the depth plane.
   - **ZLS words** as in §1. The typed transport fills `struct g5p_pass_ext` (V2 dialect: output width in ext, V3
     field 0, pools zeroed).
   - **ISP words** in Apple's form: pvr's face logic; back = front unless the faces differ; `bpres` and `two_sided`
     with stencil; the stencil write mask is kept.
   - **Depth bias:** pool7 table entries and `dbenable`.
   - In-pass depth/stencil clears before any draw become the background clear.
2. `71945c1d0ad` adds gfx-test scenes (Apple's 5 scenes in Vulkan form, plus S8 alone and an in-pass clear) and the
   rejections.
3. `ed5f13dabed` refuses depth/stencil beside 2 or 4 colour targets (no Apple capture of the combination).

Workspace files (not committed; the orchestrator commits):
- `scripts/gpu/depth.sh`;
- `tools/mesa-a9-depth/` (`check-depth.py`, `check-depth-device.py`, `depth-vk.c`, `shaders/depth-discard.frag`);
- `patches/mesa-a9/test/a9-fake-node.c`: `A9_FAKE_DEPTH=1` applies the kernel's V2-ext rules; unset, the node
  behaves as before;
- `tools/mesa-a9-production117/g5p117-core.c`: opt-in `G5P117_ACCEPT_DEPTH` admits ISPCTL `03c0/03c2/03cc…`. The
  default build is unchanged (control `081885cc`).

## 4. Offline results (`scripts/gpu/depth.sh all`)

- **Controls:**
  - the `g5p-mrt` tree rebuilds mrt's ICD `0db6fa5f`;
  - the trampoline recipe gives the default `081885cc`, and mrt's pin gives `98d03359`.
- **Regression:**
  - 34/34 round17-19 and MRT shaders compile to identical bytes;
  - all 481 files of every pre-existing gfx-test scene record identically;
  - the log differs in one named line, because a stencil test without a stencil attachment now records as disabled
    instead of being refused.
- **`check-depth.py`: 169 checks, 0 failures** against Apple, on all 8 passes of depth-store, depth-load,
  depth16-load, stencil and depth-bias:
  - ZLSCTL and ZLS_PIXELS are equal;
  - the bases match in presence, enable bits and stencil-plane offset (+0x4000);
  - BGOBJDEPTH/VALS are equal;
  - the first draw's five ISP words are equal;
  - the bias entry is byte-exact, with `dbenable` on the biased draw only.
  - The self-test catches a flipped ZLSCTL bit, a dropped stencil enable and a moved stencil plane.
- **Rejections, by name:** 4-sample pipeline and image, depth bounds, size mismatch, separate depth/stencil views,
  depth resolve, depth clear after a draw, depth with two colour targets, discard under a depth test, D24S8, sampled
  depth.
- **Fake node** (`depth-vk --offline`, `A9_FAKE_V3=1 A9_FAKE_DEPTH=1`):
  - all 18 jobs of 8 cases are accepted, directly and through the trampoline;
  - 15 V2-ext passes pass the kernel's rules, with 0 refused;
  - the vkcube-shaped job sends `05022000` (on-chip D16, no load/store);
  - without TYPED_V3, no depth format is advertised and the colour control still runs.

The offline checks prove the words equal Apple's. Only the device can say whether the ZLS stores and reloads.

## 5. Artifacts and device plan (orchestrator): **warm-safe**

| File | sha256 |
|---|---|
| `build/gpu/depth/package/depth-a9.tar.gz` (reproducible) | `439b3991ef401b50be973cfc94d05f0e0ee464e97bb67023c4e4e5ac26e6b9fa` |
| └ `lib64/libvulkan_g5p.so` (ICD, `g5p-depth@ed5f13dabed`) | `2532c2b9b16044091f0d960f607bedfcf001ce7eadb2f20cd8e78eca73fd684a` |
| └ `lib64/libvulkan_g5p117.so` (trampoline, VM-edit shift 576, `G5P117_ACCEPT_DEPTH`) | `bfe18c6bfd9efbf8bf102ad377181837e24493d4757152f8e51fe25f4e556662` |
| └ `bin/depth-vk` | `a4b55944af99cd60bfaae36a2ab7904679db4d6658efda286dbc4e3b8eea4558` |

**Base:** the running desktop on `multiclient2-995d684abd` (or deskfold/mrt1). There is no kernel change, no hop,
and labwc keeps its own ICD pair. The fixture runs as an extra client beside labwc.

**Worst case:**
- A ZLS address fault or a 3D timeout on a client job. multiclient2 loses only that context. A fault that wedges the
  device stops labwc, and the warm hop back recovers it (deskfold-era quiesce, STATUS).
- No firmware power words, no new register access.
- Every run's first job is the proven colour pass (`one`), so every depth job is a later job of its context.
- ZLS writes outside the plane would land in this client's own VM. depth-vk keeps 16 KiB guards around each plane.

Setup (`SSH=scripts/omarchy/ssh.sh`, under `caffeinate -i`, `R=/tmp/mesa102-desk` as boot-desk.sh stages it):
```
$SSH "cat > /tmp/depth-a9.tar.gz" < build/gpu/depth/package/depth-a9.tar.gz
$SSH 'cd /tmp && rm -rf depth-a9 && tar xzf depth-a9.tar.gz && cd depth-a9 && sha256sum -c SHA256SUMS && mkdir -p out &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/depth-a9/lib64/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json'
R=/tmp/mesa102-desk
dv() { $SSH "cd /tmp/depth-a9 && sudo -n env LD_LIBRARY_PATH=$R/a9-mesa/lib64 VK_DRIVER_FILES=/tmp/depth-a9/icd.json PVR_G5P_EXPERIMENTAL=1 PVR_G5P_DEBUG=refuse \
  timeout -s KILL 300 ./bin/depth-vk --case $1 --out /tmp/depth-a9/out > $2.txt 2>&1; echo $2 rc=\$?; grep -E 'formats|job=|^result=|G5P117_INIT' $2.txt"; }
mark() { $SSH "sudo -n dmesg | grep -E 'RENDER-V3 (bind|unbind|close|lost|drained|drain dead|terminal|refused|3d-refused)|G5P VM release' | tail -${1:-6}; sudo -n grep 'LABWC-A9 FRAME' /run/desk/labwc.log | tail -1"; }
```

Each step must pass before the next. Every PASS means `rc=0`, every job `result=EXACT bad=0 guard=0`, no
`lost`/`terminal`, and labwc FRAME advancing.

| Step | Run | What it shows |
|---|---|---|
| D0 | `dv one d0; mark` | Control: `depth formats=4/4 D24S8=0`, `G5P117_INIT … result=ok`, 3 jobs EXACT. Formats 0/4 means the wrong ICD or a kernel without TYPED_V3: stop. |
| D1 | `dv one,depth,cube16 d1; mark` | Depth test and write in one pass (D32F stored; vkcube's D16 on-chip shape). Red inside, blue outside. |
| D2 | `dv one,reload,reload16 d2; mark` | **Depth reload:** pass 2 reads pass 1's depth (`aa006`, `50aa006`), pass 3 reads pass 2's. |
| D3 | `dv one,stencil d3; mark` | **Stencil:** written with Replace, reloaded alone (`26002`-style LOAD), Equal then NotEqual. |
| D4 | `dv one,bias d4; mark` | **Depth bias:** a -64 constant bias wins a Less tie (green). Red means the table/`dbenable` path does nothing. |
| D5 | `dv one,size d5; mark` | 100×75: unaligned ZLS_PIXELS `250063`. Each line reports `footprint=` (the real twiddled extent). |
| D6 | real vkcube with its D16 attachment | **Not in this package:** it needs WSI (`g5p-wsi`) stacked with `g5p-depth`. Its depth shape is D1's `cube16`. Run it after integration, as the vkcube-a9 client run. |

Host verdict:
```
mkdir -p build/gpu/depth/device && $SSH 'cd /tmp/depth-a9 && tar cf - d*.txt out' | tar -C build/gpu/depth/device -xf -
python3 -I tools/mesa-a9-depth/check-depth-device.py build/gpu/depth/device   # every job EXACT, footprint <= size, readbacks = model
```
Record the D5 footprints. If they are below the allocation, the ICD can later shrink its planes to the measured
rule.

Reading a failure:
- **`refused … (-8)` before submit:** the reason is in `d*.txt` (`PVR_G5P_DEBUG=refuse`). `RENDER-V3 refused` in
  dmesg is a kernel V2-ext rule.
- **D1 passes, D2 outside the rect green or black instead of blue:** the reload did not happen. The first suspect is
  W+0x7fc (§1), which needs a kernel field. Inside red but outside wrong means pass 2's depth store.
- **D3 Equal draws nothing:** the stencil plane or the `sloaden` path.
- **`guard>0`:** the ZLS wrote outside its plane. Stop the lane and keep `out/`.
- **VkResult -4 or 2:** expect `RENDER-V3 lost … drained` (only this context). Collect dmesg and stop.
- **`terminal kind=fault` or `drain dead`:** labwc stops. Collect dmesg and pstore, then warm-hop back to the base.

## 6. Still refused, and what each needs

- **4× MSAA (colour and depth), resolve, sample shading/mask, alpha-to-coverage.** Apple's msaa4-resolve, -store,
  a2c and samplemask records differ from the base:
  - +0x698 sample count 1 → 4;
  - +0x69c..+0x6b8 sample positions (8,8) → (6,2), (14,6), (2,10), (10,14) in 1/16 px;
  - +0x4d8 `0xf → 0x7` → 3D W3c0 ISP_CTL low bits;
  - resolve only: +0x640 = 1 → ISP_CTL bit 17;
  - the RT parameter block is unchanged.

  The resolve keeps the one-sample EOT program and USC and sets one PBE bit (dword 5 `0x400`, q2 bit 42). A
  multisample store changes the PBE key (`…0a → …0c`) and the EOT USC.

  The kernel builds the AA mode (Wd0 → f30), the ISP/TA sample-position words (3D W58 → fa8 and the TA
  MULTISAMPLECTL pair, now `0x88` = 1×), ISP_CTL and the partition words (W108..130, which depend on samples).
  `CREATE_RENDER_TARGET` refuses `samples != 1`.

  Next (kernel lane): RE the kext's RT importer (`6c7b0d0`, "shared-missing" in round16) for +0x698..0x6b8, and add a
  typed `samples` field. Then the ICD side is small: sample counts, PBE q2 bit 42 for resolve, the 4× PBE key.
- **Discard / gl_FragDepth under a depth or stencil test:** needs Apple's passtype 4/5 and the shader feedback.
- **Depth-only passes (no colour attachment), shadow-map sampling (sampled depth), depth copies/blits:** the recorder
  needs a colour target. Sampling needs the twiddled depth texture state (the `g5p-gles3-tex` lane, gles3b
  `samp-shadow`).
- **D16 depth bias** is approximate (no `dbias_is_int`). The **W+0x7fc** LOAD flag is not sent. Both need a kernel
  field each if the device shows they matter.
- **Partial renders (parameter-buffer overflow) lose depth** in passes without `zloaden` (store-only and on-chip
  passes, vkcube's shape). The region-header ZLOAD bit a firmware partial render sets has nothing to load from. This
  is the same pre-existing gap as the colour PR restore (`mrt.md` §1), and only an overflow reaches it, so a D1 pass
  does not prove PRs are fine.
- **Depth/stencil beside several colour targets:** no capture of the combination.
- **Separate depth and stencil views, a depth image of another size than the colour target, depth bounds.**
