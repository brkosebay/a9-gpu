# WSI: Wayland surfaces and swapchains for the G5P ICD

Goal: GPU Wayland clients (Vulkan directly, GLES through Zink) presenting to labwc through linux-dmabuf. That is
the multi-client T2 test (`multi-client-design.md`), blocked until now because no GPU client could open a window.

## Why the extensions were missing

- **The ICD never wired up Mesa's `wsi_common`.** No `wsi_device`, no wsi entrypoints in the dispatch tables, and
  `VK_KHR_surface` / `VK_KHR_swapchain` were absent from its extension tables.
- **The sealed Mesa build had `platforms=[]`.** `wsi_common_wayland`, EGL's Wayland platform and Zink's kopper
  window surfaces were never compiled; `VK_KHR_wayland_surface` existed only as a name string.
- **`wsi_common` sets `VK_IMAGE_CREATE_ALIAS_BIT` on every swapchain image, and `vkCreateImage` refused it.**
  `vkCreateSwapchainKHR` then failed with `OUT_OF_HOST_MEMORY`.

## What was built

- **Mesa `g5p-wsi@32b83afe10c`** (on `g5p-partial@1ae79db1d8a`), worktree `build/third_party/mesa-a9-wsi-src`.
  `g5p_wsi.c` follows honeykrisp's `hk_wsi.c`:
  - Instance extensions: `VK_KHR_surface`, `VK_KHR_get_surface_capabilities2`, `VK_EXT_headless_surface`,
    `VK_KHR_wayland_surface`.
  - Device extension: `VK_KHR_swapchain`, behind the PRIME gate (`prime_experiment=Y`, which the desk launcher
    sets). Swapchain images are linear dedicated BOs, presented as `DRM_FORMAT_MOD_LINEAR` dma-bufs.
  - `can_present_on_device`: any platform-bus DRM device.
  - `VK_IMAGE_CREATE_ALIAS_BIT` is accepted, because the image layout depends only on the create info (always
    linear).
- **Build:** an ordinary meson build with the sealed build's options plus `-Dplatforms=wayland`
  (`build/third_party/mesa-a9-wsi-v1`).
- **New ICD + trampoline pair:** ICD `ac0460f9…`, trampoline `3387d083…`. The trampoline is the mesa117 one pinned
  to this ICD: its VM-edit guards were measured once each, shifted +17888 bytes, are re-verified at load, and fail
  closed.
  - labwc's pair (`a2f00cda`/`10232194`) is untouched. Clients get the new pair only through their own
    `VK_DRIVER_FILES`.
- **Regression control:** `g5p-partial` built through the sealed recipe reproduces the partial ICD `5b1d04ea` byte
  for byte. `g5p-gfx-test` old vs new: 405 files / 34 jobs / 32 scenes byte-identical, logs identical, round17
  decoder clean.

**Clients in the package:**

| client | what | ICD job evidence (offline) |
|---|---|---|
| `wsi-vk` | Vulkan surface + FIFO swapchain, a clear that cycles colour, no client shaders | `CLEAR_ONLY STRIDE` |
| `egl-wl` | EGL Wayland window → Zink kopper (weston-simple-egl's config), the same clear | `CLEAR_ONLY STRIDE` |
| `vkcube-wsi` | vkcube on the swapchain (below) | `SHADER_HOST STRIDE TEXTURE UNIFORM VARYING VS_UNIFORM`: inside labwc's flown set |
| `weston-simple-egl` | Fedora 44 `weston-demo-15.0.1-2`, unmodified; GLES2 → Zink | `ATTRIB_FETCH BINDINGS SHADER_HOST STRIDE VARYING VS_UNIFORM` |
| `eglinfo` | Fedora 44 `egl-utils-9.0.0-11`, for the renderer check | no job |

**vkcube:**

- Vulkan-Tools' own `vkcube` can't run on this ICD, depth or not. Offline, its pipeline is refused (`vertex:
  load_vertex_id, ishl`: a UBO array indexed by `gl_VertexIndex`), so it fails closed with no GPU job. It would also
  need `dFdx`/`dFdy` and a D16 attachment.
- **There is no `vkcube-nodepth` build in the repo.** The no-depth vkcube is gpu-demo's `vkcube-a9` v2 (CPU
  back-face cull, vertex buffer, CPU lighting, which presents over wl_shm).
- `vkcube-wsi` (`tools/mesa-a9-wsi/vkcube-wsi.c`) is v2's scene, pipeline, texture and render job unchanged,
  rendering into the acquired swapchain image and presented with `vkQueuePresentKHR`. Its fragment SPIR-V is
  byte-identical to what gpu-demo's shader gate passed, and the gate runs again with this ICD's compiler. v2's
  programs had **never run on hardware** before W3.
  - Since the W3 fix, the vertex shader is v2's in 12 temps instead of 16 (`tools/mesa-a9-wsi/shaders/cube-a9.vert`;
    see "W3: no cube pixels").

**Other clients:**

- **`es2gears_wayland` can't run:** EGLUT insists on a depth config, and Zink offers none because the ICD refuses
  depth formats ("failed to choose a config"). This is also why boot 2's T2a attempt aborted (`client rc=134`).
- **`glmark2-es2-wayland`:** gets a no-depth config and reports `zink … Apple A9 G5P`, but only the `clear` scene
  runs. Its other scenes need more than 2 vertex inputs (`maxVertexInputAttributes = 2`).
- None of the four was installed on the device at boot 2.

## Offline results (`scripts/gpu/wsi.sh offline`)

- **Setup:** the typed-V3 fake node with PRIME and a 4096-job table, with `wl-stub` (a linux-dmabuf v4
  compositor). Every client runs both through the trampoline and directly.
- **All 24 gated runs PASS:**
  - every acquire, submit and present succeeded;
  - every submit was accepted (30–120 per run; the fake node refused 0 across 26 runs);
  - the stub saw only good buffers: 4 linear `XR24` dma-bufs, `bad=0`;
  - `G5P117_INIT result=ok icd_sha256=ac0460f9…` in all 12 trampoline runs.
- **The runs:**
  - `vulkaninfo` lists all the WSI extensions;
  - `wsi-vk`: headless, Wayland, and Wayland with a foreign main device;
  - `egl-wl`: clear and triangle; `eglinfo -B -p wayland` reports `zink Vulkan 1.0(Apple A9 G5P …)`;
  - `vkcube-wsi`: 60 frames, 4×512² images, 0 refusals;
  - `weston-simple-egl`: 100–120 frames in 8 s, ended by SIGINT, rc 0;
  - the `MESA_VK_WSI_DEBUG=sw` fallback for `wsi-vk`, `vkcube-wsi` and `egl-wl`: 30 `wl_shm` commits each, no
    dma-buf.
- **Informational:** es2gears (no config), glmark2 (`clear` only; the `build` scene fails to link).
- **Limits:** the fake node draws nothing and doesn't model TA stalls. An offline PASS proves the API, the WSI and
  the job encoding, not the hardware.

## Package

**Superseded** by `e510a05b…` (the W3 fix, next section). The first package, kept as
`build/gpu/wsi/package-98c51ebd/`, was `wsi-a9.tar.gz` sha256 **`98c51ebd96a68668155fd5a73630adb038017bd0f7ee5b2dddfd0840ce5a3505`**.
It is byte-reproducible (GNU tar, sorted, mtime 0; two builds gave the same sha). It unpacks to
`wsi-a9/a9-mesa/`, and per-file sha256s are in `a9-mesa/SHA256SUMS` (also `package/SHA256SUMS.contents`):

```
ac0460f96a7c4032093a70e3060fff322e2124a9f6d6fe7fde7828ab22999c16  lib64/libvulkan_g5p.so
3387d083ec6ec5aaf8a69d8173c34d76b68c8eeace23ad942cb938d39990fe17  lib64/libvulkan_g5p117.so
3b48da3174745384fff754f2724c90487bfa2acd0e56d8fb00d37ef8c836878d  bin/wsi-vk
a5fe5d7ee39a08c1050d6ca62d97918acb3c4761e6b2595ef1327eb739aaf869  bin/egl-wl
a6fd91d269d7de6666b167a02cd1ad56aee13b3f6d21266d5958d3cf65cd2e41  bin/vkcube-wsi
49f276b78f27fcefc3bc3627274b16dabfed48162cdba8fc1de77e1dfdb8d019  bin/weston-simple-egl
dd28b7c1fc894e17b320fe2a46f03531748f462ea159b953f5cc22e0af6a815e  bin/eglinfo
4a14e61645d54be829913fcc8e946cac013259c2b49a694964f416e910bdea29  client-gpu-wsi.env
```

