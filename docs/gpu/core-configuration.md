# Core identity and comparison with open Rogue support

## Round 3: measured identity

**Verified on device, orchestrator experiment 02 Run 1:** after the recovered
Fender sequence, RGX `+0x20` returned `0x00090f00` and `+0x18` returned
`0x011b0602`; the system stayed alive. Evidence:
experiment 02 (`experiments/02-fender-meta.md`, not published) and
`docs/test-logs/exp02-id.log`. These are legacy `CORE_REVISION` and `CORE_ID`
words, respectively, not the four 16-bit fields of the newer PBVNC register.

**Verified decode using the pinned Linux `pvr_gpuid_decode_reg` fallback:**
B = revision bits 23:16 = 9; V = bits 15:8 = 15; N = ID bits 15:8 = 6;
C = ID bits 7:0 = 2. The measured legacy BVNC is **9.15.6.2**. Revision
designer/maintenance bytes are both zero; the separate ID field is `0x011b`.
The latter is not the config value and must not be substituted for C.
Apple's `readChipInfo` at `0xfffffff006ca644c` independently checks branch
9/version 15 and copies N=6, then computes `(N+3)>>2 = 2` into another
configuration field. **Inferred:** that latter field groups scalable units;
its hardware meaning is not established merely by the arithmetic.

**Verified compatibility limit:** the pinned Mesa and Linux device-support
tables cited below do not list `9.15.6.2`. Six measured scalable units agree
with the nominal GT7600 family, but do not establish shader encoding,
errata, cache geometry or compatibility with `4.46.6.62`. The GPU-userspace
workstream reports G5P shader encoding differs from Mesa Rogue; kernel
boot remains independent of that compiler/UAPI work.

## What is known

**Verified (local ADT):** `docs/adt/j71t.json` has `/arm-io/sgx` compatible
`gpu,s8003`, clock/power gate 124, and IRQs 170–174. `/arm-io/ranges`
maps child addresses `0..0xffffffff` to ARM physical `0x200000000` onward.
Thus the two 1 MiB GPU windows are **R0 `0x201000000`** and
**R1 `0x201e00000`**. This corrects the brief's `gpu,s8000` statement for
this unit; the kext personality does match both S8000 and S8003.

**Verified (prelink):** AGXG5P's personality is `AGXG5P_C0`, class
`AGXAcceleratorG5P_C0`, `MetalPluginName = AGXMetalA9`; firmware comments
name `g5p_c0_t0.elf` and `g5p_c0_t1.elf`. The carrier accepts revision
argument 5. See [firmware-format.md](firmware-format.md) for addresses.

**Verified (static-pass limitation):** neither the resolved prelink metadata,
printable firmware/host strings nor the identified carrier constants yield
an unambiguous B.V.N.C tuple. This is not proof there is no encoded tuple
somewhere in the stripped executable. No numeric value is promoted to a
verified hardware BVNC in Round 1.

**Inferred:** GT7600 / Series 7XT / six USC clusters is the market-family
hypothesis inherited from the brief, not a hardware measurement. Do not use
that description to select `4.46.6.62` or any other known six-cluster part.

## Concrete open-source comparison

**Verified snapshots:** Linux Hoolock `6831bc701a6ce059e71e5aaa9488c9195bea6927`
and Mesa `566ed9cd9dff0a0825c7ea71fc3c31a4af03d5a7`. Local reference copies
and provenance are in README.md (`README.md`, not published).

| Item | Verified open-source baseline | A9 conclusion / confidence |
|---|---|---|
| Device table | Mesa `common/pvr_device_info.c:83` lists `4.40.2.51`, `4.45.2.58`, `4.46.6.62`, `5.9.1.46`, `15.5.1.64`, three `22.*`, `33.15.11.3`, five `36.*` tuples; no GT7600-named entry in `common/device_info/`. | Verified absence of a named GT7600 entry in this snapshot; numeric membership unresolved until G1. |
| Kernel acceptance | `pvr_device.c:pvr_gpu_support_level` accepts `33.15.11.3`, `36.52.104.182`, `36.53.104.796`. | Verified whitelist; inferred A9 will need new support rather than merely a DT compatible. |
| Closest table by nominal cluster count | Mesa `device_info/gx6650.h` describes `4.46.6.62`, six clusters, two raster pipes, XT top infrastructure, 32×32 tiles, 512-bit SLC lines, FBCDC algorithm 2. | Verified GX6650 values only. Inferred comparison candidate; none of these numbers is verified for Apple's GPU. |
| Firmware processor | Linux supports META/MIPS/RISC-V paths; `pvr_rogue_meta.h` describes META slave port and LDR. | Verified A9 blob is META LDR, with T0/T1 entry words; inferred shared hardware mechanisms. |
| META virtual layout | Linux standard data `0x10000000`, bootloader `0x40000000`, coremem `0x80000000`/`0x82000000`. | Verified Apple record/entry words differ; physical mappings and coremem semantics unresolved. |
| Host FWIF | GPL CCB control write/read/wrap at `0/4/8`; current Linux write/read at `0/0x80`. | Verified Apple device-control ring reads `+0`, publishes `+0x10`, uses 256×20-byte slots; layouts differ. |
| USC ISA / control streams | Mesa has Rogue compiler, PDS and csbgen implementations in `src/imagination`. | Verified source availability. A9 instruction and packet compatibility remains inferred, untested. |
| Hardware errata | GX6650 table lists BRNs `44079,49927,51025,51210,51764,52354,52942,58839,62269,66011,70165,74056` and ERNs `35421,38020,38748,42064,42307,45493`. | Verified baseline only. A9 BRN/ERN set is unknown; copying these flags would invent a match. |

**Verified host constants with unresolved field meanings:** constructor-like
code at VA `0xfffffff006ca8bd8` copies vectors at `0xfffffff005940d10`
(`[0,1,1,1]`) and `0xfffffff005940d00` (`[1,1,1,0]`) into object offsets
`0x378..0x397`. Base configuration code at `0xfffffff006c80028` copies
`[0x24,0x24,0,0]` and `[3,3,3,1]` from `...40ce0`/`...40cf0` to its
configuration object. **Inferred:** these are configuration fields, but
their semantic names are not recovered; they are not a verified BVNC or
cluster count. Preserve these leads rather than assigning guessed names.

## ID layout and superseded experiment-01 scan

**Verified from Linux definitions:** legacy `CORE_ID` is GPU-register
offset `0x18`; `CORE_REVISION` is `0x20`. Decode branch/revision from
revision bits `23:16` / `15:8`, and N/C from the low-16 config half of ID:
N=`bits15:8`, C=`bits7:0`. The newer PBVNC form occupies `0x20..0x27` as
four 16-bit B,V,N,C fields (`pvr_rogue_cr_defs.h:336`,
`pvr_device.c:pvr_gpuid_decode_reg`).

**Verified correction:** experiment 01 wedged on a bare RGX access; its
two-window/raw-register scan is superseded. R0 is RGX and R1 is Fender.
Follow the staged Fender wake and `+0x20`, `+0x18` order in
experiment 02 (`experiments/02-fender-meta.md`, not published). No `+0x24` measurement was
needed or performed for this legacy decode.
