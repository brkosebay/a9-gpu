# GPU status (updated 2026-11-01)

Read this first. It is the current state of the A9 GPU work: what works, what doesn't, and what is
being worked on. Details live in the kernel branch and `docs/gpu/`.

## Works on hardware

- **Desktop on the GPU.** The Labwc compositor renders on the GPU (wlroots GLES2 -> Zink -> our Vulkan
  driver -> our kernel driver) and shows on the panel. Windows are composited over the background, with
  touch input. The desktop base is kernel `mcfold1` (`a9/gpu-mcfold1` @ 85ad2697fd). It runs a full
  desktop session: the Omarchy wallpaper (swaybg), waybar, foot, squeekboard, server-side title bars
  and a tap menu (`docs/gpu/experiments/logs/mcfold1/desktop-screen.png`). The clients still draw on the
  CPU (wl_shm), and Labwc composites them on the GPU.
- **OpenGL ES 3.0 on hardware (2026-11-02).** With Mesa `g5p-es3@6531e682cd3` and kernel
  `a9/gpu-desk-next@cdbd3717af` (fault isolation + MRT), the iPad reports `OpenGL ES 3.0 Mesa 27.0.0-devel` /
  GLSL ES 3.00 through Zink, with no override and 0 ES 3.0 terms failing. Transform feedback and depth-stencil
  sampling are exact, and vkcube runs on the desktop.
  - Open: Zink EGL window clients stall after frame 0 (swapchain creation fails with OUT_OF_HOST_MEMORY; a
    regression vs the WSI-fix ICD); MRT target 0 is off by 1 LSB.
  - See docs/gpu/experiments/es3.md and logs/es3/.
- **Fault isolation with in-boot recovery (hardware, 2026-11-02):** a faulting client loses only its context, and the
  GPU recovers in ~21 ms (`render_user_recover=1`) while labwc keeps rendering.
- **GPU apps in real windows (2026-11-02).** vkcube (Vulkan) and weston-simple-egl (GLES via Zink) render on the
  GPU into their own Wayland windows, and labwc composites them through linux-dmabuf. The vkcube frame matches a CPU
  reference. The fix: a vertex shader's unified store is sized from the program, like Apple
  (`2·ceil(max(temps, inputs+2)/4)`); a hard-coded 4 left registers r8+ reading as 0. Screenshots:
  docs/gpu/experiments/logs/wsi-ladder/fix-20261102/.
- **A real GPU app beside the compositor.** A textured spinning cube (vkcube's scene through GLES2 → Zink →
  our Vulkan driver), running as its own GPU client next to Labwc, at 9–12 fps. Frame 30 matched a CPU
  reference, with no faults, and Labwc kept running. The GPU was at 340 MHz (pstate 1); nothing on Linux raises it yet.