The package also holds EGL/GLESv2/gallium/GBM built with Wayland, drirc, and `share/vulkan/icd.d/g5p_icd.json`
(→ the trampoline). libLLVM 22.1 and libxml2 come from labwc's tree, which `client-gpu-wsi.env` lists second.

## W3: no cube pixels. Cause (prime suspect), fix, rerun

**Device, 2026-11-01 21:04Z** (multiclient2, labwc with linux-dmabuf; `build/gpu/wsi/device-20261101T210449Z/`):

- W1 and W2: PASS.
- W3: 600 frames, `bad=0`, every job `complete=0`; job 3601 had `ta=complete/7 td=complete/5`. But
  `check frame=30 … cube_px=0 … result=FAIL`: the check image is entirely the clear colour `0x333333`. The draw
  produced no pixels, and nothing reported an error.

**There was no hardware reference for this scene:**

- The cube that passed on hardware (STATUS.md, 9–12 fps, frame 30 matching the CPU) is **a9-gpu-demo**: GLES2 →
  Zink, wlroots' `common.vert`/`tex_rgbx.frag`.
- vkcube-a9 **v2** never flew (v1 stalled the TA), as this doc's own vkcube section says. W3 was the first flight
  of v2's programs.

**The fake-node diff clears the ICD branch, the client and the target** (scratch: `build/gpu/wsi/diag/`, runner
`run.sh`, word differ `jobdiff.py`; `--speed 0`, so all runs draw the same scene):

| Pair | Result |
|---|---|
| vkcube-a9 on the demo ICD `a2f00cda` (g5p-upload-fix) vs on the WSI ICD `ac0460f9` | byte-identical jobs: every arena, the PBE words, `scissor_table count=0`, no `render_area` |
| vkcube-wsi's check job vs vkcube-a9's job (WSI ICD) | byte-identical |
| vkcube-wsi's swapchain job vs its check job | only the target: `color.va`/`pbe[1]` and one state word (`0x81000000` → `0x91002000`) |
| vkcube-wsi on the demo ICD | does not load (`vkCreateInstance` = -7: no WSI extensions) |

g5p-partial's pool8 scissor table and its render-area code don't affect this job. What W3 flew is exactly v2's job.

**Prime suspect: the vertex shader needs 16 temps.**

- `g5p-shaderc` on v2's `cube-a9.vert` (`ubuf.MVP * pos`) gives `temps 16`: 326 bytes, live up to r13. The DOUTU in
  W3's job (state `+0x2d8`) carries temps 16.
- gpu-demo.md's "a 326 B VS with 12 temps" is wrong. Its own shader-gate JSON
  (`build/gpu/gpu-demo/shader-gate/gate-vert.json`) says 16. **Orchestrator: please correct gpu-demo.md.**
