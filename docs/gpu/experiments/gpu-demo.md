# GPU demo on the A9 desktop: a9-gpu-demo (low risk) and vkcube-a9 v2 (riskier)

Goal: a spinning, textured cube in a labwc window, rendered on our GPU every frame, for the owner to
screen-record. Both clients present over `wl_shm`, so labwc needs no change: keep `LABWC_A9_NO_LINUX_DMABUF=1`.
The device ICD has no Wayland WSI: it was built with `platforms=[]`, and `VK_KHR_wayland_surface` exists only as a
name string. There are no Mesa changes: the ICD pair stays on `g5p-upload-fix@5c2eb7a6d4b6`.

## Hardware result of vkcube-a9 v1: TA stall

Package `65bb5086`, deskfold boot `e46d5121`, ICD pair `a2f00cda`/`10232194`
(`logs/vkcube/{vkcube.log,dmesg-fault.txt}`). Job 1 (kernel seq 1977) went like this:

- the context bound normally (`bind slots=10/9 ret=0`);
- the firmware consumed the ring (`ring=185/185`, `ta_consumed_ms=1`);
- the TA stamp stayed at `0x7b8` and no TA event arrived. At 45 s the job ended with `ta=fault/45001`,
  `td=pending`, `unsat=0x6`, and no MMU fault;
- the client got `-EIO`, and the device-lost state took labwc down.

The stall was in the TA phase. The fragment program only runs in the 3D phase, so the cause is on the vertex side.

### How v1's job differed from the jobs that complete on hardware

Comparison set: labwc via Zink, about 6301 frames per soak. Its job evidence is `VARYING MULTIDRAW STRIDE UNIFORM
TEXTURE SHADER_HOST SHADER_PACK ATTRIB_FETCH VS_UNIFORM BLEND SAMPLER`, and its programs come from wlroots'
`common.vert` plus `tex_*`/`quad.frag`. Mesa102's triangle and a9-first-frames' round-16 scene are simpler still.
v1's job (from the ICD's own dump) had `VARYING STRIDE UNIFORM TEXTURE SHADER_HOST VARYINGS_N VS_UNIFORM SHADER_R2`.

| # | v1 | labwc (flown) | TA-phase? | Likely trigger |
|---|---|---|---|---|
| 1 | **2 vec4 varyings**: VS writes 12 dwords; `VDM_STATE4.vs_output_size=12`, `TA_OUTPUT_SEL.vtxsize=12`, `VARYING0.f32_linear=8`, a 3-item PDS varying program. Evidence `VARYINGS_N`: "ROGUE: more than one varying item", never observed on A9. | 1 varying, 8 dwords: Apple's `VARYING0=4 ⇔ output 8 ⇔ vtxsize 8` (dsx-r71 g0) | yes (VS output → VCE/TE) | **first suspect**. The vertex size and varying layout are what the TA hands the TE. A wrong vertex size, or a varying program that doesn't match the USC's writes, matches the "TE gets no primitives unless stream and state are right" history. |
| 2 | **12-dword per-binding DDMAD**: 3 × vec4 consecutive in one binding, stride 48, into r4..r15 | one narrow attribute (vec2, `ATTRIB_FETCH`). The captured per-binding form is 8 dwords (pos + vec4). | yes (PDS vertex fetch) | second suspect: the fetch shape was never flown or captured at 12 dwords |
| 3 | **largest general VS so far**: 518 B, 20 temps, 30-dword common store (MVP + MV as UBO 28 dwords), normal transform | wlroots VS: two mat3 uniforms, few temps | yes (USC VS) | possible: the temps/common-store sizing in the PDS was never exercised at this size |
| 4 | 36 vertices in one draw; 6–8 of the 12 triangles degenerate (CPU-culled to zero area) | many 6-vertex draws, never degenerate | yes (TE) | unlikely, but unflown |
| 5 | FS uses `normalize`/`max` (`SHADER_R2`, ISA round-2 forms, "HOST: never executed") | `fae17d1b`/`d4ecf7dd`/`3e18877e` only | no (3D phase) | not the TA stall, but it would be the next risk |
| 6 | UBO in the VS, texture + sampler in the FS, 512×512 linear target, one pipeline | all flown | – | no |