- **Vertex-shader memory stores (the basis of transform feedback, Asahi's way):** 4-byte stores land; the
  16-byte store form is silently dropped in vertex tasks. Records must be written as 4-byte stores.
  Vertex shaders above 12 temps wedge the tiler, so the driver refuses them at pipeline creation.
- **Render jobs are reliable.** Dozens of jobs in a row complete without error, fault or hang.
- **Memory map changes between jobs.** Buffers can be bound and unbound after jobs have run. The kernel
  only edits page tables in RAM and publishes a new tag, and the firmware picks it up at the next submit.
  It never touches GPU registers after a job retires.
- **Memory accounting.** A buffer mapped twice (GBM allocation and then the EGL import of the same dma-buf)
  pins its memory once. The per-app address space limit is 1 GiB and the memory limit 768 MiB. The
  Vulkan driver's memory pool grows on demand, so no fixed pool size is needed.
- **Exact rendering.** Full-panel (1536x2048) frames match a CPU reference pixel for pixel, and so do
  mixed opaque and blended draws.
- **Present path.** GPU buffers are exported as dma-bufs with fences and shown through simpledrm (which
  copies; there is no zero-copy scanout).
- **Long-run stability.** The firmware heartbeat runs for over an hour without failures.

## Problems (owner priority order)

1. **Desktop freeze after a few hundred frames: FIXED on hardware (6301-frame soak); a leak in our
   Vulkan driver still limits a session without the eager-arena stopgap.**
   - **Freeze, before:** labwc froze twice on an ordinary frame (seq 251 after T1, seq 339 with one
     client). The TA wait timed out after 60 s even though the firmware had posted its TA-complete event
     7–8 ms after publish. Both began as the owner started touching the screen.
   - **Freeze fix on hardware (tahang1a, boot 6e19072d, S1 touch-heavy soak):** 1262 good frames, 0 job
     errors, no timeout. 21 frames were completed by the new event rule; each would have frozen before.
     Frame gap median 0.254 s, peak 17 fps, per-frame GPU time median 66 ms (p90 255 ms).
   - **Image C on hardware (tahang1c, boot 6c7d7c70, S3):** 6301 good frames, with touch play, CPU/IRQ
     stress on both cores and a 15 keys/s burst. No timeout. 121 TAs were completed by the event rule;
     no 3D needed the 500 ms hold. Frame gap median **0.113 s**, peak **20 fps**, per-frame GPU time
     median **52 ms, p90 59 ms** (from 255 ms: completing the 3D on its event removed the long tail).
     The owner reports it's much smoother, and dragging windows stays smooth with both cores busy.
   - **New stop: a per-frame leak in our Vulkan driver (ICD), not the GPU.** At frame 1262 the driver's
     64 MiB memory arena was full (~53 KB leaked per frame). Allocations were refused, and labwc stopped
     drawing. Lead: descriptor pools never reuse GPU space for freed sets, and the arena refuses to grow
     after its first submit. A fix is being worked on (branch `g5p-arena-fix`,
     `docs/gpu/experiments/arenafix.md`). Stopgap: an eager 192 MiB arena, which lasted all 6301 frames
     of S3, so the leak with that mix is ≤ ~31 KB/frame.
   - **Next desktop kernel: `deskfold-f410c38330`** (image C + the mcfold2 kexec quiesce,
     `docs/gpu/experiments/deskfold.md`), verified offline, flying next.
   - **Cause of the freeze (`docs/gpu/experiments/tahang1.md`):** not a lost wakeup. The kernel declared a TA done only
     once the TA idle words (`G+0x724/0x728/0x72c`) read zero. The firmware sometimes leaves one set
     after finishing; it's bookkeeping that it clears at the next engine start. Everything else (stamps,
     ring, PM save) had completed. About 1 in 5 good frames showed the word set briefly; twice it never
     cleared. The 3D phase had the same bug long ago and got an event/time rule; the TA never did.
   - **Images:** `a9/gpu-tahang1`. Image A `tahang1a-ed4afeb59c` (fix only; passed S1). Image
     B `tahang1b-f9190e2a6a` (fix plus 3D completion on the firmware event). Image C
     `tahang1c-c78c0dd899` (B plus a 500 ms fallback for the 3D, so a lost 3D event can't freeze labwc).
     C replaces B for the fps test. The TA now completes on
     coherent stamps plus saved PM state plus any one of: idle words zero, the TA-complete event, or
     500 ms of continuous coherence. The test is a touch-heavy soak of ≥1000 frames.
   - Desktop speed on mcfold1, for reference: median frame gap 0.575 s, peak 8 fps, median per-frame
     GPU time 76 ms (p90 274 ms).
   - Queued, in order:
     1. 3D completion on the firmware event, removing the 200 ms retire settle: DONE (image C);
     2. partial damage / smaller render areas in the ICD, so labwc stops redrawing all 1536x2048 every
        frame (Apple's background-load program is now captured; Mesa branch `g5p-partial` in progress);
     3. GPU DVFS the Apple way (474/550/723 MHz);
     4. more than one frame in flight.
2. **Slow frames: FIXED for now with an interim workaround; the Apple-way fix is still open.**
   - Was: the firmware picked up a job within milliseconds, but the job only finished once something
     else caused memory traffic. With nothing else running, frames came on a ~20 s cycle (key to frame:
     median 7.85 s, max 27 s).
   - Cause, proven on hardware (latency123 boot 1): the firmware waits on its own root and shared pages,
     and doesn't see the update until the system-level cache line is evicted. A Normal-NC read of those
     pages from Linux makes it visible. Flushing the GPU TLBs (5.6 s), cleaning the CPU caches (3.2 s)
     or a bulk cacheable read (worse) don't fix it.
   - **Interim fix (`g5p_rv3_fw_touch()`, kernel mcfold1):** an NC read of the firmware pages, only
     while a job phase or a context bind is in flight; nothing runs while the GPU is idle. On hardware
     (mcfold1, polling off): key to frame **median 0.07 s, max 0.08 s**.
   - **Apple-way fix, still open:** iOS sets the full pstate table and the power-policy words.
     (`meta+0x6d4` was assumed to be 1; the live capture shows 0.) With `6d4 = 1` plus `policy+4 = 16`, the
     firmware accepts the first job and never starts it (boots 2 and 3). The RE
     (`docs/gpu/firmware-re/power-words-init-re.md`) blames `policy+4`: it arms the clock governor, whose
     window counts the GPU counter `R0+0x160`, which may be frozen on Linux.
   - **Bisect boot 1 (2026-11-01, latency123-405d9b96b9, `fw_meta6d4=1` only):** `6d4 = 1` alone does
     not wedge, so boots 2/3 wedged on `policy+4`. It also does not fix latency: with the interim touch
     off, key to frame was median 6.05 s, max 20.43 s (the 20 s sawtooth is back). With the touch on it
     stays at 0.07 s. Live iOS values (power capture 20261101T154856Z): `policy+4 = 16`,
     `meta+0x71c = 340`, and under stress the GPU sits in P4 = 723 MHz 72 % of the time.
   - **RE result (`docs/gpu/firmware-re/r0-160-counter-re.md`):**
     - `R0+0x160` is `ROGUE_CR_TIMER` and is not frozen. The governor window is the AIC timer.
     - The live iOS capture shows **`meta+0x6d4 = 0` in every sample**, so setting it is not the
       Apple way.
     - What Linux lacks is `policy+0x34` (0 on Linux, 1..4 live on iOS) plus the coefficient
       block. Linux also never leaves pstate 1 (340 MHz).
   - **Next:** T2, with no build: `fw_policy4=16 boot_fw_words=4a:34=1`, to unwedge. Then T3, the
     full live iOS set.
3. **Several GPU clients: stage 1 works on hardware; still root-only and never freed.**
   - **Design:** each render context gets its own VM and firmware TA/3D slot pair. The pair is bound at the
     context's first job by a DC3 command written to RAM only, with no doorbell and no MMIO. Jobs stay
     serialized on one shared channel. See `docs/gpu/multi-client-design.md`.
   - **On hardware:** labwc plus five test clients, two of them concurrent. Every frame was exact, every
     bind was consumed in 1–3 ms, the jobs interleaved, and the labwc window was unchanged. Repeated on
     mcfold1 with the latency fix and polling off (b1, then b4 ∥ b5): all exact, binds consumed in 0.8–2.5 ms.
   - **Stages 2+3 on hardware** (`a9/gpu-multiclient2@995d684abd`, first boot):
     - A closed client's slot pair, VM and pinned memory are freed, and its slots are reused only after a
       verified RAM-only unbind. In 26 open/close cycles, pinned memory returned to baseline every time.
     - Contexts are scheduled round-robin.
     - Non-root clients work: CAP_SYS_RAWIO is dropped, and the firmware window in user VMs is narrowed.
     - A stuck client job loses only its own context: the channel drains, held jobs resume, and labwc
       keeps rendering.
   - **Still open:**
     - GPU Wayland clients composited by labwc (linux-dmabuf): the second boot is running.
     - In-boot GPU recovery: built, off by default, not yet run on hardware.
     - ~~A warm kexec out of a multi-client boot.~~ **FIXED on hardware (deskfold, 2026-11-01).** The
       quiesce (`a9/gpu-mcfold2@a2ad7938e6`: unbind every slot RAM-only, then power-cycle the GPU before
       kexec/reboot; `docs/gpu/experiments/mcfold2-kexec.md`) passed both paths:
       - hop 2, manual: `unbind why=kexec slots=12 verified=12 ret=0`;
       - hop 3, automatic via the reboot notifier: `slots=6 verified=6 ret=0`.
       Both new boots came up `origin=warm-cycled failed=0` with labwc restored
       (`docs/gpu/experiments/logs/deskfold/`). **Rule (updated):** on deskfold and later, a warm kexec is fine
       after multi-client and even after a wedged, failed device. The first case was the vkcube TA
       fault on boot e46d5121: `GPU-KQ why=reboot-notifier … failed=1 wedged=1 … plan=-+cycle skip=0x18`
       skipped the detach and power-cycled, and boot eb8fab4a came up `origin=warm-cycled failed=0`.
       DFU is only needed if the warm hop itself fails.
       - Deskfold's bounded dirty start also recovers a source kernel **without** the quiesce. The
         wedged T2 governor boot (latency123, `ta=timeout/60000`) hopped into deskfold as boot
         35223fa8: `origin=warm-cycled failed=0`, labwc rendering.
       - Warm recoveries into deskfold on 2026-11-01: the multi-client hop (manual and automatic),
         two client TA faults (vkcube-a9, XFB diag `hold`) and one governor wedge (T2). Before this fix, a kexec from the T1 boot into mcfold1 never printed a line, because the
       firmware kept running from the old RAM.
4. **Sheared text and textures: FIXED (verified on hardware).**
   - Symptom: textures whose width wasn't a multiple of 16 were sheared row by row (smeared title text,
     missing terminal content).
   - Cause: the texture descriptor's stride-valid flag (bit 63 of word 1) was only set for render
     targets, so the GPU rounded every texture pitch up to 64 bytes.
   - Fix: linear image rows are aligned to 64 bytes (`G5P_LINEAR_STRIDE_ALIGN`, as Asahi does), and
     copies go through Mesa's `vk_image_buffer_copy_layout()`. It lives on the Mesa fork branch
     `g5p-upload-fix`.
   - Device ICD `a2f00cda…` with trampoline `10232194…`. Foot, the title text and a 500 px window all
     render correctly.

## Not supported yet

- **GPU Wayland clients in the desktop: work on hardware (2026-11-01).** labwc runs on the WSI ICD pair
  (`g5p-wsi`, ac0460f9/3387d083) with linux-dmabuf ON. wsi-vk and egl-wl (Zink) render on the GPU and are
  composited zero-copy (600 frames, bad=0, the window changes on screen). Open items: vkcube-wsi draws
  only its clear colour (being diagnosed), and labwc's sealed arena runs out with many windows (13
  terminals froze it; arena fix in progress, `docs/gpu/experiments/arenafix.md`).