- Every vertex shader that has drawn on hardware needs 8 or 12 (xfb.md). 16 is the only VS property of v2 outside
  that set. The one other 16-temp VS that flew (xfb's `hold`) also failed.
- **The tension, stated plainly:**
  - `hold` (16 temps) wedged the TA: `ta=fault/45001`.
  - W3 (16 temps) completed the TA in 7 ms with zero primitives.

  Same count, different failure. The mechanism is unknown. 16 temps is the prime suspect because it is the only
  vertex-side property outside every pixel-producing VS, not because the failure is understood.
- **Other unflown forms in v2 (next suspects, in order):**
  1. the per-binding 8-dword DDMAD with a *compiled* VS (captured only with Apple's native VS);
  2. the never-run FS `fdd647c7`. It runs in the 3D phase, so it is a weaker suspect for "no pixels at all".

**The fix:**

1. **Mesa `g5p-wsi-fix@4585835ac8c`**, one commit on `g5p-wsi`:
   - `g5p: refuse vertex shaders above 12 temps (they wedge the TA)`;
   - a `cherry-pick -x` of `9efe16621c4` (`g5p-xfb-fix`), without its XFB hunk, since g5p-wsi has no XFB;
   - nine lines in `g5p_pipeline.c`. A >12-temp VS now fails at pipeline creation instead of drawing nothing or
     wedging the TA.
2. **vkcube-wsi's vertex shader** `tools/mesa-a9-wsi/shaders/cube-a9.vert`:
   - same inputs, UBO and varying; the MVP product is written as four dot products on `transpose(MVP)`;
   - same 326 bytes and instruction mix (16 `alu2` FMAs, 12 `fadd`, 8 `vout`), in **12 temps** (up to r11);
   - SPIR-V `f737fcdf…`.
   - gpu-demo's `tools/gpu-demo/shaders/cube-a9.vert` is unchanged, so gpu-demo's pins and package stay as they are.
     Its vkcube-a9 v2 is refused by the fixed ICD.
3. **The new W3 job is W3's job plus one change:** vs the device-flown W3 job, only the VS bytes (+0x0c..+0x118 of
   the 326) and the DOUTU temps field (16 → 12) differ. Every other word is identical, in both the check job and
   the swapchain job.
   - So the rerun is a clean one-variable experiment: a PASS confirms the temps cause, and a `cube_px=0` again
     eliminates it.

**Regressions that would have caught it** (wsi.sh; the scissor/render-area check suggested in the brief would not have):

- **`tools`:**
  - the shader gate fails a vertex shader above 12 temps;
  - as a negative control, it must still see v2's SPIR-V (pinned `2d0e616e…`) at more than 12. The gate JSON
    showed 16 the whole time, but nothing checked it.
- **`offline`:** `vkcube-v2vs-{icd,tramp}` run vkcube-wsi with `--vert-spv` = v2's SPIR-V (a new client option).
  They must fail at pipeline creation (`rc=3`) with `vertex shader needs 16 temps (> 12 wedges the TA)`. Both do.

**Results** (`scripts/gpu/wsi.sh build test tools tramp stage offline package`, log `build/gpu/wsi/offline-fix.log`):

- **test:** g5p-gfx-test old vs new: 405 files, 34 jobs and 32 scenes byte-identical; logs identical; round17
  decoder clean. The guard changes no regression scene.
- **trampoline:** VM-edit guards shifted +17888 (unchanged). `G5P117_INIT result=ok icd_sha256=a2cb9215…` in all 13
  trampoline runs.
- **offline: PASS.** 28 fake-node runs, `refused=0`. vkcube-wsi: 60/60 frames over the ICD and over the trampoline,
  evidence unchanged (`SHADER_HOST STRIDE TEXTURE UNIFORM VARYING VS_UNIFORM`). v2's VS refused in both.
  weston-simple-egl's VS is 12 temps, so the guard passes it.
- **Two new informational messages, not gated:**
  - a JSON traceback from `job_evidence` after weston-simple-egl: an empty `job.json`, most likely a dump cut by
    SIGINT;
  - es2gears' stub `kill: usage` (no stub pid).

  Both clients still ended as before.

**Package:** `build/gpu/wsi/package/wsi-a9.tar.gz`, sha256
**`e510a05b73f48c748cbf6b607c3e2ce6217ef1e33cea8134b0c7164f639a9c44`**. It is byte-reproducible: two builds gave the
same sha. Against `98c51ebd` only three files changed (the EGL/GLES/gallium/GBM files and the env are identical):

```
a2cb9215f8a575d5a01970520c4637b5f61b80e3f34de3d6be4f491744fea8de  lib64/libvulkan_g5p.so     (was ac0460f9)
4a895f6d99a76e1da4582313a3be4fa444dc956eeda1bbeae35c2c6182335410  lib64/libvulkan_g5p117.so  (was 3387d083)
8590aadd4b925bba680d106efa8d3aa8831d64244811f7f7031a067a4c5b446f  bin/vkcube-wsi             (was a6fd91d2)
```

**Coordination with arenafix:**

- `4585835ac8c` is a single, self-contained commit. Cherry-pick it onto the combined `g5p-wsi + arena fixes` branch.
- None of `g5p-partial`, `g5p-arena-fix` or `g5p-wsi-arena` has the guard; only `g5p-xfb-fix` has it.
- The vkcube-wsi shader and client changes are workspace files (`tools/mesa-a9-wsi/`), independent of the ICD.

**W4 is a separate failure.**

- Its probe b (+21 s) shows weston-simple-egl's 50 % black clear and no triangle. Probe a showed no window.
- Its VS is 12 temps, so this fix does not touch it. Its unflown vertex form is the two-binding fetch (`BINDINGS`,
  2 DDMADs).
- The W4 rerun below is a proper measurement with earlier probes. Don't expect a PASS.

**Noted, not chased:** W3 probe a shows bare wallpaper where the window is. The swapchain is opaque `XR24`
(`alpha=0x1`), and probe b shows the clear colour over most of the window. This is labwc composition (damage-only
redraw?), not the client job.

### Rerun (orchestrator): W3, then W4. **Warm-safe**

- **Boot:** the same style as 21:04Z: multiclient2 boot 2, labwc started by `boot-desk-dmabuf-eager.sh`, linux-dmabuf
  ON.
- **No reboot is needed if that session is still up.** Clients get the new pair only through
  `/tmp/mc2/client-gpu.env`, and labwc is untouched.
- **Stop rule:** as above. On deskfold and later, a warm kexec is fine even after a wedge.

```sh
SSH=scripts/omarchy/ssh.sh
(cd build/gpu/wsi/package && shasum -a 256 -c SHA256SUMS)          # e510a05b…
$SSH 'rm -rf /tmp/wsi-a9 && tar -C /tmp -xzf - && cd /tmp/wsi-a9/a9-mesa && sha256sum -c --quiet SHA256SUMS && echo contents-ok' \
    < build/gpu/wsi/package/wsi-a9.tar.gz
$SSH 'cp /tmp/wsi-a9/a9-mesa/client-gpu-wsi.env /tmp/mc2/client-gpu.env && echo PVR_G5P_DEBUG=refuse >> /tmp/mc2/client-gpu.env'
$SSH 'sudo -n /tmp/mc2/gpu-client.sh root 30 /tmp/wsi-a9/a9-mesa/bin/eglinfo -B -p wayland >/dev/null; sudo -n grep -E "renderer|G5P117_INIT" /run/desk/client-gpu.log | tail -2'
# must show icd_sha256=a2cb9215… result=ok and the zink … Apple A9 G5P renderer
```

`rung_at` is the device plan's `rung` with the two framebuffer probes at +A and +B seconds after the client starts,
instead of +20/+21:

```sh
rung_at() { # NAME SECS A B CMD...
  local n=$1 s=$2 a=$3 b=$4 L C; shift 4
  L=$($SSH 'sudo -n dmesg | wc -l'); C=$($SSH 'sudo -n cat /run/desk/client-gpu.log 2>/dev/null | wc -l')
  $SSH "sudo -n dd if=/dev/fb0 of=/tmp/mc2/$n-base.raw bs=12582912 count=1 status=none"
  $SSH "sudo -n systemd-run --unit=wsi-$n /tmp/mc2/gpu-client.sh root $s $*"
  $SSH "sleep $a; sudo -n dd if=/dev/fb0 of=/tmp/mc2/$n-a.raw bs=12582912 count=1 status=none; sleep $((b - a))
        sudo -n dd if=/dev/fb0 of=/tmp/mc2/$n-b.raw bs=12582912 count=1 status=none"
  for i in base a b; do $SSH "sudo -n cat /tmp/mc2/$n-$i.raw" > /tmp/desk/wsi-$n-$i.raw; done
  python3 -I tools/mesa-a9-wsi/fbdiff.py /tmp/desk/wsi-$n-{base,a,b}.raw --crop /tmp/desk/wsi-$n | tee /tmp/desk/wsi-$n-fb.txt
  $SSH "while systemctl is-active -q wsi-$n; do sleep 2; done; sudo -n tail -n +$((C + 1)) /run/desk/client-gpu.log > /tmp/mc2/$n-client.log
    grep -vE 'G5P117_(PASSTYPE|BLOCK)' /tmp/mc2/$n-client.log | tail -12" | tee /tmp/desk/wsi-$n-client.txt
  $SSH "sudo -n dmesg | tail -n +$((L + 1)) > /tmp/mc2/$n-dmesg.txt; cd /tmp/mc2
    grep -E 'RENDER-V3 (bind|unbind|close|terminal|refused)|G5P VM release' $n-dmesg.txt | tail -6
    echo bad=\$(grep -cE 'RENDER-USER job=.* complete=-|terminal kind=(timeout|fault)|refused' $n-dmesg.txt)
    sudo -n grep 'LABWC-A9 FRAME' /run/desk/labwc.log | tail -1" | tee /tmp/desk/wsi-$n-kernel.txt
  $SSH "sudo -n cat /tmp/mc2/$n-dmesg.txt" > /tmp/desk/wsi-$n-dmesg.txt
}
B=/tmp/wsi-a9/a9-mesa/bin
rung_at w3r 90 20 21 $B/vkcube-wsi --frames 600 --check-at 30 --dump /tmp/mc2/vkcube-wsi-30.ppm
$SSH 'cat /tmp/mc2/vkcube-wsi-30.ppm' > /tmp/desk/vkcube-wsi-30-fix.ppm
# only if w3r's bad=0: weston-simple-egl maps its window late, so probe at +6 s and +8 s
rung_at w4r 60 6 8 $B/weston-simple-egl
```

**Reading W3r:**

- **PASS:** `check frame=30 … corners_clear=1 … cpu_mismatch=N … result=PASS` and `frames=600 bad=0 … result=PASS`.
  The PPM shows the textured cube. This confirms the temps cause: the job differs from W3's only in VS registers
  and DOUTU temps.
- **`cube_px=0` again, with jobs complete:** 16 temps is eliminated. Next is the per-binding 8-dword DDMAD with a
  compiled VS. The variant to build would force per-attribute fetch (offsets that are not consecutive vec4s), and
  that is not built yet. Report `wsi-w3r-{client,kernel,dmesg}.txt` and the PPM.
- **`ta=fault`/`timeout`:** stop rule. Report the full dmesg. The VS is now in the flown temps range, so a wedge
  would point at the DDMAD or the UBO path.

**Reading W4r:**

- `fbdiff … result=PASS` with the triangle visible in `/tmp/desk/wsi-w4r-b.ppm` would be news. Then W4's earlier
  FAIL was a probe-timing artefact.
- Otherwise: the window is visible (the dark translucent square) with no triangle. That reproduces W4 and points
  at the two-binding fetch (`BINDINGS`), a separate lane.
- `client rc=124` is the expected end either way.

## W3/W4 ladder: GPU clients draw only their clear (offline diagnosis, 2026-11-01)

**Device facts** (`device-20261101T204720Z`, `device-20261101T213240Z`): vkcube-wsi ran 600 frames, `bad=0`, TA ~7 ms,
`cube_px=0`, first with the 16-temp VS and then with the 12-temp one. weston-simple-egl shows its clear and no
triangle at +6 s and +8 s.

### The "swapchain image" premise is wrong for vkcube-wsi

- vkcube-wsi's check job never draws into the swapchain image. `check_frame()` renders into `D.check`, which
  `linear_image()` creates as a host-visible linear image at the start of the VM arena (`color.va=0x102000000`): an
  **offscreen** image.
- Both device check PPMs are 100 % `0x333333` (262144 of 262144 pixels). The offscreen render of the same scene
  drew nothing either.
- The swapchain job and the check job differ only in the target (`color.va`, `pbe[1]`, state `+0x960`; `jobdiff`
  on `diag/fixwsi2`).
- So brief rung (0), offscreen vs swapchain, already flew in W3 and W3r: **both fail**. A swapchain-only cause can't
  explain vkcube. weston-simple-egl never drew offscreen, but its words below match on every target-independent
  point.

### Fake-node job diff

Tools: `tools/mesa-a9-wsi/vdmwalk.py` (the VDM stream: VS PDS, uniform loader, PPP words) and
`tools/mesa-a9-wsi/vsforms.py` (USC forms and the common dwords a program reads). Traces are in
`build/gpu/wsi/ladder/t/` (`cube`, `dvk`, `eglt`, `wse`, `r-*`). labwc's are in `build/gpu/labwc114/userspace/offline/fix-trace`.

| job | on hardware | VS uniform loader (DOUTD) | common dwords the VS reads | VS temps | vertex fetch |
|---|---|---|---|---|---|
| labwc (labwc114 `fix-trace`) | **draws** | `ao=2 bsize=15` (UBO) | 2..16, 2..10 or none | 8 | 1 DDMAD × 2 dw (`ATTRIB_FETCH`) |
| gpu-demo cube (Zink, wlroots shaders) | **draws** | — (no bins kept) | 2..16 | — | `ATTRIB_FETCH` |
| depth-vk `one`/`cube16` | **draws** | none | none | 8 | 1 DDMAD × 8 dw, stride 32 |
| xfb `control` VS | **draws** 4096/4096 | `ao=2 bsize=10` (job block) | 2, 3, 10 | 12 | fetch-less |
| vkcube-wsi, check (offscreen) | **clear only** | `ao=2 bsize=16` (UBO `MVP`) | **2..17** | 12 | 1 DDMAD × 8 dw, stride 32 |
| vkcube-wsi, swapchain | **clear only** | identical | **2..17** | 12 | identical |
| weston-simple-egl (and egl-wl `--triangle`) | **clear only** | `ao=2 bsize=16` (UBO `rotation`) | **2..17** | 12 | 2 DDMADs (`BINDINGS`) |

**Identical in all of them, failing and flown:**

- Output select `08010000` (vtxsize 8, rhw);
- ISP `03c40000 003e0f00`: opaque, no depth test, no scissor;
- PPP control `000004a0`: cull NONE, wclampen, clip FRONT_REAR;
- W clamp `1e3ce508`;
- viewport = the attachment (512: `256 256 256 256 0 1`), region clip = every tile, render area = the attachment;
- target format 44 (B8G8R8A8), stride = width × 4;
- VS ALU forms: `99.11` fmul.shared and `99.31` fadd, as labwc's VS uses them.

Cull and front face can't matter: vkcube culls on the CPU and draws with `VK_CULL_MODE_NONE`, encoded as cullmode 0,
the same as labwc's.

**The MVP path checks out offline:**

- The DOUTD source address holds the frame's MVP: columns `(2.414 0 0 0) (0 -2.070 -0.516 -0.514) (0 1.242 -0.859
  -0.857) (0 0 5.642 5.831)`.
- The DDMAD base holds the cube's vertices.
- The compiled VS decodes to the right product: `x = sh2·x + sh6·y + sh10·z + sh14·w` … `w = sh5·x + sh9·y +
  sh13·z + sh17·w`.

### Causes, ranked

1. **The 16-dword vertex uniform: common dwords 16..17.** This is the one VS property that both failing clients
   share and that no flown VS has: every flown VS reads at most common dword 16, through ≤ 15 loaded dwords.
   - If the VS doesn't see common 16..17 (`MVP[3][2]`, `MVP[3][3]`):
     - weston's `w` is 0, so every vertex is clipped;
     - vkcube's `z ≈ 1.003·w`, so every vertex lies beyond the far plane (or `w ≤ 0`).
   - Either way the result is zero primitives, a quick TA and the exact clear colour: what both runs showed.
   - **Not pinned:** Apple's own iOS `v_mvp` capture loads its VS uniforms with the same one DOUTD of 16 dwords to
     common 2 (`tools/gpu-userspace/check-round19.py`). So the burst form alone works under Apple's programs, and the
     read side (operands of common ≥ 16, or the VS common window) is equally open.
   - labwc's one read of common 16 is, inferred from its 15-dword range (Zink packing proj as d0..8 and tex_proj as
     d9..17), `tex_proj[1][2]`, which is normally 0. A wrong read there would not show.
   - Rungs `u12`/`u`/`u2`/`up` read back exactly which dwords arrive.
   - The round-19 `hypr.vert` (`mat3 proj` + `vec4 color`) also reads common 2..17 from a 16-dword loader, but no
     hardware result for it is on record.
2. **The vpds attribute-DMA mechanism at its edge: 12 temps with DMA'd attributes at unified 4.** This is the second
   property that both failing VSes share and that no verified-flown VS has.
   - vkcube: 12 temps, 8 input dwords, STATE5 `0x1082`. weston: 12 temps, 2 DDMADs, `0x1084`. Both are unified 4.
   - The flown 12-temp VS (xfb `control`) is fetch-less.
   - labwc and depth-vk fetch attributes, but at 8 temps.
   - vpds's v-rungs fetched at 12 temps, but with unified 8..18 (their ICD).
   - vpds found on hardware (vertex-temps.md, `x1`) that at 16 temps the attribute DMA and the shader disagree about
     where a vertex's inputs sit. That explains W3's **first** flight (16 temps), not W3r or W4 (12).
   - vpds's model ("a 16-dword per-instance window that loses 4") predicts that 12 fits at unified 4. That is
     unproven with attributes.
   - **Neither failing VS reads sh ≥ 32** (vpds's `vk` wedge): the highest is sh17, with index byte 2 = `00` as in
     every flown read. The ladder reads at most sh17; its push rungs land at common 2..17 in this ICD.
   - Rung **`u12`** (12 temps, pos + selector DMA'd, uniforms only to sh13) isolates this cause from cause 1:
     - quads missing or misplaced in `u12` mean this cause;
     - `u12` exact but `u` wrong in dwords 14..15 means cause 1.
   - **Dependency on vpds:** `g5p-vpds@15ea5d8595e` keeps unified 4 inside its "flown envelope" (≤ 12 temps, ≤ 8 input
     dwords), so it would not change these jobs. If `u12` fails, that envelope rule has to grow to cover Apple's
     `2·ceil(max(t, in+2)/4)` = 6 for these shaders, and the WSI ICD must be rebased onto it.
3. **vkcube only: the texture FS `fdd647c7`**, which has never run on hardware. It can't explain weston. Rung `t`.
4. **vkcube only: clip-space `w ≠ 1`** (`w ≈ 5.8`, `z ≈ 0.97 w`). Every flown job has `w = 1`. Rung `dw` against `d`
   (both at 8 temps, so outside cause 2).
5. **weston only: the two-binding fetch** (`BINDINGS`) with the in-shader `(0,0,0,1)` fill for `vec2`/`vec3`
   formats. Not laddered (weston is unmodified). It is next if `u`/`c` pass and weston still draws nothing.

**Ruled out:**

- **the swapchain/dma-buf target:** the offscreen check is empty too, and the words are identical apart from the
  target;
- **cull and winding:** cull is NONE, and rung `b` checks this anyway;
- **16 temps:** eliminated by W3r;
- **12 temps alone:** xfb's control VS at 12 temps, fetch-less, with uniforms in common 2..11, drew 4096/4096. With
  DMA'd attributes it is still open: see cause 2;
- **the 2×vec4 stride-32 fetch:** depth-vk `cube16` flies it;
- **viewport, scissor, region, ISP and PPP:** the same words as labwc's.

**`triangles=6` then `4`:** the scene angle is 90°/s × elapsed time. Frame 30 came at 11.2 fps in one run and 9.2 fps
in the other, so the cube showed 3 faces once and 2 the other time. This is not a lead. The ladder uses a still
scene: 30°, 3 faces, 6 triangles.

**No ICD change.** Nothing pins a mechanism offline, so the ICD/trampoline pair stays `a2cb9215`/`4a895f6d`, the pair
that flew W3r. The `fix` rung is a client-side workaround inside labwc's footprint.

### The ladder (`vkcube-wsi --rung NAME`)

**What every rung does:**

- It draws one scene that changes one thing against W3, into the swapchain image (created with `TRANSFER_SRC`). Rung
  `0` also draws into the offscreen image.
- It reads the image back with `vkCmdCopyImageToBuffer` (a CPU copy in this ICD, after the job retires) at frames 2
  and 30.
- It prints `ladder rung=R target=T frame=F drawn_px=N want_px=W mismatch=M … verdict=V`:
  - `EMPTY`: clear colour only (W3's failure);
  - `WRONG`: pixels that disagree with the CPU's model;
  - `DRAWN`: pixels that agree.
- It runs 40 frames, then prints `ladder rung=R readbacks=N drawn=N bad=N` and `result=`.
- `--dump PREFIX` writes `PREFIX-TARGET-fN.ppm`, and `--rung list` lists the rungs.

**Safety:**

- Every rung's VS is ≤ 12 temps on the general path, or depth-vk's flown pass-through.
- No rung uses `gl_VertexIndex`.
- Every rung uses one binding (2×vec4, stride 32).
- Job evidence stays inside labwc's flown set.
- Push-constant loads come from the job's state arena, the source xfb's flown VS loader used.

The default mode (no `--rung`) is unchanged: its jobs are byte-identical to the previous binary's (every arena, 3/3
jobs).

| rung | one change | VS / FS | VS uniform load (offline trace) | evidence |
|---|---|---|---|---|
| `a` | depth-vk's flown draw, into the WSI image: big triangle, NDC, `w = 1`, solid green | pass / colour | none | STRIDE VARYING |
| `b` | `a` wound the other way (cull NONE) | pass / colour | none | STRIDE VARYING |
| `u12` | VS uniform read-out, 12 dwords (labwc's range) | probe12 / colour | `ao=2 bsize=12` | +SHADER_HOST VS_UNIFORM |
| `u` | the same, 16 dwords: W3's load, common 2..17 | probe / colour | `ao=2 bsize=16` | same |
| `u2` | 16 dwords as two 8-dword UBOs: same reads, split load | probe2 / colour | `ao=2 bsize=8` + `ao=10 bsize=8` | same |
| `up` | 16 dwords as push constants (copied into the job) | probep / colour | `ao=2 bsize=16` from the state arena | same |
| `d` | the cube, transformed and divided on the CPU (`w = 1`) | pass / colour | none | STRIDE VARYING |
| `dw` | the cube in CPU clip coordinates (`w ≈ 5.8`, `z ≈ 0.97 w`) | pass / colour | none | STRIDE VARYING |
| `t` | `d` with W3's texture FS | pass / cube-a9.frag | none | +TEXTURE UNIFORM SHADER_HOST |
| `c` | the cube through W3's VS (UBO MVP) with the colour FS | cube-a9.vert / colour | `ao=2 bsize=16` | +VS_UNIFORM |
| `cp` | `c` with the MVP in push constants | cubep / colour | `ao=2 bsize=16` from the state arena | same |
| `fix` | W3's scene; the VS reads 15 dwords, with `MVP[3][3]` in `attr.w` | fix15 / cube-a9.frag | `ao=2 bsize=15` (labwc's) | W3's |
| `e` | the full cube: all 12 triangles, W3's shaders, cull NONE | cube-a9 / cube-a9 | `ao=2 bsize=16` | W3's |
| `0` | W3's scene into the offscreen image **and** into the swapchain image | cube-a9 / cube-a9 | `ao=2 bsize=16` | W3's |

**The probe rungs:**

- Each of the four quads selects one column of the probe matrix, so quad `q` reads back as dwords `4q..4q+3` (R G B A).
- Dword `k` is `(k+1)/17`, stored as the byte `15(k+1)`.
- The line `q3=…(12,13,14,15)` names which dword's value arrived in each channel: its index, `0` for an empty dword,
  or `?`.
- `dwords_bad=0x….` is the mask of wrong dwords: dword 14 = common 16, and dword 15 = common 17.
- `cover_mismatch` (= `mismatch`) counts pixels, edges excluded, whose drawn/clear state disagrees with the four quads.
  Misplaced vertices (cause 2) show here; wrong uniform values (cause 1) show only in the colours.

**Offline** (`scripts/gpu/wsi.sh ladder`, through the trampoline over the fake node): **PASS**.

- Every rung ran 4 frames with `bad=0`, read back twice (rung `0` four times), and had 0 refusals.
- The stub received the frames, and no evidence fell outside labwc's set.
- The verdicts read `WRONG` offline by construction: the fake node writes no pixels.
- The probe data at every DOUTD source is exact: dwords 1..16 × 1/17 for the UBO (two halves for `u2`) and for the
  job copy (`up`).

### Package

`build/gpu/wsi/package/wsi-a9.tar.gz`, sha256 **`332b9d3feda23ef8d38effe30fe12812dab5d5ca3a579b7ef3a411998533b710`**.

- It is byte-reproducible: two builds gave the same sha.
- The W3-fix package is kept as `build/gpu/wsi/package-e510a05b/`.
- Against `e510a05b`, only one file changed:

  ```
  71a0079ad2ab11b1f7df7244c2fb3d78cf495223436ea5932d5572e34d301979  bin/vkcube-wsi   (was 8590aadd)
  ```

  The ICD `a2cb9215`, the trampoline `4a895f6d`, EGL/GLES/gallium/GBM, wsi-vk, egl-wl, weston-simple-egl, eglinfo and
  the env are byte-identical.

**Regression:**

- `offline`: PASS (`build/gpu/wsi/offline-ladder.log`). 28 fake-node runs with `refused=0`, vkcube-wsi 60/60 over
  the ICD and over the trampoline, and v2's VS still refused.
  - This ran on the client before its last change, which touched only the ladder's verdict code. The default mode
    doesn't reach that code.
- Default mode: its 3 traced jobs are byte-identical to the previous binary's.
- `ladder`: PASS (plumbing) on the packaged client (`build/gpu/wsi/ladder-final.log`).
- Shader gates: every ladder VS has 12 temps on the general path; `ladder-pass` takes the captured shape path, as
  depth-vk's does.

### Device plan (orchestrator): ladder, **warm-safe**

- **Same base as W3r:** multiclient2 boot 2 (or deskfold+), labwc with linux-dmabuf, and the same pair
  `a2cb9215`/`4a895f6d`.
- **Each rung** is one extra client context beside labwc for about 10 s.
- **Stop rule unchanged:** any `RENDER-V3 terminal kind=timeout|fault` or `complete=-` on the client's pair means
  stop.
- **`fbdiff`:** the scene is still, so `fbdiff`'s "moved inside window" is expected to FAIL. The verdict comes from
  the read-back; the full-frame images still show what labwc composited.

```sh
SSH=scripts/omarchy/ssh.sh
(cd build/gpu/wsi/package && shasum -a 256 -c SHA256SUMS)
$SSH 'rm -rf /tmp/wsi-a9 && tar -C /tmp -xzf - && cd /tmp/wsi-a9/a9-mesa && sha256sum -c --quiet SHA256SUMS && echo contents-ok' \
    < build/gpu/wsi/package/wsi-a9.tar.gz
