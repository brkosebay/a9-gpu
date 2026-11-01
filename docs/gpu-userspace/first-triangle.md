# First triangle without Mesa: staged plan (round 2, 2026-10-22)

Goal: the quickest end-to-end proof that Linux can drive the G5P. Each stage
removes one dependency on Apple-produced bytes. Compute comes first because
it needs no parameter buffer, tiling or render-target state. It is the
**gate** for the triangle, not a substitute for it.

Inputs:
- [command-stream.md](command-stream.md): records, streams, PDS, heaps.
- `tools/gpu-userspace/g5p_isa.py`: decoder and shader encoders, corpus
  verified.
- The iPad captures from device-run.md (`device-run.md`, not published).

Labels: **verified** / **inferred**.

**Verified round-3 update:** the external Mesa PCO-IR encoder now reproduces
the same own VS/FS bytes ([mesa-plan.md](mesa-plan.md), 133 checks, zero
failures). The kernel's [submission notes](../gpu/submission-g5p.md) recover
CL work/stamp/microsequence fields and identify an **inferred** absolute B
candidate of `0x300000000`; its [DRM design](../gpu/drm-design.md) proposes a
separate render-only G5P backend. These offline results do not satisfy C1:
firmware/queue, VM, absolute B and real compute still need device validation.

**Verified round-4 update:** round4-report.md (`round4-report.md`, not published) adds real
NIR compilation, compute byte emission and a one-command C1–C3 mock. There
is still no A9 `one`/`store` capture or Linux submission. **Reported by the
orchestrator:** G2 hard-locked on R0+0xa18; it is not a completed firmware
gate. The UAPI review (`uapi-needs.md`, not published) accepts the separately gated
`apple_g5p` design and keeps real compute disabled pending live context/VM,
verified base, complete state and retirement/cache semantics.

## Kernel prerequisites (needs for docs/gpu/PLAN.md G2/G3, not edits)

1. **Firmware alive:** META firmware booted, with an observable heartbeat
   or event.
2. **GPU address space:** page tables for one context, and BOs mapped at
   **caller-chosen GPU VAs**. The iOS captures use `0x1_0000_0000`+ (40-bit).
   Report page size and coherency; provide explicit flush/invalidate.
3. **Code-heap base B:** every USC/PDS address in the streams is an offset
   from one base (verified). The kernel programs B the way the kext does.
4. **One compute submission:** the firmware CL command built from {CDM
   stream VA, terminate-word VA, scratch VAs, B}. Bounded wait, completion
   status, and fault/timeout reports that preserve queue state.
5. **For render (stage R1 on):** TA + 3D commands, parameter buffer/free
   list, region headers and render-target memory (kext classes
   `AGXParameterManagementMeta`, `AGXRenderTargetMemoryG5`).

**Inferred interface, superseding the original debug-blob proposal:** use
the kernel's typed BO/VM/context/submit/syncobj contract reviewed in
uapi-needs.md (`uapi-needs.md`, not published). The kernel builds firmware work objects,
microsequences and completion state. CDM/terminate/scratch alone do not
describe all recovered PDS/context fields, and B must be queried after
verification rather than arbitrarily supplied as a register setting.

## Compute stages

**C1 — replay at identical VAs.**
- **Runs:** the `regions one` capture (`k_store_one`, 1×1 dispatch):
  code-heap pages (USC + PDS), the data buffer (CDM stream, PDS data,
  argument table, scratch) and the output buffer, all mapped at the
  capture's own GPU VAs.
- **Apple bytes:** all of them.
- **Success:** `out[0] = 0x12345678`, the other words untouched, clean
  completion, firmware still alive.
- **Needs:** prerequisites 1–4. No relocation at all.

**C2 — own shader.**
- **Runs:** C1 with the USC replaced by
  `g5p_isa.py assemble-one 0xCAFEF00D …`.
- **Apple bytes:** PDS, CDM, argument table.
- **Success:** `out[0] = 0xcafef00d`. This is our encoder on real hardware.
- **Needs:** C1.

**C3 — own PDS + CDM.**
- **Runs:** DOUTU/DOUTD programs built with Mesa `pdsc` (DOUT_EXT layout),
  a hand-built 7-word CDM record + terminate, and our own argument table.
- **Apple bytes:** regenerated PDS/CDM; USC prologue/sync parcels still
  come from characterized templates until their semantics are decoded.
- **Success:** a chosen test constant at our own VAs, with retirement and
  sentinel checks (the round-4 mock uses `0x87654321`).
- **Needs:** C2; the unresolved KERNEL0 size field and type-3 packets settled
  by trying with and without them.