- **Damage-only redraw is the desktop mode** (no `rerender`), and the owner sees no stale rectangles.
  While interacting: frame gap median 0.059 s (was 0.113), GPU time per frame median 47 ms, p90 54 ms.
- **Partial redraws (damage tracking): work on hardware; now in use** (see above). loadOp LOAD (Apple's
  background-load program, captured and decoded on iOS) and partial render areas are on Mesa branch
  `g5p-partial` (`docs/gpu/experiments/partial-redraw.md`). On hardware the fixtures are exact, and labwc
  redraws only damage with no stale rectangles (commit `867b13dd`); per-frame GPU time 52 → 41 ms. The
  soak isn't qualified yet (no frame counter in that run), so the desktop still uses
  `WLR_SCENE_DEBUG_DAMAGE=rerender`.
- **GLES 3.0, which Hyprland needs.** Remaining gaps are tracked in `docs/gpu/experiments/gles3-gap.md`
  (in progress). Lanes: depth/stencil + 4× MSAA (`g5p-depth`) and formats/textures/samplers/instancing/
  queries (`g5p-gles3-tex`). Their input is the iOS captures of 2026-11-01, run `20261101T175626Z`
  (gles3, 23 scenes, plus gles3b, 45 scenes).
  - **Depth and stencil: DONE on hardware** (`g5p-depth@ed5f13dabed`, `docs/gpu/experiments/depth-msaa.md`).
    D16/D32F/S8/D32F_S8 work, as do the depth test, depth and stencil reload across passes, and depth bias,
    all 18 jobs exact (build/gpu/depth/device-20261101T212414Z). 4× MSAA is still refused: it needs
    kernel-written words.
  - **Multiple render targets: DONE on hardware.** 2 and 4 targets exact, including 4 from one varying
    (`docs/gpu/experiments/mrt.md`, `build/gpu/mrt/device-20261101T202412Z`, kernel `mrt1-96fa98d406`).
  - **Transform feedback: works on hardware** with 4-byte vertex stores (`g5p-xfb-fix`): record,
    overflow and discard are `XFB_EXACT`.
  - **Open bug, wider than XFB:** a vertex program that both fetches an attribute and writes the
    vertex/instance index wedges the TA, even for 3 vertices with no capture (ladder rung r1,
    `docs/gpu/experiments/xfb.md`). This hits any shader using `gl_VertexIndex`/`gl_InstanceIndex` with
    attributes. Fix in progress (`g5p-vpds`) from Apple's instancing captures.