Without a kernel- or firmware-side trace, rows 1–3 can't be separated offline. Both new clients remove all three.

## a9-gpu-demo: the low-risk demo (default)

`tools/gpu-demo/a9-gpu-demo.c`. This is vkcube's cube (tables, LunarG texture, camera, 90°/s spin, light) rendered
through exactly the pipeline labwc runs on hardware every frame: GLES2 → Zink → the G5P ICD.

- **Shaders:** wlroots 0.20.2's `common.vert` and `tex_rgbx.frag`, byte for byte.
- **Calls:** wlroots' gles2 call pattern:
  - a client-side vec2 array;
  - `glDrawArrays(GL_TRIANGLES)` once per visible triangle, with the `proj`/`tex_proj` mat3 and `alpha` set per
    draw;
  - blending off (an opaque texture);
  - `glClear` (labwc-a9's full-buffer clear);
  - texture uploaded with `glTexImage2D(GL_BGRA_EXT)`, CLAMP_TO_EDGE, LINEAR filter.
- **Target:** labwc-a9's output-buffer allocation: a linear XRGB8888 GBM BO with `SCANOUT|RENDERING`, imported
  with wlroots' EGLImage → renderbuffer path.
- **Per frame:** the CPU transforms and back-face culls the cube, using vkcube-a9's verified test. Each visible
  triangle's UVs become its `tex_proj` (exact at the vertices; affine inside, so there is a slight seam along each
  face diagonal). The face lighting becomes `alpha` (rgb × alpha, blending off).
- **Readback:** after `glFinish`, `gbm_bo_map`, then the pixels are copied into a `wl_shm` buffer.
  `glReadPixels` was rejected: offline it added a second (blit) job per frame, a refused Zink pipeline, and 1 MiB
  of allocation per frame.
- **Options:** `--texture gradient` swaps the LunarG image for a red/green/blue gradient (a "colour-interpolated"
  look) with the same programs.

**Why it is expected to complete** (gated offline by `scripts/gpu/gpu-demo.sh repro`, every frame):

- **Fragment program:** the trampoline's receipts show exactly one, `fae17d1b…` (len 78, opaque, ISP words
  `fa=003e0f00 ba=00100f00`). It is identical to a program labwc ran **12,620 times** in the tahang1c soak
  (`logs/tahang1c/labwc.log`).
- **The offline Mesa is the device's Mesa.** That FS hash comes from the offline stage run and equals a hash in the
  device's labwc log, so the device's Zink + ICD compile exactly like the offline stage, and the other offline
  program hashes carry over to the device.
- **Vertex program:** `33660f45…`, identical to wlroots' `common.vert` as the l113 probe (the real wlroots gles2
  renderer) traced it with the same Mesa (`build/gpu/mesa117/test/e2e/traced-observed-trace`). The trampoline
  doesn't hash vertex programs, so "flown" is an inference: wlroots always links `common.vert` with
  `tex_rgbx.frag`, and labwc ran that pairing 12,620 times.
- **Job evidence:** `ATTRIB_FETCH MULTIDRAW SAMPLER SHADER_HOST SHADER_PACK STRIDE TEXTURE UNIFORM VARYING
  VS_UNIFORM`. That is labwc's flown set minus `BLEND`, with no `VARYINGS_N` and no `SHADER_R2`.
- **Mesa:** on the device the run script gives the client labwc's own environment (same `LD_LIBRARY_PATH`, ICD
  manifest, GBM/drirc paths and `PVR_G5P_*`), so Zink and the ICD compile the same programs there.
