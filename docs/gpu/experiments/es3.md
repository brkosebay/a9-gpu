# es3: a real OpenGL ES 3.0 context through Zink (offline, 2026-11-02)

The integration pair (`g5p-integ@673abf62de0`, integ.md) gets OpenGL ES 2.0: Mesa's `compute_version_es2()` failed
on **G10** (`D32_SFLOAT_S8_UINT` not sampled) and, on the desktop kernel, **G14** (`MaxColorAttachments = 1`). This
lane closes both, the way the hardware and Apple do it, and lifts Zink's varying cap without faking a driver ID.

- **Mesa** fork branch `g5p-es3` (worktree `build/third_party/mesa-a9-es3-src`), two commits on `g5p-integ@673abf62de0`,
  each with `Generated-by: LLM`:
  - `4d306af4ed2` g5p: sample depth/stencil ZLS planes in place (G10, §2);
  - `2abe69e60bd` zink: 15 varyings on Apple A9 (§4).
- **Kernel** branch `a9/gpu-desk-next` @ **`cdbd3717af`** (worktree `/src/wt/gpu-desk-next`): `a9/gpu-faultiso@abd31016e2`
  plus the MRT commit of `a9/gpu-mrt1` (§3). The next desktop base.
- Scripts: `scripts/gpu/es3.sh` (the Mesa side: integ.sh's gates on this branch, the smoke, the oracles),
  `scripts/gpu/desk-next-build.sh` (the kernel), `scripts/gpu/es3-device.sh` (device plan, §6). Oracles and the smoke
  client: `tools/mesa-a9-es3/` (`check-zls-sampled.py`, `check-es3.py`, `es3-smoke.c`).
- Nothing goes upstream (`src/imagination` and Asahi decline LLM-written code); private fork only.

## 1. Result

Offline over the typed-V3 fake node (`es3.sh gles` + `offline`, the desktop session's environment, Zink by
`MESA_LOADER_DRIVER_OVERRIDE`, `A9_FAKE_DEPTH=1`):

| Kernel caps | ES context Mesa offers | `compute_version_es2()` terms | Limits |
|---|---|---|---|
| **desk-next** (= mrt1's: TYPED_V3 + cap bit 29; `A9_FAKE_MRT=1`) | **`OpenGL ES 3.0 Mesa 27.0.0-devel`**, `OpenGL ES GLSL ES 3.00` (3.1/3.2 refused) | **25/25 yes**, G1–G14 all yes (G10 yes, G14 yes (4)) | `MAX_VARYING_VECTORS 15`, `MAX_DRAW_BUFFERS 4`, `MAX_COLOR_ATTACHMENTS 4`, `MAX_VARYING_FLOATS 60` |
| desktop (multiclient2/faultiso: no cap 29) | OpenGL ES 2.0 | 24/25: only **G14 NO (1)**; G10 now yes | `MAX_VARYING_VECTORS 15`, `MAX_DRAW_BUFFERS 1` |

So the ES 3.0 context needs the desk-next kernel (G14); G10 and the varyings no longer depend on the kernel.
`es3-smoke --offline` on desk-next caps, raw ICD and through the Mesa117 trampoline alike: ES 3.0 context PASS; MRT,
XFB and the sampled D24S8 texture run with no GL error, every FBO complete, **5 jobs submitted, 0 refused** by the
fake kernel, **0 trampoline refusals**; the one ICD refusal line is Zink probing a 1D D32F (format query, harmless).
Values are the device's (the fake node executes nothing).

## 2. G10: sampling a ZLS plane in place

Mesa's `ARB_depth_buffer_float` needs `Z32_FLOAT` and `Z32_FLOAT_S8X24_UINT` with DEPTH_STENCIL | SAMPLER_VIEW
(`st_extensions.c:850-852, 1204-1206`). Zink maps Z32_FLOAT_S8X24 (and GL's DEPTH24_STENCIL8) to
`VK_FORMAT_D32_SFLOAT_S8_UINT`. Advertising SAMPLED_IMAGE on it is only honest if an image that is an attachment *and* a
texture really samples what the ZLS stored. Before this lane that combination was refused by name (integ fix 07:
"never one image of both").

### 2.1 Evidence (`tools/mesa-a9-es3/check-zls-sampled.py`: PASS, 0 failures)

Apple's `g3b-samp-shadow` (capture 20261101T175626Z, gles3b) does exactly this: pass 1 draws a z=.5 triangle into a
64x64 Depth32Float (RenderTarget|ShaderRead, private) over a 1.0 clear; pass 2 samples it with `sample_compare`
LessEqual .6. From the captured words:

| Fact | Value |
|---|---|
| pass 1 ZLSCTL / ZLS_PIXELS | `0xa2004` (load/store twiddled, F32Z, zstoreen) / `0x1f803f` (64x64) |
| pass 1 ZSTORE base | `0x100040000` |
| pass 2 D32F image state | q0 `0x00007e01fc00442a` (twiddled, layout 2, code 0x221, RRR1, 64x64), q1 `0x80020000` (VA `0x100040000`, one level) |
| **texture VA == ZSTORE base** | the texture unit reads the plane the ZLS wrote, at offset 0, no copy in between |
| pass 2 colour | the stored triangle texel for texel: 1152 covered texels one colour, the other 2944 the other (y mirrored by `v_full`'s uv) |

The order inside the plane is the texture unit's twiddle (Morton with y in bit 0, `g5p_mem.c twiddle2d`, textypes.md
§1): our own device run of depth-msaa.md D5 (100x75 D32F, 3 jobs) measured a ZLS footprint of **61440 bytes**, which
is exactly Morton-y0 over whole 32x32 tiles in a 128x128 square (x-in-bit-0 would give 57344). So the ZLS store order and
the texture read order agree whenever both pad the surface to the same power-of-two square: proven for 64x64 by Apple
and for NPOT-square extents (100x75 pads to 128x128 both ways) on the store side by our device. The texture
unit's own NPOT padding (e.g. Hyprland's 1536x2048 read as 2048x2048) is the Rogue rule textypes.md lists as
device-pending (T6), not a capture. For sides that pad to
*different* powers of two (64x32, 1024x512) neither order is measured: sampling those planes stays refused by name.

### 2.2 What changed in the ICD (`4d306af4ed2`)

- `D32_SFLOAT_S8_UINT`: SAMPLED_IMAGE on optimal tiling (no transfer, no linear form). `sampledImageStencilSampleCounts`
  = 1.
- ZLS images may carry SAMPLED beside the attachment usage; D32F also the transfer bits Zink requests on every depth
  texture (`zink_resource.c get_image_usage_for_feats` adds TRANSFER_SRC/DST whenever the format has them). The plane
  layout is unchanged (the depth lane's power-of-two square of 32-pixel tiles), so an attachment-only image is
  allocated exactly as before; note that Zink now creates every D24S8 renderbuffer with usage `DS|SAMPLED` (0x24),
  which takes this same unchanged layout.
- Texture state of a ZLS view (`zls_texture_state`): the twiddled 2D form, image w/h, one level, q1 = plane VA >> 1.
  Depth aspect: D32F's key (code 0x221, RRR1: captured). Stencil aspect: the 8-bit stencil plane with R8_UINT's key
  (code 0x100, R001): **inferred** (Apple samples no stencil; GL ES 3.0 needs no stencil texturing).
- Refused by name: sampling a ZLS plane whose sides pad to different powers of two; a view with both aspects; a copy,
  blit or clear of a ZLS plane (`g5p_copy_execute`). The D32F linear sampled texture of g5p-textypes (no attachment
  usage, filled by copies) is unchanged.
- Evidence bit 37 `ZLS_SAMPLED` on every job that samples a ZLS plane.
- gfx-test: the depth group's format checks follow (D32F_S8 sampled, both usages in one image); a new group `es3` (runs
  only with all groups, i.e. in `integ-all`; no lane order includes it, so the 3-way gate is untouched) stores a
  D32F_S8 and a D32F plane and samples them (compare, plain, stencil), plus the two refusals.

### 2.3 Oracle on our dumps (`tools/mesa-a9-es3/check-es3.py`: PASS, 5/5 mutants caught)

On the `es3` gfx-test scenes (`build/gpu/es3/test/scenes/integ-all/es3-*`):

| Scene | Pass 1 (store) | Pass 2 texture state | vs Apple |
|---|---|---|---|
| `es3-zs-d32s8` (compare) | ZLSCTL `0xe2004` (Apple's stencil pass 1), ZLS_PIXELS `0x1f803f`, ZSTORE `0x1030f4000` | VA = ZSTORE, q0 `0x00007e01fc00442a`, q1 flags 0, q2 0; compare sampler `0x00c00400028e0000` | **equal** (state and sampler) |
| `es3-zs-d32s8-tex` (plain) | same plane | same state | equal |
| `es3-zs-d32s8-stencil` | STENCIL_LOAD `0x1030f8001` (+0x4000) | VA = stencil plane, q0 `0x00007e01fcb4200a` (R8_UINT 0x100, R001, twiddled 2D) | inferred (no Apple stencil sample) |
| `es3-zs-d32` / `-tex` (D32F with Zink's transfer bits) | ZSTORE `0x103120000` | VA = ZSTORE, q0 `0x00007e01fc00442a` | equal |

Every pass-2 job carries `ZLS_SAMPLED`. Mutants caught: cleared twiddle bit, texture VA one plane page off, RRR1 → R001,
stencil read from the depth plane, dropped evidence. The gfx-test also checks the two refusals (`PASS es3 rejections:
sampling a 64x32 ZLS plane, a copy into a ZLS plane`).

Through GL as well (`es3-smoke` traced over the fake node, `build/gpu/es3/gles/smoke-trace`): the GL_DEPTH24_STENCIL8
texture's pass 1 is ZLSCTL `0xe2004`, ZLS_PIXELS `0x1f803f`, ZSTORE `0x102428000`, and both sampling jobs
(`ZLS_SAMPLED`) carry one texture state naming that plane: q0 `0x00007e01fcb4442a` = Apple's form with Zink's ES depth
swizzle (R001: GL ES depth reads (D, 0, 0, 1)), q1 flags 0, q2 0.

## 3. G14: kernel `a9/gpu-desk-next`

`max_color_targets()` (`g5p_device.c:117-130`) reports 4 only with TYPED_V3 + cap bit 29
(`DRM_G5P_CAP_RENDER_PIXEL_OUTPUT_WIDTH`), which only `a9/gpu-mrt1` sets. The desktop needs fault isolation too, so:

| commit | from | what |
|---|---|---|
| `9a0faae2e4` | faultiso | detect a dead firmware from the first second of a phase |
| `5c4a4d6819` | faultiso | a client job that stops the firmware loses only its context |
| `abd31016e2` | faultiso | test hook: one client job faults on the GPU (default off) |
| **`cdbd3717af`** | mrt1 `96fa98d406`, cherry-picked | TYPED_V3 carries the pixel output width (cap bit 29) |

All on multiclient2 `995d684abd`. Cherry-picked, not merged, so the history stays linear (faultiso's and mrt1's verify
steps forbid merges). **No conflicts**: the two lanes share `a9-gpu-drm.c` (cap bits) and `drm-render.inc` (the V3
submit path); git merged both hunks cleanly. `verify` checks that the MRT commit came over whole (its
uapi/layout/validator diff is mrt1's byte for byte) and that faultiso changes no uapi, so the fake node's
`A9_FAKE_MRT=1` profile is exactly this kernel's caps.

Gates (`desk-next-build.sh check`, all PASS on `cdbd3717af`):

- faultiso: `test_fault_iso` 160 checks 0 failures; header mutants 5/5 caught; `test_latency101` 715 checks 0 failures;
  register audit (24 functions, 12 roots) PASS and its planted self-test PASS;
- mrt1: `test_render_mrt` 168 checks 0 failed; `test_render_user_v3` 1558 checks 0 failures;
- multiclient2: `test_render_close` 938, `_loss` 117, `_recover` 56, `_sched` 49, `_window` 66152 checks, all 0
  failures; `audit_mc2` 64 functions / 35 roots PASS; tahang1 gate (10767 checks, 11/11 mutants caught) PASS; mcfold2
  suite (`test_kexec_quiesce` 107/0, `test_render_bind` 177/0, `test_render_admit` 83/0, RAM-only audit 32 functions
  PASS, context audit PASS); checkpatch on all 15 commits from deskfold up: 0 errors (2-3 warnings each, the same as
  on faultiso and mrt1).

Built with `desk-next-build.sh build` (`build-kernel.sh --tree gpu-desk-next`, `KERNEL_JOBS=2`; faultiso's object tree
reused, so only the g5p driver recompiled). Config byte-identical to `build/kernel-gpucombined118/config`, not dirty.
Release `7.3.0-rc1-gcdbd3717af53`. Payload and entry exactly as faultiso-build.sh (same cmdline base: only
`linux_a9.entry` differs; tri79 initramfs; four DTBs; `prepare-entry.py --board apple,j71t`):

| artifact | sha256 |
|---|---|
| `build/boot/m1n1-linux-desk-next-cdbd3717af.bin` | `3e6279b20a8a7dcb34ddeedd04d7407bb0850715001ec7d15aac4d5e63c1a519` |
| entry `build/gpu/desk-next/desk-next-cdbd3717af-entry/Image.gz` | `0d41addc7da2f7a71149ace6b62b5b22b97d5578acde502384a0d3669527f0c0` |
| `…/cmdline` | `3808af42ccb8cb15552742d587e52055fde9c58343918d29c23f3114dfa11de3` |
| `…/dtb` | `8ca31fceecb9eea3eae50dd5c82bb3ab93dd262400093d82f1987cce964a8175` |
| `…/initramfs.gz` | `9e1ec64a121b240bb7c636c71e52e682940c7e7f99bcc297a2fb7c166a498804` |
| `…/SOURCE` | `9d5e11aff93372d25dbfe2134ca4dd0b00c11b8474e283e1f1280f51dce63521` |

## 4. Varyings: Zink's cap on A9 (`2abe69e60bd`)

Zink sets `max_varyings = MIN2(maxVertexOutputComponents / 4 / 2, 16)` = 8 (`zink_screen.c:1169`), halving for its
streamout reservation, and lifts it to 16 for Imagination's and Turnip's driver IDs when `maxVertexOutputComponents ==
64` and there is no geometry or tessellation (`:1180-1186`). That branch sets **only** `max_varyings`, and
`max_varyings` feeds only `consts.MaxVarying` (`st_extensions.c:411`); nothing else in Zink reads it. Reporting
Imagination's driver ID instead would also flip implicit sync, `needs_zs_shader_swizzle`, the dri3 modifier swap and
the `:3310` list (integ.md §3), so the patch keys the same condition on `vendorID == 0x106b && deviceID == 0xa9` (plus
the 64-component, no-GS/TS terms as conditions, not asserts).

**15, not 16.** Our fragment stage reads `maxFragmentInputComponents = 60` (`PCO_G5P_MAX_FS_VARYINGS` = 15). With 16, a
GLSL program with 16 varyings would link in Mesa and then fail at pipeline creation. The patch therefore caps at
`maxFragmentInputComponents / 4` = **15**, which is the ES 3.0 minimum (`MAX_VARYING_VECTORS >= 15`). GL ES 3.0 does not
gate the version on it (`compute_version_es2()` never reads it), but conformance needs 15.

## 5. Offline proof

    scripts/gpu/es3.sh configure        # once: meson setup of build/third_party/mesa-a9-es3-v1 (the WSI lane's options)
    scripts/gpu/es3.sh all              # build refs test tramp fixtures vpds stage smoke gles offline check package

`es3.sh` runs integ.sh's gates unchanged through `INTEG_SRC/INTEG_BRANCH/INTEG_BUILD/INTEG_OUT` (outputs
`build/gpu/es3`); integ.sh itself is not edited by this lane. Results on `g5p-es3@6531e682cd3`:

**Integration gates (`integ.sh test`): PASS.**
- g5p-gfx-test: 0 failures in all six integration orders (`-`, depth, gles3tex, gles3tex+textypes, gles3tex+varyings,
  all: 41/44/67/97/83/122 PASS lines).
- Shaders: 105 (45 identical, 49 = the changing lane, 11 composed), 0 unexplained; the 30 round17–19 shaders (labwc +
  Hyprland) as on g5p-integ.
- Scenes: 112 (16 identical, 93 lane, 1 composed, 2 composed-link), 0 unexplained, 0 integ-only: the es3 group runs
  only in `integ-all`, which no lane order compares.
- Unified-store rule: 699 draws, 699 ok.
- Oracles: round17 PASS (11 scenes), partial LOAD PASS (18 mutants), mrt PASS, depth PASS, gles3tex 24/24,
  textypes 39 checks PASS, varyings PASS, g5p-xfb-test 263 checks 0 failures, XFB resolve 10/10 `XFB_EXACT`,
  33 vertex stores all 4-byte.
- One base expectation changed on purpose: `scene_rejections` asserted "sampled depth refused" (a D32F image with
  attachment + sampled usage). It now expects the image to be created when the device has ZLS words, and refused
  without them.

**Trampoline (`tramp`):** control `081885cc` reproduced; pinned to the new ICD with `G5P117_ACCEPT_DEPTH` +
`G5P117_ACCEPT_DISCARD`, no other change (the ES 3.0 paths need none: its filters are on fragment programs, and the
smoke's shaders are branch-free).

**Fixtures (`fixtures`): PASS**, 20/20 (mrt, mrt-nocap, depth, gles3tex, textypes, varyings, partial, xfb, xfb-run3;
ICD and trampoline), depth without TYPED_V3 PASS, cf on the raw ICD PASS and through the trampoline refused before
the kernel as designed; 0 fake-node refusals, `G5P117_INIT result=ok`.

**vpds (`vpds`): PASS.** Apple's LAST rule on all 188 integration draws; the 21 jobs varyings' link does not touch are
byte-identical to the vpds tip (13 composed modes as on g5p-integ).

**GL (`gles`, `offline`):** §1. Full lines: `build/gpu/es3/gles/integ-gl-{mrt1,desktop}.txt`,
`es3-smoke-{desk-next,desktop}-{icd,tramp}.txt`, `eglinfo-*.txt`, `vk10-*.txt`.

**Oracles (`check`):** `check-zls-sampled.py` PASS (Apple's evidence, §2.1), `check-es3.py` PASS 5/5 (§2.3).

**Package** `build/gpu/es3/package/integ-a9.tar.gz` (integ.sh's reproducible tar, tree name `integ-a9`, ~8 MB):

| file | sha256 |
|---|---|
| `integ-a9.tar.gz` | **`aab6d7d2456c9d7b91371b8046b9717a885412a0dbed8ffbb19c2a72e1e7461d`** |
| `lib64/libvulkan_g5p.so` (ICD, `g5p-es3@6531e682cd3`) | `c05c22789a3601b4542ada663bdb48049809a02c3ae94195d8ddd827b03dad29` |
| `lib64/libvulkan_g5p117.so` (trampoline, pinned to that ICD, depth + discard) | `49a753e129521d5c16c7a0e6b1d63e10a002e0d0b030e050c725f09b53606190` |
| `lib64/libgallium-27.0.0-devel.so` (Zink with the varyings patch) | `93c373e51aadcfa16da7314f2192552513cef182eec10b5cf40f21df8bfab87f` |
| `bin/es3-smoke` | `c039343dfd8ee7f3ceaf84c6341afcdac9d1de4fdf8d11bd003c7f9eeda65814` |
| `bin/integ-gl` (unchanged) | `ab0861900cbb8c029d71ce3f74bb11326254197b23c7be4678c2819d518b4809` |

The rest of the tree (EGL/GLES/GBM of this build, the WSI clients, `client-gpu-integ.env`) is as integ.sh stages it;
`SHA256SUMS.contents` lists every file.

## 6. Device plan (orchestrator; warm-safe)

Kernel: desk-next (`build/boot/m1n1-linux-desk-next-cdbd3717af.bin`, entry `desk-next-cdbd3717af`), warm-hopped from
the device base, faultiso (`a9/gpu-faultiso@abd31016e2`), which **passed on hardware** (two injected client faults,
`GPU-RECOVER … ret=0` in 21 ms, labwc kept rendering). desk-next is that kernel plus one commit: mrt1's RAM-only width
words, flown on the mrt1 boot (`build/gpu/mrt/device-20261101T202412Z`: 2 and 4 targets exact). No firmware power words, no new
register access, no change to scheduling, binding or the kexec quiesce. The multiclient2 quiesce makes the warm kexec
safe after multi-client use and after a wedged client (STATUS "Rules"), and faultiso keeps a client's fault to its own
context; a wedge is recovered by a warm hop back to faultiso. The fault test hook stays off
(`render_user_test_fault_job` default 0).

    scripts/gpu/es3-device.sh            # = INTEG_PKG=build/gpu/es3/package INTEG_LOGS=build/gpu/es3 \
                                         #   scripts/gpu/integ-device.sh <desk-next payload> desk-next-cdbd3717af 0

It checks the payload/entry/package checksums first, then:

1. **Warm hop** with `ARENAFIX=integ DMABUF=1 DESKTOP=1` (`boot-desk.sh`'s integ case; `INTEG_PKG` points it at the es3
   package, which keeps integ.sh's tree name, so the client tree is `/tmp/integ-a9` and labwc gets the es3 ICD +
   trampoline). Pre-checks: `uname -r` = `7.3.0-rc1-gcdbd3717af53`, `ldd` with 0 missing, `G5P117_INIT … result=ok`.
2. **Probes** (extra GPU clients, surfaceless):
   - `integ-gl`: PASS = `ES-VERSION request 3.0: created` (3.1/3.2 refused), `GL_VERSION OpenGL ES 3.0 Mesa …`,
     `GL_SHADING_LANGUAGE_VERSION OpenGL ES GLSL ES 3.00`, every `GATE … yes` including **G10** and **G14 (4)**,
     `LIMIT GL_MAX_VARYING_VECTORS 15`, `GL_MAX_DRAW_BUFFERS 4`, the D24S8 FBO probe complete.
   - `eglinfo -B -p wayland`: the OpenGL ES profile version line reads `OpenGL ES 3.0`.
   - `es3-smoke` (one ES 3.0 context; branch-free shaders, so the trampoline admits them):
     - `mrt`: four RGBA8 targets from one draw, read back `4080c0ff ff0000ff 00ff00ff 0000ffff`;
     - `xfb`: one vec4 attribute per vertex captured with rasterizer discard, read back 0.5, 1.5, … 11.5 exactly;
     - `zs`: a 64x64 GL_DEPTH24_STENCIL8 texture written as the depth attachment (z=.5 over 1.0), then sampled in
       place: plain read `7f|80` inside / `ff` outside, compare LEQUAL .6 `00` inside / `ff` outside.
     PASS = `RESULT PASS 0 failure(s)`.
3. **W1–W4** + W1/W2 ×3 (`integ-device.sh`'s ladder: wsi-vk, egl-wl, vkcube-wsi, weston-simple-egl), each with its fb
   probe and `bad=0`, `client_refusals=0`.
4. The foot soak is off by default (`es3-device.sh 5` runs 5 cycles).

Outputs: `build/gpu/es3/device-<UTC>/` (probe text and fb crops only; fb dumps are pulled and deleted one by one, the
package unpacks to ~32 MB in the 757 MB `/tmp`).

**Stop rules:** any `complete=-`, timeout or fault stops the runner (one wedge per boot); then warm-hop back to the
previous desktop kernel. **What a failure means:**
- `ES-VERSION request 3.0: refused` with a `GATE … NO`: that gate's term on hardware (compare with §5's offline list);
  `G14 (1)` means the old kernel is running (check `uname -r`).
- `es3-smoke zs` plain/compare inverted (`00`/`ff` swapped on compare only): Apple's compare is texel-op-ref, not
  ref-op-texel (textypes T5's open question), not a sampling fault; plain values wrong but the shapes right: the ZLS
  plane is read at the wrong scale; values scattered: the ZLS and texture orders differ (stop and keep the logs: §2.1
  says they agree for 64x64).
- `mrt` with only t0 right: the kernel's width words (mrt1's own M0–M3 rungs). `xfb` all `-7`: the store variant did
  not run (rasterizer discard path).
- A `G5P refuse g5p_compile_graphics … no witnessed vertex control tuple` line: a vertex shape the compiler has no
  hardware-witnessed form for. The smoke's vertex stages only pass attributes through for this reason (its first
  draft, `uv = p.xy * 0.5 + 0.5`, was refused offline); real ES 3.0 apps will meet the same compiler coverage limit.
- `G5P refuse …` lines in a probe: the ICD's by-name refusals (`PVR_G5P_DEBUG=refuse` is in the client env).

## 7. Not done, and why

- **Sampling planes whose sides pad to different powers of two**: refused by name until a device run measures the ZLS
  order of a non-square surface (a D5-style footprint of 128x64 decides it in one job).
- **Copies of ZLS planes** (glTexImage2D with depth data, glReadPixels of depth): refused by name. The order is now known
  for square-pot surfaces, so CPU copies through `twiddle2d` are the obvious next step if a client needs them.
- **Stencil texturing** (ES 3.1 `DEPTH_STENCIL_TEXTURE_MODE`): the state is built (inferred key), not device-proven.
- **The control-flow trampoline question** (integ.md §2): unchanged; ES 3.0 shaders with branches still run only on
  the raw ICD.

## 8. Files

- Mesa `g5p-es3`: `src/imagination/vulkan/g5p/{g5p_mem.c,g5p_gfx.h,g5p_device.c,g5p_record.c,g5p_submit.c,
  g5p_transfer.c,g5p_gfx_test.c}`, `src/gallium/drivers/zink/zink_screen.c`.
- Kernel `a9/gpu-desk-next`: mrt1's five files (`include/uapi/drm/apple_g5p_drm.h`, `render-user-layout.h`,
  `render-user-validate.h`, `drm-render.inc`, `a9-gpu-drm.c`).
- Workspace (not committed; the orchestrator commits): `scripts/gpu/{es3.sh,es3-device.sh,desk-next-build.sh}`,
  `tools/mesa-a9-es3/{check-zls-sampled.py,check-es3.py,es3-smoke.c}`, this file; small env knobs with unchanged
  defaults in `scripts/gpu/integ-device.sh` (`INTEG_PKG`, `INTEG_LOGS`, the es3-smoke probe) and
  `scripts/omarchy/desk/boot-desk.sh` (`INTEG_PKG` in the integ case).