- **In-boot GPU recovery works on a real wedge** (off by default). With `render_user_recover=1`, the r1
  wedge lost only its own context, the firmware restarted in the same boot in 20 ms (`GPU-RECOVER …
  ret=0`), and a new client attached and rendered exactly. Not yet checked: that labwc's next frame
  re-attaches. Once it is, turn recovery on by default.
- **Shader ISA matches A10:** G5P (A9) and Hendrik's G9P (A10) produce identical words for every
  instruction form compared (`docs/gpu/hendrik/a9-reply-20261102.md`), so one compiler can serve both.
- **Hyprland on the GPU.** Hyprland still runs on llvmpipe (CPU).

## Standards (owner's rule)

Follow Mesa and Asahi (Apple AGX) driver standards and architecture as closely as possible.

- **Kernel:**
  - use the DRM scheduler (`drm_sched`) for jobs and `drm_gpuvm` for VMs;
  - explicit `VM_BIND`, with syncobj and dma-fence for synchronization;
  - shape the UAPI like Asahi's `asahi_drm.h` or mainline powervr's `pvr_drm.h`;
  - kernel coding style, checkpatch-clean;
  - no new default-off experiment variants in production paths.
- **Mesa:** follow NIR compiler conventions, use the common Vulkan runtime (`vk_*`) helpers, and keep the in-tree structure. Use
  Asahi's drivers as the design reference.