- **What still differs from labwc (unflown, but no new state forms):**
  - arbitrary triangles in 3-vertex draws, instead of 6-vertex rects;
  - a 512×512 target and a 256×256 texture. `--size 1024` would be the largest target of any job except labwc's
    own;
  - readback by `gbm_bo_map`. CPU reads of GPU-written memory are proven on the device (uf-gl's device PPMs), but
    those went through `glReadPixels`.

## vkcube-a9 v2: riskier, opt-in

`tools/gpu-demo/vkcube-a9.c`: the Vulkan port with the three suspects removed:

- one vec4 varying (u, v, shade, 1): VS outputs 8, `varyings=1`;
- two vec4 attributes in one binding: one 8-dword DDMAD, so no `BINDINGS`. That is the captured fetch form, but it
  was captured with the native-shape VS and is paired here with a new general VS. Two narrow
  attributes would have needed two DDMADs (`BINDINGS`, ROGUE);
- a 326 B VS with 12 temps and an MVP-only UBO;
- the shade computed on the CPU, and only front triangles packed (no degenerates);
- a 64 B multiply-only FS, `opaque`, with no R2 forms.

Its job evidence is `SHADER_HOST STRIDE TEXTURE UNIFORM VARYING VS_UNIFORM`, inside labwc's set. Its programs,
though, are new: the VS and the FS `fdd647c7…` have never run on hardware. Fly it only after a9-gpu-demo has
passed, and only when a lost desktop is acceptable.

## Offline results (`scripts/gpu/gpu-demo.sh all`)

The fake node doesn't render, so pixels are checked only on the device.

- **build:**
  - vkcube-a9's shader gate: general path, exactly one varying, none of
    `varyings/select/math/discard/fragcoord/flatten/tex_computed`, FS `class=opaque`;
  - both binaries need GLIBC_2.38 at most, like the ICD. a9-gpu-demo links labwc's Mesa `libEGL.so.1`,
    `libGLESv2.so.2` and `libgbm.so.1`;
  - binaries and the tarball are byte-reproducible.
- **repro:** 60/60 frames of each, through the trampoline's manifest (`G5P117_INIT result=ok`), and vkcube-a9 also
  through the plain ICD. In every frame:
  - the CPU cull keeps +Y front and −Y back (spin swept at 20000°/s);
  - the evidence gate and the a9-gpu-demo program-identity gate above pass.
- **soak:** 2000/2000 frames of each through the trampoline, over a fake node rebuilt with a 4096-job table. (The
  stock fake node remembers 64 jobs, so job 65's status reads ENOENT; that is a test-tool limit.)
  - a9-gpu-demo: `post_seal_allocs=18`, all in warm-up (the same count at 300 frames), allocs = frees,
    `peak_used_bytes=11812864`, binds 14;
  - vkcube-a9: `post_seal_allocs=0`, binds 12;
  - neither grows, so both use a lazy arena: 64 MiB committed.
- **wltest:** both clients ran in `tools/gpu-demo/wl-test-compositor.c` (headless) with configure acked, 60 commits
  of 512×512 `wl_shm` buffers, frame callbacks and the title.

## Package

`build/gpu/gpu-demo/package/gpu-demo-a9.tar.gz`, sha256
`44cee87f46c55d6a3e0c58f70c9dcebe982e1c77676109dd81badfb306eb81bf`:

| File | sha256 |
|---|---|
| `gpu-demo-a9/bin/a9-gpu-demo` | `4d31f509be1cd345c6a0a10803dfafa2027daf4cce88cecd037b8794e402cff8` |
| `gpu-demo-a9/bin/vkcube-a9` (v2) | `e35d7d638329036d1422f15599b53f7cc6cce9757bc40f5b067290653a1461e7` |
| `gpu-demo-a9/run-gpu-demo.sh` | `f6cb2a0e099b2e635b28c9526463ab543414c1616959561fa0093598adc8518c` |

`run-gpu-demo.sh` finds labwc-a9 by `LABWC_A9_RENDER_NODE` in `/proc/PID/environ`. From it, it takes:

- `XDG_RUNTIME_DIR` and that directory's `wayland-N` socket;
- the render node;
- labwc's Mesa environment, unchanged (`LD_LIBRARY_PATH`, `VK_DRIVER_FILES`, `GBM_BACKENDS_PATH`,
  `DRIRC_CONFIGDIR`, `PVR_G5P_*`, `MESA_*`, `ZINK_*`), except labwc's `PVR_G5P_ARENA_*`. The arena is always
  `PVR_G5P_ARENA_COMMIT_CAP=192M PVR_G5P_ARENA_POLICY=lazy` (64 MiB committed); `GPU_DEMO_ARENA_POLICY=eager`
  overrides the policy. Neither client grows after warm-up, so an eager 192 MiB arena would only cost RAM. The
  trampoline passes only
  `PVR_G5P_ARENA_{SIZE,COMMIT_CAP,POLICY,HEADROOM}`; `PVR_G5P_ARENA_MB` reads as unset inside the ICD.

It refuses unless the ICD is `a2f00cda` and the trampoline is `10232194`.

## Device run (orchestrator)

```sh
SSH=scripts/omarchy/ssh.sh
$SSH 'rm -rf /tmp/gpu-demo-a9 && tar -C /tmp -xzf -' < build/gpu/gpu-demo/package/gpu-demo-a9.tar.gz
$SSH 'cd /tmp/gpu-demo-a9 && sha256sum -c SHA256SUMS && sudo -n ./run-gpu-demo.sh start'
# ~15 s later
$SSH 'grep -E "^check|fps|GL_RENDERER|error|refus" /tmp/gpu-demo.log | head; sudo -n /tmp/gpu-demo-a9/run-gpu-demo.sh status'
$SSH 'cat /tmp/gpu-demo-frame30.ppm' > build/gpu/gpu-demo/device-frame30.ppm
# after the recording
$SSH 'sudo -n /tmp/gpu-demo-a9/run-gpu-demo.sh stop'
```

Riskier, only after a9-gpu-demo has passed (a new context): `sudo -n ./run-gpu-demo.sh start-vkcube`.

Client arguments go after `start`: `--speed 45`, `--texture gradient` or `--size 1024`. Passing any argument
replaces the default `--check-at 30 --dump /tmp/gpu-demo-frame30.ppm`, so add those back when you want the check.

### Pass criteria

- `check frame=30 … corners_clear=1 … cpu_mismatch=N … result=PASS`. The corners are the clear colour
  `0x333333`, the centre is cube, and the cube covers 10–60 % of the image. Fewer than 3 % of the cube's pixels
  may disagree with the CPU's inside/outside test of the same triangles; this catches a flipped, shifted or
  missing draw.
- The fps lines keep coming, and dmesg shows `RENDER-USER … complete=0` for the new slot pair.
- labwc stays responsive.

### Reading a failure

- No `check` line and dmesg `ta=fault`: a TA stall. That would contradict the identity argument, so report it
  with the full dmesg.
- All pixels clear (`cube_px=0`): the GPU wrote nothing to the GBM buffer, or the map read it too early.
- A large `cpu_mismatch` with a plausible cube in the PPM: an orientation or transform error in the client, not
  the GPU.

## What the owner should see

- A 512×512 window titled `a9-gpu-demo (A9 GPU) N.N fps` (vkcube-a9: `vkcube (A9 GPU) …`) with a labwc title bar.
- A dark grey background, with the LunarG-textured cube turning about its vertical axis at 90°/s and its faces
  brightening and dimming as they turn. In a9-gpu-demo a faint texture seam may show along face diagonals.
- About 8–15 fps, up to ~18. Each commit makes labwc redraw the full 1536×2048 panel (~52 ms of GPU time), and
  the demo's small job interleaves with labwc's on the one channel.
- `--speed 45` looks smoother. If the panel runs at scale 2, `--size 1024` gives a sharp window at a 4 MiB copy
  per frame.

## Risks

- **Until multiclient2, a stuck job of any client fails the whole device.** a9-gpu-demo reuses labwc's programs and
  state forms, but not its exact geometry, sizes or draw count. The residual risk is a data-dependent TA problem,
  which labwc's thousands of frames make unlikely.
- Every start binds a new context until reboot. Start a demo a few times per boot at most.
- vkcube-a9 v2 runs new programs. If it stalls too, the remaining suspects are the VS's UBO-sourced MVP or the
  per-binding 8-dword DDMAD with a general VS. That would point the next step at RE of Apple's vertex-output
  layout, not at the client.

## Reproduce

`scripts/gpu/gpu-demo.sh all` (fetch, build, repro, soak, wltest, package). It needs the `linux-a9-g5p-r5`
container, the uploadfix stage (`scripts/gpu/uploadfix.sh stage`), the mesa117 stage and the labwc108 Mesa
source. Pins: Vulkan-Tools v1.4.341 `48a4bcbd`, wayland-protocols 1.45 `0091197f`, wlroots 0.20.2 shaders
(verbatim).
