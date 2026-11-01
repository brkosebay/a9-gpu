# Transform feedback (XFB) for the G5P Vulkan driver

Status 2026-11-01: diag2 on hardware showed that the **16-byte** vertex store is the silent no-op, while 4-byte stores
land. A vertex shader above 12 temps **wedges the TA**. The fix (records as 4-byte stores, Mesa `g5p-xfb-fix`) works on
hardware (run 3: record, overflow and discard are `XFB_EXACT`), but run 3's 3000-vertex `dup` draw stalled the TA. The
ladder's first rung pinned it on the vertex PDS: a DDMAD followed by an index DOUTW wedges the TA. Apple sets LAST on
the final DDMAD as well as the final DOUTW; we followed Rogue and cleared it. The fix (Mesa `g5p-vpds`, which also raises
the vertex-input limit from 2 to 8 and lifts the 12-temp vertex limit, vertex-temps.md (`vertex-temps.md`, not published)) waits for the
device; see
[Vertex PDS: Apple's LAST rule](#vertex-pds-apples-last-rule-g5p-vpds) at the end. Mesa fork branch `g5p-xfb`
(3 commits on `g5p-upload-fix` 5c2eb7a6d4b: `aed010a323e`, `0310c029098`, `764994cda0b`; worktree
`build/third_party/mesa-a9-xfb-src`). Recipe: `scripts/gpu/xfb.sh all`.

## What was built

Mesa's software XFB route, as Panfrost does it. While capturing, a draw uses a second variant of the vertex shader, compiled
with `nir_lower_xfb_to_stores` and outputs kept: capture and rasterization happen in the same draw. Metal has no XFB, so
there is no Apple stream-out to copy. Everything else runs on the CPU at execution time, in command order, like the
driver's copies already do (the queue waits for every job). The semantics follow lavapipe:

- Begin and End read and write the counter buffers. A counter holds the bytes written from the binding offset.
- Each captured draw gets its record addresses patched into a per-draw driver block (`PCO_G5P_UPLOAD_XFB`) just before
  the job is submitted. The first vertex is folded into those addresses, so the shader subtracts nothing.
- **Overflow:** a draw whose whole primitives don't all fit (the buffer with the least room decides), or whose last
  primitive is incomplete, stores into a staging BO. The CPU then copies only the primitives that fit, and only the
  record ranges, so stride padding is never touched.
- `vkCmdDrawIndirectByteCountEXT` reads its counter, and the vertex count is patched into the VDM index list.
- Stream queries (`VK_QUERY_TYPE_TRANSFORM_FEEDBACK_STREAM_EXT`) count primitives written and needed. Query pools
  were refused before this change.
- **Rasterizer discard,** static or through `VK_EXT_extended_dynamic_state2`, uses the Rogue rule: ISPCTL tag write
  disable, no depth, no scissor. A draw with an empty scissor that still has to capture uses the same path.
- **Vertex PDS:** it gains the vertex and instance index DOUTWs. A VS without attributes now gets Apple's captured
  fetch-less PDS where it used to be refused.
- **Capability:** SPIR-V `TransformFeedback`.
- **Advertised:** `VK_EXT_transform_feedback` with the spec minimums (4 buffers, 1 stream, 512-byte stride),
  `transformFeedbackQueries` and `transformFeedbackDraw`; `VK_EXT_extended_dynamic_state2`; `VK_EXT_host_query_reset`.

**Refused (loud, `G5P_REFUSE_WHY`):** capturing an indexed draw or a strip or fan topology. Per-invocation stores follow
vertex invocations, and with an index buffer or a strip those are not the primitive stream (a vertex is shaded once but
appears in several primitives). GLES 3.0 never issues these: while capturing it allows only `glDrawArrays` with the
matching list mode. `POINT_LIST` was never supported by the recorder, and `firstInstance ≠ 0` is still refused.

**Duplicate stores (the open hardware question):** the design doesn't depend on the answer. A store's address and data
depend only on the vertex (index, instance, uniforms, attributes), and no counter lives on the GPU. If the TA shades a
vertex twice, the second store rewrites the same bytes. The device test still measures it (case `dup`).

**Kernel:** no change. Capture buffers and the staging BO are arena slices. The job already names their backing BO
READ|WRITE for the colour target, so the resource list doesn't grow. The validator accepts WRITE whenever the BO has
`DRM_G5P_BO_WRITE` (`patches/gpu-render/0002…`, resource loop), and the transport syncs written BOs back after the job.

**Trampoline:** `tools/mesa-a9-production117` gains two opt-in macros. With neither, the build is the device's
`081885cc…` byte for byte.
- `G5P117_VM_EDIT_SHIFT`: the three VM-edit guards in `g5p_winsys.c` moved 224 bytes in the new ICD
  (`tools/mesa-a9-xfb/vm-edit-shift.py` measures it and checks it is one unique shift).
- `G5P117_ACCEPT_DISCARD`: its ISP-block check also accepts the tag-write-disable ISPCTL `0x03e40000`.

## Encodings (Apple's applegpu_g5p compiler)

Probe sources are in `tools/mesa-a9-xfb-re/probes.metal` and the build is `compile-probes.sh` (37 vertex probes; round 2
added `v_st_gather`, `v_iadd_lit_mid`, `v_shl4` and `v_st_stride12`). Output goes to `build/gpu-userspace/xfb-re{,2}/`
and is never checked in. The encoders are `pco/g5p_usc.c`. `g5p-xfb-test` checks 38 forms byte for byte against these
probes.

| Form | Bytes | Fields | Probes |
|---|---|---|---|
| store | `10 BB DD AA 00 88` | BB = `0x23 + 8(n−1)` for 4/8/12/16 bytes from consecutive registers; DD = `0x80 \| data half`; AA = `addr half << 1 \| 1` (a 64-bit register pair); b4 = immediate offset in 4-byte units for 4-byte stores (unused) | v_st_vid, v_st_u2, v_st_u3, v_st_rec, v_st_bytes, v_st_stride12 |
| 64-bit address add | `89 11 DD 03 SS OO XX 41 a1 01 00 TT` | dst pair = shared pointer pair (SS/XX: dword, even) + (32-bit reg OO << s); TT = `s << 5 \| 6`, s = 0..4 | v_st_vid, v_st_two, v_st_xfb, v_st_gather |
| vertex index | PDS `DOUTW t0:t1, c` with control `0x80000000` (lower 32) | Apple's captured `f0000a01 fc000002 d3000000` (out/render-post; also in the round-16 template's code arena) | codex-run18 §2.2 |
| instance index | the same DOUTW, `bsize` upper 32 | USC code identical to the vertex-index probe; the PDS picks the half (Rogue `pvr_pds_vertex_shader`) | v_vid_f = v_iid_f |
| id registers | Apple: vertex r0, instance r1 without attributes; with attributes, right after them (gles3 `instanced`: instance → r5 after r0..r4). Ours: the same. LAST goes on the final DDMAD **and** the final DOUTW (Apple's captures; Rogue's "DDMAD loses LAST" wedges A9, see [Vertex PDS](#vertex-pds-apples-last-rule-g5p-vpds)) | | v_st_xfb, v_st_gather, gles3 `instanced` |
| iadd/imul register pair | `99 31 DD 03 AA BB a1/a2 00` | dying operand in B | v_iadd_rr, v_imul_rr |
| iadd/imul shared operand | `99 11 DD 03 SS BB XX 41 a1/a2 00` | | v_iadd_k, v_imul_k |
| iadd literal | short prefix + `a1 09 70 00` up to 0xffff (bit 9 in header bit 5); long prefix + `a1 01 70 00` above | | v_iadd_lit, v_iadd_lit_mid, v_iadd_lit_big |
| x << s (multiply by 2^s) | `99 11 DD 03 01 SS 20 01 a1 05 00 (s<<5)` | | v_shl4, v_imul_lit* |
| register move (gather) | `98 21 DD 83 SS 01 54 01 38 00` | | v_st_gather |
| guarded store (not used) | compare `85/81`, mask push `c8 … d1 46`, `br.cond`, store, pop `c8 … d2 46` | operand order ambiguous between the two probes | v_st_cond, v_st_cond_xfb2 |

Our XFB VS for `xfb-rec.vert`: `imul` by the shared vertex count, `iadd` with the vertex index, `<< 4`, gather into
r5..r8, address add from block dword 2, then one 16-byte store (262 bytes in all).

## Offline results (`scripts/gpu/xfb.sh all`)

- **Control:** the branch base through this recipe gives the device ICD `a2f00cda…`, and the trampoline sources give
  `081885cc…`.
- **`g5p-xfb-test`: 181 checks, 0 failures.**
  - Encoders checked against Apple's probe bytes.
  - The compiled XFB variants: buffers, strides, store widths, id registers, block alignment.
  - The resolve over simulated stores, using exactly the address formula the shader computes: fit, overflow,
    firstVertex 3, stride padding, padding plus overflow, an incomplete primitive, resume, resume-full, lines,
    byte count, instanced, instanced overflow, instanced with an incomplete primitive (with and without overflow:
    staging holds each instance's tail, the copy takes each instance's complete primitives). The query totals are
    checked too.
- **`oracles.py xfb`** (new CLI, now per primitive): the 10 exported resolve cases are `XFB_EXACT`.
- **Regression:** all 30 round17–19 shaders compile to identical bytes (old vs new compiler), and all 315
  `g5p-gfx-test` scene files are identical.
- **`xfb-test --offline` over the typed-V3 fake node:** all 8 cases `PASS-OFFLINE` with the ICD directly and through
  the packaged trampoline. The trampoline run shows `G5P117_INIT result=ok hooks=6`, three VM-edit patches, and 11/11
  typed submits classified `pass` (the discard ISPCTL included); the fake node shows `refused=0`.
- **Vertex PDS bytes, read from the job dumps** (`PVR_G5P_TRACE_DIR`): our two-index program is
  `f0000a01 f0000e01 fc000002 d3000000`, data `00004000 00000018 00000000 80002001` (DOUTU at c0:1, vertex index →
  r0 lower, instance index → r1 upper and last). The first word is Apple's. The vertex-index-only form is the same
  packer with one DOUTW (`f0000a01 fc000002 d3000000`, control `0x80000000` at c2), which is Apple's capture.
  Hardware has not checked either.
- **Zink** (`xfb.sh zink`: uploadfix's `uf-gl`, GLES2 via Zink and GBM, on the mesa117 stage with the new pair):
  `result=PASS` with the ICD directly and through the trampoline. This covers advertising EDS2, transform feedback
  and host query reset; it does not exercise capture from GL.

## Artifacts

`build/gpu/xfb/package/xfb-a9.tar.gz` `7dcce2f2892c026df406b3eb4d4c8711ae00a4077cb1f7addffc81da8989ffd0`

| File | sha256 |
|---|---|
| `libvulkan_g5p.so` (ICD) | `b4273e98cbda5a342cd8a428f93112dcb5eca3725c8e0bceed5c99a856c609f0` |
| `libvulkan_g5p117.so` (trampoline, pins that ICD) | `3b1b8e93b61dbfde2c576c220fce6cf924c1d8c9219d7e54a352b4fd7cc98614` |
| `xfb-test` (device client, `tools/mesa-a9-xfb/xfb-test.c`) | `2452549bf783e15851e0b63b7e87bf6ec1a244c41b59977c76f7e0ac4e391abe` |

Swap the ICD and trampoline together, or neither. For this test they stay in `/tmp/xfb-a9` and the session's files are
not touched.

## Device test plan (orchestrator)

**Kernel and timing.** Run it on the current desktop kernel (mcfold1) as an extra GPU client beside labwc (multi-client
stage 1). No restart is needed. **It must be the last GPU test of the boot.** Until mcfold2's kexec fix has flown, follow
it with a cold boot or a DFU, never a warm kexec.

**Variables.**
- `SSH=scripts/omarchy/ssh.sh`.
- `RN` is the session's render node (`LABWC_A9_RENDER_NODE`, normally `/dev/dri/renderD128`).
- Kill only PIDs you started, and never `qemu-nbd`.

**0. Upload (no GPU).**
```sh
$SSH 'cat > /tmp/xfb-a9.tar.gz' < build/gpu/xfb/package/xfb-a9.tar.gz
$SSH 'cd /tmp && sha256sum xfb-a9.tar.gz && tar xzf xfb-a9.tar.gz && cd xfb-a9 && sha256sum -c SHA256SUMS &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/xfb-a9/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json && mkdir -p out'
```
Expect `7dcce2f2…` and three `OK` lines.

**1. First case alone: `rec`.** This is the first vertex-shader store, the first DOUTW vertex index and the first
fetch-less PDS on hardware.
```sh
run() { $SSH "cd /tmp/xfb-a9 && sudo -n env -i PATH=/usr/bin:/bin VK_DRIVER_FILES=/tmp/xfb-a9/icd.json PVR_G5P_RENDER_NODE=$RN \
  timeout -s KILL 300 ./xfb-test $* /tmp/xfb-a9/out >/tmp/xfb-a9/run.txt 2>&1; echo rc=\$?; cat /tmp/xfb-a9/run.txt"; }
run --case=rec
```
Want `G5P117_INIT … result=ok`, `case=rec records=6 counter=96 result=PASS` and `rc=0`. If it fails, stop and keep
`dmesg | tail -80`.

**2. The rest of the suite**, if step 1 passed: `run` with no case. Every case prints one line; `result=PASS` and `rc=0`
are wanted. `dup` prints `PASS-DUPLICATES` or `PASS-NO-DUPLICATES`, and both are fine (see below).

**3. Collect and check.**
```sh
mkdir -p build/gpu/xfb/device && $SSH 'cd /tmp/xfb-a9 && tar cf - run.txt out' | tar -C build/gpu/xfb/device -xf -
python3 -I tools/mesa-a9-loadop-re/oracles.py xfb build/gpu/xfb/device/out
```
Every line should be `XFB_EXACT`.

**The cases:**

| Case | What it proves | Want |
|---|---|---|
| `rec` | VS store, vertex index, fetch-less PDS, direct path | 6 records `(id, ~id, float(id), 1.0)` from byte 32, counter 96; every other byte `a5` |
| `first3` | the hardware index includes firstVertex (the fold in the addresses) | records for ids 3..8 at slots 0..5 |
| `overflow` | bounded buffer: staging + CPU copy, per-primitive clamp, query | 48-byte binding: 3 records, counter 48, query written 1 / needed 2; `a5` from byte 80 |
| `discard` | rasterizer discard (tag write disable), the trampoline accepts it | two triangles covering the target: with discard, records and positions captured and `pixels_with=0`; the same draw without discard (control) `pixels_without=4096` |
| `instanced` | instance index DOUTW (upper 32), instance-major order | `(id, instance, id+7, 1.0)` × 2 instances |
| `attr` | id registers after an attribute; two buffers; stride padding | buffer 0 records; buffer 1 positions at stride 32, padding `a5` |
| `resume` | End/Begin through a counter across render passes; DrawIndirectByteCount | 6 contiguous records; byte-count draw recaptures 6 |
| `dup` | duplicate detection: capture memory also bound as the vertex buffer, 3000 vertices across VDM splits | `twice`/`more` = vertices re-shaded after their first store landed |

**Reading the results:**

| Result | Meaning | Next |
|---|---|---|
| all `PASS`, `dup` `NO-DUPLICATES` | XFB works; no sequential re-shading seen | swap the pair into the session (Zink sees the extensions); next ES 3.0 blocker (MRT) |
| `discard` control has 0 pixels | the covering draw doesn't rasterize at all, so the discard result means nothing | check the positions capture (`discard-control-pos`) |
| `dup` `twice > 0` | the TA re-shades some vertices (e.g. at splits) | nothing to fix: stores are idempotent. Record which vertices (`first_dup`) for the TA notes |
| `first3` records 0..5 where 3..8 were wanted, `rec` OK | the VDM index offset doesn't reach the PDS index | add the first vertex in the shader (raw = index − first, a shared operand) instead of the fold |
| `rec` buffer all `a5`, counter right | stores didn't land: store form, address add or block address | fault in dmesg? Compare the VA in the block (`PVR_G5P_TRACE_DIR` dump) with the binding |
| `rec` records shifted by a whole record, or garbage in r0 | the DOUTW index register or its half | check `id_reg` and the control word (`0x80000000` / `0x80000001`) |
| `instanced` instance column 0 | the upper half of t0:t1 isn't the instance on A9 | check the IR1 temp: DOUTW `t1` alone (`bsize` lower on a 32-bit source) |
| `overflow` only fails | staging allocation, its resource entry or the copy | the counter/query lines show whether the CPU side got there |
| GPU fault or job timeout | a wrong store address | the session may be wedged: cold boot or DFU (as planned anyway) |

`dup`'s blind spot: a vertex shaded twice inside one fetch batch reads the stale 0 and is invisible. That gives only
false negatives, never false positives. It doesn't matter for XFB correctness either way (stores are idempotent).

## Device run 1: rec FAIL

**Run.** Post-hop deskfold boot `d9fe9d1f`, beside labwc, package `7dcce2f2…` (ICD `b4273e98…`, trampoline `3b1b8e93…`),
`run --case=rec`. Artifacts: `build/gpu/xfb/device/{run.txt,out/xfb-rec.{bin,json}}`, `build/gpu/xfb/rec-fail-dmesg.txt`.

- Trampoline: `G5P117_INIT result=ok`, three VM-edit patches, `G5P117_PASSTYPE job=1 packets=24 isp_blocks=1 opaque=1`;
  the one ISP block is the fragment shader (`usc=0x300004140 len=32`, user arena +0x140; the VS is at +0x0).
- Kernel: `RENDER-V3 bind slots=12/11 ret=0`, `end seq=698 ret=0 ta=complete/7 td=complete/9`, `retire … how=idle-clear`.
  No fault, refusal or timeout line. labwc unaffected (jobs 699+ normal).
- Result: `case=rec bad_bytes=96 first_bad=32 (got 0xa5)`. **All 480 dumped bytes are `a5`**: nothing landed in the binding
  and nothing landed in the 32 bytes before it or the 384 after it.
- `counter=96` (json) is right, but it **is not GPU evidence**. The counter is written by the CPU at End (`write32`,
  `g5p_xfb.c`) from the CPU-computed fit; there is no GPU counter. The counter path is separate from the record
  stores, so a correct counter is expected even when no store ran.

**What the offline job shows** (the same ICD, `PVR_G5P_TRACE_DIR` + the new submit-time debug, `build/gpu/xfb-diag`):

| Piece | Value | Agrees? |
|---|---|---|
| XFB VS (user +0x0, 262 B) | `… addr.add r9:r10 = sh2:sh3 + r1` ; `st.16 [r9] <- r5..r8` | encodings = the probes (181 checks) |
| vertex uniform loader | `20040001 data=state+0x2c0 code=user+0x240`, DOUTD src `0x3000302d0`, control `9000400a` = common ao 2, 10 dwords, last | the shader reads sh2:3 (address) and sh10 (vertex count) = block dwords 0..1 and 8 |
| per-draw block at submit | `addr0=0x102008020 num_vertices=6` (`G5P_XFB_GPUBLOCK`, read back from the uploaded arena) | = binding VA (`match=yes`) |
| fetch PDS (user +0x180) | `f0000a01 f0000e01 fc000002 d3000000`, data `00004000 00000018 00000000 80002001` | DOUTU temps 12 (r0..r11 cover r9:r10); DOUTWs t0 lower→ao0, t0:t1 upper→ao1 last |
| residency | store range `0x102008020..0x102008080` inside the arena BO `0x102000000+64 MiB`, roles `READ|WRITE` | the same BO as the colour target the PBE writes in the same job |

**Ranked candidates.**

1. **C1, the store never executes, or it goes to another mapped VA (most likely).** The offline chain above is
   self-consistent, so what is left is what offline can't see:
   - *a. The VS doesn't run with the right index.* Everything on this path is new on hardware: the fetch-less vertex PDS,
     the two DOUTWs, and an XFB VS. **Run 1 can't tell whether the VS ran at all**, because `xfb-rec.vert` puts every
     vertex on y = 0, so the triangles are zero-area and no pixel depends on the VS. A garbage index would also
     have given a garbage address, and a fault would be expected unless that address happens to be mapped.
   - *b. The hardware drops vertex-stage stores without some state Apple sets.* The probes give USC bytes only. No iOS
     job with a storing vertex function was ever captured, so a VDM/TA enable bit can't be excluded.
   - *c. A wrong but mapped VA* (job arenas, the firmware region that is mapped into every VM) faults nowhere. The
     scan below finds the job-arena case.
   - *d. A register hazard (real, but secondary).* Apple never rewrites a store's data or address registers after the
     store unless a `csync` intervenes (`v_st_two`, `v_st_cond`, `v_st_cond_xfb2`). Ours does: `st.16 [r9] <- r5..r8`
     is followed by `mov.imm r5, 1.0`. With an asynchronous register read, that clobbers word 0 of each record
     (12 of 48 bytes in the cover draw). It doesn't explain 96 untouched bytes.
2. **C2, visibility (unlikely).** The GPU node has no `dma-coherent`, so the CPU mapping is write-combined
   (`shmem->map_wc`). User GPU PTEs are `CC | SLC bypass` (attrs `0xc`). The ICD syncs the whole BO TO_CPU after the
   job. The colour target in the same BO reads back fine in every other test. One mechanism remains open: the SoC SLC
   still holds the CPU's freshly written `a5` lines while the GPU's SLC-bypass writes go to DRAM. latency123
   found the same stale-SLC behaviour in the other direction (firmware reads). The 0xa5 prefill is exactly the data
   the CPU wrote just before the job.
3. **C3, not bound/resident (ruled out offline).** See the residency row. The arena is one BO with one VM_BIND
   (`vm_binds=1`), and the buffer is inside its committed chunk.

**`opaque=1`** is the passtype of the fragment shader's ISP state (every draw is OPAQUE). It is applied after vertex
shading. The TA must shade a vertex before it can cull a zero-area triangle, so the stores should still run. That
is not proven on A9, and `diag=rec` against `diag=vs-xfb` splits it.

### Diagnostic package (one run, beside labwc)

Mesa branch `g5p-xfb-diag` @ `af27b9ab590` (worktree `build/third_party/mesa-a9-xfb-diag-src`; recipe
`XFB_LANE=diag scripts/gpu/xfb.sh build test client repro tramp package repro-tramp`, all green: 181 checks, 10
`XFB_EXACT`, 30/30 shaders and 315 scenes identical, the default trampoline = `081885cc`).

- **ICD**, opt-in by environment. Without the variables it's the baseline: same shader bytes, same scenes.
  - `PVR_G5P_XFB_DEBUG` prints `G5P_XFB_BLOCK` per captured draw: the block VA, the record address, `num_vertices`, the
    binding VA, `match=` and `resident=`.
  - `PVR_G5P_XFB_SCAN` prints `G5P_XFB_GPUBLOCK` after each job (the block as GPU memory holds it). It then searches
    every job arena and resource BO for xfb-test's record shapes and prints `G5P_XFB_SCAN` (hits, first VA), ending
    with `G5P_XFB_SCAN_DONE`.
  - `PVR_G5P_XFB_HOLD` is read at pipeline creation. It keeps the store's registers reserved to the end of the VS.
- **Trampoline** gains the opt-in `-DG5P117_XFB_DIAG`, which passes those four knobs. Without it the trampoline seals
  every `PVR_G5P_*` and the ICD would see none of them.
- **Client** `xfb-test --diag` with the new `xfb-cover.vert`: a full-screen triangle `(-1,-1) (3,-1) (-1,3)` computed
  from `gl_VertexIndex` alone (fetch-less), with the `rec` record.

`build/gpu/xfb-diag/package/xfb-diag-a9.tar.gz` `15e3704fd66de7232c2575581a70aec90943b9d8c78b9a3bb19d897d3066feca`

| File | sha256 |
|---|---|
| `libvulkan_g5p.so` (diag ICD) | `83e17a6422c68a4201a517e682473390c06f4615da94886aae758a746d69a5fc` |
| `libvulkan_g5p117.so` (trampoline, pins it, `G5P117_XFB_DIAG`) | `05e8296eee0e0f764e3b83430fe448cecc389e63aa968e08d3b0e53751326acc` |
| `xfb-test` | `d4cf801cf68a23bcb005280d8c86a8ae133aa7560fd60c8fd07d0039e08493a2` |

Offline through the packaged trampoline, `PVR_G5P_XFB_HOLD` changes the code as intended (after the store, the
baseline cover VS rewrites r6, its data register; the hold variant uses r12). Every diag line ran (5 typed submits, `refused=0`, 5 `G5P_XFB_BLOCK` lines with
`match=yes resident=1`).

**Commands** (same rules as above: extra GPU client beside labwc, the last GPU test of the boot unless the deskfold
kexec quiesce is trusted; kill only PIDs you started).
```sh
SSH=scripts/omarchy/ssh.sh; RN=/dev/dri/renderD128   # the session's LABWC_A9_RENDER_NODE
$SSH 'cat > /tmp/xfb-diag-a9.tar.gz' < build/gpu/xfb-diag/package/xfb-diag-a9.tar.gz
$SSH 'cd /tmp && sha256sum xfb-diag-a9.tar.gz && tar xzf xfb-diag-a9.tar.gz && cd xfb-diag-a9 && sha256sum -c SHA256SUMS &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/xfb-diag-a9/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json && mkdir -p out'
$SSH "cd /tmp/xfb-diag-a9 && sudo -n env -i PATH=/usr/bin:/bin VK_DRIVER_FILES=/tmp/xfb-diag-a9/icd.json PVR_G5P_RENDER_NODE=$RN \
  PVR_G5P_XFB_DEBUG=1 PVR_G5P_XFB_SCAN=1 timeout -s KILL 300 ./xfb-test --diag /tmp/xfb-diag-a9/out >/tmp/xfb-diag-a9/run.txt 2>&1;
  echo rc=\$?; sudo -n dmesg | tail -300 >/tmp/xfb-diag-a9/dmesg.txt;
  grep -E '^diag|^case=|G5P_XFB_(BLOCK|GPUBLOCK|SCAN)|G5P117_INIT' /tmp/xfb-diag-a9/run.txt;
  grep -ciE 'fault|mmu' /tmp/xfb-diag-a9/dmesg.txt"
mkdir -p build/gpu/xfb-diag/device && $SSH 'cd /tmp/xfb-diag-a9 && tar cf - run.txt dmesg.txt out' | tar -C build/gpu/xfb-diag/device -xf -
```
Expect `15e3704f…` and three `OK` lines. rc is 1 if any capture fails; that's expected, and the lines are the result.
Five render jobs run (six if `diag=visible` adds its second job).

**Reading the lines** (in order):

| Line | Rules on | Reading |
|---|---|---|
| `G5P_XFB_BLOCK … match=yes resident=1` | C3, CPU-side address | `match=NO` = wrong patched address; `resident=0` = C3 |
| `G5P_XFB_GPUBLOCK addr0=… num_vertices=…` | the uploaded block | must equal the BLOCK line (it is read from the job arena after the job) |
| `G5P_XFB_SCAN where=… hits=N first_va=…` | C1c | records found at another VA: an address bug (delta = `first_va` − `bind_va`); no SCAN line, only `SCAN_DONE hits=0` = not in any client-visible BO. A hit whose `first=` hex starts `00000000ffffffff` is a mask in a state arena, not a record |
| `diag=rec stored=` | reproduces run 1 | `no` expected |
| `diag=vs-plain pixels=4096/4096 PASS` | C1a | FAIL (0 or partial) = the fetch-less PDS / vertex-index DOUTW doesn't run the VS with the right index |
| `diag=vs-xfb pixels= bad_bytes=` | C1 vs the rest | pixels 4096 = the store variant runs to its outputs; `bad_bytes` 48 = nothing landed, 12 = word 0 clobbered (C1d), 0 = works |
| `diag=visible … invalidate= sleep= thrash= second_job=` | C2 | printed only if records are missing. Any 0 = the GPU wrote and the CPU saw it late; the column says what made it visible |
| `diag=hold bad_bytes=` | C1d | 0 while `vs-xfb` failed = the register hazard; make HOLD the default |
| `diag=attr stored=` | the DDMAD path | `yes` while `rec` fails = the fetch-less path is at fault |
| `diag-verdict … verdict=` | summary | the first matching rule, in the order of this table |
| dmesg `fault`/`mmu` count | C1 wild address | non-zero = a store went to an unmapped VA (then the scan VA tells nothing) |

**Fastest fix per verdict.**
- `C1-vs`: give attribute-less XFB shaders a dummy fetched binding, so the proven DDMAD PDS runs and the ids follow
  the attribute (the `attr` form).
- `C1-hazard`: make the HOLD rule the compiler default.
- `draw-specific`: the TA culls zero-area primitives before vertex stores. Capture can't rely on degenerate draws (GL
  doesn't need them), so `rec` gets real positions.
- `C2-visibility`: read back through the mechanism the `visible` column names (e.g. an eviction/NC read in the ICD
  after captured jobs).
- `C1-store/C3` with no SCAN hit and no fault: the stores are dropped. Next: an iOS capture of a Metal vertex function
  that writes a device buffer (`tools/ios-gpu-probe`), to find the VDM/TA state Apple sets for vertex stores. The
  other route is the compute path's `st buffer[index]` form (class 0x12, `v_st_c3`).

No fix package is built yet. Offline didn't isolate one cause with confidence: the only definite bug (the hazard)
predicts 12 bad bytes, not 96.

## Device run 2 (diag): the VS store is a no-op; a 16-temp VS wedged the TA

Package `15e3704f…` on deskfold, beside labwc. Artifacts: `build/gpu/xfb-diag/device/{run.txt,dmesg.txt,out}`.

| Line | Result |
|---|---|
| `G5P_XFB_BLOCK` (all three captures) | `addr=0x102008020 = bind_va`, `match=yes resident=1`, inside the 64 MiB arena BO, roles 3 |
| `G5P_XFB_SCAN_DONE` (jobs 1 and 3) | `hits=0` in all 12 regions: the records are in no BO the client owns |
| `diag=rec` | `stored=no` (run 1 reproduced) |
| `diag=vs-plain` | **4096/4096 PASS**: the fetch-less PDS and the vertex-index DOUTW run the VS with the right index |
| `diag=vs-xfb` | **4096/4096 pixels, 48/48 bad bytes**: the store variant runs to its outputs; nothing lands |
| `diag=visible` | 48 after invalidate, sleep, 64 MiB thrash and a second job: **C2 ruled out** |
| `diag=hold` (job 5) | **TA wedge**: `slow … idle=00000001,00000001`, then `end seq=2283 ret=-5 phase=ta-wait ta=fault/45001` (the 45 s timeout, not an MMU report); device lost for the client; `attr` never ran |

### (c) Why `hold` wedged: the VS needed 16 temps

The baseline cover XFB VS and the `hold` VS are **both 326 bytes**. The only difference is register allocation. In
`hold`, the store's data run r6..r9 stays reserved, so the `mov.imm 1.0` after the store goes to **r12**. That
lifts `max_dword` to 13, which the compiler rounds to **16 temps** (DOUTU src0 temps field 3 → 4). The baseline needs
12. The same jobs, same block, same PDS, same FS ran clean twice with 12 temps and wedged with 16.

**vkcube-a9 shares the trait.** Its `G5P117_BLOCK … len=306` is the **fragment** shader (the ISP block's USC, 8
temps), but the TA phase runs its vertex shader, and that needs **20 temps** (`g5p-shaderc` on
`tools/gpu-demo/shaders/cube-a9.vert`: 518 bytes, temps 20, 12 outputs). Every vertex shader that has flown needs 8 or
12 (all round-17..19 VS: `depth`/`zink-pass`/`scale`/`uv` 8; `ab`/`hypr`/`two`/`mvp` 12; labwc's are in this set).
Above 12, two out of two wedged.

**Mechanism (inferred).** VDM_STATE5 is our fixed `0x1082`, so `vs_max_instances = 0` (32 instances per vertex task).
A 32-instance task of 16 or 20 temps asks for more unified store than the vertex partition can ever grant, so the TA
waits forever. Public Rogue would only stall such a task, not deadlock (its limits allow up to 248 temps per
instance), so the A9 partition for VDM tasks must be small. Upstream sizes `vs_max_instances` by output size
only (`pvr_arch_calculate_vertex_cam_size`), so the A9 limit is not in public code.

**Guard (in diag2, `g5p-xfb-diag@9efe16621c4`):** the ICD refuses any vertex shader (plain or XFB variant) above 12
temps at pipeline creation: `vertex shader needs N temps (> 12 wedges the TA)`. labwc is unaffected (≤ 12; 30/30
shaders and 315 scenes identical). vkcube-a9 now fails at pipeline creation instead of wedging the session. Lifting
the limit (a smaller `vs_max_instances` for larger VS, or spilling) is a separate experiment with wedge risk. It
must not fly beside labwc before multiclient2.

### (a)/(b) Why the store is a no-op, ranked

1. **(a) This GPU doesn't commit USC stores from vertex tasks in our configuration. The supported vertex-to-memory
   path is the TA's hardware stream-out (most likely).**
   - **IMG's own Rogue driver** (in this tree) reports `vertexPipelineStoresAndAtomics = false` but
     `transformFeedback = true` (`vulkan/pvr_physical_device.c:273,502`). It implements XFB **without** vertex
     stores: the TA streams the USC output dwords beyond VTXSIZE through a per-draw PDS stream-out program. That's PPP
     `STATE_STREAM_OUT0` (stream sizes, `stream0_ta_output`/`stream0_mem_output`), `STREAM_OUT1/2` (PDS data/temps,
     `sync`), and PDS `STM` instructions (`vulkan/pvr_arch_xfb.c`, `pdsc/programs/pdsc_xfb_{init,main,end}.h`).
   - **The A9 firmware already carries stream-out programs.** The VDM context store/resume task words
     (`W+0x58..0x80`, registers `0x438..0x464`) hold the Mesa-shaped PDS state update and stream-out sync programs
     (`stream_out1 = 0x4820`, `stream_out2 = 0x390/0x6d0`; dsx-nx48 §4.3). So the stream-out hardware exists and
     survives context switches.
   - **The only Rogue state keyed on VS side effects** is VDM INDEX_LIST0 `degen_cull_enable`
     (`pvr_arch_cmd_buffer.c:9139`). It culls index-degenerate primitives before shading, which doesn't apply to
     distinct auto-indices. No VDM/PPP/TA "store enable" exists in public Rogue.
   - **Our encodings are pinned.** The class-0x10 store is the compute-proven form (`102384010088`, r19/r20 hardware
     PASS). The pointer operand's scale is fixed by `v_st_xfb`, where consecutive `K` fields read `0x91/0x95/0x99` =
     dwords 4/5/6, so our `0x89` is sh2:sh3, and `G5P_XFB_GPUBLOCK` shows the right pointer there.
   - **Apple:** iOS GLES 3.0 implements transform feedback on these PowerVR parts, presumably through the same TA
     stream-out. Not verified. I also recall, without having checked it, that Metal on iOS requires a vertex
     function that writes device memory to return `void` (no rasterization). If true, Apple's vertex stores only
     commit in a non-rasterizing mode. Diag2's `discard` case tests the cheap half of that.
2. **(b) The store goes through another MMU context (possible, not decidable on deskfold).**
   - *For the client VM:* the firmware maps the TA requesters by one per-job write of the slot into
     `CONTEXT_MAPPING0` byte 0 (`f078`) and the replicated `136c/136d` (firmware-re README, "Per-requester
     registers"). The kernel checks they all equal the slot. TA-phase PDS vertex fetches and uniform DMAs do read the
     client VM.
   - *Against certainty:* no USC-originated request has ever been proven in a TA task (uniforms and attributes arrive
     by PDS DMA). The compute stores ran in context 1 only, so a static USC context of 1 would also have passed
     them.
   - *Silent fault:* a store into another context would fault there silently. The V3 path is RAM-only and never reads
     `MMU_FAULT_STATUS` (`0xE150/0xE154/0xE158`), and there is no dummy page.
   - *Deciding it* needs a kernel rider that reads `0xE150..0xE15C` after a captured job (the old RENDER-USER poll
     read them; V3 dropped it). That's a kernel change, not part of this package.
3. **Ruled out by run 2:** C2 visibility (`diag=visible`), C3 residency (`resident=1`), the fetch-less PDS and index
   (`vs-plain` 4096), a wrong patched address (`match=yes`, `GPUBLOCK`), records landing elsewhere in client memory
   (`SCAN hits=0`). The register hazard is moot (nothing lands at all).

**Apple reference: none.** The `tools/mesa-a9-xfb-re` probes are compile-only (metal-tt), and no iOS job with a
storing vertex function or a GLES transform-feedback draw was ever captured. A minimal iOS capture (the
`tools/ios-gpu-probe` KPF recorder, jailbroken iOS side, needs an iOS boot) would need:
- **Metal A:** a render pipeline whose vertex function writes `device uint4 *o` at `[[vertex_id]]` and returns
  `void` (`rasterizationEnabled = NO`), 3 vertices.
- **Metal B:** the same vertex function returning a position (does Metal accept it on iOS? if it does, does the job
  differ from A?).
- **GLES 3.0:** `glBeginTransformFeedback(GL_TRIANGLES)` + `glDrawArrays(GL_TRIANGLES, 0, 3)`, with and without
  `GL_RASTERIZER_DISCARD`.
- **Record per job:** the W words, VDM/PPP streams (look for PPP header bits 22/23 = `STREAM_OUT` words), PDS code
  (look for `STM` opcodes), VDM_STATE5, and the USC code (does Metal A's VS contain our `10 3b … 88` store or
  something else?).

### Diag2 package (one run, beside labwc)

Branch `g5p-xfb-diag@9efe16621c4`. Recipe: `XFB_LANE=diag XFB_OUT=$PWD/build/gpu/xfb-diag2 XFB_PKG=xfb-diag2-a9
scripts/gpu/xfb.sh build test client tramp package`. All gates green: 181 checks, 10 `XFB_EXACT`, 30/30 shaders
and 315 scenes identical, default trampoline = `081885cc`. The suite `repro` now stops at `attr` by design: the
guard refuses its 16-temp XFB variant.

`build/gpu/xfb-diag2/package/xfb-diag2-a9.tar.gz` `8f1a6378fa3935103c70daf1886f18bde83e8c60ed496c00aea60d7bf4f5c3f0`

| File | sha256 |
|---|---|
| `libvulkan_g5p.so` (diag ICD + the temps guard) | `1cb95f8de689805222e28f123dc6e133cd734230586c4e578695493d811ede5a` |
| `libvulkan_g5p117.so` (trampoline, pins it, `G5P117_XFB_DIAG`) | `1ac2d2f17c832dffd12408bf0bdc2d93f2c35c47d75169aef29882167b51feb4` |
| `xfb-test` (`--diag2`) | `254d7178b970c33d2cff4e4f59e113910857a4b7ce7e7dcbb5adcc0139d5c608` |

**Safety.**
- Three render jobs, all built from shapes already flown clean in run 2:
  - `control` and `discard`: the run-2 cover XFB VS, 12 temps, 326 bytes, the same `10 3b 8c 29 00 88` store.
  - `vid4`: 8 temps, one `10 23 80 11 00 88` 4-byte store.
- DOUTU temps were checked in the submitted job dumps: 12, 12, 8.
- The `attr` pipeline (16 temps) is only *created*, to show the guard refusing it. Nothing of it is submitted.
- `discard` uses the ISPCTL tag-write-disable word `03e40000`, which the trampoline accepts (`G5P117_ACCEPT_DISCARD`;
  offline classified `pass`). This is its first flight, but it only changes an ISP state bit, downstream of
  the TA.
- No new PPP/TA state is sent, and no stream-out word.

**Commands** (the last GPU test of the boot; kill only PIDs you started):
```sh
SSH=scripts/omarchy/ssh.sh; RN=/dev/dri/renderD128   # the session's LABWC_A9_RENDER_NODE
$SSH 'cat > /tmp/xfb-diag2-a9.tar.gz' < build/gpu/xfb-diag2/package/xfb-diag2-a9.tar.gz
$SSH 'cd /tmp && sha256sum xfb-diag2-a9.tar.gz && tar xzf xfb-diag2-a9.tar.gz && cd xfb-diag2-a9 && sha256sum -c SHA256SUMS &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/xfb-diag2-a9/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json && mkdir -p out'
$SSH "cd /tmp/xfb-diag2-a9 && sudo -n env -i PATH=/usr/bin:/bin VK_DRIVER_FILES=/tmp/xfb-diag2-a9/icd.json PVR_G5P_RENDER_NODE=$RN \
  PVR_G5P_XFB_DEBUG=1 PVR_G5P_XFB_SCAN=1 timeout -s KILL 300 ./xfb-test --diag2 /tmp/xfb-diag2-a9/out >/tmp/xfb-diag2-a9/run.txt 2>&1;
  echo rc=\$?; sudo -n dmesg | tail -200 >/tmp/xfb-diag2-a9/dmesg.txt;
  grep -E '^d2|G5P_XFB_(BLOCK|SCAN)|G5P117_INIT|G5P117_BLOCK' /tmp/xfb-diag2-a9/run.txt"
mkdir -p build/gpu/xfb-diag2/device && $SSH 'cd /tmp/xfb-diag2-a9 && tar cf - run.txt dmesg.txt out' | tar -C build/gpu/xfb-diag2/device -xf -
```
Expect `8f1a6378…`, three `OK` lines and `rc=0` (`--diag2` always exits 0; the lines are the result).

**Reading the lines:**

| Line | Want / meaning |
|---|---|
| `d2=guard attr_pipeline=-8 PASS` | the guard refuses a >12-temp VS at creation. FAIL (0) = the guard is missing: **stop**, don't trust the rest |
| `G5P117_BLOCK job=2 … ispctl=03e40000` | the discard job carried tag-write-disable |
| `d2=control pixels=4096 bad_bytes=48` | run 2 reproduced (48 = nothing landed) |
| `d2=discard pixels=0 bad_bytes=N` | N = 0: vertex stores commit only when nothing is rasterized. **Fix:** capture in a separate discard draw (Metal's void-vertex-function analogue), rasterize in a second draw |
| `d2=vid4 bad_bytes=N/12` | N = 0: 4-byte stores land and the 16-byte form is the no-op. **Fix:** split each record into 4-byte stores |
| `G5P_XFB_SCAN … hits` | any hit names where a store went; `SCAN_DONE hits=0` as in run 2 |
| `d2-verdict … verdict=` | the first matching rule |
| a `ta-wait` / `ret=-5` line in dmesg | not expected (all shapes flew clean). If it happens, it's the `discard` ISP word: drop it from future runs |

**If all three stay at 48/48/12** (my expectation), vertex-task stores are dead in our configuration. The fix
direction is IMG's TA stream-out, and its first flight carries wedge risk, so it waits for multiclient2:
- PPP `STREAM_OUT0..2` in the draw's PPP block;
- a PDS `STM` program per draw, with the record addresses as its constants, the same per-draw patching the XFB block
  does now;
- the XFB outputs placed after VTXSIZE as streams;
- the init/end/query PDS programs.

The iOS GLES capture above would confirm it's Apple's path too. (b) can be settled first, at no GPU risk to labwc,
with a kernel rider that logs `0xE150..0xE15C` after each V3 job.

## Device run 2b (diag2): the 16-byte store is the no-op

Package `8f1a6378…` on deskfold, beside labwc. Raw data: `build/gpu/xfb-diag2/device/`.

| Line | Result |
|---|---|
| `d2=guard attr_pipeline=-8` | PASS: the ICD refuses the 16-temp VS at creation |
| `d2=control pixels=4096/4096 bad_bytes=48/48` | run 2 reproduced: 16-byte stores, nothing lands |
| `d2=discard pixels=0 bad_bytes=48/48` | FAIL: rasterizer discard changes nothing |
| `d2=vid4 pixels=4096/4096 bad_bytes=0/12 counter=12` | **PASS: 4-byte stores land** |

`G5P_XFB_BLOCK` matched in all three jobs, `SCAN hits=0`, no `ta-wait` or `ret=-5` in dmesg. So the Asahi route
(`nir_lower_xfb_to_stores`, vertex-shader global stores) works on A9, as long as every store is 32-bit.

## Device run 3: records as 4-byte stores

Mesa branch **`g5p-xfb-fix` @ `51a5f4bb7f8`** (one commit on `g5p-xfb-diag@9efe16621c4`, worktree
`build/third_party/mesa-a9-xfb-diag-src`, now checked out on `g5p-xfb-fix`). Recipe:
`XFB_LANE=fix scripts/gpu/xfb.sh build test client repro tramp package repro-tramp` (output `build/gpu/xfb-fix`).
`XFB_LANE=diag` needs that worktree back on `g5p-xfb-diag` first.

### What changed

- **The split, the standard NIR way.** The backend's `lower()` runs `nir_lower_mem_access_bit_sizes` on every
  vertex shader (modes `nir_var_mem_global`). Its size callback returns one 32-bit component for every access, the way
  Asahi limits its memory access widths. A record's `store_global` vec4 becomes four scalar stores at
  `base`, `base + 4`, `base + 8` and `base + 12`. The counter, the resolve, the address math of `nir_lower_xfb_to_stores`
  and the per-draw block are unchanged.
- **One address add per record.** The backend folds the split's +4/+8/+12 into the store's immediate dword offset (byte 4
  of `10 23 DD AA OO 88`), as Apple's compiler does. Witnessed values: 1 (`v_st_bytes`, `vid*20+4`), 2 (`v_st_stride12`),
  and **3** (new round-3 probe `v_st_off12`, `vid*16+12` → `102380050388`). The encoder accepts offsets 0..3, on 4-byte
  stores only.
- **Width restricted in the backend.** `store_refusal()` refuses any vertex `store_global` that isn't a 32-bit scalar
  (`vertex tasks commit only 32-bit scalar stores`), and `vs_store()` refuses it at emission too. Global stores exist only in
  vertex shaders in the general backend (scan: `store_global` is VS-only), so no path can emit the dead form.
- **Stores go last, and their registers stay put.** `sink_vs_stores()` moves every store, plus the ALU that only feeds
  stores, to the end of the shader: first all record offsets, then the stores. That is where Apple's compiler puts them
  (`vout … st, br, vend`). Each store's data register and each record's address pair stay reserved to the end, so nothing
  rewrites a store's registers after it. That matches Apple's rule (no rewrite without a csync), and it replaces
  `PVR_G5P_XFB_HOLD`, which is gone.
- **No gather run.** A store reads its dword straight from the value's register, so the four-register run of the old
  16-byte form disappears.

**Temps (XFB variant; the >12 guard is kept).**

| Shader | 16-byte store (diag2) | 4-byte stores | Code |
|---|---|---|---|
| `xfb-rec` | 12 | **8** | 198 B, 4 stores, 1 address add |
| `xfb-inst` | 12 | **8** | 198 B |
| `xfb-dup` (one fetched attribute) | 12 | **8** | 262 B |
| `xfb-cover` (full-screen triangle from the index) | 12 | 12 | 326 B |
| `xfb-vid` (one dword) | 8 | 8 | 262 B |
| `xfb-attr` (two buffers: fetched vec4 + record) | 16 | **16, still refused** | 8 stores, 2 address adds |
| `xfb-two` (new: one buffer, stride 32, record + position interleaved) | — | 12 | 390 B, 8 stores, 2 address adds |

The split doesn't push any of these past 12; three drop to 8. `xfb-attr` stays at 16 because its two buffers keep 8
captured values live (pos ×4, record ×4) plus two reserved address pairs. Even perfect packing needs 12 dwords, and the
allocator lands on 13. The address pair is per captured **output**, not per buffer: `nir_lower_xfb_to_stores` gives each
output its own `index*stride + xfb_offset`, and the second output's +16 is beyond the witnessed immediate range 0..3. So
`GL_INTERLEAVED_ATTRS` with two vec4 outputs also takes two pairs. `xfb-two` measures that shape at 12 temps (it fits
because its position is computed, not fetched beside the record). Two pairs plus more than about 7 other live values go
past 12. The ICD keeps refusing shapes above 12 at pipeline creation. Fitting them needs one of two things:
- the `vs_max_instances` experiment (wedge risk, separate);
- a wider immediate offset. **Lead:** probe `+28`/`+60` stores to learn the offset field's width. If it reaches the
  whole record, one pair covers every output of a buffer.

### Is the 16-byte form wrong only in vertex tasks? (task 2)

- **Compute never ran it either.** Every compute store on hardware is the 4-byte `10 23 …` form:
  - r19d/r20: `102384010088`;
  - r24's loads test asserts only `1023…` stores (`tools/gpu-userspace/test-compute-r24-loads.py`);
  - dsx-nx94: `st.32`.
- **So the 4-byte store is the only width proven on any stage.** Whether 8/12/16-byte stores work in compute is unknown,
  and the backend allows no vector store anywhere.
- **Apple's own vertex code uses the wide form.** Apple emits `103b…` (16 bytes) in vertex functions for a 16-byte
  aligned `uint4 *` (`v_st_rec`, `v_st_xfb_rec`).
- **Apple doesn't split a 4-aligned record either.** The new probe `v_st_rec4` writes the record through a 4-byte
  aligned `uint *`. Apple still emits one 16-byte store, `103f80110088`, with byte-1 bit 2 set (`0x3f`, not `0x3b`).
- **Lead (not followed).** Bit 2's meaning is unknown, perhaps an alignment or cache-policy bit. If the A9's vertex path
  needs it, `0x3f` might land where `0x3b` doesn't. That's a cheap one-job test for later. The fix doesn't depend on it.
- Probe output: `build/gpu-userspace/xfb-re3{,.hex.txt}`; sources `tools/mesa-a9-xfb-re/probes.metal` (round 3).

### Offline results

- **`g5p-xfb-test`: 263 checks, 0 failures.**
  - Encoders, including the immediate offsets 1/2/3 against `v_st_bytes`/`v_st_stride12`/`v_st_off12`; offset 4 and an
    offset on a 16-byte store are rejected.
  - Per XFB variant:
    - store count, all 4-byte;
    - one address add per record;
    - per-offset store counts (0..3);
    - nothing after the first store except stores, address adds, moves and outputs;
    - no write after a store into a register a store read;
    - exact temps (table above).
  - Resolve cases.
- **`scripts/gpu/xfb.sh test` scans every dumped `.usc`.** 33 stores in the XFB variants, 0 wider than 4 bytes; 0 stores
  in the 30 regression shaders.
- **`oracles.py xfb`:**
  - 10 resolve cases are `XFB_EXACT`.
  - 39 unit tests (`tools/mesa-a9-loadop-re/test_oracles.py`). New ones cover:
    - guard bytes written before the binding and in stride padding;
    - exact counter and clamped overflow on a 48-byte buffer;
    - a partial-primitive counter;
    - a missing last vertex and an extra vertex past the end (off by one);
    - one lost dword and the immediate offset being ignored.
  - A failing record now names its bad dwords (`record 2 differs in dwords [2]`).
  - The device client dumps the whole buffer (json `offset` = 32), so the 32 guard bytes before the binding are checked
    on device data too.
- **Regression:** all 30 round-17..19 shaders compile to identical bytes and all 315 scenes are identical, so labwc is
  unaffected. The control ICD is `a2f00cda…` and the control trampoline is `081885cc…`.
- **Offline repro over the typed-V3 fake node,** with the ICD directly and through the packaged trampoline:
  - `--run3`: all four `PASS-OFFLINE`;
  - the suite: rec, first3, overflow, instanced, resume and dup are `PASS-OFFLINE`; attr and discard print
    `REFUSED` (the guard), which is expected;
  - the trampoline shows `G5P117_INIT result=ok hooks=6`, three VM-edit patches (the guards moved 544 bytes), and every
    submit classified `pass`, the discard ISPCTL `03e40000` included;
  - the fake node reports `refused=0`.

### Package

`build/gpu/xfb-fix/package/xfb-fix-a9.tar.gz` **`ea343b2b6bf4ea51ee239cf5c29552909bf3b8170851a54f7b0113ac38a7437b`**

| File | sha256 |
|---|---|
| `libvulkan_g5p.so` (fix ICD, diag knobs opt-in, temps guard) | `5db851c6b1a4ac591f6985ea2bdf19d46bf19aea4dca3dbfd9cd5a4e0399705e` |
| `libvulkan_g5p117.so` (trampoline, pins it, `G5P117_XFB_DIAG`, `G5P117_ACCEPT_DISCARD`) | `568e386599cca817b55de2696737dd38f3e1b3d51b63d89c9e0ea6054350efce` |
| `xfb-test` (`--run3`, the suite) | `c99f2749d8a5d0df923d79c4879d4040d966d8db8d3db699226696d24de0cde8` |

### Device plan: **warm-safe**

**Base:** multiclient2 (`a9/gpu-multiclient2@995d684abd`, release `7.3.0-rc1-g995d684abd67`). Fault isolation there means a
stuck client job loses only its own context. Run it as an extra GPU client beside labwc, last in the boot (same rules as
diag2). Kill only PIDs you started; never `qemu-nbd`.

**Why warm-safe:**
- **Step 1 is four render jobs.**
  - `record`, `overflow` and `discard` use the cover XFB VS: 12 temps, the same temps as diag2's control/discard, which
    flew clean.
  - `dup` uses an 8-temp VS with one fetched attribute.
  - Everything is ≤ 12 temps, and the ICD refuses more.
- **New on hardware:**
  - 4-byte stores with immediate dword offsets 1..3 (Apple-compiled, not yet flown);
  - four stores per vertex, at the end of the program;
  - a 3000-vertex captured draw that reads its own capture buffer as the vertex buffer (`dup`; runs 1 and 2 never got
    that far).
- **Worst case:** a wrong store address faults in the client's own VM, or a client TA stall. multiclient2 drops only the
  xfb-test context, labwc keeps running, and a warm kexec back to the base remains the fallback.
- **Nothing is DFU-risk:**
  - no firmware power words;
  - no new PPP/TA/stream-out state;
  - no kernel change;
  - the discard ISPCTL already flew in diag2.

**Commands:**
```sh
SSH=scripts/omarchy/ssh.sh; RN=/dev/dri/renderD128   # the session's LABWC_A9_RENDER_NODE
$SSH 'uname -r'                                       # want 7.3.0-rc1-g995d684abd67 (multiclient2)
$SSH 'cat > /tmp/xfb-fix-a9.tar.gz' < build/gpu/xfb-fix/package/xfb-fix-a9.tar.gz
$SSH 'cd /tmp && sha256sum xfb-fix-a9.tar.gz && tar xzf xfb-fix-a9.tar.gz && cd xfb-fix-a9 && sha256sum -c SHA256SUMS &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/xfb-fix-a9/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json && mkdir -p out out2'
# step 1: device run 3 (4 jobs)
$SSH "cd /tmp/xfb-fix-a9 && sudo -n env -i PATH=/usr/bin:/bin VK_DRIVER_FILES=/tmp/xfb-fix-a9/icd.json PVR_G5P_RENDER_NODE=$RN \
  PVR_G5P_XFB_DEBUG=1 timeout -s KILL 300 ./xfb-test --run3 /tmp/xfb-fix-a9/out >/tmp/xfb-fix-a9/run.txt 2>&1;
  echo rc=\$?; sudo -n dmesg | tail -200 >/tmp/xfb-fix-a9/dmesg.txt;
  grep -E '^r3|^case=|G5P_XFB_BLOCK|G5P117_INIT|G5P117_BLOCK' /tmp/xfb-fix-a9/run.txt;
  grep -E 'ta-wait|ret=-5|fault|terminal|refused' /tmp/xfb-fix-a9/dmesg.txt | tail -5"
# step 2, only if step 1 printed r3-result ... result=PASS: the full suite (8 more jobs)
$SSH "cd /tmp/xfb-fix-a9 && sudo -n env -i PATH=/usr/bin:/bin VK_DRIVER_FILES=/tmp/xfb-fix-a9/icd.json PVR_G5P_RENDER_NODE=$RN \
  timeout -s KILL 300 ./xfb-test /tmp/xfb-fix-a9/out2 >/tmp/xfb-fix-a9/run2.txt 2>&1;
  echo rc=\$?; sudo -n dmesg | tail -200 >/tmp/xfb-fix-a9/dmesg2.txt; grep -E '^case=|^result=' /tmp/xfb-fix-a9/run2.txt"
# collect and check
mkdir -p build/gpu/xfb-fix/device && $SSH 'cd /tmp/xfb-fix-a9 && tar cf - run*.txt dmesg*.txt out out2' | tar -C build/gpu/xfb-fix/device -xf -
python3 -I tools/mesa-a9-loadop-re/oracles.py xfb build/gpu/xfb-fix/device/out
python3 -I tools/mesa-a9-loadop-re/oracles.py xfb build/gpu/xfb-fix/device/out2   # if step 2 ran
```
Expect `ea343b2b…`, three `OK` lines and `rc=0` for step 1. `--run3` exits 1 if any case fails; the lines are the
result.

**Reading the lines:**

| Line | Want | If not |
|---|---|---|
| `G5P117_INIT … result=ok` | the pinned pair loaded | stop: wrong files |
| `G5P_XFB_BLOCK … resident=1`, `match=yes` (record, discard, dup) or `match=staged` (overflow: the staging BO) | the patched record address is the binding | `match=NO`: CPU-side address bug |
| `r3=record pixels=4096/4096 records=6 counter=96/96 lanes=0/0/0/0 outside=0 query=2/2 result=PASS` | **XFB records land**: 4-byte stores at immediate offsets 0..3, exact readback, 32 guard bytes before and every byte after untouched | see the lane rows below |
| … `lanes=24/24/24/24 outside=0` | | nothing landed: the 4-byte store doesn't commit in this shape (four stores, or stores at the end); compare vid4 (one store mid-program) |
| … lanes 1..3 = 24, lane 0 < 24, `outside=0`, and the oracle shows every dword 0 = `0000803f` | | the immediate offset is ignored: all four stores hit the base, the last (1.0f) wins. Fix: one address add per dword (no immediate), at a temps cost |
| … `lanes` bad only for some k, `outside=0` | | the store with offset k doesn't land: that offset value is wrong on hardware |
| … `outside>0` | | stores hit outside the records: the offset is scaled differently (e.g. 16-byte units) |
| … `pixels` ≠ 4096 | | the VS doesn't run its outputs right; the capture says nothing |
| `r3=overflow pixels=4096/4096 records=3 counter=48/48 lanes=0/0/0/0 outside=0 query=1/2 result=PASS` | the 48-byte bound: staging + CPU copy of whole primitives, 3 records, counter 48, guard from byte 80 | records fine but `outside>0`: the copy wrote past the bound; `query` wrong: CPU query path |
| `r3=discard pixels=0/0 records=6 … lanes=0/0/0/0 result=PASS` | capture under rasterizer discard (ISP tag-write disable), no pixel drawn | `pixels>0`: discard not applied; records bad here only: the stores need the raster path |
| `r3=dup vertices=3000 counter=48000/48000 exact=3000 wrong=0 tail_bad=0 … result=PASS` | **one exact record per vertex** across VDM splits, nothing past the binding | `wrong>0`: `first_wrong` names the vertex. At a split boundary (multiples of the VDM batch), the index there doesn't reach the PDS |
| `r3=dup … once= twice= more=` | information: `twice`/`more` = vertices shaded again after their record landed (still exact, the stores are idempotent) | record for the TA notes |
| `r3-result … result=PASS` | all four | |
| dmesg grep empty | no `ta-wait`, `ret=-5`, fault or terminal context | a line: the xfb-test context was lost (multiclient2 isolates it). Keep `dmesg.txt` |
| oracle: every line `XFB_EXACT` | `xfb-record`, `xfb-overflow`, `xfb-discard` (+ step 2's) | `REFUSED` names the record and the bad dwords |
| step 2: `case=rec bad_bytes=96` while step 1's `record` passed | | not a fix bug: the suite's `xfb-rec.vert` puts every vertex on y = 0 (zero-area triangles), so the TA culls them before the vertex stores commit. That's a hardware fact; GL never needs degenerate captures. first3, overflow, resume (`xfb-rec.vert`) and instanced (`xfb-inst.vert`) have the same zero-area positions: read them the same way |
| step 2: `case=… result=PASS` for rec, first3, overflow, instanced, resume, dup; `case=attr/discard result=REFUSED` | first3 = the firstVertex fold reaches the PDS index; instanced = the instance DOUTW; resume = counters across passes and DrawIndirectByteCount. REFUSED is the temps guard (expected) | `first3` records 0..5 where 3..8 were wanted: add the first vertex in the shader instead of the fold |

**If all PASS:** transform feedback works on A9 through Asahi's route, with no TA stream-out needed. Next: put the pair
into the session so Zink's GLES 3.0 sees `VK_EXT_transform_feedback` with captures that land. Then the next ES 3.0
blocker (MRT).

## Device run 3 result and the dup stall: diagnosis and ladder

**Run 3** (multiclient2 `7.3.0-rc1-g995d684abd67`, package `ea343b2b…`; data `build/gpu/xfb-fix/device/`):
- `record`, `overflow` and `discard` PASS, and `oracles.py` gives `XFB_EXACT` for all three. **4-byte vertex stores at
  immediate offsets 0..3 land exactly.**
- **`dup` stalled the TA.** Job 4 (3000 vertices, `G5P_XFB_BLOCK … match=yes resident=1`) was consumed at 1 ms and went
  busy at 7 ms. The `slow` line at 1 s shows `idle=00000001,00000001,…`, and then nothing came until the soft timeout:
  `end seq=9 ret=-5 phase=ta-wait ta=fault/45000`. There was no MMU fault line. `close … plan=retain`, labwc kept
  running, and the client got device lost. Step 2 (the suite) was not run.

### What is new in the dup job (offline job dumps, `build/gpu/xfb-dupl/trace/`)

Compared with every job that has completed on hardware, the dup job adds these:

| # | What | Dup job | Flown so far |
|---|---|---|---|
| 1 | **Vertex PDS shape** | `e0180002 f0002201 f0002601 fc00000a d3000000` = DDMAD (attribute, 4 dwords → r0..r3), DOUTW vertex index → r4, DOUTW instance index → r5 (last), DOUTU. Data `… 00008000 8000a001`, `VDM_STATE5 = 0x1083` (48-byte PDS data) | DDMAD without ids (labwc, `0x1081/0x1082`). Ids without DDMAD (run 2/3 cover VS: `f0000a01 f0000e01 fc000002`). **DDMAD + DOUTW never flew**: `attr` was never submitted (run 2 stopped at `hold`; diag2 and run 3 refuse it). |
| 2 | **Vertex count** | 3000 vertices: `INDEX_LIST … 00000bb8 … split 0xaa` = 6 VDM splits of 170 triangles, about 94 vertex tasks of 32 | at most 6 vertices per draw (labwc, run 3), 1 task, 1 split. vkcube-a9's 36-vertex draw wedged, but with a 20-temp VS, so that result is confounded: a large count is **unflown, not cleared** |
| 3 | Stores in many vertex tasks | 4 stores × 3000 vertices | 4 × 6 in 1 task (`record`) |
| 4 | Vertex buffer = capture buffer | the attribute DMA reads the memory the stores write | never. Reading a buffer as a VB while it is also an active XFB binding is undefined behaviour in Vulkan and in GL. Only the test does it, no real client |
| 5 | Geometry | y = 0 everywhere (zero area), x up to 374 (off screen beyond vertex 15) | zero area flew (run 1 `rec`, 6 vertices, complete); far off screen never flew |

**Ruled out:**
- **Temps.** The dup VS's XFB variant and its plain variant both have DOUTU temps 8 (data word `0x10`; the cover VS has
  12, `0x18`). The >12-temp wedge doesn't apply, so the ladder has no temps rung.
- **Address.** `match=yes resident=1`, and there was no fault.
- **Parameter buffer.** More than 99 % of the primitives are degenerate or trivially rejected, so the TA writes
  almost nothing. PB exhaustion is the fallback only if the no-capture 3000-vertex rung fails while the 3-vertex
  rungs pass.

**Reading of the signature.** `idle=00000001,00000001` with no MMU fault is the same stall class as the `hold` wedge
(a vertex task that never completes or never gets its resources), not an address fault.

**Ranked:**
1. Row 1, the never-flown DDMAD + DOUTW PDS. This rung is cheapest: 3 vertices are enough.
2. Row 2, a large draw (several tasks, VDM splits), with or without stores.
3. Row 3, stores across many tasks.
4. Row 4, aliasing. If this turns out to be the cause, it is a test artefact (undefined behaviour) and not a driver
   bug.

No Mesa change. The ICD and the trampoline are run 3's, byte for byte.

### The ladder client

`tools/mesa-a9-xfb/xfb-test.c` gains one job per process:
- `--ctl`: run 3's passing `record` job alone.
- `--dup=N[,sep][,nocap]`: `xfb-dup.vert` over N vertices.
  - Default: capture into a buffer that is also the vertex buffer (run 3's dup).
  - `sep`: a separate, zero-filled vertex buffer.
  - `nocap`: the same pipeline without capture (its plain variant). The capture memory must stay untouched.

Each prints one `dl=` line. `--run3`'s output is unchanged; its dup is now `dup_draw(3000, alias, capture)`.

**What each rung's PDS is** (offline dumps):
- `nocap`: `e0180002 f0002201 fc00000a d3000000`, which is DDMAD + one DOUTW (the vertex index → r4, last), `0x1083`,
  8 temps. The attribute is not dead-code eliminated, so the no-capture rungs keep the new DDMAD + DOUTW class and
  only lose the instance DOUTW and the stores.
- The capture rungs: the exact run-3 dup PDS (row 1).

Recipe (client only; the ICD and trampoline are cloned from `build/gpu/xfb-fix`; `build/gpu/xfb-fix/package` is left
alone):
```sh
O=build/gpu/xfb-dupl; mkdir -p $O && cp -Rc build/gpu/xfb-fix/bin build/gpu/xfb-fix/tramp $O/
XFB_LANE=fix XFB_OUT=$PWD/$O XFB_PKG=xfb-dupl-a9 XFB_REPRO_MODES="--run3 --ctl --dup=3,sep,nocap --dup=3,sep --dup=3 \
  --dup=3000,sep,nocap --dup=300,sep --dup=1020,sep --dup=3000,sep --dup=3000" scripts/gpu/xfb.sh client repro package repro-tramp
```
Offline over the typed-V3 fake node, with the ICD directly and through the packaged trampoline:
- every rung prints `PASS-OFFLINE`;
- `--run3` and the suite are unchanged;
- 11 × `G5P117_INIT result=ok`, 21 submits `decision=pass`, `refused=0`.

A rebuild gives the same client bytes.

`build/gpu/xfb-dupl/package/xfb-dupl-a9.tar.gz` **`48271baba1eb9a5d8904d1be8298e19eeafcce6be4824bfbbf198b761f5d3450`**

| File | sha256 |
|---|---|
| `libvulkan_g5p.so` (= run 3) | `5db851c6b1a4ac591f6985ea2bdf19d46bf19aea4dca3dbfd9cd5a4e0399705e` |
| `libvulkan_g5p117.so` (= run 3) | `568e386599cca817b55de2696737dd38f3e1b3d51b63d89c9e0ea6054350efce` |
| `xfb-test` (ladder) | `aa0ef5fae52d107c64b4dce1d5b0a5a601dddedddb55dd4706f962e5a7e52475` |

### Device plan: ladder (warm-safe, as run 3)

**Base:** multiclient2 as an extra client beside labwc. Kill only PIDs you started; never `qemu-nbd`.

**Rules:**
- One job per process, in order.
- A rung FAILs on `rc≠0`, a missing `dl=` line, or a `ta-wait`/`ret=-5` line.
- **After every FAIL, run `--ctl` before the next rung.**
  - `ctl` FAIL: stop and warm kexec (deskfold rule, fine after a wedged client).
  - `ctl` PASS: carry on.
- Each wedge costs 45 s and **keeps a slot pair for the rest of the boot** (`plan=retain`; watch `slots=` in
  `RENDER-V3 start`). Stop after 3 wedges in one boot.
- Run R8 only if R7 passed.

```sh
SSH=scripts/omarchy/ssh.sh; RN=/dev/dri/renderD128   # the session's LABWC_A9_RENDER_NODE
$SSH 'uname -r'                                       # want 7.3.0-rc1-g995d684abd67
$SSH 'cat > /tmp/xfb-dupl-a9.tar.gz' < build/gpu/xfb-dupl/package/xfb-dupl-a9.tar.gz
$SSH 'cd /tmp && sha256sum xfb-dupl-a9.tar.gz && tar xzf xfb-dupl-a9.tar.gz && cd xfb-dupl-a9 && sha256sum -c SHA256SUMS &&
  printf "{\"file_format_version\":\"1.0.0\",\"ICD\":{\"library_path\":\"/tmp/xfb-dupl-a9/libvulkan_g5p117.so\",\"api_version\":\"1.0.0\"}}\n" > icd.json && mkdir -p out'
dl() { $SSH "cd /tmp/xfb-dupl-a9 && sudo -n env -i PATH=/usr/bin:/bin VK_DRIVER_FILES=/tmp/xfb-dupl-a9/icd.json PVR_G5P_RENDER_NODE=$RN \
  PVR_G5P_XFB_DEBUG=1 timeout -s KILL 300 ./xfb-test $1 /tmp/xfb-dupl-a9/out >/tmp/xfb-dupl-a9/run-$2.txt 2>&1;
  echo rung=$2 rc=\$?; sudo -n dmesg | tail -80 >/tmp/xfb-dupl-a9/dmesg-$2.txt;
  grep -E '^dl=|^r3=|G5P_XFB_BLOCK|G5P117_INIT|vkQueueSubmit' /tmp/xfb-dupl-a9/run-$2.txt;
  grep -E 'ta-wait|ret=-5|fault|plan=retain' /tmp/xfb-dupl-a9/dmesg-$2.txt | tail -3"; }
dl --ctl                 r0   # GPU sane, the run-3 record job
dl --dup=3,sep,nocap     r1   # the DDMAD+DOUTW PDS alone: 3 vertices, 1 task, no stores
dl --dup=3,sep           r2   # the exact dup PDS (+ instance DOUTW) + 4 stores/vertex, 1 task
dl --dup=3               r3   # + VB aliases the capture buffer
dl --dup=3000,sep,nocap  r4   # 3000 vertices, 6 splits, ~94 tasks, no stores
dl --dup=300,sep         r5   # stores in ~10 tasks, 1 split
dl --dup=1020,sep        r6   # 2 splits (340 triangles)
dl --dup=3000,sep        r7   # run 3's dup without the alias
dl --dup=3000            r8   # run 3's dup, alone (only if r7 passed)
# after a FAIL at rN: dl --ctl rNc
mkdir -p build/gpu/xfb-dupl/device && $SSH 'cd /tmp/xfb-dupl-a9 && tar cf - run-*.txt dmesg-*.txt' | tar -C build/gpu/xfb-dupl/device -xf -
```
Expect `48271bab…` and three `OK` lines.

**PASS lines:**
- `dl=ctl result=PASS`.
- Capture rungs: `dl=<spec> … cap=1 counter=16N/16N exact=N wrong=0 tail_bad=0 … result=PASS`. With `sep`, expect
  `once=N`. With alias, `twice`/`more` are information only.
- No-capture rungs: `dl=<spec> … cap=0 completed=1 touched=0 tail_bad=0 result=PASS`.
- `G5P_XFB_BLOCK … match=yes resident=1` on the capture rungs.

**Verdict table** (the first failing rung decides):

| Pattern | Cause | Next |
|---|---|---|
| r0 FAIL | the GPU or the session is not sane before the ladder | stop, warm kexec, retry |
| **r1 FAIL** | the **DDMAD + DOUTW vertex PDS** stalls the TA by itself (row 1). It affects any VS that reads `gl_VertexIndex` and has an attribute, with or without XFB. labwc never hit it because its shaders don't read the index (`id_mask` = 0) | ICD: refuse (or pad) that shape until fixed. Offline: compare our DOUTW placement and `STATE5` data/unified sizes with Rogue's `pvr_pds_vertex_shader` (ids before the DDMADs, the unified size covering r4/r5). Next one-job rungs: ids before DDMAD; `0x1083` with ids dropped |
| r1 PASS, **r2 FAIL** | the dup PDS's second DOUTW (instance → r5 upper, last) after a DDMAD, or stores after a DMA'd attribute, at 1 task | split it next: a diagnostic ICD knob that feeds instance 0 as a constant instead of the DOUTW (not "drop it": `nir_lower_xfb_to_stores` folds the instance into every record address), or a fetch + store VS without the instance id |
| r2 PASS, **r3 FAIL** | **aliasing** stalls even at 1 task | test artefact (undefined behaviour): drop the alias from `dup`, measure duplicates another way. No driver fix |
| r1–r3 PASS, **r4 FAIL** | **large draws** stall without any store: VDM splits / many vertex tasks on this PDS (row 2). It blocks every GL app with > 6-vertex draws, not just XFB | top priority, outside XFB. Next: the same at 6/33/510/513 vertices (task and split edges), and a fetch-less 3000-vertex draw (`xfb-cover` VS, no capture) to clear the DDMAD. Then the PB/OOM path |
| r4 PASS, **r5 FAIL** | stores from several vertex tasks (row 3), 1 split | the store tail: Apple's VS ends `st, br, vend`, so probe whether Apple adds a data fence (`wdf`) before `vend` when several tasks store; add it |
| r5 PASS, **r6 FAIL** | stores across a VDM split boundary | the index at the split (`first_wrong`), the split count `0xaa` with stores; try split 510/3 vs 170 |
| r6 PASS, **r7 FAIL** | store volume (~12 k stores) or task count with stores | bisect N between 1020 and 3000 (`--dup=1530,sep`, `--dup=2040,sep`) |
| r7 PASS, **r8 FAIL** | aliasing at scale | as r3: test artefact, drop the alias. **XFB is then done** for real clients |
| all PASS (r8 too) | dup alone works, so run 3's stall depends on the job sequence: dup was job 4, right after the `discard` job (ISPCTL `03e40000`) in the same context | rerun `--run3` once. If it stalls again, reorder (dup before discard) to rule on the discard job's after-effect |

**Most likely outcome (my expectation):** r1 or r4 fails. Both point at things that are new on hardware but not
specific to XFB (the DDMAD + DOUTW PDS, large draws). The store and alias rungs are only reached if those pass.

## Vertex PDS: Apple's LAST rule (g5p-vpds)

### Ladder result (2026-11-01, `build/gpu/xfb-dupl/ladder-20261101T204048Z.txt`)

- **r0 `--ctl` PASS.**
- **r1 `--dup=3,sep,nocap` wedged the TA.** That draw is 3 vertices, 1 task and no stores. Its vertex PDS fetches an
  attribute and then writes the vertex index: `e0180002 f0002201 fc00000a d3000000`, `VDM_STATE5 0x1083`.
  The job was consumed at 1 ms and busy at 7 ms, then nothing came until `ret=-5 phase=ta-wait ta=fault/45000`,
  `ta_unsat=0xe`. There was no MMU fault.
- **r1c `--ctl` was refused** (`vkQueueSubmit = -4`). After a wedge, new GPU contexts are refused until a warm kexec, so
  one wedge per boot is the budget.

So the never-flown row 1 (DDMAD + DOUTW) is the bug. It is not specific to XFB: it hits every VS that reads
`gl_VertexIndex` or `gl_InstanceIndex` and has an attribute.

### Apple's encoding (iOS captures 20261101T175626Z)

`tools/mesa-a9-xfb/vpds-check.py apple <phase dir> <stage>` decodes every vertex draw of a capture:
- the VDM stream is allocator pool 0;
- `STATE3` is relative to the code heap;
- `STATE2` points into pool 5, the PDS data heap at GPU `0x30000`.

Only one shape in the bundle fetches attributes *and* writes an index: `v_inst`, which reads `pos[vid]` and `off[iid]`
and uses `iid`. It appears in gles3 `instanced` and in gles3b `g3b-basevertex`. In `g3b-basevertex` an `add32 t1, c4, t1`
comes first, with `c4` = baseInstance 2. All 24 vertex draws in gles3/gles3b follow the same rule.

| | Apple `instanced` | ours r1 before | ours r1 now (`879f0c8725c`) |
|---|---|---|---|
| code | `e0300004 e0342045 f0003a01 fc00001a d3000000` | `e0180002 f0002201 fc00000a d3000000` | same as before |
| DDMAD 1 | `c12·t0 + c0:1`, ctl c8 `00000003` (r0..r2) | `c6·t0 + c0:1`, ctl c4 `00000004` (r0..r3), **no LAST** | ctl c4 **`80000004`** (LAST) |
| DDMAD 2 | `c13·t1 + c2:3`, ctl c10 **`80006002`** (r3..r4, **LAST**) | — | — |
| DOUTW | `t0:t1`, ctl c14 **`8000a001`** (instance → r5, upper, **LAST**) | `t0:t1`, ctl c8 `80008000` (vertex → r4, lower, LAST) | same as before |
| DOUTU | c6:7 `000025c0 00000010` (temps 8), end | c2:3 `00004000 00000010` (temps 8), end | same as before |
| STATE5 | `0x1084` (unified 4, PDS temps 2, data 4) | `0x1083` (unified 4, PDS temps 2, data 3) | same as before |

The order (DDMADs, DOUTW, DOUTU.end, HALT), the DOUTW form, the id register right after the attributes, the temps and the
unified size already matched. The one difference is the **LAST bit on the final DDMAD**:
- Apple sets it on the final DDMAD and on the final DOUTW.
- Rogue's `pvr_pds_vertex_shader` (`terminate = !issue_empty_ddmad && !direct_writes_needed`) clears the DDMAD's LAST
  when DOUTWs follow, and our packer copied that.

The ladder's r2 shape (`dup` with capture) now has DDMAD ctl `80000004`, then DOUTWs `00008000` (r4 lower) and `8000a001`
(r5 upper, LAST). The last word is Apple's exact DOUTW. The two-DOUTW pair without a DDMAD already flew (run 3 `record`),
so r1 and r2 test the LAST bit alone.

### The fix and the vertex-input limit (Mesa `g5p-vpds`, worktree `build/third_party/mesa-a9-vpds-src`)

Branch `g5p-vpds` on `g5p-xfb-fix@51a5f4bb7f8`:

1. **`879f0c8725c`: LAST on the final DDMAD.** This is a data-segment change only. The code words and the layout are
   unchanged.
2. **`64764cc5487`: up to 8 vertex inputs.** This is the added scope. The ICD advertised 2, so glmark2 runs only its
   clear scene and Hyprland's `tex300.vert` (3 vec2) fails to link. Apple's `g3b-vtx16` (16 mixed-format attributes) shows
   the model:
   - 5 DDMADs into r0..r18, LAST only on the last one;
   - DOUTU **temps 8**, but the USC reads r14/r15 and writes only r4/r5;
   - `STATE5` unified **12 units**.

   So the inputs live in the unified store, and the temps cover the registers the program writes. The commit does four
   things:
   - **Unified size = 4 units per 8 input dwords.** This fits every captured point (0 → 0; 1, 4, 6 → 4; 19 → 12) and the
     flown 8-dword shapes. One path serves draws with and without index writes. It is inferred from five points; 9..16
     input dwords (→ 8 units) are not witnessed directly.
   - **DOUTU temps.**
     - Within the 12-temp guard, the old count (inputs included) is kept, so every accepted shape is byte-identical.
     - Above the guard, the temps count only the registers the program writes.
     - A VS whose inputs reach past r11 does not reuse a dying input at r12+ in place while a lower register is free, so
       its writes stay low, as Apple's do.
   - **Location limits.** Locations 8+ (or an index after location 7) are refused, because r4L must stay inside the
     32-dword file. The operand byte `0x81 + 4·reg` encodes up to r31. Apple encodes r14 the same way (`0xb9`), and the
     disassembly of `vpds-attr8` (`build/gpu/vpds/isa/`) reads r4..r31 that way while writing only r0..r11.
   - **Limits:** `maxVertexInputAttributes`/`Bindings` 8, `maxVertexInputAttributeOffset` 2047 (the DDMAD base is the
     buffer address plus the offset; Apple uses 4-aligned bases).
3. **`82568ceddf5`: vertex temps past 12** (vertex-temps.md (`vertex-temps.md`, not published)). Apple's compiler emits 72-temp vertex
   programs, and Apple's driver caps nothing. It sizes VDM_STATE5's unified store from the program,
   `2·ceil(max(temps, inputs + 2)/4)`; we held it at 4. Outside the flown envelope (more than 12 temps or more than 8 input
   dwords), the unified size is now the larger of that and the input-based size. The refusal moves to the 32-dword register
   file. Every flown shape is unchanged.
4. **`15ea5d8595e`: shared dwords ≥ 32 through a MOV.** The `vk` rung (two pushed mat4s, sh2..sh33) wedged the TA. Its only
   never-flown form was an inline ALU source above sh31, whose high-index byte Apple uses only on the shared MOV. Those
   dwords now go through the witnessed MOV. No flown shader changes. (It did not cure vk; commit 5 did the real work.)
5. **`5069bd6a02b`: the vertex common store sized as Apple's.** `2·ceil(dwords/16)`, from AGXMetalA9's
   `VertexProgramVariantPDSState`. We asked for 4 (32 dwords) for vk's 34-dword loader. Up to 32 dwords nothing changes.
6. **`1a2ef450a22`: literal multiplies as shift-add chains.** x1's stride-32 offset (MOVI + register IMUL, unflown) came
   out 0. It is now `(x << 4) << 1`, flown forms only, as Apple's compiler builds literal products. (Round 3: x1 is
   unchanged, so this was not x1's cause.)
7. **`b983e1697fe`: the unified store grows above 8 temps** (from the WSI lane, `g5p-wsi-unified@9ceb3df22f6`): the
   captured 4 holds r0..r7. 12-temp draws go 4 → 6, among them vk2 and the ctl/run3 cover VS.

**Not done:** 16 attributes. That needs Apple's packed input layout (byte runs, not r4L) and a register file beyond 32
dwords. Instance-rate attributes are also still refused; Apple fetches them with `DDMAD … t1` (the instance index), as the
second DDMAD above shows. The XFB `attr` variant (16 written temps) is accepted since `82568ceddf5`; it is rung x1.

**Lead (not followed):** Apple also runs a fetch-less, index-less vertex PDS, `fc000002 d3000000` with `STATE5 0x0081`
(unified 0). Our job template already contains it. We still refuse a VS with neither attributes nor `gl_VertexIndex`.

### Offline results (`XFB_LANE=vpds scripts/gpu/xfb.sh build test client repro tramp package repro-tramp trace`)

- **Control:**
  - the upload-fix tree gives the device ICD `a2f00cda…`;
  - the trampoline sources give `081885cc…`;
  - the ICD rebuilds bit-identically (`2ad4a3d2…` twice at `82568ceddf5`), and the client too;
  - the package tarball is deterministic (fixed mtimes and owner, sorted entries, `gzip -n`).
- **Gates:**
  - `g5p-xfb-test`: 263 checks, 0 failures (its temps table is unchanged);
  - `oracles.py`: 10 cases `XFB_EXACT`;
  - all 30 round-17..19 shaders compile identically.
- **The 315 scenes:** identical except `alu` and `two-varyings`. Those two change only `STATE5` unified 4 → 8, for their 12
  and 10 input dwords. Both have 3 attributes, beyond the old advertised 2, and neither has a hardware result.
  `vpds-check.py diff` allows exactly that: Apple's value for the draw's own input dwords, and nothing else in the
  job's GPU arenas.
- **`trace` (every mode's job dumps, run 3's ICD vs the new one):**
  - `ctl`, run3's record/overflow/discard and 7 of the suite's jobs are byte-identical;
  - every `dup` job, `inst` and `inst,vid` differ only in their DDMAD control dword (`00000004` → `80000004`);
  - the suite gains the XFB `attr` case's 3 jobs (refused before);
  - 164 draws, all following Apple's rule;
  - commit 1 vs commit 2: every ladder, run3 and suite job is byte-identical (commit 2 changes no shape that existed).
- **New scenes (`--vi=`):** all 16 record and submit through the ICD and through the trampoline (28 ×
  `G5P117_INIT result=ok`, 41 submits `decision=pass`, fake node `refused=0` in all 56 runs). Their vertex PDS (from
  the job dumps):

| scene | DDMADs (ao+dwords) | DOUTW | STATE5 unified | DOUTU temps | new on hardware |
|---|---|---|---|---|---|
| `inst` | 0+4 L | r4 upper L | 4 | 8 | Apple's exact structure |
| `inst,vid` | 0+4 L | r4 lower, r5 upper L | 4 | 8 | r2's PDS without stores |
| `v2x3` (Hyprland), `,sep` | 0+2, 4+2, 8+2 L | — | 8 | 12 (inputs counted) | unified 8 |
| `attr3`, `,sep` | 0+12 L / 3 × 4 | — | 8 | 12 (inputs counted) | unified 8 |
| `bump` (glmark2), `,sep` | 0+3, 4+3, 8+2, 12+4 L | — | 10 | 12 (writes only) | + reads above the temps (r12..r15) |
| `attr5`, `,sep` | 0+20 L / 5 × 4 | — | 12 | 12 (writes only) | Apple's unified value, reads to r19 |
| `attr8`, `,sep` | 0+32 L / 8 × 4 | — | 18 | 12 (writes only) | beyond Apple's witnessed 12; a 32-dword DDMAD |
| `t16` / `t20` / `t24` | 0+4 L / 0+8 L / 0+8 L | — | 8 / 10 / 12 | **16 / 20 / 24** | vertex temps past 12 |
| `vid16` (vkcube without its UBO) | — | r0 lower L | 4 | 12 | Apple's `occlusion` program + push-constant MVP math |
| XFB `attr` (suite, `--case=attr`) | 0+4 L | r4 lower, r5 upper L | 8 | 16 | the two-buffer XFB capture |

### Package

`build/gpu/vpds/package/vpds-a9.tar.gz` **`e31d0521621bcc79feac26ec992523d3669477fe76281a1b178f76b6189d091f`**

| File | sha256 |
|---|---|
| `libvulkan_g5p.so` (`g5p-vpds@b983e1697fe`, rebuilt bit-identically) | `bc2f7d2bc1e11103cf90235ee8586eef65b881614558f6ac361b17295b0b8fef` |
| `libvulkan_g5p117.so` (pins it; VM-edit guards moved 544 bytes; `G5P117_XFB_DIAG`, `G5P117_ACCEPT_DISCARD`) | `ca44e950420546e2105b4df3d542e272a8b63a421466a844919d42310e8c51bb` |
| `xfb-test` (ladder + `--vi=` incl. `ia`/`mr`/`m32`, `--case=attr16`) | `564701a9277b93f091035b0627b2e8f47b39ff829a6c330aeb674ca2282890e9` |

Offline at `b983e1697fe`:
- gate: 263 checks, 30/30 shaders; scene diffs are STATE5 unified only (the 12-temp draws 4 → 6, `alu`/`two-varyings` 8);
- `trace` passes, with `vid16` and `m32` the intended code changes;
- 188 draws follow Apple's rule;
- 34 × `G5P117_INIT result=ok`, 47 submits `decision=pass`, `refused=0` (68 runs).

### Device results: boots 1–2 (package `0f69c704…`, logs `logs/vpds/`)

| rungs | result |
|---|---|
| r0–r8 (the whole dup ladder, 3000 vertices, splits, aliasing) | **PASS**: the LAST fix also cured run 3's dup stall |
| v1–v4, v5–v11 (instance/vertex index with attributes; 3, 4, 5 and 8 attributes; separate bindings) | **PASS** |
| vk (`vid16`) | **TA wedge** (r1's signature, no MMU fault); labwc lost its device. Cause (common store under-allocated past 32 dwords) and fix: vertex-temps.md (`vertex-temps.md`, not published) |
| x1 (XFB `attr`, 16 temps) | **FAIL, no wedge:** records exact, positions wrong: the stride-32 offset was 0 (decoded in round 2a), vertex-temps.md (`vertex-temps.md`, not published) |
| t16/t20/t24 | not run (skipped after vk) |

**Round 2a (package `0069d2dd…`):**
- PASS: r0, **t16/t20/t24** (the temps fix holds to 24), vk1.
- x1: the same FAIL. Decoded: positions correct, all at buffer 1's slot 0, so the stride-32 multiply gave 0.
- vk2: wedged again (common store 4 < 6).
- Details in vertex-temps.md (`vertex-temps.md`, not published).

**Round 3 (package `73d1e01a…`):**
- PASS: r0, t16–t24, vk1 and all of r1–r8 and v1–v11.
- x1: FAIL, byte-identical.
- im: FAIL (`bands=512/512/512/0`).
- vk2: wedge.
- Analysis in vertex-temps.md (`vertex-temps.md`, not published).

### Device plan (warm-safe, extra client on multiclient2)

**Base:** multiclient2 (`7.3.0-rc1-g995d684abd67`), as an extra GPU client beside labwc. Kill only PIDs you started; never
`qemu-nbd`. Nothing touches firmware power words or the kernel.

**Runner:** `scripts/gpu/vpds-ladder.sh [FIRST_RUNG]`:
- uploads and checks the package;
- runs one job per process, in this order;
- **stops at the first wedge** (`ret=-5`/`ta-wait`): one wedge per boot, and new contexts are refused after it;
- a FAIL without a wedge (wrong pixels, `REFUSED`) does not stop the ladder.

After a wedge, warm kexec back to the base (fine after a wedged client, deskfold rule), then run
`SKIP="<rungs>" scripts/gpu/vpds-ladder.sh <next rung>` with the skips below (example after an r1 wedge:
`SKIP="r2 v1 v2 r3 r4 r5 r6 r7 r8" scripts/gpu/vpds-ladder.sh v3`). Logs go to `build/gpu/vpds/ladder-*.txt` and
`build/gpu/vpds/device/`. The runner's flow (stop at a wedge, resume, skip) was dry-run against a stub SSH.

**Next boot (package `e31d0521…`).** `scripts/gpu/vpds-ladder.sh` from r0. It stops at the first wedge and collects
`out/`. Order: r0, then **x1b, x1, m32, ia, mr, im** (the x1 and im splits, none of which can wedge), then t16, t20, t24,
vk1, then the regression rungs r1, r2, v1–v11, r3–r8, then **vk2 last**.

| rung | args | PASS line |
|---|---|---|
| x1b | `--case=attr16` | `case=attr16 records=6 counters=96/96 result=PASS` |
| x1 | `--case=attr` | `case=attr records=6 counters=96/192 result=PASS` |
| m32, ia, mr, im | `--vi=m32/ia/mr/imul` | `… pixels=4096/4096 bands=1024/1024/1024/1024 result=PASS` |
| t16, t20, t24, vk1, r*, v* | as before | as before |
| vk2 | `--vi=vid16` (now unified 6, common 6) | `vi=vid16 … pixels=4096/4096 result=PASS` |

What each split result means: vertex-temps.md (`vertex-temps.md`, not published).

**Skips after a wedge at rung X:**

| wedge at | meaning | skip |
|---|---|---|
| x1b, x1, m32, ia, mr, im | unexpected: none of them wedged before or should | nothing; resume at the next rung, report |
| t16–t24, vk1, r*, v* | a regression on a shape that passed | stop; report |
| vk2 | not the unified or common size | last rung: nothing to skip |

**Reading the results:**
- **r1 and v1 PASS:** Apple's LAST rule is the fix. Every VS that reads `gl_VertexIndex`/`gl_InstanceIndex` with
  attributes works, and XFB `dup` can resume (r2..r8).
- **v3/v4 PASS:** 3 attributes work. Hyprland's `tex300.vert` inputs are done; its varyings and D/S format remain
  (gles3-gap.md §0.3).
- **v6/v8 PASS:** Apple's register model holds on our stack. Up to 8 attributes are real, and glmark2's bump inputs fit.
- **t16 PASS:** the 12-temp vertex limit was our unified-store sizing. t20/t24 say how far it scales. vkcube's real
  shader then needs only the merge with `g5p-gles3-tex` (its UBO index); `vk` covers its index path.
- **v10 PASS:** the whole 32-dword file works. The next step toward 16 is Apple's packed input layout.
- **A FAIL without a wedge** (wrong pixels) on a `vi=` rung: compare the `,sep` variant to tell the single wide DMA from
  per-binding DMAs, and keep `out/` for the pixel check.