Notes (verified unless stated):
- `assemble_one` reproduces all 25 compiled fixed-store kernels
  byte-for-byte. Its branches are computed, but its prologue and sync
  parcels are copied templates (not yet decoded).
- The captured k_fadd chain (archive → resident USC → PDS → CDM) is closed,
  so the same chain is expected for `one` (inferred until captured).
- Apple adds driver helper tasks and type-3 packets after the user dispatch.
  C1 keeps them; C3 tests whether they are needed. Don't drop them silently.

**Verified offline runner:**

```sh
python3 -B tools/gpu-userspace/compute-harness.py \
  --compiled-usc third_party/g5p-round4/corpus/k_one_87654321_0/g5p.bin
```

**Verified scope:** default C1 is a synthetic fixture, C2 replaces USC and
C3 regenerates/relocates bytes. All three pass the byte-consuming mock;
none satisfies the hardware stages above. `--backend kernel` deliberately
returns blocked. A future `--replay-manifest` needs a validated A9 capture
mapping, and `--transport-module` needs the actual kernel adapter. See
round4-report.md (`round4-report.md`, not published) for the protocol and limitations.

## Render stages

**R1 — replay a draw.**
- **Runs:** the `vc` capture (vertex buffer) or the run-1 `render` capture
  (`v_novb` + `f_solid`). Map the VDM stream, PPP state, all PDS programs
  (vertex, fragment, background, end-of-tile), the USC code, and the render
  target at the captured VAs.
- **Apple bytes:** all of them.
- **Success:** the 64×64 target reads back as the iOS result. Expected
  values: solid = 4096 red, vc = 2048 shaded. These are host-verified; the
  iPad `run` that records the real iOS reference pixels is still pending
  (§2a).
- **Needs:** prerequisite 5. The kernel must rebuild the TA/3D commands and
  the parameter buffer, which no userspace capture contains.

**R2 — own fragment shader.**
- **Runs:** R1 with the fragment USC replaced by
  `assemble_solid_fs((0,1,0,1))`.
- **Apple bytes:** all but the fragment USC.
- **Success:** a green triangle, proving our fragment encoder.
- **Needs:** R1.

**R3 — own vertex shader.**
- **Runs:** R2 plus `assemble_passthrough_vs()` and our own vertex buffer of
  three positions. The vertex PDS from the `vc` capture fetches `pos[vid]`
  into r0–r3.
- **Apple bytes:** PDS, VDM, PPP, background and end-of-tile programs.
- **Success:** a green triangle with **our coordinates**. This is the first
  "own triangle".
- **Needs:** R2.

**R4 — own streams.**
- **Runs:** VDM and PPP state built with Mesa `csbgen` (vdm.xml/ppp.xml),
  our own vertex-fetch PDS (Mesa `pvr_pds` vertex program, DOUT_EXT),
  background/end-of-tile programs and PBE words.
- **Apple bytes:** none.
- **Success:** same image from fully self-built bytes.
- **Needs:** R3, PBE/ISP/region-header layouts (open), more captures.

Encoder evidence (verified by current `g5p_isa.py encoder-tests`, 22 checks
against the saved corpus, zero failures):
- `assemble_passthrough_vs()` is byte-identical to Apple's `v_passthrough`
  as compiled on the host **and on the iPad**.
- `assemble_solid_fs()` is byte-identical to `f_solid` and value-identical
  to all 14 constant-color variants (BGRA8 and RGBA32F targets).

## Before each hardware attempt: offline checks

1. Rebuild every blob from the captures with the strict reader
   (`g5p_cs.py run1`/`regions`), which rejects uncaptured pages. Never
   replay zero-filled holes.
2. Disassemble every USC blob with `g5p_isa.py dis` and every PDS blob with
   `pds_dis.py`. Every pointer must land inside a mapped blob, and every
   branch target on an instruction boundary.
3. Keep Apple-derived blobs under `build/`. Only encoders and the plan live
   in `tools/` and `docs/`.

## Failure handling

- A fault or timeout must leave the firmware queue state and GPU fault
  registers for the orchestrator. Never retry blindly; the next attempt
  changes exactly one thing.
- A changed output without clean completion counts as partial evidence, not
  success.
- Order of suspicion when C1 fails: wrong B, missing cache flush of written
  blobs, scratch/context-switch state the firmware expects, type-3 helper
  tasks.

## Next captures that unblock stages

From device-run.md (`device-run.md`, not published) §2a, queued for the next iOS window:
- `regions one` for C1.
- `regions vc` and `regions tex` for R1/R3, with buffer VAs and texture
  resource IDs logged.
- `run` to confirm the `APINotSupported` fix and record iOS reference pixels.