$SSH 'cp /tmp/wsi-a9/a9-mesa/client-gpu-wsi.env /tmp/mc2/client-gpu.env && echo PVR_G5P_DEBUG=refuse >> /tmp/mc2/client-gpu.env'
B=/tmp/wsi-a9/a9-mesa/bin
# no GPU job: lists the 14 rungs into the client log
$SSH "sudo -n /tmp/mc2/gpu-client.sh root 20 $B/vkcube-wsi --rung list >/dev/null; sudo -n tail -n 15 /run/desk/client-gpu.log"
# rung_at as in W3r. Order: a (flown base), the uniform probes, the vkcube-only suspects, the cube rungs, the
# target split, the winding.
for r in a u12 u u2 up d dw t c cp fix e 0 b; do
  rung_at l$r 30 4 6 $B/vkcube-wsi --rung $r --dump /tmp/mc2/l$r
  $SSH "grep -h 'ladder rung' /tmp/mc2/l$r-client.log" | tee /tmp/desk/wsi-l$r-verdict.txt
  for t in swapchain offscreen; do for f in 2 30; do
    $SSH "cat /tmp/mc2/l$r-$t-f$f.ppm 2>/dev/null" > /tmp/desk/wsi-l$r-$t-f$f.ppm; done; done
done
```

**Reading the verdicts.** The first rung that departs from `DRAWN` is the answer:

| first non-`DRAWN` | means | next |
|---|---|---|
| `a` `EMPTY` | depth-vk's flown draw shows nothing in a WSI image: the target after all (against W3's own offscreen check) | rung `0`: offscreen vs swapchain; the target words `pbe[1]`, state `+0x960` |
| `u12` quads missing or misplaced, `a` `DRAWN` | 12 temps with DMA'd attributes at unified 4 (cause 2, vpds's mechanism at its edge) | vpds: Apple's unified sizing inside the envelope too; rebase the WSI ICD |
| `u12` quads in place but colours wrong | VS uniforms fail even inside labwc's range: unexpected, since labwc flies them | diff against labwc's loader word for word |
| `u` `dwords_bad=0xc000` (quad 3 shows `12,13,·,·`) and `u2` the same | the VS doesn't see common 16..17 whatever the load: the read side (shared operand ≥ 16 or the VS common window) | ICD: keep VS uniforms inside common 2..16 (move the base, or spill), measured against Apple's `v_mvp` |
| `u` bad, `u2` `DRAWN` | the one 16-dword DOUTD loses data, and two 8-dword DOUTDs don't | ICD fix: split VS uploads into ≤ 8-dword DOUTDs (u2's form) |
| `u` `0xffff` (all zero), `u12` good | the 16-dword burst doesn't land at all | `u2` decides, as above |
| `u` bad, `up` `DRAWN` | the UBO read at execution time is the problem, not the size | ICD: copy small VS UBO ranges into the job as push constants |
| `u`/`u2`/`up` all `DRAWN` | VS uniforms are cleared, and cause 1 with them | read `dw`, `t`, `c` |
| `dw` `EMPTY`, `d` `DRAWN` | the TA's handling of `w ≠ 1` (divide or clip) | the PPP clip/W-clamp words for perspective |
| `t` `EMPTY`/`WRONG`, `d` `DRAWN` | the texture FS `fdd647c7` | the FS lane |
| `c` `EMPTY` with `u` and `dw` `DRAWN` | the cube VS itself: c and u differ only in where the product goes | VS output order and position slot |
| `fix` `DRAWN`, `0` `EMPTY` | confirms the 16th dword as vkcube's cause; `ladder-fix15.vert` is a working vkcube | the ICD fix for weston (which a client workaround can't reach) |
| `0`: offscreen `DRAWN`, swapchain `EMPTY` | the target, against W3/W3r's own offscreen result | target words |
| `b` `EMPTY`, `a` `DRAWN` | cull NONE drops one winding | PPP control `cullmode` |
| `e` alone `EMPTY` | the 6 back-face triangles (both windings in one draw) | — |

weston-simple-egl follows cause 1: its rotation is a mat4 (16 dwords, common 2..17). If `u` shows common 16..17
missing, W4 is explained by the same fix. If `u` passes, W4's next suspect is cause 4.

## W3/W4 ladder on hardware, and the fix: the vertex unified store (2026-11-02)

**Device run** (package `332b9d3f`, labwc on the WSI pair, dmabuf ON; verdicts in
`logs/wsi-ladder/verdicts-20261101T223313Z.txt`, dumps in `build/gpu/wsi/device-ladder-20261101T223313Z/`):

- All 14 rungs ran with `bad=0` and no wedge.
- **DRAWN:** `a`, `b`, `d`, `dw`, `t`. That covers the WSI target, both windings, `w ≠ 1` and the texture FS.
- **EMPTY:** `c`, `cp`, `fix`, `e`, and `0` on both the offscreen and the swapchain target.
- **WRONG:** the probes, all with `cover_mismatch=0`.
  - `u`, `u2`, `up`: every quad reads back as `(0,0,0,dword 4q+3)`, `dwords_bad=0x7777`. That is the same for one
    16-dword DOUTD, two 8-dword DOUTDs and push constants.
  - `u12`: `q0=(dword 0, 0, 0, dword 3)`, then `(0,0,0,7)` and `(0,0,0,11)`, and `q3=0`: `dwords_bad=0x0776`.

### It is not the uniform reads: registers r8..r11 read 0

The read-out looks like "only `.w` arrives", but its pattern follows the **registers** the compiled programs use,
not the vector components. The decoding below is from `build/gpu/wsi/gen/gate-ladder-probe*.usc`.

- **`ladder-probe.vert`** (12 temps):
  - Inputs are DMA'd to r0..r3 (`pos`) and r4..r7 (`sel`).
  - `v.x`, `v.y` and `v.z` are each built as a chain of `fmul.shared` + `fadd` into **r9, r10 and r11** (scratch
    r8).
  - `v.w`'s chain runs in place on **r7 → r4** (the last reads of `sel`).
  - The outputs are `vout r9/r10/r11` (slots 4..6) and `vend r4` (slot 7).
- **`ladder-probe12.vert`:** `v.x = r7 ← (r8 ← c1.x·s.y + (r7 ← c2.x·s.z)) + c0.x·s.x`, `v.y/v.z` in **r8/r9**, and
  `v.w` in place on r6..r4.

**Model: everything a 12-temp VS writes to r8..r11 reads back 0.** The model predicts every probe colour exactly:

- `u`/`u2`/`up`: `(0, 0, 0, w)` on every quad, `0x7777`;
- `u12`: quad 0 keeps `c0.x` (the `0x0f`), because only the r8 partial is lost. Quads 1 and 2 lose `x`, `y` and
  `z`, which gives `0x0776`;
- the cube VS writes `gl_Position.xyz` from r9..r11 and `.w` from r0, so every vertex is `(0, 0, 0, w)`: zero area and
  `EMPTY` for `c`, `cp`, `fix`, `e` and `0`;
- the 8-temp pass-through VS (`a`, `b`, `d`, `dw`, `t`) and every flown 8-temp VS (labwc, depth-vk) never use r8+.

So the uniforms arrive: every `.w` column passes through the multiply with the right value, from a UBO and from push
constants alike.

**Cause:** VDM_STATE5 `vs_usc_unified_size`.

- Every branch except `g5p-vpds` programs the captured constant `0x1082`: **unified 4**, enough for r0..r7.
- DOUTU says 12 temps, but the vertex task is only given 8.
- Apple sizes this store from the program (AGXMetalA9 `VertexProgramVariantPDSState`; vertex-temps.md):
  `2·ceil(max(temps, inputs + 2)/4)`, which is 6 for a 12-temp VS.
- This also fits the earlier hardware facts:
  - XFB runs 1–2: a 12-temp VS stored through `[r9:r10]` and nothing landed;
  - the 16-temp `hold` and vkcube v2 failures happened at unified 4;
  - vpds' 12-temp v-rungs passed, because more than 8 input dwords grew their store to 8+.

### The fix: `g5p-wsi-unified@9ceb3df22f6`

This is one commit on `g5p-wsi-fix`: `g5p: size the vertex unified store from the program`.

- The new rule: `unified = 4·ceil(inputs/8)`; for a VS above 8 temps, also `2·ceil(max(temps, inputs + 2)/4)`.
- This is g5p-vpds' formula, but gated at **> 8 temps** (the measured r0..r7 window) instead of vpds' `> 12`.
- Every VS that fits r0..r7 keeps the captured 4, including labwc's and depth-vk's.
- New values: 12-temp VS → **6** (vkcube `0x1882`, weston `0x1884`).

**Regression** (`wsi.sh build test tramp stage offline ladder package`):

- **test:** `compare-scenes.py --vs-unified` masks only STATE5's unified field (found by `vdmwalk.py`) and checks it
  against `vdmwalk.vs_unified_rule` on every new draw:
  - `draws=256 grown=18 rule_ok=128 rule_bad=0`;
  - `result=IDENTICAL files=405 jobs=34 scenes=32`;
  - logs identical, round17 decoder clean.
  - The 18 grown draws are exactly the gfx-test draws above 8 temps. The same check flags all 18 on the old ICD.
- **trampoline:** VM-edit guards +17888, as before.
- **ICD and trampoline:** `8383df6e…` / `02e5e078…`.
- **offline:** PASS (`build/gpu/wsi/offline-unified.log`): 28 fake-node runs with `refused=0`, 13 ×
  `G5P117_INIT icd_sha256=8383df6e… result=ok`, and v2's 16-temp VS still refused.
- **ladder:** 14/14 plumbing PASS through the trampoline. A trace of rung `u` shows `VS_STATE5 00001882` (unified 6,
  temps 12), while the 8-temp epilogue keeps `0x1082`.

**vpds overlap:**

- `g5p-vpds@1a2ef450a22` has the same formula, behind the gate `vs->temps > G5P_VS_TEMPS_FLOWN (12) || inputs > 8`.
  So a 12-temp VS with ≤ 8 input dwords still gets 4 there, and it has the bug.
- Its fix is that one gate: `vs->temps > 8 || inputs > 8`. The cherry-pick conflicts textually, because vpds already
  has the sizing code.
- vpds' common-store sizing `2·ceil(dwords/16)` is the other store (PDS_STATE0 `usc_common_size`). For the WSI
  shaders (≤ 18 common dwords) both give 4: no overlap.
- Shift-add constant multiplies are an ALU change: no overlap.

**Other branches** (all program a fixed unified 4):

- `g5p-arena-fix` (desktop/labwc), `g5p-wsi-arena`, `g5p-gles3-tex`, `g5p-g9p-port`, `g5p-cf`, `g5p-depth`,
  `g5p-mrt`: **the commit cherry-picks cleanly** (`git merge-tree`).
- `g5p-xfb-fix`: it conflicts (its XFB variant touches the same lines). Port it by hand.
- labwc's own VSes are 8 temps, so the desktop is unaffected today. Any >8-temp VS through those ICDs loses r8+
  (Zink apps, GLES3 shaders, Hyprland's).

### Package and device plan (fix), **warm-safe**

`build/gpu/wsi/package/wsi-a9.tar.gz`, sha256 **`078ff500fe255fa2ebc69a98dc1056b1b8a6790b01fc94842343678c7c6774ca`** (byte-reproducible: two builds; `332b9d3f` kept as `build/gpu/wsi/package-332b9d3f/`). Against `332b9d3f` only the ICD (`8383df6e…`) and the
trampoline pinned to it (`02e5e078…`) changed. The clients, including vkcube-wsi `71a0079a`, are unchanged.

The ladder's own rungs are the check. `fix` stays the 15-dword client workaround; it is a 12-temp VS, so it drew
nothing before too. On the fixed ICD it is one more cube. Expected results:

- `u12`, `u`, `u2`, `up`: `DRAWN`, `dwords_bad=0x0000`;
- `c`, `cp`, `fix`, `e`, `0` (both targets): `DRAWN`;
- W3: `check frame=30 … result=PASS`;
- W4: the rotating triangle.

**Risk:** unified 6 itself has not flown, but vpds flew 8..18 beside labwc without a wedge. The stop rule is
unchanged.

```sh
SSH=scripts/omarchy/ssh.sh
(cd build/gpu/wsi/package && shasum -a 256 -c SHA256SUMS)
$SSH 'rm -rf /tmp/wsi-a9 && tar -C /tmp -xzf - && cd /tmp/wsi-a9/a9-mesa && sha256sum -c --quiet SHA256SUMS && echo contents-ok' \
    < build/gpu/wsi/package/wsi-a9.tar.gz