- **Where Asahi already solves something, keep Asahi's way** (owner, 2026-11-01). Imagination's Rogue driver
  (`src/imagination/vulkan/pvr_*`, csbgen) is the reference for hardware encodings and for features Asahi has no answer to,
  because the A9 GPU is PowerVR Rogue (Series 7XT).
- **Upstream:** Mesa's `src/imagination` and Asahi decline AI-written contributions. Following their standards in our private fork is
  fine, but nothing goes upstream as AI-written code.

## Rules learned the hard way

- Never access GPU registers after a job retires. If the block is power-gated, the whole SoC hangs and
  the iPad needs DFU.
- After a TA stall caused by the firmware power words (`meta+0x6d4` / `policy+4`), a warm kexec is NOT safe: the
  firmware stays wedged on the timed-out job, and the next kernel never comes up. Recovery needs DFU. Only test
  those words when the owner can do a DFU.
  (Not yet retested with the kexec quiesce below.)
- Since the kexec quiesce (mcfold2 → deskfold → multiclient2), a warm kexec out of a multi-client boot works. On
  2026-11-01 it also worked out of a boot whose TA had faulted on a client job (XFB dup:
  `GPU-KQ … failed=1 wedged=1 plan=-+cycle`, new boot `origin=warm-cycled failed=0`). The
  `EXT4 Orphan file not empty` line at shutdown is unrelated: it also appears before kexecs that work.
- Only the main orchestrator runs anything on the iPad. Agents write exact test plans instead.
- The live desktop session is valuable. Don't propose anything that ends it unless it's needed.
- Keep `PVR_G5P_GROUPS=last-read` and `WLR_SCENE_DEBUG_DAMAGE=rerender` in the session environment. Don't
  use the old `LD_PRELOAD` arena shim.
