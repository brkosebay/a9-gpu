# SoC power round 2 cross-workstream handoff

2026-10-22, same SHA256-identified 20H350 j71t kernelcache as the main README.
Static review only; no device access or GPU driver change. Kernel power branch
adds WFI accounting and opt-in SPI PM; it does not alter the GPU's domain hold.

## Host policy table: narrower producer search, still unresolved

New retained decompilations in `docs/power/evidence/`:

- `agx-host-init-round2.json`: `6c9562c` retains accelerator at host+1f0,
  calls virtual+98 (allocation), builds locks/state, then virtual+90
  (`6c95770`, ADT policy defaults). This establishes init order; it does not
  populate host+450 by itself.
- `agx-policy-init-round2.c`: complete available decompilation of virtual+90.
  It writes defaults/ADT coefficients from host+5b8 onward; no direct producer
  of host+450..5af is present in this function's recovered stores.
- `agx-alloc-round2.json`: virtual+98 `6c95da0` constructs shared allocator
  groups via virtual+158 with host-field destination pointers. Host pointers
  must be tracked through that call; immediate-offset text search alone cannot
  prove a table is all zero or identify every indirect producer.
- `agx-shared-control-init-round2.json`: `6c96be8` prepares roots/control and
  companion; finishes by calling virtual+b0 (`6c974f0`, policy export).
- `agx-platform-power-round2.json`: `6ca0f78` maintains power reference count
  at host+cc8 and rejects power-off unless its controller reports stable-off
  (or the explicit exception flag host+212 is set), before provider clock/power
  virtual+568/+570 calls. Supports retaining the Linux genpd reference across
  timed-out firmware; no safe GPU autosuspend protocol follows from timeout.

A raw instruction search found `6c973ec: str w0,[x8,#0x450]`. This is **not the
host table producer**: x8 is loaded from host+220 (host qword index44), and
virtual+1f0 converts host+2b0 (index56) into the stored firmware address. Adjacent
stores fill shared control+44c/+454/+458/+45c. Do not confuse shared control
pointer fields with the inline policy-table bytes at host+450. Other +450
matches belong to different class objects and require the same provenance.

The host+450 → policy+3c copy remains an established byte-transfer edge from
prior raw disassembly. Ghidra currently has no function defined at `6c974f0`;
this round could not obtain a new decompilation there. No fabricated
"successful decompile" is retained. Next producer work should follow inherited
object initialization and indirect/bulk stores covering the entire 0x160 bytes,
then match the source to the export in the same object instance. A failed
immediate-offset search is not proof of zero initialization or irrelevance.

## Shader power sequence and next experiment boundary

The main power report's existing sequence still stands:

1. PMGR GFX genpd on (GFX gate124, PMGR+80340); retain it across DMA.
2. Fender controller initialization and state request; preserve host/firmware
   ownership and busy/ack handling.
3. Host shared roots, companion zero initialization and complete policy inputs.
4. Core hold1, hardware reset/FIFO/MMU/SLC/PIO setup; core hold0 then GPC state1;
   firmware ALIVE and DC consumption before context/job publication.

Exact previously recovered writes are R1+10000=ffff0000, R1+0=40000000,
R1+1000=11 (busy bit4), and R1+20000=20/0 for hold/release. These are already
known controller operations, **not newly proved shader-cluster enable writes**.
They are not a sufficient standalone boot recipe and must not be replayed
while firmware owns policy. No new per-USC voltage, PLL or island write can be
proved in this round, so none is proposed for blind testing.

Next bounded experiment remains observation at the known ownership boundaries:
PMGR target/actual/AUTO and reset fields; Fender hold/busy/ack; stable R0 timer
reads; initialized policy inputs before release; ALIVE/DC/CL progress and output
stamp afterward. Keep existing quarantine on firmware/job timeout. An outer
GFX f/f state plus timer progress rules out a missing *outer* PMGR get but
cannot establish the complete shader sequence. Use the GPU kernel agent's
current runner and keep firmware/PTE/policy variants independent.

CPU idle and Linux PM measurement details: power round2 (`../../power/round2.md`, not published).
Safe next-iOS observation commands: ios-round2 (`../../power/ios-round2.md`, not published).
These do not revive the kernel heap capture that panicked in round9.