$SSH 'cp /tmp/wsi-a9/a9-mesa/client-gpu-wsi.env /tmp/mc2/client-gpu.env && echo PVR_G5P_DEBUG=refuse >> /tmp/mc2/client-gpu.env'
$SSH 'sudo -n /tmp/mc2/gpu-client.sh root 30 /tmp/wsi-a9/a9-mesa/bin/eglinfo -B -p wayland >/dev/null; sudo -n grep -E "renderer|G5P117_INIT" /run/desk/client-gpu.log | tail -2'
# must show icd_sha256=8383df6e… result=ok
B=/tmp/wsi-a9/a9-mesa/bin
for r in u12 u u2 up c cp fix e 0; do      # rung_at as in W3r
  rung_at f$r 30 4 6 $B/vkcube-wsi --rung $r --dump /tmp/mc2/f$r
  $SSH "grep -h 'ladder rung' /tmp/mc2/f$r-client.log" | tee /tmp/desk/wsi-f$r-verdict.txt
done
rung_at w3f 90 20 21 $B/vkcube-wsi --frames 600 --check-at 30 --dump /tmp/mc2/vkcube-wsi-30.ppm
$SSH 'cat /tmp/mc2/vkcube-wsi-30.ppm' > /tmp/desk/vkcube-wsi-30-unified.ppm
rung_at w4f 60 6 8 $B/weston-simple-egl
```

**Reading:**

- `u` all exact: the fix holds.
- `u` still `0x7777`: the unified field is not the r8+ limit. Then look at VDM_STATE5 `vs_max_instances`, which is 0
  (32 per task) and is the other per-instance knob.
- W4 still showing no triangle while `u` is exact: cause 5 (two bindings with the component fill) is next.

## Device plan (orchestrator): **warm-safe**

**Base and safety:**

- Run on `multiclient2-995d684abd`, boot 2 of `multi-client-design.md` §11: labwc started by
  `boot-desk-dmabuf.sh`, so linux-dmabuf is ON.
- Each client is one extra render context beside labwc. There is no kernel change, no labwc restart and no
  firmware-word change.
- On multiclient2, a stuck client job loses only its own context while labwc keeps rendering. After deskfold, a
  warm kexec is fine even after a failed context.
- **Order (by risk):**
  1. W1 and W2 run no client shaders (clear-only jobs: the captured epilogue). They isolate swapchain → dma-buf →
     labwc import.
  2. W3 runs v2's never-flown programs.
  3. W4 adds a two-binding vertex fetch (`BINDINGS`, a ROGUE form).

  This order is an argument, not hardware evidence. A 0-draw job (`CLEAR_ONLY`) is outside labwc's traced set
  too, and has never run on hardware.
- **Stop rule:** after any `RENDER-V3 terminal kind=timeout|fault` or `complete=-` on the client's pair, skip the
  later rungs and keep the session.

```sh
SSH=scripts/omarchy/ssh.sh
(cd build/gpu/wsi/package && shasum -a 256 -c SHA256SUMS)
$SSH 'rm -rf /tmp/wsi-a9 && tar -C /tmp -xzf - && cd /tmp/wsi-a9/a9-mesa && sha256sum -c --quiet SHA256SUMS && echo contents-ok' \
    < build/gpu/wsi/package/wsi-a9.tar.gz
$SSH 'mkdir -p /tmp/mc2 && chmod 0777 /tmp/mc2 && cp /tmp/wsi-a9/a9-mesa/client-gpu-wsi.env /tmp/mc2/client-gpu.env'
# refusals logged with their source line, as labwc's session has them (Zink hides the VkResult)
$SSH 'echo PVR_G5P_DEBUG=refuse >> /tmp/mc2/client-gpu.env'
$SSH 'cat > /tmp/mc2/gpu-client.sh && chmod 0755 /tmp/mc2/gpu-client.sh' < scripts/gpu/multiclient2/gpu-client.sh
# pre-checks, no GPU job: Y 0 0, then 0 unresolved libraries
$SSH 'cat /sys/module/a9_gpu_drm/parameters/prime_experiment; sudo -n grep -c NO_LINUX_DMABUF /run/desk/gpu.env; sudo -n grep -c "LINUX-DMABUF disabled" /run/desk/labwc.log'
$SSH 'cd /tmp/wsi-a9/a9-mesa && LD_LIBRARY_PATH=$PWD/lib64:/tmp/mesa102-desk/a9-mesa/lib64 ldd lib64/*.so* lib64/gbm/*.so bin/* | grep -c "not found"'
$SSH 'sudo -n /tmp/mc2/gpu-client.sh root 30 /tmp/wsi-a9/a9-mesa/bin/eglinfo -B -p wayland >/dev/null; sudo -n grep -E "renderer|G5P117_INIT" /run/desk/client-gpu.log | tail -4'
```

**If `ldd` shows `libLLVM.so.22.1` or `libxml2.so.16` not found:**

1. Find labwc's copy with `sudo grep -o '/[^ ]*libLLVM[^ ]*' /proc/$(pidof labwc-a9)/maps`.
2. Append its directory to `LD_LIBRARY_PATH` in `/tmp/mc2/client-gpu.env`.

The `eglinfo` check must show `OpenGL ES profile renderer: zink Vulkan 1.0(Apple A9 G5P …)` and
`G5P117_INIT … result=ok … icd_sha256=ac0460f9…`.

`gpu-client.sh` runs the client with `env -i` plus `client-gpu-wsi.env` only:

```
LD_LIBRARY_PATH=/tmp/wsi-a9/a9-mesa/lib64:/tmp/mesa102-desk/a9-mesa/lib64
VK_DRIVER_FILES=/tmp/wsi-a9/a9-mesa/share/vulkan/icd.d/g5p_icd.json
GBM_BACKENDS_PATH=/tmp/wsi-a9/a9-mesa/lib64/gbm  DRIRC_CONFIGDIR=/tmp/wsi-a9/a9-mesa/share/drirc.d
PVR_G5P_EXPERIMENTAL=1  PVR_G5P_GROUPS=last-read  MESA_LOADER_DRIVER_OVERRIDE=zink  EGL_PLATFORM=wayland
PVR_G5P_DEBUG=refuse   (appended above)
WAYLAND_DISPLAY=<labwc's socket>  XDG_RUNTIME_DIR=/run/labwc-desk
```

**One rung.** The `fbdiff` call finds the new window from base → a, and checks that a → b changed only inside it:

```sh
rung() { # NAME SECS CMD...
  local n=$1 s=$2 L C; shift 2
  L=$($SSH 'sudo -n dmesg | wc -l'); C=$($SSH 'sudo -n cat /run/desk/client-gpu.log 2>/dev/null | wc -l')
  $SSH "sudo -n dd if=/dev/fb0 of=/tmp/mc2/$n-base.raw bs=12582912 count=1 status=none"
  $SSH "sudo -n systemd-run --unit=wsi-$n /tmp/mc2/gpu-client.sh root $s $*"
  $SSH "sleep 20; for i in a b; do sudo -n dd if=/dev/fb0 of=/tmp/mc2/$n-\$i.raw bs=12582912 count=1 status=none; sleep 1; done"
  for i in base a b; do $SSH "sudo -n cat /tmp/mc2/$n-$i.raw" > /tmp/desk/wsi-$n-$i.raw; done
  python3 -I tools/mesa-a9-wsi/fbdiff.py /tmp/desk/wsi-$n-{base,a,b}.raw --crop /tmp/desk/wsi-$n | tee /tmp/desk/wsi-$n-fb.txt
  # this rung's client lines only (the trampoline prints a G5P117_PASSTYPE line per submit)
  $SSH "while systemctl is-active -q wsi-$n; do sleep 2; done; sudo -n tail -n +$((C + 1)) /run/desk/client-gpu.log > /tmp/mc2/$n-client.log
    grep -E 'G5P117_INIT|device=|GL_RENDERER|swapchain|surface|check frame|frames=|result=|client rc|refuse|rror' /tmp/mc2/$n-client.log | head -40" \
    | tee /tmp/desk/wsi-$n-client.txt
  $SSH "sudo -n dmesg | tail -n +$((L + 1)) > /tmp/mc2/$n-dmesg.txt; cd /tmp/mc2
    grep -E 'RENDER-V3 (bind|unbind|close|terminal|refused)|G5P VM release' $n-dmesg.txt | tail -6
    grep -o 'RENDER-USER job=.*' $n-dmesg.txt | grep -o 'slots=[0-9]*/[0-9]*' | uniq -c | head -12
    echo bad=\$(grep -cE 'RENDER-USER job=.* complete=-|terminal kind=(timeout|fault)|refused' $n-dmesg.txt)
    sudo -n grep 'LABWC-A9 FRAME' /run/desk/labwc.log | tail -1; sudo -n grep -iE 'dmabuf|dma-buf|import' /run/desk/labwc.log | tail -3" \
    | tee /tmp/desk/wsi-$n-kernel.txt
}
B=/tmp/wsi-a9/a9-mesa/bin
rung w1 90 $B/wsi-vk --frames 600                 # Vulkan WSI, clear only
rung w2 90 $B/egl-wl --frames 600                 # EGL -> Zink kopper, clear only
rung w3 90 $B/vkcube-wsi --frames 600 --check-at 30 --dump /tmp/mc2/vkcube-wsi-30.ppm
rung w4 60 $B/weston-simple-egl                   # ended by gpu-client's timeout
```

**PASS, per rung (the multi-client T2 criteria):**

- **The client reports Zink/g5p:**
  - W1/W3 print `device=Apple A9 G5P 9.15.6.2…`; W2 prints `GL_RENDERER=zink Vulkan 1.0(Apple A9 G5P …)`;
  - W4 is covered by the `eglinfo` pre-check with the same env;
  - every client log has `G5P117_INIT … result=ok … icd_sha256=ac0460f9…`. With the W3-fix package (`e510a05b`)
    expect `a2cb9215…` instead, here and in the `eglinfo` pre-check;
  - no `G5P refuse` line other than the D32 format query that Zink makes at start;
  - none says llvmpipe.
- **Its jobs complete, interleaved with labwc's:**
  - one `RENDER-V3 bind slots=X/Y ret=0` for a pair other than labwc's `8/7`;
  - the `uniq -c` lines alternate between `slots=8/7` and `slots=X/Y`;
  - `bad=0`.
- **Its window content changes between the two framebuffer probes:** `fbdiff … moved_inside_window=1 result=PASS`.
  - The crops `/tmp/desk/wsi-<n>-{a,b}.ppm` show: W1/W2 a flat window changing hue; W3 the textured cube turned
    between a and b; W4 simple-egl's RGB triangle at another angle.
  - A desktop clock ticking between a and b can push the moved box outside the window. Take the probes again
    before calling that a FAIL.
- **The client ends cleanly:**
  - W1–W3 end with `frames=600 bad=0 … result=PASS`; W3 also prints `check frame=30 … result=PASS`;
  - W4 ends with `client rc=124`;
  - after each exit, `close … plan=unbind` and `G5P VM release` appear;
  - labwc's FRAME keeps advancing, and labwc.log has no dma-buf import error.

**If a rung fails:**

- **`acquire`/`present` errors, an import error in labwc.log, or a black window while the jobs complete:** the
  dma-buf path is suspect (R7). Rerun the same rung with the software fallback:
  ```sh
  $SSH "echo MESA_VK_WSI_DEBUG=sw >> /tmp/mc2/client-gpu.env"
  ```
  The GPU job is unchanged, but `wsi_common` copies each image into a `wl_shm` buffer and labwc composites it
  without any dma-buf import. This was verified offline for `wsi-vk`, `vkcube-wsi` and `egl-wl`. A PASS with `sw`
  and a FAIL without it puts the fault in labwc's linux-dmabuf import or the ICD's dma-buf export. Restore with
  `cp /tmp/wsi-a9/a9-mesa/client-gpu-wsi.env /tmp/mc2/client-gpu.env`.
- **`ta=fault` or a `timeout` on the client's pair with W1/W2 clean:** a program problem, as with vkcube-a9 v1
  (`gpu-demo.md`), not WSI.
- **W1 failing with no import error:** suspect the clear-only job itself. Report it with the full `wsi-w1-dmesg`.
- **Non-root (T4 style):** repeat W2 with `gpu-client.sh linux …`.

## Reproduce

`scripts/gpu/wsi.sh all` runs: build, base, test, clients, tools, tramp, stage, offline, package. Run `protocols`
and `configure` once before it.

It needs:

- the `linux-a9-g5p-r5` container;
- the Arena106 sealed build and the mesa117 stage;
- Vulkan-Tools v1.4.341 and wayland-protocols 1.45 (`gpu-demo.sh fetch`).

`clients` uses `dnf download` with sha256-pinned RPMs and unpacks them with `rpm2cpio`. The shared container's
packages are never changed.
