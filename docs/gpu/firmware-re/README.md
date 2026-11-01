# G5P firmware research — 2026-10-22

SoC power round 2: [host policy producer search and power ownership](power-round2.md).

Offline instruction replay: emulator round1 — conditional TA event20 boundary,
capture gaps and next r25 variants (`emulator-round1.md`, not published).

TA→VDM kick path, register by register, and the CDM comparison:
[ds-fw-ta-kick — the ordered write list, its inputs and the ranked suspects](ds-fw-ta-kick.md).

R26 implementation and replay follow-up: real raw20 collection, corrected
GbP mapping and physical geometry clock profiles (`../experiments/26-event20.md`, not published),
with the ordered warm-boot ladder (`../experiments/26-device-tests.md`, not published).

Research only; no kernel changes, commits or device access. The orchestrator owns
kernel implementation. Generated firmware/tool binaries stay in `build/gpu/firmware-re/`.

## Round 16 — first normal TA→3D job: PM bootstrap, HWRT arithmetic and retirement

2026-10-23. Research only: **no device access, kernel edits or commits**.
The starting hardware result remains r19c's numerical CDM store in user slot7;
the r19d helper-END/retirement fix and r20 user-BO path are implemented but are
not promoted here to observed passes. Read
r19c (`../experiments/19c-context-activation.md`, not published),
r19d (`../experiments/19d-completion-retirement.md`, not published) and
r20 (`../experiments/20-user-compute.md`, not published) before selecting a device baseline.

**Implement the first draw as a fresh, serialized, normal tag1 3D job,
64×64 BGRA8, one sample, with the complete captured VDM/helper stream.**
TA is tag0; normal3D is tag1; neither is CDM tag3. New concrete results:

* G5P PM parameters are **`32, 0x80, 0x10000, 0x600, 0`**, supplied by
  accelerator virtual808, `6ca5e9c`. They close the scaffold's S1/S2/S3
  inputs: two per-record backing strides and one shared backing extent.
* TA and 3D use **the same PM control and selected state record**:
  `Dcb8=D718`, `Dcc0=D720`. These are not pool8/pool7 from userspace.
* The actual render kicks are **R0+b00=10 for TA, 11 for 3D**; CDM uses12.
  Round15's prose saying the CL wrapper uses10 is incorrect; its cited
  `6ca0358` and the retained Linux `compute.inc` both use12. DC submission
  still uses10. These MTS values are distinct from notification type,
  hardware context slot, channel identity, stamp identity and ring endpoint.
* The unmodified G5 render-target arithmetic routine has been executed
  **offline in an ARM64 userspace process**, with no allocator/MMIO/kernel
  calls. It yields the exact 64×64 state below. The unmodified 3D wait
  emitters independently reproduce the complete **0x88-byte** wait block,
  including the relative-loop word; this is host arithmetic, not GPU execution.
* TA's finish/retirement stamps are **W530/W52c**; normal3D's are
  **W88c/W888**. The second is a later retirement mirror, as W258 is for CDM.

Same j71t 20H350 kernelcache, SHA256
`c6b95fcf0efe52ee9258454d01d14adc9b0cb922d94c54a04a4ce11d1ae81e0a`.
Short host addresses below have prefix `fffffff0`; firmware addresses are
MINIM execution PCs. Offsets, opcodes and numeric constants are **hex**
unless explicitly decimal. Full decompiles, raw firmware spans, GNU checks,
arithmetic-oracle outputs and machine-readable recipes are in
`build/gpu/firmware-re/round16/`. **[Proven]** means source/byte backed;
**[Derived]** is the proposed bounded Linux implementation, not a device result.

### 1. Prepare one render VM, two channels and two user bindings

**[Derived]** On a fresh firmware epoch, reserve slots0..6 and acquire
**3D first, then TA**, matching `6c7ad2c →6cb9afc`. If this is the first
user operation, this gives **S3D=7, STA=8**. Do not run CDM first and then
overwrite its retained slot7. On an already occupied pool allocate another
pair; handles/PM IDs are not hardware slot numbers.

Both bindings point at the **same owned physical PC**, with the working
16KiB PCE/PDE/PTE format. Map the twelve bounded userspace BOs, kernel RT
backing, PM hardware backing, and the private context resources needed by
both engines before publication. W/SKU/CH/control/stamps/dependencies use
kernel GPU→META aliases, with firmware mappings retained as in r20. A GPU
VA in an upload block remains a GPU VA; only a field consumed by META as a
pointer is converted with M(). Never substitute a CPU pointer or catalogue PA.

For each new S, publish unshifted `PA[S]=PC_PA` and the packed host MMU tag,
complete table/DMA visibility, then consume **DC `{3,S,0,0,0}`**. DC3 writes
e140=S/e148=PA>>12 and PC-invalidates `(S<<3)|4`; the normal engine launch
does not bind a new PC. For the example pair these invalidate values are
**3c/44**, or **3f/47** for an explicitly requested all-level invalidate.
Keep the already qualified firmware boot/DC8/power path. There is **no
additional recovered render-specific DC kick**: PM setup is TA work below,
and render launch is pointer-ring work plus TA/3D notifications.

Construct one CH per engine using `6c89420`'s layout, not an inline W in a
global notification. CH+0 points to its control, CH+4 to its pointer ring,
CH+8 to the retained dependency/context object; CH+c=4 and CH+18=ffffffff.
Control+50 is ring capacity. Preserve the CDM-proven dependency object's
sentinels and firmware-mutated state; a zero replacement is unsupported.

| Engine | Global notification ring, host object fields | Boot-layout CPU object IDs; root pointers | Entry; MTS kick |
|---|---|---|---|
|TA|firmware+300 control, +308 data|60/61; root+0/+4|`{0,M(CH_TA),endpoint_TA,0,0}`; b00=10|
|3D|+320/+328|64/65; root+8/+c|`{1,M(CH_3D),endpoint_3D,0,0}`; b00=11|
|CDM comparison|+340/+348|68/69; root+10/+14|type2; b00=12|

Source: **`6c97d84 /6ca00a0`** (TA), **`6c97f10 /6ca01f8`** (3D),
`6c980a0 /6ca0358` (CDM). These global rings have256 entries, stride14,
consumer+0 and producer+10, modulo100. `endpoint` is the **next channel
pointer-ring index after the published work**, not the MTS value or sequence.
Check free space in both rings, write each work META pointer, DMA barrier,
publish CH.control+40; barrier, publish the appropriate global producer;
barrier, perform the existing Fender/power pre-kick check, then MTS.
Firmware copies the notified endpoint into CH.control+30.

For an initially empty channel, `[PM tag6, TA tag0]` gives TA endpoint2;
`[dependency tag4, normal3D tag1]` gives3D endpoint2. A single already
host-waited 3D work would give endpoint1, but retaining the explicit tag4
is the faithful first paired-job implementation. Do not insert a work
object's byte length into the endpoint field.

### 2. PM: one record, physical free pages, firmware-owned reserved contexts

There are **three different namespaces**:

1. User MMU engine bindings, slots7+ (two for a render VM).
2. PM resource IDs0..126, managed by accelerator+11a0, independent of VM slots.
3. Selected PM state-record index `P2c`, bounded by record count `P50`.

For a one-job diagnostic, **[Derived]** retain PM ID0 and record0, N=1,
with no reuse/growth/unbind until reset. N=1 is a serialization policy,
**not an observed iOS configured record count**; iOS chooses accelerator+ca4
or its +538 default. The normal firmware packets receive a selected record
pointer, not N or a request to allocate user MMU slots1..6.

**[Proven sizing]** `6ca5e9c` returns four qwords from5940fc0 and explicitly
zeros the fifth. `6caf9f8` stores them in P1c0/1c8/1d0/1d8/1e0;
`6cb2378` uses:

```
pages_per_128KiB_block = 0x20                  //4KiB hardware pages
control size = 0x80 + N*0xc0
aux size     = N*0x80 + N*0x10000 + 0x600
S[i]         = control_GPU + 0x80 + i*0xc0
S[i]+a8:u64  = aux_GPU + i*0x80
S[i]+a0:u64  = aux_GPU + N*0x80 + i*0x10000
S[i]+b0:u64  = aux_GPU + N*0x80 + N*0x10000
S[i]+b8:u32  = M(list_GPU + 0x40)
```

Thus N=1 needs logical control140 and auxiliary10680, rounded to actual
16KiB mappings. Initialize the explicit state zeros in `6cb2378` (qwords
00..48 and90), supply all a0/a8/b0/b8 fields, and retain the whole c0 record.
Its intervening fields are **firmware outputs**, not missing userspace BOs.
Zero allocation followed by these constructors is reasonable Linux policy;
an unfilled all-zero PM object is not a valid constructor.

Choose separately **B resident blocks** and **Bmax capacity**. A conservative
first diagnostic can preallocate B=Bmax=32 decimal, **4MiB**, with both
limits fixed at that size and OOM treated as failure. This is a test budget,
not a proved minimum or the iOS capacity default. iOS's capacity getter
`6ca8aac` rounds in1020000-byte quanta and considers5% of accelerator+2a8;
one quantum is129 blocks. Do not confuse that host policy with a hardware
requirement to fault/grow on the first triangle.

Construct the pool as `6cb1108/6cb124c/6cb13f4/6cb14dc` do:

* Allocate B separate20000-byte blocks. At16KiB CPU pages each supplies
  eight DMA pages. The eight pages need not form one physically contiguous
  128KiB allocation; retain every DMA mapping. For block j, the linked-list
  slice is **`L+80+j*24`**. Its eight dwords at+4..20 are **physical
  `PA>>12`**, not GPU VAs. Each is expanded into four consecutive4KiB PPNs
  in the hardware free-page array **F**, giving B*32 entries. Validate the
  same40-bit PA bound as the user MMU; CPU16KiB pages and PM4KiB frames differ.
* Link slice+0 to the prior slice's **META** address, initially0; install
  M(last_slice) as control+10. Build F in backing/list traversal order,
  not by inserting META list pointers into the physical-page array.
* Control+14 is **unaligned u64 F_GPU**, +1c its descriptor extent;
  +20=B*20 pages, +24=B*20−1, +28:u64=0, +30=0,
  +34=4000 (CPU page size), +38:u64=0, +40=M(L_GPU).
  Reset also clears control+0/+c/+44/+48/+50 and initializes limit
  dwords+54/+58 to their corresponding block limits shifted left5.
  The usage block at L+40 has dwords0/4/8/c=0 and+20=1.
* F's requested size is Bmax*20*4. The Apple list allocation request is
  **44+Bmax*24**, but its constructor places slices at **+80**. Preserve
  actual rounded allocation coverage through **80+Bmax*24**, including
  the last slice; the two logical extents are not interchangeable. For
  the proposed32-block budget both fit one4000-byte DMA allocation.
  Keep P40/P48 and P88/Pb0 host pointer arrays out of firmware memory.

`6cba78c` copies P98 and the selected Pb0 entry to **Dcb8/Dcc0** and then
calls `6c7b73c`, which copies exactly those sources to **D718/D720**.
Therefore **TA.W14=3D.W18=M(control_GPU)** and
**TA.W18=3D.W1c=M(S_GPU)**. `6cb091c/930/944` also expose a8/a0/b0 as GPU
addresses. Do not create separate zeroed S objects for TA and3D.

**First PM work is tag6, not tag5 and not a DC.** Pool reset leaves P28=1
and P26c..274=0; the TA builder `6cb9050` selects tag6 for state1 or3,
tag5 for state2. With a host-prebuilt initial free list the first packet is
**`{6,STA,PMid,M(control),0,0,0}`**,1c bytes. It precedes TA in its pointer
ring. Do not claim tag6's zero *growth* fields mean an empty pool: control+10,
F, counts and the physical backing above must already be valid. Growth's
tag5 `{5,STA,PMid,M(control),new_head,last}` is not needed in the bounded
preallocated first test; reset/growth states must not be silently mixed.

**[Proven — PM MMU ownership]** Boot `100187b4` installs f088=30102 and
f090=60405: TE0/VCE0/ALIST0 use1/2/3, the other bank uses4/5/6. User PA-array
publication has no ordinary low-slot allocation caller: boot zeroes the
array and installs even its zero entries; the host user allocator starts7.
There is **no recovered requirement to DC3-bind the render PC to1..6**.
PM's reserved translation state follows its own hardware load/save/restore:

1. Fresh TA23 selects STA, uploads A/B, and takes the zero runtime-flag path
   **800067b0→8000a874→10005f30→1002706c→800098a4**.
   Firmware writes **240=S.a8&~f,248=S.b0&~f,2d8=S.a0&~f**, performs
   **290=1,198=1,258=1** with their completion waits, and clears S.bc.
   The aux addresses must be mapped in the engine VM used for these loads.
2. TA24 saves bank0 through **8000a844→10005ef0→1001886c(S,0)**.
   This populates PM runtime/MMU state from hardware. Its PA/catalogue-like
   values are **not** the ordinary user catalogue PA or the original GPU
   address of aux. Preserve them as produced.
3. Normal3D25 unconditionally reaches
   **8000a824→10005e18→10018e5c(S,1)** before its hardware upload. This
   restores bank1 from S and PC-invalidates its reserved requester triple.
   Bank0 versus bank1 use register groups
   **e008/e028/e048/e098/e0b8/e0d8** versus
   **e050/e070/e090/e0e0/e100/e120**, plus the associated PM registers.
   `100193a8` supplies reserved-triple SLC/MMU maintenance on its paths.

These are special firmware-managed PM translations. Their complete hardware
bitfield names remain open; assigning them the user CBASE e148 format would
be an invented encoder. Supplying PM backing, tag6, shared S and the TA→3D
dependency lets the existing firmware perform the actual binding. The host
must leave f088/f090 and these transactions under firmware ownership.

### 3. Kernel RT/HWRT construction for the measured small render

The relevant kernel object is **AGXRenderTargetMemoryG5**, host size2b8,
vtable6ff64a0. It is not a userspace pool and not a contemporary Rogue
`RGXFWIF_HWRTDATA` blob to paste into this firmware. Its setup is
`6cb3bcc →6cb3d68 →6ca6d5c`, with secondary packing/allocation in
`6cb40e0`. Host importers **6cba480/6c7b64c** turn it into D fields;
round6's complete **6ca9710/6ca7ae0** fillers turn those into hardware blocks.

The input at R+40 is the14-qword RT parameter copy. The bounded capture's
first five dwords are **64,64,1,8,8 decimal**: dimensions, one sample and
sample position. Keep the rest from the bounded color-only record, with
unsupported attachments/multiview/resource arrays rejected. The arithmetic
leaf's exact output for these inputs is:

| R bytes | Value | Constructor consequence |
|---|---|---|
|10,20|500,50, u64|Header payload; associated count/size field. `6cb3d68` rounds R10 to1000.|
|b0; b8/bc|88:u64;0/40004:u32|Packed one-sample state and padded dimensions.|
|c0,c8,d0|3b89309c3b89309c,3cddb3d93cddb3d9,4480000044800000|Exact packed float scaling; do not use decimal approximations.|
|f0,f8|0,88, u64|TA state.|
|100,108,110|00001001003f003f,0010100c0010100c,10|Two qwords and a dword.|
|118|4:u32|2×2 actual32×32 tiles.|
|138,178|800,40, u64|Secondary extent and its alignment.|

For a fresh one-job object, the common initializer clears optional extents
R30/R250 and R140..170. `6cb3d68` retains a **10000-byte main ring allocation**
for this case (power-of-two capacity for16 times the1000-byte slice), selects
offsets **R298=0,R2a0=1000,R2a8=1000**, and reserves the slice through its
completion token. `6cb40e0` places the sole800-byte secondary extent at
offset0; map a complete4000-byte page for it. Main and secondary allocations
are **additional kernel-owned resources**, absent from the twelve-BO package.
They are not justified by the userspace pool7/8 zero snapshots.

Let H/Haux be their full GPU addresses. Let L/Laux be the address returned
by the legacy-address getter for those mappings. **`6caa694`** returns the
GPU address, or subtracts the selected heap base when mapping+58.bit0 is
set; `6caa4bc` derives that flag from mapping flags bit36. Preserve the
selected mapping dialect, rather than using a physical DMA address here.
For native mappings with that bit clear, L=H. The direct first-draw anchors
are then:

```
TA.W28=L; TA.W30=TA.W444=Laux; TA.W44c=0x800
TA.W38=0; TA.W40=0x88
TA.W3ac=0x00001001003f003f
TA.W3b4=0x0010100c0010100c; TA.W3bc=0x10
3D.W20=H; 3D.W28=0x88; 3D.W30=0; 3D.W34=0x40004; 3D.W38=0x50
3D.W3c/44/4c = the three packed float qwords above
3D.W58=0x88; 3D.W3a0=0x40004
```

These are **anchors, not the entire W**. Allocate/construct full TA560 and
normal3D8c0 envelopes, fill **every direct A/B read** listed in
round6-hardware-reads.txt (`round6-hardware-reads.txt`, not published), and preserve the full
runtime/context blocks TA464..50f and3D6d8..7ff plus804..84c helpers.
Importers/fillers in the bulk captures are the byte-offset authority for
the remaining clear/EOT, VDM, context PDS, depth and resource fields.
Reject unsupported nonzero resource-array counts instead of inventing
per-core descriptors. Retain the normal zero initial runtime flag written
by the constructors; it is separate from userspace scratch[0].

The same unmodified arithmetic leaf gives one actual tile at32×32, but it
still pads the header payload to500 and needs the800 secondary extent.
**Do not start by shrinking the payload to one tile**: the captured PBE,
viewport, scissor, rectangle helper and expected pixels are64×64. Changing
only RT dimensions would leave inconsistent GPU state.64×64 is the smallest
complete source-and-pixel-backed input currently packaged.

### 4. Exact microsequences and TA→3D publication

Use **normal3D25/26**, selected by D630=0. The descriptor constructor clears
that field; the bounded one-color case does not need alternate tag2/27/28.
TA's work slot is W4=STA;3D's W4=S3D. Start and finish context operands must
agree with these and with DC3. Let w=M(W), ch=M(CH),
g=M(the engine's global object; firmware host+288 TA /+290 3D),
p=M(S), c=M(control), k=*(u32*)(D138+4), s=a new sequence. k is copied from
the retained dependency/context object; do not substitute the packed MMU tag.

The full word-level recipes, including mode operands, are:

```
TA start,44 bytes:
23 w+3a4 w+20 p g+724 ch STA k PMid b(bd8) Dcf8 W53c
   w+464 b(bd9) b(bdb) (D760==2) s
TA finish,38 bytes:
24 p c g+724 ch STA (b(bd8)!=0) w+464 b(bd9) b(bdb) s
   W530 W534 signed_backlink

3D start,54 bytes:
25 w+3a0 w+58 p g+724 w+7f0 w+7f4 w+878 ch w S3D k PMid
   b(6b5) D764 W898 w+6d8 b(6b1) b(6b3) (D760==2) s
3D finish,48 bytes:
26 s W88c W890 p c (D681==0 ? b(683)^1 :0) g+724 w+878
   w+7f0 ch w S3D w+6d8 (D318!=0) b(6b1) b(6b3) signed_backlink
```

`b(x)` is D's byte, not a W offset. Source **6ca9368/6ca7388**. Timestamp
enabled, instrumentation off is the captured normal choice: TA Dbd8=0;
3D D6b0=0. Finish3D's stamp-enable expression evaluates1 from the retained
record's D681=D683=0 (whole+4c9/+4cb, because 3D subrecord base is whole+208).
This base is **whole+38+1d0**, not whole+1d0. Preserve all expanded mode
fields from the validated bounded record; do not enable instrumentation.

Between start and finish, emit the normal two20-byte-hex timestamp packets:
`80000018 result timestamp0 timestamp0 s`, wait block,
`18 result timestamp0 timestamp1 s`. For TA the result/timestamp pointer
fields are W550/558/55c;3D uses W8ac/8b4/8b8. The packet operands address
those fields within W. Optional external timestamp destinations may remain
zero, as the existing CDM normal recipe does; they are not GPU color targets.

TA's entire wait is **`00000801`**, internal event mask20 (`801>>6`).
3D's wait is **`00000201`**, mask8, followed by **all** the following words
from6ca81a4 and accelerator virtual6b0=`6ca8598`:

```
00138204 00000010                      //R0+138 write10
for i=0..C-1:
  08388204 i 0104000a 0000001f 0000001f
00f48008 10000015 00000004 00000000 00000014
0000109f                             //resolved relative-loop word
0013000a 00004000 00004000
00138204 00004000                      //ACK4000, NOT40
for i=0..C-1:
  08388204 i 0104000a 00000008 00000008
```

C is the partition count used by accelerator+378, **not the six USC-cluster
count**. The G5 read-chip-info routine6ca644c computes the corresponding
count as `(clusters+3)>>2`; the retained A9 ID011b0602 has six clusters, so
C=2. With that input, the two unmodified wait emitters produce exactly88
bytes, independently equal to `3d-wait-two-partitions.bin`. The derived
109f includes the forward patch performed after emitting the poll; copying
the builder's intermediate49f would be wrong. Keep the register polls and
the4000 event even if mask8 has already been acknowledged by the callback.

Append **40000017**, then round the *allocation/advertised* length to40.
With the choices above TA has finish at+70, backlink **−70**, terminal+a8,
used lengthac, advertisedc0.3D has wait+68, finish+104, backlink **−104**,
terminal+14c, used150, advertised180. TA stores SKU pointer/length at
W454/458; normal3D at Wc/10. Recompute every backlink after any packet
change; compute's−6c and c0-byte SKU are not render templates.

Publish TA `[tag6,tag0]`. Publish 3D
**`{4,TA_finish_stamp_address,TA_required_value,sequence&7fffffff}`** before
its tag1 W. The tag4 stamp is the META word addressed by TA.W530; it is not
the later W52c mirror. Firmware compares signed32 `required−*stamp >0`
and defers unsatisfied work. TA therefore controls eligibility while the
whole PM/RT data set remains pinned through3D. Do not use global-ring
consumption, TA mask20 or a host software fence as this dependency.

### 5. Completion, caches, lifetimes and CDM differences

**TA**: wait for internal mask20; finish24 waits for **430.bit0=1** and tests
430.bit1, saves PM bank0, runs cleanup and stores **`*W530=W534`** at
80006a1c on its normal path. It requests engine1 scheduler follow-up.
The TA retirement scan **80003584..39f4** copies six dwords starting at
**W52c** to its local record (four plus two dwords at800036d4..36ec),
retires the channel, then calls **100104d8(0,record,last_sequence)**.
That publishes **`*W52c=W534`** and the host stamp event. The10 independently
decoded MINIM anchors in `retire-isa.json` include this copy and its call
argument, not just a guessed parallel with compute.

**3D**: wait for mask8 **and the complete wait block**, then finish26 polls
**1090.bit0** through80007d7c. Its normal path increments **S+0**, stores
**`*W88c=W890`** when the finish-mode operand is1, and performs the remaining
cleanup before terminal17. The3D retirement scan copies the six dwords
at **W888** (80001030..104c), retires the channel and calls
**100104d8(1,record,last_sequence)** at800014e8. The mirror is
**`*W888=W890`**. A finish stamp may be visible before the rest of its
microsequence and channel retire: wait for the latter too before CPU access.

Both use execution-slot active-bit clear → `100130a0` ctl+10 update →
engine retirement scan ctl+0 update → retirement mirror → type1 event.
The44-byte type1 event sets bitmap[identity>>5] bit(identity&31), consumed
through the existing event/IRQ174 path; choose distinct owned identities
(for example TA0,3D1), not7/8 because of the MMU slots. W538 and W894 carry
those identities. Internal hardware masks20/8/4 and host IRQ174 are separate.
Match each fresh target and reset epoch; keep both channels' watermarks
ctl0/10/20/30/40 and both global rings drained. Expected endpoints include
the PM/dependency records. Global runtime words G724 etc must also be idle.

| CDM path already present | Additional/different render obligation |
|---|---|
|One role2 user slot; W8; start32/finish33|Two roles0/1, W4; start23/24 and25/26; same PC, separate slot bindings.|
|GPU CDM terminator80000000 plus firmware17|Use the captured VDM through+284, including helper/barrier/trailing rectangle; retain firmware17 independently.|
|Internal4; context-store4a0; W25c/W258|TA20/430 and3D8/1090 plus explicit3D wait polls; W530/W52c and W88c/W888.|
|Private CDM scratch/terminate helpers|Retain TA/3D save/resume helpers, RT buffers, user scratch/markers, full PM state and parameter pages. No CDM-shaped zero W.|
|One type2 global work ring, kick12|TA type0/kick10 and3D type1/kick11; independent pointer rings/endpoints.|
|No PM pool|Physical4KiB free pages in128KiB blocks; PM ID, selected state record and fixed requester banks1..6.|
|One VM tag publication|Publish the same hierarchical MMU tag to **both** resident user bindings; PM save/restore has separate reserved-triple invalidations.|
|CDM finish cleanup plus CPU DMA sync|TA PM-save/cleanup orders parameter production;3D wait/cleanup orders pixel stores. Wait for final retirement, then DMA-sync the linear target to CPU.|

Keep the successful bring-up cache configuration as a baseline while
adding the pair. If its explicit host all-level invalidation is retained,
apply it to both user slots; do not replace the PM firmware operations with
that helper. The normal steady-state render path owns its maintenance in
firmware; no new evidence requires replaying boot11f00/688/1720 before
every TA or3D job. CPU→GPU visibility of BO contents, visibility of
page tables, MMU invalidation, firmware META-cache maintenance and final
GPU→CPU ownership transfer are different obligations.

Keep the round15 packed generation layout (**PC31:22,PD21:12,PT11:0**) and
highest-changed-level invalidation. Slot reuse epoch, PM record lifetime,
work sequence and stamp identity remain separate. Content-only updates do
not bump MMU tags. A retained PM record must not be reset between its TA
and3D; a new pair must not reuse an old stamp value or overwrite in-flight
RT ring slices. On OOM/retry/fault/timeout, preserve both complete W/SKUs,
PM lists/backing and user BOs until reset/quiescence. PM OOM/partial render
and general context-switch recovery remain unqualified; this first path
does not implement them by faking completion or freeing retained resources.

### 6. What round14 triangle-record supplies, and the remaining boundary

`round14-device/run.sh triangle-record` runs the **offline** Vulkan recorder;
its primitive output is not a TA/3D submission. Round14 also bundles round13's
separate **`first-render/bounded-job/`** input. Use that **12-BO combined
linear input** for the first kernel fixture, not just the recorder's original
five BOs. It already contains full code/PDS/USC, static/dynamic PPP, the
288-byte-hex VDM including both skip links and trailing rectangle, vertices,
quad, scissor, scratch/viewport/four markers, target, and pool7/8. Its header
also supplies all four context/resume descriptors, six scratch fields,
background/EOT words, VDM bounds and execution bases.

The measured target is64×64 BGRA8, **256-byte rows**, linear PBE state:
`00007e01fb050502 800180008001c000 c000000000000000` before relocation
from captured VA100038000. Address relocation changes qword1 bits0..38
(VA>>1), preserving stride/high bits. The package already relocates it to
its target10000c000. Keep the target's4000 bytes initialized **a5**, with
expected pixels separate. Its negative-Y viewport expects **1152 red,
2944 blue**, all4096 pixels equal to the captured linear image. The
primitive Vulkan recorder's positive-Y expectation is a different artifact.

Pool8=80000 and pool7=c0000 are userspace allocator BOs at whole-record
2d8/2e0. They do not replace the PM control, S, F, physical blocks, kernel
main RT ring or secondary RT buffer above. Whole+308/+310 are depth fields,
zero in this color-only case. No display/PRIME/scanout dependency exists for
the linear-buffer test.

**Still to implement in the kernel:** a typed, validated bounded render
constructor (including remaining record scalars/flags and every hardware
upload input), RT allocations/fields, PM initialization/residency, both
contexts/channels/W/SKUs, dependency/stamp ownership and final retirement/DMA
sync. The existing `render-prepare` scaffold allocates host memory only;
its `render-submit` rejection is correct. The source-backed S1/S2/S3 and RT
arithmetic here remove those research unknowns, not that implementation work.

**Still unqualified:** complete save/restore operand semantics, preemption,
PM OOM/partial render, generation/reuse wrap, and live repetition across
contexts. The combined input preserves opaque helpers and resume-only
HALTs; the normal fresh runtime-flag branch is understood, but a successful
first draw would not certify switching/retry. `SUBMIT_READY=0` in userspace
must remain truthful until the kernel constructor and device ladder pass.
Do not use a raw Apple record ioctl or call the packager a render driver.

### Reproduction and saved analysis

Run `python3 -B scripts/gpu/firmware-re/round16-evidence.py --verify` for
offline source identity, firmware-span bytes, constants, exact packet/wait
recipes and userspace geometry/flag checks. Add **`--verify-isa --rt-oracle
--wait-oracle`** to rebuild the ten GNU metag instruction checks and execute
only the unmodified RT arithmetic/wait-emission routines in the existing
ARM64 build container (`--network=none`). No tool opens an iPad/render node,
boots firmware or edits the kernel. The helper also supports shared-Ghidra
`--host ADDR...`; `round16-aux.py` fills only two vtable-proven missing
functions in the existing auxiliary analysis. No new dependency is needed.
Bulk C/JSON/ELF/bin/disassembly remains under the gitignored round16 directory.

Shared Ghidra now names the two selected-PM-record binders, two RT importers,
RT slice preparer and legacy mapping-address getter. Eleven host locations
carry the recovered sizing, pointer identity, wait, queue and RT-import
notes. `scripts/ghidra-mcp.sh save` completed with **`SAVED ok, changed
now=False`**; its output is retained in `round16/ghidra-save.txt`.

The firmware's register-map provenance remains same-image code. The retained
[primary IMG register header](https://codebrowser.dev/linux/linux/drivers/gpu/drm/imagination/pvr_rogue_cr_defs.h.html)
corroborates requester selectors and event names; it does not define all
Apple PM fields or authorize using legacy PM catalogue encoders.

### Ranked kernel implementation and owner-only device checkpoints

1. **P0 — finish the existing numerical CDM/retirement qualification first.**
   On a fresh integrated r19d boot, use the already documented corrected
   HELPER_END full fixture, slot7 and baseline waits. **Expected:**12345678,
   63 sentinels, W25c and W258 at the new sequence, all ring/watermark/global
   retirement checks and no MMU fault. Output alone is not this checkpoint.
   This is a separate boot from the render pair; it qualifies the completion
   observer and does not qualify a user-BO render path.
2. **P0 — construct offline, then attach without launching.** Implement the
   twelve-BO fixture import, full normal W fillers, RT64 arithmetic, PM
   constructors and locked two-slot ownership. Validate every pointer/extent
   and exact SKU/wait bytes offline. On a fresh owner boot bind3D7 and TA8
   through DC3 with producers still hidden. **Expected:**both DCs consumed,
   CBASEs equal the same PC_PA>>12, host/cached tag entries agree, f088/f090
   retain30102/60405 and no new fault. User selectors need not change before
   engine launch. A `render-prepare` allocation log alone is insufficient.
3. **P1 — TA-only diagnostic, fresh boot.** Publish `[tag6,TA]` with valid
   physical pool/RT/user data and endpoint2; notify type0/kick10. **Expected:**
   TA selector byte f078=8 and136c low16=0808; internal event20 may be
   latched then ACKed;430 completes normally; TA.W530 then W52c reach their
   target, TA channel retires to2 and PM S contains the saved bank0 state.
   Linear target should still be a5; this step has no3D color store.
   Preserve PM/RT/BOs after the diagnostic. A PM-control count change alone
   does not prove a valid TA completion.
4. **P1 — paired64×64 linear draw, fresh boot.** Recreate the complete
   data set; publish TA as above and3D `[tag4(TA.W530),tag1]`, endpoint2,
   type1/kick11. **Expected:**3D waits until TA's new target, selects7 in
   f079 and1368 low16=0707, restores shared S into bank1, completes mask8,
   4000/per-partition waits and1090; W88c then W888 reach the target,
   S+0 increments and both channels/global rings retire. After DMA sync,
   all4096 target pixels match the saved linear image:1152 red/2944 blue,
   no outside-target modification or MMU/PM error. This is the first
   rendered-triangle milestone; no display pipeline is involved.
5. **P2 — expose one bounded user render, then qualify reuse separately.**
   Add an agreed typed render ABI consuming actual owned user BOs and
   kernel-owned RT/PM/context state; the r20 compute dialect cannot carry
   this job. **Expected for the first fresh-boot raw render:**the same pixels
   and both retirement chains, status before fence, CPU ownership only after
   completion. Only afterwards test sequence2, BO-content changes, a new PTE,
   new PT/PD and alternating VMs with full two-engine tag publication.
   **Expected for each reuse step:**new matching stamps and pixels, no stale
   completion, retained PM/RT integrity and fault-free translations. Keep
   general render/switch/recovery claims gated until those separate results.

## Round 15 — CDM helper end, retirement, context reuse and dirty restart

2026-10-23. Research only: no device access, kernel edits or commits.
The owner's r19c slot7 result now proves shader execution (`12345678`) but
not retirement (stamp0, timeout). Item0 below takes priority for r19d.
The new result explains round 14's reservation: firmware assigns **all six
low slots to PM requesters**, independently of TA/3D/CDM's user bindings.
The other consequential distinction is **job retirement leaves a slot
resident; VM destruction unbinds it**. Ordinary PTE edits publish generations
to every engine slot of the VM, without another DC3. Recovery rebuilds the
firmware epoch and channel/PM state; ACKing the MMU event is insufficient.

Source is the same j71t iOS 16.7.10/20H350 kernelcache, SHA256
`c6b95fcf0efe52ee9258454d01d14adc9b0cb922d94c54a04a4ce11d1ae81e0a`.
Short host addresses have prefix `fffffff0` with the low word zero-padded;
firmware addresses are **MINIM execution PCs**, not storage addresses.
Offsets and register values below are hexadecimal unless stated otherwise.
**[Proven]** identifies recovered code/data; **[Linux sequence]** is the
implementation derived from it; **[Open]** marks an unproved hardware or
recovery boundary. Bulk host C/JSON, checked firmware spans and independent
GNU ISA decoding are in `build/gpu/firmware-re/round15/`.

### 0. CDM retirement — r19c omits the final helper's USC end word

**Top suspect, byte-proven fixture defect:**
`scripts/gpu/make-compute-fixture.py` copies only **0x40 bytes** into the
page mapped at **B+28000**. The final ordinary helper at CDM+8c points via
PDS code **B+14440**, data **B+14450**, DOUTU **28000/10**, to that USC
program through a conditional DOUTU. In the successful iOS `one-post` capture, the program begins
`01 c4 00 01 3e 01 00 00`, whose branch at+2 targets **+40**. At+40 the
capture has **`00 80` (USC end)**; the Linux fixture has **`00 00`**.
The lost byte is page2+41 (whole fixture file offset **8051**, including
its0x10-byte header). The shader's earlier numerical store can succeed
while this later helper prevents the stream/fence/terminate sequence from
finishing. This fits r19c's **4a0=0**; hardware causality still needs the
owner's corrected-payload run. Research did not edit the standard fixture or kernel.

Read-only snapshot is `a9/gpu` **fa850de794e6dc17f5cb723d6bffe4e6591a4f85**.
The existing r19c `diagnostic_blob.txt` additionally shows `4a0=0`,
`688=1728=0`, correct B=300000000 and slot7 selectors. All saved execution
records read zero/inactive, so their RAM snapshots do not locate a live
firmware PC. The normal firmware recipe waits for internal **event4** and
then **4a0.COMPLETE** before finish33 writes **`*(u32*)W25c=W260`**.
IRQ174/event-ring retirement is downstream. Keep those waits and the
distinct firmware stamp; fixing host IRQ handling alone cannot explain
this missing end marker or manufacture the completion store.

**Immutable-byte check:** r19c's `compute.inc` requires full-payload SHA256
`1a945de3bb39d817a588d8c1840cb47c49048189a3ca3570032a9bdb6812e2ed`.
The retained payload matches that pin. Its complete CDM stream through+c8
equals the strict successful iOS capture, including the terminate word;
the **transitively fetched USC helper**, rather than the CDM tail itself,
is truncated. Branch decoding and the end marker are established by the
existing compiler/corpus decoder `g5p_isa.py`. This is a stronger candidate
than speculative stamp-ID, reuse-epoch or IRQ changes.

`round15-retirement.py --verify-isa --candidate` records the proof and writes
research-only candidates under `build/gpu/firmware-re/round15/retirement/`.
The full candidate is `g5p-compute13-full-helper-end-candidate.bin`, SHA256
**`3389b8090f966e828dcc8a760bcc5586a0c4844ca8d1cd9b81221a17b7d22918`**.
It differs from the r19c full file in **exactly one byte**,8051:00→80.
The corresponding minimal candidate hash is
`33fa9ae4a390ee215f10dbd7b6c40bc1521fe2bec8d54c8c9136d4cedff933f8`;
the minimal stream does not fetch this helper, but both generator variants
share page2. The owner should retain the complete0x42-byte helper in the
generator and update both pinned hashes. Do not bypass its hash check.

**[Proven — two kinds of termination and two interrupt stages]** The normal
uninstrumented iOS path is:

1. `6c8a98c` constructs W and `6ca8da4` emits the microsequence. Start32
   carries **user hardware slot S**, while W, SKU, CH, dependency and stamp
   pointers are **META addresses**. Start32 selects S in f078/136c, uploads
   W10→4c0, W14→4c8, W60→498, W68→4b8, W70→480, and the context-PDS
   block W21c/W224→4a8/4b0. It kicks478=1. See round7 for the full writer
   chain. r19c's readback matches the captured packed states and B.
2. The **GPU CDM stream** runs the user task, its helper/fence tasks and
   **80000000 at stream+c4**. `endComputePass /207d0bfa0` appends that word
   and records its GPU VA in C+60, forwarded to W23c and the H retry
   comparison. No additional host-side “end kick” is emitted after
   publication. The register-uploaded terminate PDS at **B+420**, whose
   code includes DOUTC at+42c and HALT at+430, is distinct from the
   **USC helper at B+28000** and the firmware's terminal opcode below.
   Preserve context/terminate PDS code, data, scratch and all helper end
   markers; the first USC result does not certify their completion.
3. At SKU+48, opcode **101** waits for **R0+130 event mask4 (bit2)**, named
   COMPUTE_FINISHED in the retained primary IMG header. The firmware's
   interrupt callback `80009edc` collects hardware events into
   **private+2bc / GbP+70=8000a2bc**, filters with private+2b8=83ec,
   acknowledges at138, then dispatches source-kind3 to the interpreter.
   Boot **b70=12c** includes4. Thus host130=0 can mean already
   acknowledged; it does not establish whether the wait was satisfied.
4. At SKU+4c, **004a000a,1,1** waits for **(4a0&1)==1**, the CDM
   context-store COMPLETE bit. It can spin until the recovery predicate
   changes. r19c's observed4a0=0 makes hardware end/context-store progress
   an unresolved prerequisite even if the event was collected. Neither
   replacing opcode101 nor fabricating4a0 is a valid fix.
5. Finish33 at SKU+6c invokes `80007500`: **80008cdc** runs11f00=1,
   688=4/wait and1720=(global44\|13)/1728.bit0 wait, then `10018300`
   clears11f00.bit3. It tests4a0.NEED_RESUME through80008f00. On the
   normal path it clears G724/G728/G72c and at **80007590 stores
   `*(u32*)W25c=W260`**. If NEED_RESUME is set, H=W1e8 is checked
   against GPU scratch using S; a mismatch marks H and takes the backlink
   to start32. This is the separate save/restore retry contract, not a
   requirement to seed CH24..30. r19c has H=0 and no observed retry.
6. Finish33 calls **1001ede0(2)**: set firmware private+fc.bit2 and write
   **b00=2**, scheduling the CL follow-up. The subsequent **40000017**
   is a **firmware microsequence** terminal operation; it clears the
   current execution-slot active bit (`8000504c..5088`). It is not the
   GPU's80000000 CDM word. Keep both in their respective streams.
7. On its next scheduler pass, **100130a0** observes completed execution
   entries; when an entry's active bit is clear and its channel/update
   fields are valid, **10013124..148** writes **CH.control+10** to
   `(saved ring index+1) % ctl50`. The CL retire scan **80001c68**, called
   through100270fc from10002734, walks **ctl0→ctl10**, performs META
   cache maintenance on finished W/SKU ranges, updates scheduler accounting
   and publishes **ctl0** at8000206c. It then
   invokes **8000a994→100104d8(2,stamp_record,last_sequence)**. The earlier
   admission scan at ctl20 is a different watermark.

**[Proven — W258 and the host completion event, newly closed]** The retire
scan copies **0x18 bytes (six u32s) starting at W258** into a local record, independently
checked against TRM3.2.15/21 and GNU decoding at80001dd8/1de0/1de8/20e8:

| Local stamp-record offset | Work source | Use |
|---|---|---|
|00,04|W258,W25c|META stamp addresses|
|08,0c|W260,W264|u32 value; stamp-manager identity|
|10,14|W268,W26c|Channel identity; associated flag|

For **engine2**, `100104d8` writes **`*record[0]=record[8]`**, hence
**`*(u32*)W258=W260`**, after channel retirement. It calls **1001fe0c**
with identity=record+c. That routine builds a **type1,44-byte (0x2c) event**:
four u32 bitmaps at event+4/+8/+c/+10, setting
**bitmap[identity>>5] bit(identity&31)**. **Identity0 is valid**: its
notification is event+4 bit0. It does not denote hardware slot0, and there
is no recovered need to change stamp identity to7 when choosing user slot7.
W25c is the finish33 hardware-work stamp; **W258 is a later retirement
stamp mirror, not an unconditional start stamp**.

Common publisher **1001fb9c** copies the event into the root+20/+24 ring
(256 entries, stride2c), orders publication and advances producer+10.
When cached meta6c0 and6c8 are both nonzero it takes the platform-aware
notification through10007a44; the bypass branch writes04820010=10 and
waits for the notification latch. This is the downstream **host IRQ174**
path, distinct from internal COMPUTE_FINISHED event4. iOS **6c99788**
drains complete2c-byte records and, for each set identity bit, calls
**6c587bc** to wake that identity's wait queue and the manager wait queue.
The callback itself does not write a stamp. The event consumer advances
after a DMB; iOS then schedules follow-up work. **6ca28d8** acknowledges
the configured platform IRQ endpoint; Linux's established endpoint is
20e104094=4000. A lost host interrupt can delay a wakeup, but cannot be
the sole explanation when both firmware stamps remain0 in coherent RAM.

**[Proven — address-space distinction]** For r19c, W25c is **c0254944**,
the firmware META alias of kernel shared GPU VA **e1c0254944**, and
W258 is **c0254940**. These remain in the firmware/kernel mapping
served through context0; the USC output **100000200**, scratch
**100044000..10004a067**, CDM10005c000 and B-relative code/data are in
the **user slot7** VM. Finish33 is a META CPU store and does not translate
its stamp pointer using the slot carried in its packet. Do not convert
W25c to a low user GPU VA or add the stamp page to slot7 as a supposed
completion requirement. The S-dependent GPU translation is the retry
scratch check, not the normal stamp store.

**Comparison with immutable fa850de794:** Its start32/101/poll/finish33/
40000017 bytes and both stamp pointers match this uninstrumented recipe;
slot7 is propagated to W+8 and both opcode packets. The observed cache
variant is **3**, post-cache CBASE/invalidate enabled, **preinstall0,
boot_vm0**; it is not the originally proposed cache0/preinstall run.
Its `g5p_compute_wait` polls the real W25c target with DMA ordering while
draining events, so it already tolerates a missing IRQ. Its repeat path
requires all channel ring watermarks to retire before reusing W/SKU.
It logs W258 as `w258_unwritten`; the annotation should now say **retire
mirror**. The concrete differing input is the missing B+28040 end marker.
W14=0, reuse epoch0 and identity0 remain uncaptured iOS object assumptions,
but are lower-priority experiments than repairing that byte.

**[Linux sequence / next discriminator]** Keep the successful slot7 mapping
and the complete iOS microsequence; supply the full helper through its end
word with an updated verified payload hash. Require **output12345678,
W25c=sequence**, then eventual **ctl10=ctl0=endpoint, W258=sequence** and
type1 bitmap0.bit0 for the baseline identity. Host fence success comes
from the current expected stamp/epoch; backing reuse additionally needs
retirement. If the corrected run still stalls, classify using4a0, both
stamps, ctl10/ctl0, host-event producer/consumer, raw130/b70/b20 and the
post-finish688/1728 status. 4a0=0 keeps hardware helper/terminate work
first; 4a0.COMPLETE with stamp0 moves investigation to event delivery or
the finish cache waits; W25c advancing but ctl10/ctl0 stale moves it to
microsequence termination/retire scheduling. The zero/inactive saved
execution-record RAM seen in r19c is not evidence that no execution ran.

### 1. Context lifetime and allocation for N VMs

**[Proven]** `6c7be80` initializes a common pool with lower bound 7,
configured upper bound **C** from accelerator virtual710 (`6ca8638`, +c48),
maximum 256 from virtual708, and per-engine bookkeeping enabled by
virtual720=1. If C is zero, at most 7, or greater than 256, it falls back
to 256. Thus user slots are **7..C−1**, at most **249 resident engine
bindings**, rather than 249 guaranteed simultaneous executing jobs. The
configured C was not read from a live iOS object in this round.

The manager is accelerator+11e0. Its relevant byte offsets are:

| Manager offset | Role |
|---|---|
|08,10,14,18|Lock; C; first user slot 7; C−7|
|1c,1d|Per-engine assignment enabled; allocation-exhausted/wakeup flag|
|20|Queue/channel affinity array; `ffffffff` means none; queue ID80 is skipped|
|28|u8 slot reuse epoch array; **separate from MMU packed generations**|
|30,38|GART owner pointers; u32 outstanding/prepared-use counts|
|40,48|Free stack and signed top; initialized C−1..7, so first pop is 7|
|50|Per-slot role array: initially 4; kernel slot0 explicitly uses role3|

`AGXContextManager_acquireSlot / 6c859a4` receives `(manager,GART,queue,
role,&reuse_epoch)`. GART stores four role assignments at
**1d8/1dc/1e0/1e4**. Actual callers establish **role0=TA, role1=3D,
role2=CDM**; kernel task's role3 is explicitly assigned slot0. There is no
recovered ordinary user caller requiring a name for role3.

* If that GART/role already has a slot, increment its use count, update
  queue affinity and return the existing epoch. **No PA republish or DC3.**
* Otherwise pop the free stack. If empty, scan **7 upwards for the first
  use-count-zero slot**, detach its previous GART assignment and invalidate
  matching queue affinity. This is first-available eviction, not a recovered
  LRU policy. Increment the new slot's use count and reuse epoch, install
  owner/role, then publish PA/tag through accelerator virtual920 (`6c81794`).
* While firmware is live (accelerator+4e2), send **DC3(slot)** through the
  device-control gate and use its consumed/ordering protocol. With firmware
  stopped, the next boot's 256-base install consumes the published array.
* If all user slots have nonzero counts, return `ffffffff`, set the wakeup
  flag, and backpressure/retry. Do not overwrite an active slot.

`6c8b0f8` (CDM) and `6c7ae8c` (3D) release descriptor resources, decrement
the corresponding use count and invalidate the **descriptor's** slot field.
They do **not** clear the GART's assignment, CBASE or PA array. An idle VM
therefore keeps its slot until eviction or destruction. This is what makes
consecutive jobs avoid repeated bind/unbind transactions.

`AGXGart_detachSlotsAndDestroyAddressSpace / 6caef14` handles destruction:
for every assigned role, increment reuse epoch, clear owner, set role4,
clear GART/queue associations and push the slot back onto the free stack.
It calls virtual920 with NULL, publishing **PA=0, tag=0**, and sends DC3
for each detached slot when firmware is live. The firmware handler binds
zero and invalidates that slot's PC cache. **A zero generation alone is
not an unload command.** The caller must already have retired/cancelled
uses; this destructor is not a mechanism to revoke DMA from a running job.

**[Proven — PM reservation]** Boot hardware init `1000515c` calls
**`100187b4` at10005214**. That routine stores the slot arrays
`[2,1,3]` and `[5,4,6]` through global+120, and writes:

| Requester mapping | Normal boot value | Slots, low byte first |
|---|---|---|
|f088, CONTEXT_MAPPING2|`00030102`|VCE0=2, TE0=1, ALIST0=3|
|f090, CONTEXT_MAPPING3|`00060405`|VCE1=5, TE1=4, ALIST1=6|

The writes and six numbers are independently instruction-checked; requester
names/shifts agree with the already-retained primary IMG register header
`build/gpu/refs/linux/pvr_rogue_cr_defs.h`. This supplies round 14's missing
hardware purpose for 1..6. It **does not prove** that using slot1 caused the
owner's CDM fault. Diagnostic helper `1001e14c` temporarily changes f090's
low byte; `1001e120` restores `060405`, so the table is a boot baseline,
not a claim those registers can never change.

**[Linux sequence — resident contexts]** Follow this order for each VM:

1. Allocate its owned 16KiB PC/PD/PT and persistent engine state; retain the
   kernel/META mappings that work execution needs. Populate user BO mappings
   using the round 13 encoders. Complete CPU/DMA visibility before publication.
2. Acquire one user slot **per engine used**, under a VM/pool lock. A render
   preparation (`6cb9afc`) first acquires **3D**, then separately **TA**;
   a compute preparation (`6c8afcc`) acquires **CDM**. Each can bind the
   **same physical catalogue PA** into a different hardware slot.
3. For a new/reassigned slot S, publish `PA[S]=catalogue physical PA` (u64,
   unshifted) and `host_tag[S]=packed MMU tag` (u32). Keep firmware's cached
   tags in their separate allocation. DMA barrier, then DC3 `{3,S,0,0,0}`.
   Firmware pointers are control+1048 → PA[256], control+1044 → host tags,
   and root+458 → cached tags; the pointers are not inline array entries.
4. Firmware DC3 writes **e140=S, e148=PA[S]>>12**, copies the host tag to its
   cache and issues **e138=(S<<3)|4**, waiting for completion. Do not select
   f078 or the 136x replicas from the CPU while firmware schedules work.
5. Fill **every** job identity consistently: CDM W+8/start32+14/finish33+0c;
   TA and 3D W+4 and their start/finish packet context fields. On dispatch,
   firmware writes the selector triplets below and uploads engine state.
6. Retain the slot's use reference through real completion or successful
   reset/cancellation; dropping it when a pointer-ring consumer advances
   permits eviction while hardware still uses the old catalogue.
7. Retire the job and decrement use count. Keep its VM slot resident for
   the next dispatch. On eviction, rebind the replacement through DC3
   even if its tag happens to equal the previous VM's tag.
8. On VM destruction, stop new jobs, wait for all engine uses, serialize
   allocation/unbind, publish zero PA/tag and consume DC3 before recycling
   the slot or freeing its translations. The gate must prevent an old
   unbind from clearing a new owner's PA.

| Engine | GART role slot | Firmware selector helper | Hardware byte stores |
|---|---|---|---|
|TA|1d8|10017d6c|f078,136c,136d|
|3D|1dc|10018110|f079,1368,1369|
|CDM|1e0|1001838c|f07a,136e,136f|

All three bytes in a row receive the same 8-bit S. With all engines used,
N VMs may need **3N resident slots**; with TA+3D, **2N**. At C=256 this is
83 complete triples or 124 complete render pairs, with idle assignments
evictable. Software contexts may outnumber these bindings through eviction
and backpressure. The two fixed PM requester groups are a separate dimension.

### 2. Fault recovery and a kexec takeover

**[Proven — host call chain]** Event4 in `6c99788` records recovery state at
accelerator+1a24=2 and signals the scheduler. The common restart tail of
**`6c81dac`** is:

```text
firmware virtual358 ->6ca1e40: stop via virtuald8/6c9fa10;
                             optional platform power off/on
6c83630: reset slot/dependency/PM/channel bookkeeping
firmware virtuala8 ->6ca3ccc ->6c9edcc ->6c96be8:
                     rebuild shared roots, control, companion and queues
increment restart epoch; DMB
firmware virtual360 ->6ca1efc ->virtuald0/6c9f954:
                     boot and wait for the new firmware boot event
resume scheduler
```

G5 virtual118 (`6ca3d00`) earlier in the reporter is a **return0 no-op**;
do not describe that virtual call as the hardware reset. The actual reset
program is `6c9feb4` plus the controller/PIO boot path and firmware init.

**Power cycling is conditional in this ordinary path.** `6ca1e40` calls
platform power virtual278(0), then(1), only when accelerator+510 bit4 and
firmware virtual290 are enabled, or bit3 and the optional controller+0c are
enabled. There is also a stronger, explicit fallback:
**`6c9e070`**, called when TA/3D/CL/DC submission finds a non-progressing
controller, blocks scheduling, sets **firmware+212=1**, stops firmware,
requests platform **off then on**, resets context/dependency state,
reinitializes shared state and boots/waits. Failed boot panics. `6ca0f78`
shows why +212 matters: normal power-off rejects a controller that is not
stable-off, while this restart exception permits the request. Its provider
virtual568/570 own the platform clock/power operations. These are not proof
of a universal raw PMGR register sequence.

**Shared state really is rebuilt.** `6c96be8` sets ring consumers to their
producers; on a cold start it first zeros producer indices, whereas a
recovery discards pending entries at retained indices. It clears engine
globals and the full **0x28-byte** companion, restores pointers/flags and arrays.
`6c83630` frees idle role assignments and bumps reuse epochs, reconstructs
the **0x280-byte dependency objects** with `-1`/`f` sentinels, calls PM
virtuale0 (`6cb1108`) and channel/workqueue reset methods. Retaining old
companion2 or resetting only the host ring index is not equivalent.

**[Linux sequence — in-driver recovery]**

1. Stop admission and scheduler publication; preserve the first event,
   e150/e158, companion, current W/SKU, selectors and firmware cursor.
   Mark affected work failed and invalidate its completion epoch. Quarantine
   BOs/page tables while hardware can still access them.
2. Synchronize the host IRQ/work handlers and stop the old firmware/controller.
   Treat terminal recovery/park as failure, not proof every GPU requester is
   idle. Firmware already ACKs MMU event200 at138 before reporting event4;
   a second host ACK cannot restart the transaction.
3. Re-enter the **complete hardware boot/reset path**. A stronger dirty-state
   fallback follows `6c9e070`'s platform off/on request under the PM owner,
   then controller initialization. Do not wait indefinitely for the old
   faulted 1720/SLC operation to finish before asserting reset. If a domain
   cycle is used, verify actual off/on; a genpd get on an already-on domain
   does not establish a cycle. The known Linux GFX domain is gate124,
   PMGR+80340; use its driver/owner rather than an uncoordinated raw write.
4. Reset per-job/channel/dependency/PM state and completion epochs. Rebuild
   roots, policy, companion, queue controls and PA/tag arrays from retained
   live VMs, or fail all work and start empty. Reuse retained firmware-image
   backing only when its lifetime and contents are owned; iOS restarts the
   retained boot image, not a newly fetched firmware file for every fault.
5. Execute the reset/MMU/SLC/PIO sequence below, boot firmware and wait for
   a **new-epoch ALIVE plus DC consumption**. Rebind surviving user slots;
   restore PM state through its initializer. Old tags, stamps or ALIVE
   entries cannot satisfy the new boot's waits.
6. Check a clean fault/event baseline and run a fresh job/stamp/output check
   before reopening admission. Retire quarantined DMA only after reset or
   quiescence is established. A failed reset remains terminal.

**[Linux sequence — freshly kexec'd kernel]** Treat all inherited GPU state
as dirty. The departing driver's shutdown hook should stop submissions and
establish requester/META quiescence **before old DMA RAM is reusable**;
kexec alone is not a GPU reset. If no trustworthy handoff exists, the new
driver needs an early platform/controller reset before using inherited R0
state or allowing old DMA allocations to be reclaimed. Obtain/retain GFX
power ownership, establish Fender hold/off state, perform the complete reset
sequence, load **new-kernel-owned** tables/image/roots and boot as a cold
firmware epoch. Start with all user PA/tag entries empty and context0 valid,
then bind new slots7+. Carry no old pointer, PM ID, stamp or queue state
across the kernel boundary. Preserve the currently-required pre-release
VA0 containment mapping independently of this restart work.

**[Open]** These are the recovered iOS sequence and its Linux ownership
requirements, **not an observed successful warm recovery on this iPad**.
No static evidence establishes that a PMGR GFX cycle alone drains every
outstanding faulted transaction, or that a new kernel can retroactively
protect RAM already reused before its driver probes. A dirty-kexec test
must establish that boundary; do not advertise kexec-safe recovery solely
from a second ALIVE.

The concrete register/state restoration belongs to these owners:

| State | Recovered sequence and required interpretation |
|---|---|
|Broad reset / META release|`6c9feb4`: R0+100=`00e7fffffffffc1d`, then `00e7fe071ffffc1d`, then `0000000100000000`; bf8=1; kernel MMU/SLC setup; final100=0 through the normal boot path. Controller owns hold/FIFO/GPC ordering.|
|Boot MMU/SLC configuration|`6ca4e10/6ca4e70`: e140=0, e148=kernel_PC_PA>>12; e200=3; e208/e210=`b42dd24b4bd22db4`; e160=physical zero-page PA; bb8=0; Fender2010=bf,2014=233.|
|Firmware maintenance|`1000515c ->1001b638`:11f00=1;688=7/wait;1720=`global44\|1f`/1728 wait; e230=c03/e238 wait; e138=804/wait. Then11f00=2/wait; `100052d0` installs all256 CBASEs. This follows reset, not a host pre-reset drain of wedged hardware.|
|Fault banks|e150/e158 are diagnostic reads in the recovered handlers. **No established host write-zero clear exists.** Preserve them, reset/reinitialize the hardware, and check the new baseline; clearing138 alone does not clear/retry the fault.|
|Event masks/pending|Boot `10020224` restores private+2b8=`83ec`, b58=c0, b68=8000, b70=12c, b78=200. Reinitialize private software pending/dispatch state through firmware boot. Callback80009edc samples130 and ACKs enabled pending bits at138. Do not carry the parked firmware's pending/mask state forward.|
|Host IRQ acknowledgement|`6ca28d8` uses ac8/the IRQ resource; Fender/AIC ACK and firmware138 ACK are separate. Synchronize host handlers across reset and let the newly booted firmware own its event masks.|
|PM requester state|Firmware boot restores f088/f090 and render helpers restore PM state. User CBASE installation is not a substitute for PM reinitialization.|

### 3. Steady-state submission, new BO mappings and stamps

**[Proven — generation producer]** Leaf writer `6cac314` increments the
GART PT mutation count at+350 on each PTE store; its optional compare mode
skips identical values. Table allocation
`6cab44c` changes PD/PC counts at+354/+358; removal `6cab928` increments
them when freeing tables. `6cabe70` compares these with snapshots and
increments one hierarchical generation, choosing **PC before PD before PT**:

| Kind | Internal +368 bits | Published tag bits from `6cabf40` | Firmware invalidate |
|---|---|---|---|
|PC|9:0, 10 bits|31:22|type1 → e138 `(S<<3)\|4`|
|PD|19:10, 10 bits|21:12|type2 → `(S<<3)\|2`|
|PT|31:20, 12 bits|11:0|type3 → `(S<<3)\|1`|

It snapshots **all** mutation counts after taking the highest changed
level. Nothing changed returns0. Host firmware virtual1f8,
**`6c98990`**, invokes this producer and writes the result into
`host_tag[S]` for **all assigned role slots** of that GART. Compute builder
`6c8a98c` invokes it before building/publishing work. Firmware
`10017ac4` compares host and cached tags before the job; equality skips
maintenance, otherwise it chooses the highest changed field, issues the
corresponding invalidate through `80009030`, waits, and caches the new tag.
This path **does not reload CBASE**. DC3 is for binding/rebinding/unbinding;
it is not required for every new BO in an unchanged catalogue.
For S=7 the individual PT/PD/PC requests are **39/3a/3c**; **3f** is the
separate all-level request. A PT-only tag change is +1, PD is +1000 and PC
is +400000 in the corresponding published field, with field-local wrap.

**[Linux sequence — each submission on a retained VM]** Serialize VM edits
with dispatch. For new BOs, construct tables/mappings and establish DMA
visibility, then bump the applicable generation(s), publish the packed tag
to every resident engine slot, and issue `dma_wmb()` before work/ring
publication. For content-only writes to an existing BO, satisfy that BO's
CPU/GPU synchronization contract; a page-table generation bump is not a
data-cache flush. Never reuse/unmap backing still referenced by an in-flight
job. When the **catalogue base PA** changes, use DC3 in addition to generations.

If no mappings changed, leave tags and binding resident: build a new job
with retained per-context state, a new sequence/fence target, and a fresh
or fully reconstructed mutable W/SKU. The recovered normal start32 does
its own engine reset pulse/selector/state upload. It does **not** repeat
boot's host11f00/688/1720/e230 preamble between ordinary jobs. There is no
recovered requirement to issue all-context SLC invalidation for every BO
mapping; MMU translation invalidation and BO/cache ownership are distinct.
Before a finite generation or reuse-epoch wraps into a still-observable
old value, Linux must force an ordered invalidate/rebind or defer reuse.
The exact iOS wrap-avoidance policy was not established here.

**[Proven — publication and finish]** `6c88840` checks pointer-ring capacity,
writes the work META pointer, executes a DMB and publishes the channel
producer. `6c980a0` separately emits a **0x14-byte** CL notification with
type2, converted channel and endpoint, barriers, then advances its 256-entry
global-ring producer; the CL wrapper kicks MTS with b00=10. Both rings and
the kick are needed for the next job, with wrapping/free-space checks.
Do not reinitialize CH with `6c89420` between jobs: it is a reset initializer.

Finish33 (`80007500`) runs the completion/cache helpers and normal-status
test, clears its G runtime state and then **stores the packet's address/value
pair at +1c/+20** (`80007578/590`), corresponding to **W25c/W260** in the
captured recipe, before notifying engine2. Error/retry branches bypass this
normal finish. W258 is not an unconditional start stamp. TA and 3D have
their own finish stamps and retries (round6); they cannot borrow compute's
offsets. Dependencies compare signed32 **`required−*stamp > 0`**, so retain
an unambiguous timeline window and use a new target for every job.

Linux must consume completion with DMA read ordering, match the current
channel/reset epoch and expected target, then signal its fence/read shader
output and release slot/BO references. A previously written stamp1 cannot
complete job2; a consumed ring record or event4 cannot complete any job.
Keep the full dependency object and per-context scratch alive across jobs;
firmware mutates them. Resetting them to zeros for repeat dispatch would
discard the iOS sentinel/state contract.

### 4. Render context and PM requirements beyond CDM

The byte-level envelopes, upload read sets and exact microsequence recipes
remain in **round6 below**. The additional lifecycle conclusions here are:

* A render descriptor prepares **two engine bindings**, D768 (3D) and Dcfc
  (TA), for the same GART. The hardware slot is render W+4, unlike CDM W+8.
  It publishes both jobs and an explicit dependency; TA does not generate
  its own 3D job. Preserve TA-produced parameter data through 3D completion.
* Keep TA's **0x560-byte W**, mutable state **W464..50f**, A/B upload blocks
  and context/scratch GPU allocations. Normal3D uses **0x8c0-byte W**, runtime
  state **W6d8..7ff**, seven context helper qwords at804..84c and its own
  A/B blocks. Alternate3D uses **0x5f0 bytes**, different upload layout and finish.
  These are firmware-visible extents, not guessed allocator slab sizes;
  zero CDM-shaped replacements are unsupported. GPU state pointers are
  not catalogue physical addresses.
* **PM ID is not an MMU slot.** Accelerator startup creates a separate
  **127-entry `ParamBuffer` ID pool**, IDs0..126. `6cb9afc` acquires its ID
  under a prepare count/lock, puts it in Dcb0/D710 and obtains the PM
  control/per-slot state pointers. Exhaustion is a real prepare failure.
* Allocate PM control **0x80 + slots*0xc0 bytes**, its per-slot **0xc0-byte**
  state and auxiliary GPU backing (`6cb2378`), linked block/page lists and owned
  parameter pages. Each parameter block is **0x20000 bytes (128KiB)**.
  At a 16KiB host page, list stride is **0x24 bytes (36 decimal)** (`4+8*4`),
  list extent **0x44 + max_blocks*0x24 bytes**. List entries are **physical PA>>12 per host
  page**, expanded into four consecutive 4KiB frames, not user GPU VAs.
  `6cb1108/6cb14dc` establish the nonzero heads, counts, limits and sentinels;
  a zero PM page does not implement an empty valid PM pool.

**[Proven — TA versus 3D PM loading]** Fresh TA start23 at800067b0 passes
`S=M(Dcc0)` to `8000a874 ->10005f30 ->1002706c ->800098a4`.
The last routine loads **S+a8, S+b0, S+a0** (GPU addresses with low4 bits
masked off) into **R0+240,248,2d8**, respectively; issues **290=1**, then
**198=1**, then **258=1**, with waits for bit0 to clear. `10005f30` clears
S+bc after this load. These are real PM/context transactions before the TA
kick, not fields to omit from a CDM-derived fixture. TA finish's save helper
`8000a844 ->10005ef0 ->1001886c(S,0)` captures bank0 runtime state.
Normal3D start25 uses `8000a824 ->10005e18 ->10018e5c(S,1)` instead: it
restores bank1 from the PM state, including values produced by TA. Keep S
and its backing alive across that dependency.

**[Proven — reserved PM translations]** Firmware `1001873c` calls PC
invalidation for three entries in the reserved-slot array; `10018e5c`
restores PM runtime/MMU state
and invalidates three reserved slots before its upload. Bank0 builds
register groups including **e008/e028/e048/e098/e0b8/e0d8** and2b8/268/2d8;
bank1 selects **e050/e070/e090/e0e0/e100/e120** and2c8/278/2e0, then joins
the same upload. Independent correction of indexed `SETL` at10018fb4,
1001900c and10019034 confirms these are **R0 + register-offset stores**;
the generic printer's `[D0Ar6+D0Ar6]` is wrong. `100193a8` also applies
SLC/MMU maintenance across a reserved triple. Keep those operations under
firmware ownership, with valid PM backing/state supplied by the host.
The mapping registers retain **slots1..6**, rather than substituting the
current TA or 3D user slot into every PM requester.

**[Open]** The complete semantic names/encoding of every Apple PM state
qword and the PM-OOM→partial-render→resume protocol are not proved. This
round establishes the slot roles, separate ID pool, allocation/lifetime
requirements and hardware upload boundary; it does not certify a new
minimal render state or fabricate a valid all-zero HWRT. Reuse round6's
full source-backed constructors/read sets before shrinking a fixture.

### Ranked kernel requirements and reproduction

1. **P0 — completion, item0:** retain the final helper's end word atB+28040
   (copy0x42 bytes, update pinned payload hashes), keep both firmware waits
   and both kinds of terminate, and verify W25c→execution-slot clear→ctl10/
   ctl0→W258/type1. Keep stamp identity independent of user hardware slot.
2. **P0 — contexts:** reserve0 and1..6; implement locked per-engine slots7+,
   use counts and safe DC3 rebind/unbind. Publish one VM's PA/tag to all its
   engine bindings. Confirm selectors, CBASE and reuse after alternating VMs.
3. **P0 — recovery/kexec:** stop admission/IRQs, retain DMA, reset through
   controller/platform ownership, rebuild the firmware/PM/channel epoch and
   rebind. Establish shutdown/early takeover quiescence before freeing old
   DMA; validate dirty warm reboot with clean fault baseline and a fresh job.
4. **P0 — repeat/BO mapping:** publish hierarchical tags after table writes,
   use new stamp targets and both ring publications, and retire on matching
   completion only. Test job1→job2, a new PTE in existing tables, a new PT/PD,
   then unmap/remap after fences, without boot cache maintenance per job.
5. **P1 — render:** acquire separate TA/3D slots and a separate PM ID, build
   complete context/PM/free-list state, retain it through both fences and
   reset it on recovery. Preserve fixed PM requester slots and let firmware
   perform PM upload/invalidation; complete OOM handling before general use.

Run **`python3 -B scripts/gpu/firmware-re/round15-evidence.py --verify-isa`**.
It checks the retained firmware/host-section hashes, raw vtable/getter and
generation instructions, lifecycle captures, PM slot layout and **38 GNU
metag instruction anchors**, and writes the transcript/ISA listing under
`build/gpu/firmware-re/round15/`. `--verify` omits Docker/objdump and remains
fully offline. `--host ADDR...` refreshes read-only shared-Ghidra captures;
`--aux ADDR...` imports already-generated auxiliary decompilations for
`FUNCTION_NOT_FOUND` gaps. The auxiliary source is the existing
`tools/gpu/ghidra_offline.py`, never a replacement target binary.
For item0 also run
**`python3 -B scripts/gpu/firmware-re/round15-retirement.py --capture-kernel --verify-isa --candidate`**.
The Docker kernel volume is mounted read-only at fa850de794; verification
uses the original payloads pinned by that commit, strict resident iOS bytes,
the existing owner's log/blob, and **7 additional GNU instruction anchors**
closing the retire-record copy and bitmap indexing. Bulk evidence and
one-byte candidate payloads remain in `build/gpu/firmware-re/round15/retirement/`.
It never replaces the standard payload or edits the kernel. `--verify`
omits GNU/Docker unless `--capture-kernel` is explicitly included.
Ten host routines were renamed; lifecycle/generation/restart, PM-slot and
completion findings were annotated in shared Ghidra and saved with
`scripts/ghidra-mcp.sh save` (`changed now=False`).
All ranked device tests above are **owner work, not executed results**.

## Round 14 — CDM context activation: iOS user slots start at7; CBASE survives the fault

2026-10-23. Research only; no device access, kernel edits or commits.
**Next boot: move the complete user-context binding from slot1 to slot7.**
The G5P accelerator's virtual718 returns **7**, and the iOS context manager
allocates user slots from7 upwards. Slots1..6 are excluded from that pool;
context0 is assigned explicitly to the kernel task. This is a proved iOS
software reservation and a new difference from Linux's hardcoded1. **Its
hardware significance, and whether it causes this fault, remain inferred.**
Keep the table bytes and cache settings fixed for this comparison.

The owner's four r19b runs now strongly demote a persistent CBASE overwrite,
ordinary dirty CPU table cache, runtime table-publication race and generation0.
The remaining fault is still **valid, Page Invalid, read, numeric level3,
requester CDM, context1, VA`0x1_0005_c000`**, immediately after CL publication.
**No evidence supports relabeling level3 as a leaf fault or changing PDE3/PTEd.**
Device META reads prove the table bytes are accessible through context0; they
do not test the CDM context1 walk or its requester-specific context state.

Sources: the same j71t iOS16.7.10/20H350 kernelcache as round13; immutable
`a9/gpu` **`3ab2d63e58da`** (r19), plus **`38c2d02dd8de`** (the owner's r19b)
read through `linux-a9-src:/src:ro`. All kernel filenames below are relative
to `drivers/gpu/apple-g5p/` in those commits, never a moving worktree.
Short iOS addresses such as`6ca517c` mean`fffffff006ca517c`; firmware
addresses are **MINIM execution PCs**, not compressed storage addresses.
Full evidence is generated under`build/gpu/firmware-re/round14/`.

### What the new device evidence establishes

| Owner run | Observed evidence | Consequence and limit |
|---|---|---|
|v1 after-cache (`../experiments/logs/2026-10-23-exp19b-v1-after-cache-dmesg-gpu.txt`, not published)|Slot1 CBASE=`0080c670`, match at post-cache/pre-producer/fault; completed post-cache e138=`f`; PC/PD/PT CPU mappings Normal-NC, MAIR attr44|A persistent slot1 overwrite and CPU dirty-cache explanation are weak. Readback alone does not prove the CDM walker uses that bank.|
|v5 preinstall (`../experiments/logs/2026-10-23-exp19b-v5-preinstall-dmesg-gpu.txt`, not published)|PA`84addc000`, CBASE=`0084addc` installed from the pre-reset array by firmware; correct at every later sample; same fault|Runtime CBASE publication and a cache containing table data predating construction are weak. A later internal context-state change remains possible.|
|v6 device readback (`../experiments/logs/2026-10-23-exp19b-v6-readback-dmesg-gpu.txt`, not published)|CPU and META aliases match at all sampled PC/PD/PT words and cookies; CBASE=`0083b9dc` at fault; same fault|The bytes are device-accessible. This is a META/context0 alias read, not a CDM/context1 walker observation. `after-clean` is a stage name: clean_poc=0, so no extra clean ran.|
|v7 generations (`../experiments/logs/2026-10-23-exp19b-v7-generations-dmesg-gpu.txt`, not published)|Packed tag`01002008` before DC3 and before dispatch, DC3 consumed; same fault|Generation0 is not the cause. DC3 caches the initial tag, so this does not force a later generation mismatch.|

**[Observed]** In v6 the exact walk is PC[4]=`084cc8c1` ->
PD`84cc8c000`, PD[0]=`00000008_4cc88003` -> PT`84cc88000`,
PT[23]=`00000008_4d2d000d`. The working boot VM uses PC[387]=`080b9141`,
PD[0]=`00000008_4d934003`, PTE[0]=`40000008_4d600001`.
Thus PCE/PDE syntax agrees with the working VM; the leaf's bit62/low cache
attributes differ for META versus user data as expected from the iOS writer.
All four runs produce event4/word1=`200`, state`00000008/81058181`.

### New distinction: iOS reserves low context slots

**[Proven — same-image host code]** G5P accelerator vtable address point
**`6ff6820`** contains:

| Virtual offset | Function | Result/use |
|---|---|---|
|708|`6ca5174`|`mov w0,#0x100; ret`: maximum256 slots|
|710|`6ca8638`|Read configured slot count at accelerator+c48|
|718|`6ca517c`|`mov w0,#7; ret`: user-pool lower bound|
|720|`6ca5184`|Returns1; enables per-engine slot bookkeeping|

**`6c7be80`**, now `AGXAccelerator_startAndInitializeContextPool`, initializes
the manager at accelerator+11e0. It stores virtual718 at accelerator+11f4
(manager+14), the count at+11f0 (manager+10), and count-minus7 at+11f8.
Its free-stack at accelerator+1220 (manager+40) contains
**count-1, count-2, ... ,7**; top index is+1228 (manager+48).
**`AGXContextManager_acquireSlot` / `6c859a4`** pops that stack; its fallback
scan also starts at manager+14, namely7. Startup explicitly assigns the
kernel task slot0 with GART virtual120 `(task,0,3)`.

```text
6ca517c: mov w0,#7; ret
6c7be80: manager.lower = accelerator.virtual718()  //7
          free_stack = [count-1 ... lower]
6c859a4: N = free_stack[top--]                     //or scan from lower
          publish physical catalogue/generation; submit DC3(N)
```

**[Inferred]** Trying a normal iOS user slot is more informative than another
table clean or generation0 replacement. **[Open]** This does not establish
why iOS reserves1..6, a hidden hardware enable mask, or that hardware cannot
run user CDM at1. No numeric slot was captured with the successful iOS
userspace stream dumps; their actual slot must not be described as observed7.

### CBASE ownership and the exact job-to-slot path

**[Proven — firmware plus immutable r19]** There is no recovered conventional
`FWMEMCONTEXT { PA, regset }` in W. The equivalent physical catalogue lives
in the shared **u64 PA[256]** array addressed by control+1048; control+1044
points to **u32 host_generation[256]**. Firmware cached generations are
separate, through root+458. Boot-layout object slots86/89 share PA backing;
84/87 share host-tag backing;85/88 share separate cached-tag backing.
These pairs are views of one allocation, not two arrays to populate.

| Path | Relevant instructions/dataflow | Result |
|---|---|---|
|DC3 `1000218c..10002218`|Read cmd+4=N; GETL at`100021b8` from`ctrl1048+8*N`; call`10019b84` at`100021c0`; copy host tag to cached tag; invalidate|Unconditionally binds PA[N], including generation0; e138=`(N<<3)|4` for catalogue invalidation|
|Boot `100052d0`|Sync cached tags via`100179cc(1)`; call writer at`10005310/10005340`|Installs all256 slots,255 down to0, including zero entries; no generation0 means unloaded rule|
|Job `80001940`|Read W+8 at`80001954`; trampoline`8000aad4 ->10017ac4`|Compare packed tags; equal returns; unequal invalidates changed levels and updates cached tag. **No PA lookup or CBASE write.**|
|Start32 interpreter `80004bd4`|Pass **packet+4** to`80007390`; `800073a0` loads args+10 hex|Slot comes from **opcode32+14 hex**, independently of W+8|
|CDM select `800073b0 ->8000a704 ->1001838c`|Mask N to8 bits; three SETB stores|Write N to R0+**f07a,136e,136f**|

The CBASE writer's corrected short excerpt is:

```text
10019b8c: LSL D0Ar2,D1Ar3,#20    //PA high32 <<20
10019b94: LSR D0Ar4,D0Ar4,#12   //PA low32 >>12; OR the pieces
10019bb0: SETD [R0+e140],N
10019bb8: SETD [R0+e148],PPN    //u32 PA[39:12]
```

The two firmware listings contain only the three **decoded direct** call
sites above to`10019b84`. This bounds the search; it is not a proof against
every possible computed target. The traced CDM fresh-job path does not
rewrite CBASE at scheduling time. The writer and the matching public
`MMU_CBASE_MAPPING` definition have **no recovered INVALID/valid bit**;
do not OR1 into the PA>>12 or introduce a regset sentinel.

**[Proven — r19]** `compute.inc:g5p_compute_attach()` publishes
`catalogues+8 = c->catalogue.dma` **unshifted physical PA**, `versions+4=0`,
and DC record`{3,1,0,0,0}`. `compute-layout.h:g5p_make_compute()` sets
**W+8=1, start32 word5=1, finish33 word3=1**. iOS **`6ca8da4`** emits the
last two fields from W+8. These four independently supplied IDs currently
agree; no zero PA, device-VA-for-PA or wrong shift is found in the r19 bind.

**Fault-time prediction:** a persistent CBASE clobber would produce a PA
mismatch, unlike all four runs. Wrong requester state, reserved-slot state,
VA/table attributes, or a hidden context-valid latch can all coexist with
the observed correct CBASE. A transient overwrite-and-restore is not excluded
by sampling alone, but has no supporting write in this traced job path.

### Per-requester registers and other addresses before the stream fetch

**[Proven — same firmware]** This image uses the8-bit-context
**CONTEXT_MAPPING0** layout, not the legacy3-bit `BIF_CAT_BASE_INDEX` at1240.

| Engine helper | CONTEXT_MAPPING0 byte | Additional byte stores |
|---|---|---|
|TA `10017d6c`|f078, bits7:0|136c,136d|
|3D `10018110`|f079, bits15:8|1368,1369|
|CDM `1001838c`|f07a, bits23:16|136e,136f|

Each row writes the **same masked N** to all three bytes. The public IMG
header corroborates f078/CDM shift16. The Apple136x bytes' independent
hardware names remain unknown; their role as replicated per-engine slot
writes is proved by this code. For the r19 packet, the expected CDM values
are `(readl(f078)>>16)&ff = 1`, `readl(136c)&ffff0000 = 01010000`.
For slot7 expect **7** and **07070000**. The supplied logs do not yet sample
these registers. e150.context1 corroborates context selection, but does not
replace a sample of every requester mapping copy. Add aligned readl samples
at failure; no host subword writes or speculative legacy1240 writes are needed.

**[Proven — bounded fresh-start path]** Before the stream kick,
`80007398 ->1001e7b8` pulses64-bit R0+108=`2`, then0, polling bit1;
the selector writes follow. **`80008d24`** programs:

| R0 register | Host field | Address interpretation |
|---|---|---|
|4c0,4c8|W+10, W+14, u32|CDM configuration words|
|498|W+60, u64|GPU VA of CDM scratch/context-state tail, not physical catalogue PA|
|4b8|W+68, u64|Packed PDS state|
|480/484|W+70, u64|CDM stream GPU VA; observed`1:0005c000`|
|11040 indexed by11ff8=0..7|W resource state|CDM resource-array setup|
|4a8,4b0|H+34/H+3c, H=W+1e8, via`10018358`|Later CDM context-state fields|

**`80008f30`** writes478=1. W's common-context/channel pointer carries
scheduler/timestamp state; it is not another PA/regset structure in the
traced path. **[Open]** A malformed scratch/context-state pointer could cause
another read, but the latched fault VA here is exactly the stream start.
No separate user-context enable, per-context VA-width setting or alternative
PA-source field was found in these bind/select/fresh-start routines. This
negative finding is bounded to those routines, not the entire chip.

### Level numbering,16KiB descriptors and the above4GiB address

**[Proven — decoder provenance]** The same-image iOS decoder **`6ca5c14`**
extracts numeric level `(status>>4)&3`. The primary
[IMG `_RGXDecodeMMULevel`](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/refs/heads/chromeos-4.4/drivers/gpu/drm/img-rogue/1.10/rgxdebug.c)
maps **0=Page Table,1=Page Directory,2=Page Catalog,3=Cat Base** and is used
for the corresponding S7 MMU status layout. No same-image alternative
human-readable G5P level map was found. The earlier PBE VA0 status`ff2a1`
has numeric2; absent boot PC[0] fits Page Catalog. The sink patch populated
PC/PD/PT together, so that old run corroborates the interpretation without
being an isolated fault injection at each level. There is no positive
evidence for reversing the numbering and treating3 as a leaf fault.

**[Proven — user GART virtuals]** `6ff77e0+170 ->6ca9a44` supplies PCE1;
virtual178 **`6ca9a4c`** returns `(GPU_PAGE_SHIFT-12)|1`; virtual180
**`6ca9a60`** returns `(attrs&c)|((attrs&1)<<1)|((attrs&10)<<58)|1`.
The on-disk global GPU shift12 is **overridden at startup**:
accelerator virtual848 **`6ca8818`** returns1, so **`6c7be80`** requires
CPU PAGE_SHIFT14 (panic text: GPU uses16K page size but CPU does not), then
copies runtime CPU page_shift/mask/size into globals`7b3ed50/58/60`.
Therefore **PDE=`PT_PA|3` is the live G5P16KiB format**, and attrs c produces
user PTE=`PA|d`. META's bit62 is deliberately derived from attrs10.

**[Observed — retained successful iOS captures]** Both `one` and `store` in
`build/gpu-userspace/device/out5-20261022T184530Z` contain userspace C0=
**`10005c000`**, C60=`10005c0c4`, the stream terminator at+c4, return code0
and the expected output (one changed word /64 changed words respectively).
The script strictly resolves the recorded CPU regions and verifies those
bytes. See [userspace capture interpretation](../../gpu-userspace/command-stream.md).
This is the **same above4GiB stream VA** as Linux, not an inferred allocation
range. It rules out a general G5P/CDM4GiB limit; it does not measure a
hypothetical incorrectly configured per-context limit in Linux.

### Maintenance order, SLC command and coherency limits

**[Proven — firmware]** `1001b5c4(context,all,flag)`, wrapped by
**`1001dfec`** with flag1, builds
**e230=`(all ? 0x403 : (context<<2)|3) | 0x800`**, then waits for
e238.bit0 clear via`100018c8`. The general helper **`10019564`** independently
constructs `(all?400:context<<2)|(flag?800:0)|(op1?2:0)|(op0?1:0)`.
Thus **807 is correct for context1**,803 for0,81f for7,c03 for all.
Context bits are9:2, all-context bit10, operation bits1:0, flag bit11.
**[Open]** Exact hardware names of operations0/1 and flag11 are not independently
proved; do not rename flag11 a sync-enable solely because the helper polls.
MMU e138 uses **context<<3**, not<<2; all-level N=7 is3f, PC-only is3c.

**[Proven — order]** Boot calls **`1000515c` before `100052d0`**:

```text
10005184 ->1001b638: 11f00=1
  ->688=7; wait(688&7)==0
  ->1720=global44|1f; wait(1728&1)==0
  ->e230=c03; wait(e238&1)==0
  ->e138=804; wait completion
100051c4 ->10019bc4: 11f00=2; poll bit1 clear
  ...10019ae0 sets e400 low3=1...
10000aec ->100052d0: install all256 CBASEs
DC3: write CBASE ->cache tag ->context PC invalidate
fresh start32: pulse108 ->CDM selectors ->CDM state/stream ->478=1
```

The normal traced fresh start has no11f00/688/1720/e230 sequence.
Linux **`compute.inc:g5p_compute_cache()`** repeats a **boot-helper subset
after attaching the user context** when cache_variant3 is selected, as in
these r19b runs. That differs from the recovered normal-job order.
**[Inferred]** An internal context-state effect of this extra subset could
survive correct CBASE readback; cache_variant0 is an informative follow-up.
No evidence establishes that11f00 clears validity. Its sampled readback0
after writing1 is self-clearing behavior, not proof it was left enabled.

**[Proven — bounded host/ADT comparison]** iOS **`6ca4e70`** and r19
`boot.inc` both initialize e200=3, e208/e210=`b42dd24b4bd22db4`,
wrapper2010=bf,2014=233. e160 comes from GART virtualf0 **`6ca9a08`**,
which obtains the sink descriptor's physical PA through`6cbca84`; it is
not a per-context valid mask. `6ca4f74`, renamed
`AGXAcceleratorG5_waitForSLCAndBIFIdle`, polls SLC/BIF idle/outstanding state.
Static `docs/adt/j71t.json` and the already-pulled live sgx ADT block have
no coher/snoop/AXI/MMU configuration property. Property absence does not
identify the interconnect's transaction attributes.

**[Open]** No verified iOS-versus-Linux ARSNOOP/MMU-walk cache-policy register
delta was recovered. The tempting ADD38c0 at firmware`1001aa00` is preceded
by ADDT1: the actual address is **R0+138c0**, a diagnostic counter block,
not legacy AXI_ACE_LITE_CONFIGURATION38c0. Do not write legacy38c0 from
that false match. iOS's cached tables/dsb-only flush contract from round13
remains relevant to correct DMA description, but Normal-NC plus META byte
equality makes **coherent DTB a low-priority fault experiment**. Neither
those facts nor a successful META read establishes the CDM walk's physical
snoop attributes. e230807/MMU0 is valid syntax but lower value than the new
slot and requester-state comparisons.

### Ranked concrete changes for the kernel agent

These are diagnostic changes, not a claim that the root cause is solved.
Use one existing failing fixture as the baseline and keep its unrelated
parameters fixed. Capture requester selectors in the same boot as the first
change; a separate DFU boot solely to add readback is unnecessary.

1. **Move the user fixture to N=7.** In `compute.inc:g5p_compute_attach()`,
   publish unshifted PA at **PA array+38 hex**, generation at **tag array+1c**,
   DC3+4=7; update guards, `g5p_compute_preinstall()` and
   `g5p_compute_publish_generation()` to the same N when those modes are used.
   In `compute-layout.h:g5p_make_compute()`, set **W+8=7, start32+14=7,
   finish33+0c=7** (offsets hex, relative to each opcode). Parameterize the
   CBASE selector/readback and `g5p_compute_mmu()` as N; all-level request
   **e138=3f**, not f. Keep PCE/PDE/PTE bytes, cache mode, stream VA and
   generation policy unchanged. Add `g5p_compute_hardware()` samples using
   aligned **readl(f078), readl(136c)** at pre-producer and compute-failed.
   **Expected:** firmware installs slot7; after start32 CDM byte=7 and136c
   upper16=0707. If the same fault follows the selected slot, expect
   **e150=`010005c0:00700139`**. Completion supports a low-slot state issue;
   identical context7 failure weakens it. If selectors disagree, fix the
   packet/source of the disagreeing byte before changing table syntax.

2. **Remove the extra post-attach boot-maintenance subset.** In
   `compute.inc:g5p_compute_cache()`, select **compute_cache_variant=0**;
   compare with **mmu_preinstall_context=1, compute_mmu_variant=0** so firmware
   boot binding is retained. Keep the chosen N and all table bytes fixed.
   Ensure the runner does not overwrite cache0 with its default3.
   **Expected:** no host11f00/688/1720 writes after DC3, yet firmware CBASE
   remains correct. Completion implicates that extra sequence/order;
   unchanged fault with correct selectors weakens this mechanism. Do not
   replace it with an invented context-valid write.

3. **If user slots still fail, run CDM against the already-working slot0
   catalogue as a controlled diagnostic.** In
   `compute.inc:g5p_compute_prepare()` and pre-release map construction,
   install user PC[4]/PC[12] entries into **b->catalogue** before reset release,
   preserving its existing VA0 sink and PC[387]; use that PA for the fixture
   and keep ownership/freeing with the boot allocation. Set W+8/start32/finish33
   to **0** in `g5p_make_compute()`. In `g5p_compute_attach()`, avoid rebinding
   slot0 or treating its nonzero PA as EBUSY; retain its boot PA/tag and mark
   only the experiment's attachment bookkeeping complete. Use cache0 and
   no host CBASE rewrite. **Expected:** CDM selects0 and either completes,
   advances to another fault, or reports the same VA with context0
   (**`010005c0:00000139`**). Success isolates user-slot activation from the
   shared CDM/stream/table path; same-level failure at0 points toward
   CDM-wide state or a walker interpretation not measured by META aliases.
   This is a bring-up probe, not a production sharing model.

**Fault-time CBASE alone cannot discriminate these three.** All can retain
the correct PA>>12; mapping-register samples, selected fault context and
completion/fault progression provide the distinguishing observations.
Re-running generation0/nonzero as already implemented, clean/readback,
preinstall alone, or coherent DTB has lower information value now.

### Reproduction and saved analysis

Run **`python3 -B scripts/gpu/firmware-re/round14-evidence.py --verify`**.
It checks SHA-bound firmware and extracted host sections; independently
verifies18 targeted META instructions with the existing GNU metag binutils;
checks the iOS vtable/pool, immutable r19 fields and alias backing, all four
owner logs, the level map and successful iOS above4GiB captures.
It writes`build/gpu/firmware-re/round14/evidence.txt` and the bounded
disassembly there. The generic MINIM printer still misdecodes some indexed
and cross-unit instructions; the checked anchors take precedence.

Optional **`--capture-kernel --capture-r19b --capture-host`** refreshes only
read-only immutable-source/shared-Ghidra captures. **`--capture-public`**
fetches the primary IMG decoder and requires its pinned SHA256; `--verify`
itself uses the local capture without network or device access. Firmware
extraction and GNU tools are the existing rounds11–13 prerequisites.
Ghidra's accelerator startup and idle-wait routines were renamed, the
slot getter, emitter and embedded firmware paths were commented, and
`scripts/ghidra-mcp.sh save` persists the shared analysis.

## Round 13 — CDM context1 fault: valid PCE, correct CBASE, firmware recovery

2026-10-23. Research only; no device access, kernel edits or commits.
R18c/r19 `e150=010005c0:00100139` decodes to **valid1, type0 Page Invalid,
read1, numeric level3, requester4 CDM, sideband0, context1, VA10005c000**.
The public IMG label for level3 is **Cat Base**, not level2 Page Catalog.
**R19 supersedes the initial missing-CBASE hypothesis:** context1 already
read back `80c38c` before the direct rewrite; a completed context1 invalidate
`e138=f` did not change the fault. Do not turn this label into proof of a
zero base or malformed PCE.

**Definitive same-image PCE:** 32-bit little-endian, bit0 valid,
bits31:4 = PD PA39:12, bits3:1 zero; `PCE=(PD_PA>>8)|1`.
`080fb281` is valid and decodes to **PD PA80fb28000**. There are no
context-ID or page-size bits in this PCE. PDE/PTE are64-bit;16KiB PDE flags3.
The iOS table allocator uses default cached memory, not an uncached option;
this kernel's `_flush_dcache64` branches to **`dsb sy; ret`** and descriptor
cache operations1/2 also perform only `dsb sy`. The recovered iOS path relies
on coherent DMA, rather than cleaning each table store to RAM. Linux's
missing `dma-coherent` property is a concrete device-description difference;
its effective allocation policy and relation to this fault require the
logged comparison below.

R18c log424..430 contains **firmware event4, word1=200**, while event130 is0.
Firmware ACK/recovery is verified by the callback and companion transition,
not a different context-specific fault bit. **The remaining fault's physical
cause is not proved by these captures.** The format, registration and
coherency contract below are established; the ranked changes distinguish
the remaining runtime state/visibility possibilities.

Sources: r18c policy7 (`../experiments/logs/2026-10-23-exp18c-sink-p7-dmesg-gpu.txt`, not published),
r19 CBASE/invalidate run (`../experiments/logs/2026-10-23-exp19-mmu1-dmesg-gpu.txt`, not published),
r19 CPU walks (`../experiments/logs/2026-10-23-exp19-mmu1-walks.txt`, not published),
same j71t 20H350 kernelcache as round11. Kernel comparisons are immutable
`a9/gpu` **`9e54857fd20c`** (r18c) and **`3ab2d63e58da`** (r19), read through
`linux-a9-src:/src:ro`, never the changing worktree. The
bounded evidence (`round13-evidence.txt`, not published) is reproduced with
`python3 -B scripts/gpu/firmware-re/round13-evidence.py --verify-isa`.
Optional `--capture-r19 --capture-coherence` reads the committed kernel and
shared Ghidra, never the device. Ghidra's generation getter was renamed;
the PCE store and barrier-only cache primitive were annotated and saved.

### Complete e150 decode and what r19 excludes

Treat the kernel's print as **high32:low32**:
`S=0x010005c000100139`, not the reverse order.

| Status bits | Value | Interpretation |
|---|---|---|
|0|1|Valid MMU fault|
|2:1|0|Type0, **Page Invalid**, not Read-Only/Protected|
|3|1|Read|
|5:4|3|Numeric level3; public IMG label **Cat Base**|
|11:6|4|**CDM**, from this G5P image's requester table|
|19:12|0|Sideband0; no further same-image subdecode established|
|27:20|1|Hardware MMU context/catalogue-base slot1|
|63:28|`0x10005c00`|VA39:4, therefore **`0x1_0005_c000`** at16-byte precision|

The round11 iOS decoder **`6ca5c14`** supplies these shifts/masks. Its table
**`6ff5ed8 + 16*4`** points to `594613f`, string **CDM**, classification8;
type table`6ff6258[0]` says Page Invalid. `e158=0` means the separate META
fault bank has valid0. The address formula is
`(S >>24) & 0xfffffffff0`; the low4 VA bits are unavailable. This is a
read of the start of the submitted CDM stream, matching R0+480=`5c000`,
R0+484=`1`, rather than a shader output write or META instruction fault.

The public primary
[IMG fault decoder](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/refs/heads/chromeos-4.4/drivers/gpu/drm/img-rogue/1.10/rgxdebug.c)
labels levels3/2/1/0 **Cat Base / Page Catalog / Page Directory / Page Table**.
The same-image iOS routine records the numeric level. This distinction matters:
**level3 is not evidence that PC[4].valid is zero**. Level2 was the previous
PBE VA0/missing-PC[0] case. A numeric-level3 fault with correct preceding
CBASE readback leaves a runtime state/cache or G5P-specific interpretation
question; it does not justify changing valid PCE syntax.

R19 GPU log**401..404** proves `ctx1=80c38c` **before** the direct rewrite,
then `match=1`, then `e138=f ... post=0 result=0`, with a clean fault bank.
Log**413/414** proves1728 clears; log**416** publishes the CL;
log**423** has the identical new CDM fault and log**425** has event4/200.
Thus **DC3 failed to bind the base**, **wrong PA>>12 encoding**, and
**absence of a targeted all-level invalidate before the maintenance
preamble** are each demoted as standalone explanations. No log samples
banked CBASE after the subsequent11f00 preamble or at the fault.

### Definitive user-context table format: no hidden PCE context or size field

This comes from **the user GART writer**, not only the boot dummy tree:

* Geometry constructor **`6ca9b34`** stores bytes`03,04,04,0c` at+1b:
  three levels; catalogue entry size4; encoded-address left shift4;
  physical-address right shift12. Catalogue VA shift30,1024 entries,
  valid mask1, encoded address mask`fffffff0`.
* **`6cab0d0`** copies accelerator+1a10 geometry+1c.. into each GART+2f0..
  before allocating its tables. The copied geometry is the same for the
  kernel GART and ordinary task GARTs; context ID does not select a different
  PCE format.
* **`6cab44c`** obtains the new PD's physical address through`6cbca84`,
  shifts it down by GART.byte2f2=12, up by byte2f1=4, ORs virtual170's
  flags, then at **`6cab698` executes `str w8,[x9,x23,lsl #2]`**.
  Virtual170 (`6ff77e0+170 ->6ca9a44`) returns GART+2f8=1. This is an
  indexed **32-bit** store, definitively not a64-bit PC slot.

```text
PCE[ci] u32 LE = ((PD_PA >>12) <<4) |1 = (PD_PA >>8) |1
  bit0 valid; bits3:1 zero; bits31:4 = PD_PA[39:12]
decode PD_PA   = (PCE &0xfffffff0) <<8
ci            = (VA >>30) &1023

PDE[di] u64 LE = PT_PA |3                  // G5P16KiB pages
  bit0 valid; size code1 in bits3:1; address field PA[39:5]
  recovered virtual178 computes (PAGE_SHIFT-12)|1, here3
di            = (VA >>21) &511

PTE[ti] u64 LE = (PA &0xfffffff000) |flags
  bit0 valid; bit1 read-only; bit2 CC; bit3 SLC bypass;
  bit62 META protection; no separate execute permission established
ti            = (VA >>14) &127; page offset = VA &0x3fff
```

The leaf flag writer **`6ca9a60`**, virtual180, maps host attrs bit0 to
PTE.bit1, keeps attrs`c`, moves attrs10 to bit62 and sets bit0. Current
attrs`c` therefore produces **`PA|d`**, valid and writable. These leaf cache
flags describe the mapped resource; they do not set the CPU memory type
of the PC/PD/PT pages or make a missing CBASE valid.
The compared Linux `g5p_leaf_entry()` omits iOS's attrs.bit0-to-RO conversion;
that is a future permission-helper correction, with **no effect on this
fixture's attrs0/c** and no explanation for type0/level3.

**`080fb281` passes the format:** bit0=1, bits3:1=0,
`(080fb280 <<8)=80fb28000`. This is a16KiB-aligned PD. It contains no
extra context-ID bits and no page-size bits. Do not change it to PA>>12,
raw PA, a64-bit catalogue layout or an invented context-valid bit.

R19 walks**72..79** yield:

| Selection | CPU-visible entry | Next physical address |
|---|---|---|
|CBASE[1]|`0080c38c`|PC=`80c38c000`|
|PC[4], at PC+10 hex|`080fb281`|PD=`80fb28000`|
|PD[0]|`0000000848420003`|PT=`848420000`|
|PT[23], offsetb8 hex|`000000080f5b400d`|stream=`80f5b4000`|

The arithmetic and valid bits are correct. A CPU walk proves these bytes in
the CPU view; it does not sample what a faulting hardware walker cached/read.
Boot PC=`84959c000`, PC[4]=0, is normal for the firmware-only boot VM and
does not invalidate this context1 walk.

### Context registration, firmware mappings and MMU invalidation ordering

**CBASE is a banked physical address, not a PCE.** Firmware
**`10019b84`** writes e140=context8, then e148=`catalogue_PA>>12`;
the high32 PA contributes `high32<<20`. The register's address field is
bits27:0, representing PA39:12; it has no separate valid/permission field.
R19's `80c38c` is exactly right for `80c38c000`.

iOS **`6c859a4 ->6c81794 ->6c98a60`** publishes the GART physical catalogue
PA (virtualf8 ->`6ca9a10`) and packed MMU generation (virtuale8 ->
`6cabf58`, virtuale0 ->`6cabf40`) into per-context arrays. Host setter raw
stores are **u64 `[fw+430][context]`=unshifted PA** and
**u32 `[fw+420][context]`=generation**. Firmware control+1048 and+1044
refer to their GPU-visible views, respectively. Slots86/89 are two views
of the same catalogue array backing;84/87 are two views of the same host
generation array. Slots85/88 back the firmware's cached-generation array.
**Do not allocate/populate slots86 and89 as independent arrays.**

Boot **`100052d0`** installs all256 context bases from that physical-PA
array. Later DC3 **`1000218c..10002218`** reads `cmd+4` as the context,
reads `ctrl1048[context]` as a64-bit PA, **unconditionally calls10019b84**,
copies `ctrl1044[context]` into cached generations through root+458, then
calls **`1002700c ->80009030`** with `(context, allContexts=0, type=1)`.
**Generation0 does not skip the DC3 CBASE write.** The corrected register
offset GETL/GETD/SETD forms are independently checked in round13-evidence;
the generic MINIM pretty-printer still misprints some of these operands.

The job's **W+8=1**, DC3's slot=1 and e150.context=1 agree. Firmware's CDM
context setter **`1001838c`** masks the supplied slot to8 bits and writes
R0 bytes**f07a,136e,136f**. Slot1 is not a pointer, a PC index, or the
firmware's PC index387. **`0x387` is VA[39:30]** for the firmware's
`0xe1_c000_0000` region; it covers one1GiB VA region within each individual
catalogue. Context0's entry387 does not globally map user contexts, and
entry387 cannot cover user stream PC index4 or code PC index12. Linux
copies the boot PC into the context1 PC then installs4/12; this preserves
the firmware mappings in context1. The whole-PC `memcpy` also copies the
VA0 sink, correcting the old comment that it shares *only* entry387.

**Required translation ordering:** fill owned PC/PD/PT pages; make CPU
writes visible under the DMA mapping's actual coherency contract;
`dma_wmb()`; publish unshifted PA and generation; consume DC3/bind;
invalidate affected translation caches and wait; publish W/CL/ring producer
with barriers; kick MTS. For changes after attach, publish a changed MMU
generation before the job. Firmware **`10017ac4`** compares host and cached
tags; equality skips lazy invalidation. Changed PC/PD/PT generation fields
are bits**31:22 /21:12 /11:0**, selecting request types1/2/3, respectively.
The kernel's permanently zero generation works only for its immutable
single-attachment fixture; it is insufficient for future table edits.

**`80009030`** maps type1/2/3 to masks**4/2/1**, ORs`context<<3`, writes
**e138** and waits for the request clear via100018c8. Thus DC3/context1 PC
invalidate is **`c`**; context1 all-level is **`f`**. All-context mode adds
**800** and updates firmware generation bookkeeping. **There is no extra
TLB bit8 here: bit3 is the low context bit.** Do not import a non-SLC-VIVT
legacy TLB flag into this encoding. R19 testedf successfully, so simply
adding that same write again in the same position is not a new fix.

688/1720 maintenance, SLC e230/e238 and translation e138 are separate
transactions. Host `1720=1f` completion does not prove an MMU/SLC flush.
Firmware boot1001b638 also calls **`1001dfec ->1001b5c4`**, emitting
**e230=`c03`, wait `(e238&1)==0`**, before e138=`804`. The latter helper's
context-specific raw expression is **`(context<<2)|3|800`**, with the
all-context argument substituting403 before OR800. These are recovered
command literals, not proof a host all-context SLC operation is required
before every CDM job. Prefer existing firmware ownership; use the bounded
context1 literal807 only as the separate SLC comparison described below.

### CPU/GPU coherency: cached iOS tables; cache primitives are barriers here

The allocation/cache question has direct same-kernel answers:

1. G5P constructor **`6ca8c94`** clears accelerator+1afc. Catalogue allocator
   **`6cac5c4`**, PD/PT allocator **`6cab44c`** call the imported
   **`IOBufferMemoryDescriptor::withOptions(accel1afc|3, size, alignment)`**.
   On the recovered normal path, options are **3**, direction in/out,
   **cache-mode maskf00=0**. The descriptor is prepared once and its CPU
   pointer is filled with ordinary stores. There is no per-entry CPU clean.
2. Import thunk6cbcc10 resolves to kernel **`7776a58`**, which calls
   **`IOBufferMemoryDescriptor::initWithPhysicalMask 77757d4`** for
   `kernel_task`. Options3 uses ordinary kernel RAM allocation; the
   cache-override/remapping branch is selected by explicit cache bitsf00,
   absent here. This is default cached RAM, not an iOS table-only
   uncached mapping hidden behind `withOptions`.
3. Even the explicit GPU buffer cache helper **`6c87568`** normally calls
   descriptor `performOperation(1, offset, length)`. Kernel
   **`IOMemoryDescriptor::performOperation 777e968`** implements cache
   operations1 and2 as **`dsb sy` at777e9b0** and success. Its mapping/failure
   fallback invokes **`IOFlushProcessorCache 772b140`**, which calls
   **`flush_dcache64 7181738`**. That thunk branches to **`72d770c`**,
   whose complete body is **`dsb sy; ret`**, with no DC CVAC/CIVAC loop.
   Both raw instruction pairs are retained in the evidence, not inferred
   from the C function names. IOGPUFamily helpers6c77bd4/6c77d50 reach
   the same descriptor/fallback primitives; they do not reveal a missing
   per-PCE clean instruction.

**Conclusion:** iOS relies on coherent cached CPU/GPU DMA for its live
page tables on this image/SoC. This does not mean GPU translation caches
or SLC entries never require their own invalidation. It also does not
measure whether Linux has enabled the same fabric/coherency behavior.
An ordinary CPU dirty-cacheline theory that assumes iOS cleans each new
PCE or maps tables uncached is contradicted by the recovered normal path.

**Linux difference:** r19's `s8003.dtsi:72` GPU node and parent
`s800-0-3.dtsi:66` contain no `dma-coherent`. The OF walker defaults to
noncoherent on this ARM64 configuration. `g5p_boot_alloc_page()` correctly
uses `dmam_alloc_coherent`, but that API name alone does not imply cached
CPU memory. With `dev_is_dma_coherent=false`, `dma_direct_alloc()` remaps
it using `pgprot_dmacoherent` = **MT_NORMAL_NC**, cleans the initial linear
alias, then all table stores use the returned mapping. With true, it can
use normal cached linear RAM. ARM64 setup also has a **CLIDR.LoC=0 override**
that forces coherent; no such warning appears in the supplied full r19
dmesg, but **the actual device flag/PTE attributes are not logged**. Log
them before claiming the effective mapping. Existing `dma_wmb()` is an
ordering barrier, not a missing per-store CPU cache-clean operation.

### Ranked remaining causes and exact kernel changes to discriminate them

There is no confirmed CDM fault fix yet. Prioritize these bounded changes;
retain the now-working pre-release sink and policy7 so the old reset fault
and the independent6c8 stall do not obscure the result.

1. **Catalogue-base state changes after the observed sample** — highest
   diagnostic priority because the status is level3. In
   `compute.inc:g5p_compute_submit`, r19 calls`g5p_compute_mmu()` at589,
   **then**`g5p_compute_cache()` at595; the latter writes11f00=1. Add the
   same clock-safe banked CBASE0/1 observation **after cache maintenance,
   immediately before producer publication**, and retain a first-fault
   CBASE/clock/context-selector snapshot before park. A fresh-run variant
   should move the existing context1 bind+e138=f operation **after** that
   maintenance, keeping all preflight/failure gates. This is an ordering
   experiment, not a proven claim that11f00 clears CBASE. Confirm with
   `CBASE stage=post-cache ctx1=... expected=... match=...`,
   `MMU invalidate stage=post-cache request=0000000f post=00000000`,
   and decoded fault level/address. If CBASE changes, fix the identified
   writer/retention ordering. If CBASE stays correct at fault, demote this
   hypothesis; repeating the already-tested pre-cache rebind is pointless.
2. **CPU/SLC visibility under the Linux mapping policy** — next concrete
   implementation comparison. Log `dev_is_dma_coherent`, allocation CPU
   mapping type/AttrIndx, MAIR and PC/PD/PT DMA addresses. Declare
   **`dma-coherent;` on the s8003 G5P node before device DMA setup/allocation**
   for a fresh-boot comparison with iOS's cached-coherent allocation contract;
   keep `dmam_alloc_coherent`, the existing encoders and `dma_wmb()`.
   Do not flip `dev->dma_coherent` after allocations or cache-clean an
   arbitrary remapped VA. Expected evidence is
   `DMA coherent=1 table_memtype=Normal-WB ... PC4=080fb281` (addresses vary),
   with correct post-cache CBASE, and absence/change of the CDM fault.
   If the flag was already true, there is no allocation-policy delta to fix.
   A separately selected, first-submit-only **e230=807, wait e238.bit0=0,
   then e138=f/wait**, after CPU publication and maintenance, tests an SLC
   stale-table path. Record pre/request/post for both; do not equate an SLC
   comparison's success with proof that CPU caches needed cleaning.
   The parallel r19b device-readback plan (`../experiments/19b-table-visibility.md`, not published)
   adds a useful CPU-versus-device comparison through fixed external META
   aliases. Compare its clean0/clean1 runs with allocation/readback mode
   held fixed. A matching META/context0 read narrows visibility but does
   not read CDM/context1's internal walker or translation caches; preserve
   that distinction in the log interpretation. No r19b device result is
   available in the supplied data.
   These experiments distinguish visibility; neither is already proved
   necessary by a CPU walk or by the DMA API's name.
3. **Runtime table publication/invalidation timing beyond a one-shot VM** —
   production fix is real packed generations: bump/publish the PC/PD/PT
   generation when that level changes, then let firmware compare/invalidate
   before kick. This matters for future maps/unmaps, but r19's immutable
   tables plus explicitf make “zero tag alone caused this run” weak.
   For a strong isolation run, build the small context1 fixture PC/PD/PT
   **before reset release** and seed slot1's PA before the firmware's256-base
   install loop; keep actual W/CL publication later. If that alone changes
   the fault while identical runtime tables fail, investigate runtime cache
   publication rather than changing PCE width or mapping every VA hole.
   Log the pre-release slot1 PA, PC4/PD0/PT23, boot-install/readback and
   the first post-kick fault, not just ALIVE.

**Demoted/rejected fixes:** changing `080fb281`, adding PCE context bits,
populating context0 PC4 as though entry387 were global, reissuing identical
pre-cache CBASE/f writes, deleting the successful1720 command, host-ACKing
the already-consumed event or forcing companion0 cannot be justified by
these captures. The next successful run must still report stamp1,
output12345678, preserved other sentinels and no firmware fault; a different
fault level merely shows translation progress.

### event130=0 means firmware already handled the fault; recovery is CDM state2

There is positive fault-delivery evidence. Firmware callback
**`80009fb0..8000a000`** reads R0+130, ANDs enable mask at private+2b8
(captured83ec, which includes200), ORs private pending+2bc, then writes
**R0+138=pending before dispatch**. Source4 dispatch
**`100039b8 ->10003a90`** consumes software bit200, calls
**`1001a494(companion+18)`** to decode/classify e150/e158, sets
**companion+0=2**, and enters **`1000d180`** recovery. It is the same MMU
event bit as the PBE fault; it is neither masked out nor an unhandled
context1-specific event.

This CDM requester maps through firmware **`100173d0[4]=8`**; the bit3 path
at **`1001a6f4..1001a704`** sets **companion+18=2**. R18c's before/after
bulk RAM confirms **root+3980:0->2** (companion state),
**root+3998:0->2** (classification), **root+3994:0->1** (recovery count).
R18c log**424** and r19 log**425** actually contain **event type4,
word1=200**, after the hardware event latch has been ACKed. E150 may retain
the diagnostic fault while event130 is0. This is why polling event130 alone
misses the failure; use both fault-valid banks, the event ring and companion.

The captured recovery event has raw state words8/81058181; their further
semantics are unresolved. Static state2 recovery can reach
**`1001fda0 ->1000d448 ->1001fb9c`**, construct/publish the event, and park
in terminal self-loop **`1001fe08`**. The event and RAM prove recovery;
there is no captured live PC proving that particular final loop executed.
Retain the event/owned DMA and the existing host terminal/quarantine
behavior; this is not a normal completion to retire or a fault to ACK again
to resume the shader.

### Separate open item: policy10/6c8=1 still prevents DC consumption

The r18c sink policy10 log (`../experiments/logs/2026-10-23-exp18c-sink-p10-dmesg-gpu.txt`, not published)
has reset-released **317** and ALIVE **321** entirely clean (event130/e150/
e158 zero), yet **354** says `DC timeout consumer=0 producer=1 target=1`;
the timeout's fault-bank sample at353 is also clean. Consequently the
**6c8=1 DC/cmd8 stall is independent of the reset-release PBE fault**.
The VA0 sink fixed the latter and did not release this policy gate. Keep
this as a separate scheduling/policy investigation; policy7's submitted
CDM stream fault remains this round's primary result.

## Round 12 — release introduces the fault; conditional iOS dummy tables and verified firmware ACK

2026-10-22. Research only; no device access, kernel edits or commits.
This section incorporates the orchestrator's decisive
r18b full GPU log (`../experiments/logs/2026-10-22-exp18b-r18-c3-dmesg-gpu.txt`, not published)
and runner (`../experiments/logs/2026-10-22-exp18b-r18-c3-runner.txt`, not published).
Kernel source was read from committed `a9/gpu` **`131270da966d`**, with
`linux-a9-src` mounted **read-only**; concurrent later kernel work is not
the source of these conclusions. iOS is the same 20H350 j71t image as round11.
Bounded source/disassembly and byte checks (`round12-evidence.txt`, not published) are generated
by `python3 -B scripts/gpu/firmware-re/round12-evidence.py`. Optional capture:
`--capture-host --capture-kernel --kernel-ref 131270da966d` uses shared Ghidra
and a read-only Docker mount, never the iPad. Three understood Ghidra functions
were renamed and the conditional dummy-tree path annotated/saved.

**Verified:** the first *sampled* fault belongs to the release interval;
normal recovered iOS boot does **not** enable its all-address dummy-page
mapping; the actual firmware IRQ callback **does ACK bit200 at R0+138**.
**Not verified:** an instruction/register that issues the PBE write, a normal
power-on requirement to write VA0, or that ACK alone releases1720. No production
fix is established. The ranked changes below are concrete controlled experiments.

### R18b eliminates the stale-latch, arm-pause and cmd8 origins

All eight sampled stages `wrapper-awake`, `id-checked`, `slave-sampled`,
`maps-ready`, `before-arm`, `reset-held-arm`, `firmware-loaded`,
`before-release` have **event130=0, e150=0, e158=0**. In particular:

* Log238/241: `reset-held-arm` event/status zero; log245 verifies
  `R0+100=0000000100000000`. Holding META while the other reset bits are
  already released does not produce the observed fault during this pause.
* Log279/282/285: `before-release` is still entirely clean.
* Log360/363/366: **`reset-released event130=00000200`,
  `e150=00000000000ff2a1`, e158=0**, with GPC1/released1 at log358.
  The round11 PBE0_TEXAS0/write/VA0/context0/level2/type0 decode still applies.
* The host first consumes ALIVE at log368..372; cmd8 and context attach are
  later. Neither cmd8 nor the test shader can originate this first fault.

**Timing precision matters:** GPC-start is written at21.930338; active-state
poll completes21.931643; event130 is read21.934123. The first fault sample is
approximately **3.8ms after the start write and2.5ms after the active poll**,
after a sleep and wrapper/power logging. Firmware can execute in this interval;
the event ring is not drained until22.958. “Before the host reads ALIVE” is
not proof of “before firmware starts/emits ALIVE”. The release stream also
repeats the broad resets before its final Garten-only reset release; there is
no sample between those FIFO operations. Thus **release/early firmware origin
is verified; a hardware-only reset-edge PBE self-write remains an inference**.
The earlier suggestion to blame the long arm pause is demoted by this run.

### Exact pre-release comparison: no omitted PBE target/kick in the recovered stream

The complete logical stream from iOS `6c9feb4`, `6ca4e10`, `6ca4e70` and
Linux `g5p_boot_program_stream()` is below. R0 qword setters emit high32
first, then low32 with FIFO flagsff. R18b log302..333 matches these values
and reports45 FIFO words. There are12 R0 operations, plus wrapper setup.

| Ordered operation | iOS | Linux/r18b | Finding |
|---|---|---|---|
| R0+100 qword | `00e7fffffffffc1d` | same | Broad reset; PBE bit19 included |
| R0+100 qword | `00e7fe071ffffc1d` | same | Same staged reset mask |
| R0+100 qword | `0000000100000000` | same | Garten/META held; PBE bit19 now clear |
| R0+bf8 dword |1|same|META master boot|
| R0+e140 dword |0|same|Select catalogue-base context0|
| R0+e148 qword |Kernel GART catalogue PA>>12|`0084eb78`, PC DMA=`84eb78000`|Same encoding; actual selected base not read back in this run|
| R0+e200 qword |3|same|SLC3 control|
| R0+e208/e210 qwords |`b42dd24b4bd22db4` each|same|SLC3 scrambling|
| R1+2010/+2014 dwords |bf/233|same|Direct Fender writes, not GPU render-target addresses|
| R0+e160 qword |PA of GART's zeroed physical page|`000000084ea50000`|Same address domain; not a GPU VA0 PTE|
| R0+bb8 qword |0|same|Garten/SLC control|
| R0+100 qword |0 in Fender boot|same|Final release; omitted only in experimental arm|

Wrapper operations also agree with the recovered paths: park core0/GPC0,
request core20 while GPC0, reset FIFO/control (R1+10=0,+0=40000000),
terminate withffff0000, then core0/GPC1 (`+20000=0,+1000=11`, poll active).
The **extra executed arm stream** is a Linux diagnostic split; iOS loads its
RAM image/config before running one complete release stream. R18b's clean
arm interval makes deleting that extra execution a lower-priority comparison.

No operation above programs TA start400, ISP renderf08, a PBE surface address,
background-object PDS state, a tile-clear address, or an MMU fault-disable bit.
There is **no identified bad individual init write to skip**. In particular,
do not removee140/e148, change the two reset masks, or write zero toe150/e158
as a proposed fix. There is no recovered iOS pre-release “ignore context0
faults” or “bypass MMU for context0” write to copy.

### iOS memory setup: a real dummy-tree facility, disabled for the normal boot task

This closes a useful static question without assuming that a scratch allocation
implies a mapping:

1. `IOGPU_getKernelGPUTask` **`6c71488`** returns accelerator+D0. IOGPU
   startup `6c6e590` obtains that object through accelerator virtual620;
   the G5P vtable entry **`6ff6e40 ->6c808b4`** is now named
   `AGXAccelerator_createKernelGPUTaskFlags0`. It calls task/GART virtuala8
   with **`kernel_task`, flags0** (`6c808f0: mov x3,#0`). That virtual entry
   is **`6cab0d0`**, not a separately guessed context0 null-map constructor.
2. `6cab100..114` sets **GART byte258 = `(accelerator.bc0 !=0) || (flags&8)`**.
   Both the G5P constructor (`6ca8c7c`) and accelerator startup (`6c7c074`)
   explicitly clear accelerator+bc0. No enabling store is identified on the
   recovered boot path. This is a normal-path conclusion, not proof that no
   debug/configuration path can ever enable the facility.
3. `AGXGartG3ThruG9_initTablesAndOptionalDummyTree` **`6cac5c4`** allocates
   and zeroes the ordinary catalogue, then **always** allocates/zeroes a
   host-page-sized physical page: descriptor at GART+2a0, CPU pointer+2a8.
   If byte258 is0 it returns successfully **before constructing dummy tables**.
   Getter **`6ca9a08`**, GART virtualf0, returns that page's physical address;
   `6ca4e70` writes it toe160. Linux's separate16KiB `b->zero` matches this
   role. It is not evidence of a missing VA0 mapping.
4. With byte258 nonzero, the same function really does build a valid fallback:
   leaf descriptor+2b0/CPU+2b8; every leaf PTE points to the zero page with
   **flags0 -> valid writable PTE `PA|1`**; directory descriptor+2c8/CPU+2d0;
   all directory entries point to that leaf (`PA|3` on16KiB G5P); then all1024
   catalogue entries point to that directory (`PA>>8|1`). It therefore covers
   VA0 as well as other unmapped addresses. This is conditional all-address
   fallback, not an unconditional one-page VA0 boot mapping.
5. The twelve allocator ranges at **`59426d8`**, walked by6cab0d0, start at
   `300000000,500000000,600000000,6ffc00000,100000000` and the firmware
   ranges from`e1c0000000` upward. **The lowest is4GiB**; none contains VA0
   or catalogue index0. The recovered ordinary allocation path therefore
   does not fill index0 incidentally. Image/root/PIO mappings use index**387**.
   `6caa6e4` also uses the zero-page PA for padding **within an allocated
   range**, with flags1; that is not a VA0 mapping either.

**Actual delta:** Linux lacks iOS's *optional* all-address fallback and its
padding behavior, but the recovered normal iOS boot leaves that fallback off.
There is no demonstrated normal-path delta at PC[0] to repair “the iOS way”.
Both normal paths install the firmware catalogue for context0 and have a
physical zero page ate160. A dedicated VA0 sink is a well-targeted containment
experiment for r18b's absent catalogue[0], **not a proved restoration of iOS
boot state**. Do not map all holes or alias the real firmware tables to a sink.

One **confirmed pre-release RAM difference** should remain in the comparison:
iOS accelerator virtual768 **`6ca8648` returns1**, which6c96be8 stores at
META init+6c8. Linux policy7 deliberately overrides it to**0** in
`g5p_dc_init_variant()`. This changes firmware policy/lifecycle branches;
it is not an MMU-disable flag. Policy8/9/10 previously retained1 but lack
the new release-stage MMU samples, so those runs do not show whether this
particular early fault occurs with the iOS flag. Other policy/optional resource
tables remain incomplete; none is newly proved to supply a PBE VA0 target.

### Firmware boot audit: reset/configuration writes, no established dummy render

The evidence retains **every direct helper** of hardware initializer1000515c,
including its tail1001e754, plus the boot catalogue installer and IRQ setup.
The render-relevant operations are:

* **1001a038** pulses100/108 with reset values, then clears them/readbacks.
  It is a real early firmware reset sequence, before1001b638; it is not a
  render kick. Merely removing it is not justified.
* **1001a400** configures690/730; **1001b638** issues11f00=1,688=7,
  1720=`global44|1f`, e230/e238 ande138=804 with waits. These are boot
  configuration/maintenance, not a submitted job or a proved PBE clear.
* **1001d1fc/1001d388** program USC/PDS bases4008/4010/4028 and610 plus
  associated controls; **1001c434** writesf30=0,fa0=88,f70/f78=0,1c0=0.
  Those zero states must not be relabelled “write framebuffer at0”: this
  code does not write the actual render launch register.
* **1001bd70** resets PM-relatedcd8/ce0/cb8; **10019880** sets indexed
  resource state in12000/13800 windows. Some MINIM operands in indexed
  uploads remain UNKNOWN; they are retained as such. These gaps preclude
  an exhaustive proof that no transitive/indirect boot path can launch work.
* **100052d0 ->10019b84** installs the firmware-supplied physical catalogues
  in the256 context-base slots usinge140/e148. Those are **context slots**,
  not the1024 entries *inside* a catalogue. This path creates no VA0 PTE.

By comparison, real work paths contain **TA400=1 at8000912c** and **ISP f08
stores at800083ac/80008588/8000946c**, reached through state uploads and
start25/start27 or resume helpers. No corresponding queued render/dummy-job
publication was found in the audited boot path. A null background target,
power-on PBE self-initialization or a boot-time PBE write is **not verified**.
R18b now makes the early reset/initialization paths worth instrumenting;
it does not identify which one caused the transaction.

### ACK is now recovered; iOS uses recovery, not a transparent null-page fill

This **supersedes round11's “runtime138=200 writer not identified”**:

```
10020234..240: private[2b8] = 0x83ec;  // includes MMU bit0x200
10020290..2a0: R0[b78] = 0x200;       // DM4 interrupt enable
10022778:     source = (R0[b20] & 0x3c) >> 2; sample = R0[130];
80009fb0..fd8: private[2bc] = (sample & private[2b8]) | private[2bc];
80009ff0..a000: R0[138] = private[2bc]; // actual hardware event ACK
8000a018 ->8000a514 ->100037d4: dispatch(source, pending, ...);
source4 ->100039b8 ->10003a90: classify e150/e158, companion[0]=2, recover;
```

Private data base is8000a000; enabled/pending words are**8000a2b8/a2bc**.
The callback ACKs the latched event **before** the dispatcher removes200
from its software pending word. For this requester classification writes1
to companion+18 and state2 to companion+0, as round11 established.
An AIC/IRQ174 or Fender ACK still does not substitute for138.

There is also an explicit **boot-time dispatch gate**: callback80009efc..f20
tests private+0; when0,80009f28..f84 writesR0+b08=2 and returns **without
sampling130 or ACKing138**. Boot10000a78 sets this word0;10000b70 sets1
before entering the scheduler/ALIVE path. Thus an early pending event need
not have reached recovery at the first release sample. Capture private+0
with the mask/pending words at ALIVE to distinguish deliberate deferral from
a broken DM4 route; do not force the flag1 from the host.

iOS host6c99788 consumes recovery event4;6c81dac decodes BIF faults and
drives channel restart/recovery. The supplied captures do **not** establish
that iOS actually encounters this exact boot fault; its decoder/recovery
support is not an observation of one. There is no recovered host boot recipe
that silently maps VA0 after this error, or a complete hardware abort/retry
protocol permitting a host138 ACK to be called successful recovery.

R18b has the fault **before** the later host1720 attempt, and log2534 still
has `post688=0 post1728=1 result=-110`; publication remains suppressed.
An outstanding faulted write preventing a selected cache/drain participant
from retiring is plausible. A notification bit itself being an electrical
enable gate for1720/CDM is unproved. Firmware waits can escape on companion2;
ALIVE/return is therefore not proof of successful hardware maintenance.
For this run CDM has not been dispatched (`compute_submitted=0`); it does not
add a new independent post-kick CDM hang observation.

### Ranked kernel changes after r18b, with discriminating confirmation

Keep r18b's stage sampling in every comparison. **Do not ACK before the
`reset-released` sample**: that would conceal the result the next experiment
needs to measure. The strings below are proposed confirming lines, not results.

1. **Preinstall one dedicated writable VA0 sink in the boot/context0 VM.**
   This tests the exact missing translation before the release window, with
   the fewest extra hardware changes. In `boot.inc`, latch an opt-in
   `mmu_null_sink` at map and allocate three additional16KiB pages
   (`null_directory`, `null_leaf`, `null_sink`); retain all through reboot.
   After allocating the normal catalogue and before any arm/release MMIO:
   `null_leaf[0]=g5p_leaf_entry(sink_dma,0)`;
   `null_directory[0]=g5p_directory_entry(null_leaf_dma)`;
   `catalogue[0]=g5p_catalogue_entry(null_directory_dma)`; then`dma_wmb()`.
   Keep only these three entries valid; don't repurpose`b->zero` or catalogue387.
   Existing`g5p_boot_map_page()` rejects index0, so use an explicit owned low
   mapping path. Flags0 reproduces iOS's *conditional* writable sink PTE;
   cache-bypass0xc is a separate comparison, not silently part of this one.
   Use a known canary/snapshot in the dedicated sink to detect writes, with
   appropriate coherent-DMA ordering; an untouched zero-filled sink alone
   cannot distinguish “no write” from “wrote zeros”.
   Required lines: `MMU null-sink before-release ctx=0 pc_dma=... ci=0 di=0
   ti=0 cat=... pde=... pte=... flags=00`; **`MMU reset-released
   event130=00000000 ... pending=0`**, e150/e158 clean, no subsequent new fault;
   `MMU sink changed=... first_offset=...`; then`CDM inv ... post1728=00000000
   result=0`. Clean release plus a changed sink strongly supports a real null
   write; clean release alone supports containment but doesn't identify its
   producer. A level2→level1/0 change is table-walk progress, not success.
2. **One pre-release RAM-variable comparison: retain META+6c8=1.**
   In`boot-layout.h`, add an independent opt-in override after policy7's
   existing+6c8=0 store; leave the other policy7 inputs/mappings unchanged.
   This is the specific proved iOS boot flag difference. Stop after release/
   ALIVE/MMU capture if policy later stalls; no cmd8 or compute is needed to
   answer this question. Do not broadly re-run policy10 and call a later DC
   timeout the same experiment. Log`BOOT meta6c8=00000001` **before release**,
   then require`MMU reset-released event130=00000000 ... pending=0` and clean
   e150/e158, with PC[0] still0. If that alone prevents the fault, trace the
   newly enabled lifecycle path instead of making a permanent null mapping.
3. **Trace firmware-owned ACK/recovery; make host ACK an isolated secondary test.**
   Extend RAM observation to private+0,private2b8/2bc and all0x28 companion bytes at
   reset-released/alive. At an established post-release R0-safe boundary,
   captureb20 and the boot IRQ enablesb58/b68/b70/b78; expected boot literals
   arec0/8000/12c/200. Trace callback80009edc, ACK8000a000, dispatcher
   100039b8 and recovery10003a90 entry/return using a properly reserved trace
   buffer. Do not manufacture companion2 or force a guessed MTS kick.
   Firmware-owned success is`FW IRQ source=4 dispatch_enabled=1 enabled=000083ec
   sampled200=1 ack200=1 recovery_enter=1`, followed by a real reported
   failure/restart, not fake completion. If a host ACK comparison is selected,
   change the reserved`mmu_recovery_variant` gate to permit exactly one
   diagnostic mode, preserve the first record, do
   `writel(BIT(9),g->rgx+0x138)` **after the unmodified release sample**, and
   read130/status back once with ordering. Require`MMU ack before=00000200
   after=00000000 reassert=0` and separately test1720. This cannot make the
   earlier`MMU reset-released` line clean or prove prevention. A retained
   e150 alone may be historical; a reasserted event proves ACK was insufficient.
4. **Defer individual PBE/ISP/reset edits and whole-address dummy trees.**
   No same-image pre-release target/control delta identifies such an edit.
   If1/2 leave the fault, tighten the release sampler to immediately after
   the active poll, then add byte-validated firmware boot entry/return markers
   around1001a038,1001b638 and1001c434. The current2.5ms of intervening work
   matters. A single-stream comparison can omit Linux's extra arm execution
   while preserving RAM load validation and the complete final stream, but
   the clean arm interval makes it weaker than1/2. Logs must retain
   `MMU before-release ... pending=0`, the exact stream identity, and
   **`MMU reset-released ... pending=0`** with the changed variable named.
   Never describe merely ACKing the event later as fixing reset-release state.

For every candidate, restored1720 is only the next milestone. Actual compute
success still requires publication,`compute_submitted=1 compute_complete=1`,
stamp1 and shader output12345678. Clean release with a continuing1720 timeout
separates the early fault from the maintenance defect.

## Round 11 — pre-attach PBE0 write to VA zero, invalid page catalogue

2026-10-22. Research only: no device access, kernel edits or commits. Immutable
comparison: GPU `ba54ba7f89`, integration `cc1f23fbc6`, policy7, cache variant3.
Sources: r17c GPU log (`../experiments/logs/2026-10-22-exp17-r17c-c3-dmesg-gpu.txt`, not published),
runner (`../experiments/logs/2026-10-22-exp17-r17c-c3-runner.txt`, not published), same-image
iOS AGXG5P tables/disassembly and META firmware. Reproduce the numerical/table
checks with `python3 -B scripts/gpu/firmware-re/round11-evidence.py`;
bounded extracts and log line numbers (`round11-evidence.txt`, not published) are saved as
research evidence. This section does not duplicate or change the FWTRACE work.

### Decoded fault: G5P-specific iOS evidence, not a generic requester guess

**`e150=0x00000000000ff2a1` is a valid PBE0_TEXAS0 write fault at GPU VA
`0x0000000000`, context/MMU slot0, translation level2 (page catalogue),
type0 (“Page Invalid”), sideband `0xff`.** The hardware address field has
16-byte precision; the containing Linux GPU page is `[0,0x4000)`. There is no
evidence of an instruction-fetch fault. PBE0 is the pixel backend, not META,
CDM, PDS, USC or the maintenance engine. Sideband `ff` is retained numerically:
no same-image subdecode establishes a particular PBE pipe/render target.

The decisive iOS entry is **`fffffff006ca5c14`**, accelerator virtual+740
(G5P address point `6ff6820`, entry `6ff6f60`). It lacks a function definition
in the shared Ghidra project; MCP `read_memory` and the existing same-image
`build/gpu/agx.dis` establish its instructions. For bank0 it reads e150,
bank1 e158; bit0 gates the entire decode. Exact fields:

* bit0=1: fault valid; bits2:1=0: type0;
* bit3=0: write (1 means read); bits5:4=2: translation level2;
* bits11:6=`0x0a`: requester10; bits19:12=`0xff`: sideband;
* bits27:20=0: context; `(status >> 24) & 0xfffffffff0`=0: address.

At `6ca5c48..68`, NEON shifts come from **`5940c30=(-6,-12)`**, masks from
`5940c38=(63,255)`. This removes the risk of mistaking the vector operation
for a different scalar bit layout. The name table is **`6ff5ed8 + 16*10`**:
pointer **`5946167` → `PBE0_TEXAS0`**, classification4. Type table
**`6ff6258[0]` → `59460e2` → `Page Invalid`**; type2 is Read-Only and type3
PM/META Protected, so neither describes this fault. The public IMG register
definitions/debug decoder already downloaded under `build/gpu/refs/` agree
on the field positions, address alignment and level2=Page Catalog. Use iOS's
actual requester table: public revisions can assign requester10 to PDS/PBE
differently. **`e158=0` has valid bit0 clear: no valid fault in the META bank**;
its zero fields are not a second valid address-zero fault.

The iOS hang reporter **`6c81dac`** invokes virtual+740 for banks0/1, records
`requestor`, `sideband`, `level`, `is_read`, `pm_protect`, `address`, and
classifies the restart as a BIF page fault. The strings “Unexpected page fault
requestor for G5P” at `55edb08` exist elsewhere in the kernelcache, but have
no shared-project xrefs; they are not the proof of this decoder's table.

### Timing and missing mapping: stronger than “before compute submission”

Log107/110 already has event200/e150=ff2a1 at `compute-prepared`.
Log273 still says **`context=0`**, and the later stage only then changes to1
(log565). Immutable `compute.inc:g5p_compute_prepare()` rejects an already
submitted context and logs `compute-prepared` before its attach function.
Thus **this fault predates the tested context attach as well as CL
publication, the host11f00 preamble and the1720 command**. Context attach
and this compute shader cannot be its original trigger. DC cmd8 has already
completed (`DC_consumer=1`, log138); earlier hardware stages lack this sample.
There is no evidence yet choosing definitively between boot/release, cmd8,
or a stale latch predating our GPU reset sequence.

Read-only inspection of the **committed** boot code, not the concurrently
changing kernel worktree, gives:

* `boot.inc:g5p_boot_map_page()` accepts only catalogue index
  `(G5P_IMAGE_VA >> 30)&1023 = 0x387` (903). All image/root/shared/PIO mappings
  lie there. **This is not catalogue slot3.** Context0 and catalogue
  index0x387 are different dimensions.
* The zeroed catalogue has no index0 entry. A VA0 access in the current
  context0 table therefore fails at the catalogue, exactly matching level2.
  The code's separate `b->zero` allocation goes into **R0+e160 as a physical
  address**; it does not map GPU VA0. Do not infer a null mapping from it.
* Encoders are catalogue=`(pa>>8)|1`, directory=`pa|3`,
  leaf=`(pa&0xfffffff000)|(attrs&0xc)|((attrs&0x10)<<58)|1`.
  Changing a firmware leaf's RO/cache/META-protect bits cannot repair the
  absent VA0 catalogue entry. The reported type is not permission failure.

This explains **which translation is absent if the latch belongs to this
boot epoch**. It does not prove that PBE should legitimately access VA0, nor
that the installed context0 catalogue base was correct when the fault arose.
The catalogue-select/base pair e140/e148 should be captured as well; iOS's
decoder temporarily changes clock-control bits, selects the decoded context
in e140, reads e148, and restores control. That is a diagnostic bank selection,
not a fault acknowledgement; avoid silently copying its side effects into a
read-only logger.

Ranked root-cause hypotheses:

1. **Unexpected boot-era PBE0 write/null target or residual PBE transaction**,
   faulting against context0's missing low-VA catalogue. This fits the client,
   address and pre-attach timing. Distinguishing a newly issued request from
   a pre-existing latch requires the missing baseline; neither is proved.
2. **DC cmd8/lifecycle setup exposes the PBE transaction**, or installs/selects
   the wrong context0 base. Still possible because the first sample follows
   cmd8; no recovered cmd8 instruction is yet proved to launch a PBE write.
3. **Stale fault plus an independent maintenance/scheduler defect.** The same
   retained status across stages alone does not establish that it blocks1720.
   Current PMGR target/actual=f rules out only the outer GFX domain being off.

### Fault ownership, acknowledgement and the two apparent hangs

Firmware100039b8 tests200;10003a90 clears200 in its software pending mask
(`A1GbP+6c` object+2bc), calls1001a494(companion+18), then sets companion+0=2
and calls1000d180. **Clearing this RAM mask is not writing EVENT_CLEAR.**
The same-image firmware requester-class table at `A1GbP+154` points to
`100173d0`; entry10=4. Hence1001a55c tests bit2 and1001a568 stores1 at
companion+18 for this requester. Firmware then reads e158 as well. Expected
handler-side evidence for this specific fault is **companion state2 and
classification1**, subject to subsequent recovery clearing/changing it.
All-zero companion snapshots cannot establish that the path never ran; they
do establish that the expected recovery state is not retained in these CPU
snapshots. Entry/return tracing remains necessary.

1000d180 increments per-engine/recovery counters and its state2 path reaches
1001fda0 when no active engine qualifies. This is recovery/termination logic,
not a transparent page-fill-and-retry path. iOS host recovery consumes event4
(`6c99788`), schedules recovery, and `6c81dac` diagnoses/restarts the channel.
The normal host firmware-IRQ acknowledgement `6ca28d8` writes **R0+ac8** (or
its alternate IRQ resource), not R0+138. AIC ACK, Fender+14=7, firmware event
ring consumption, and the GPU MMU event clear are distinct operations.

Public IMG definitions identify **R0+138=EVENT_CLEAR, bit9=MMU_PAGE_FAULT**.
The firmware's800092d8 command-construction path also names138, but emits
masks10/4000, **not200**; it is not proof of the MMU fault's runtime ACK.
**This round did not identify a same-image runtime writer of138=200 or a
complete MMU transaction-abort/retry sequence.** There is therefore no basis
to declare a missing host138 write the established fix. In particular, clearing
the notification latch need not clear e150, retire an outstanding write, or
resume the MMU. Do not zero e150/e158 or force companion2 to fake recovery.

A stalled translation could keep a cache-drain/invalidate waiting for traffic
to retire, explaining1728.bit0=1 while688 completes. This is a plausible
mechanism, **not yet a demonstrated hardware dependency**. R17c does not
independently demonstrate a CDM execution hang: maintenance fails before
publication and `compute_submitted=0`. It demonstrates a maintenance hang
plus no compute dispatch; earlier no-maintenance runs are separate evidence.

### Concrete kernel changes and discriminating confirmation

The supported immediate patch is diagnostic/fail-fast; **a production mapping
or recovery fix is not established by the available samples**. For the
orchestrator, exact next changes are:

1. In `core.inc`/`lifecycle.inc`, extend the existing MMU observer to decode the
   fields above, preserve the first valid status and stage, and print the
   context0 catalogue DMA plus CPU catalogue[0] and catalogue[0x387]. Read each
   status high/low/high with a bounded retry and mark unstable samples. Add
   staged calls at the existing R0-safe, powered/reset-held snapshot boundary,
   after release, after alive, immediately before/after cmd8 and before attach.
   Do not add R0 reads while GPC is off or violate existing readback gates.
   Keep this independent of the in-progress FWTRACE patch.
2. In `compute.inc`, abort before cache maintenance/publication with `-EIO`
   when event200 and e150.valid/e158.valid are present; retain the decoded
   first fault for the DRM status path. Leave a separately selected diagnostic
   mode able to proceed for the following controlled comparisons. Ordinary
   submission must not relabel this as shader timeout or successful work.
3. If a diagnostic mapping comparison is needed, add **three dedicated16KiB
   pages** (low directory, low leaf, writable sink), publish
   `leaf[0]=g5p_leaf_entry(sink_dma,0xc)`,
   `directory[0]=g5p_directory_entry(leaf_dma)`,
   `catalogue[0]=g5p_catalogue_entry(directory_dma)` **before firmware release**,
   followed by `dma_wmb()`. Existing `g5p_boot_map_page()` rejects index0, so
   use an explicit opt-in low-page path; do not repurpose the firmware
   directory/leaf or e160 scratch page. Give the sink a marker and check
   changes after DMA synchronization. This is a null-write containment
   experiment, not a justified permanent mapping. A level2→level0 fault only
   proves partial table progress, not a fix.
4. Test ACK separately only after preserving the first-fault record:
   one bounded `writel(BIT(9), R0+0x138)` plus ordered event/status readback,
   under an explicit diagnostic selector. Do not spin on ACK or clear all
   events. If200 clears/reasserts or e150.valid persists and1728 remains1,
   ACK alone is not recovery. A real implementation must recover/reset the
   faulty engine and propagate failure, or repair the legitimate producer's
   target mapping before launch; the missing abort/retry protocol must first
   be recovered. Do not perform live page-table surgery behind a wedged
   maintenance operation.

Required new log lines (expected examples, **not observed results**):
`MMU first stage=after-release ... va=0000000000 access=write req=10(PBE0_TEXAS0) sb=ff ctx=0 level=2 type=0(Page Invalid)`;
`MMU PT ctx=0 pc_dma=... cat[0]=00000000 cat[387]=...`;
`MMU ack event_before=00000200 event_after=... status_valid=...`;
`MMU sink changed=...`;
`CDM inv ... post1728=00000000 result=0`;
then **actual publication and completion**, `compute_submitted=1
compute_complete=1`, stamp1 and changed shader output. A mapping comparison
that removes the *new-epoch* fault and restores1720 completion supports the
translation-blocks-maintenance hypothesis. ACK-only restoration with the
null page still absent points to stale notification/recovery instead.
Persistence of the hang after a clean fault baseline separates the defects.

## Round 10 reconciliation — normal companion0, policy10 gates pass, maintenance still pending

FWTRACE follow-up: [exact candidate sites, stubs and verifier](fwtrace.md).
Six sites pass LDR-byte, MINIM and GNU operand checks. Deployment remains
blocked: the proposed local executable extension has no proved exclusive
reservation; the separate new host trace page still requires implementation.

2026-10-22. This section supersedes the earlier recommendations to repeat
unchanged policy9/10, the inference that companion0 needs population, and the
use of a post-attach688/1720 subset as if it reproduced the entire firmware
maintenance preamble. Sources: r17 policy10 (`../experiments/logs/2026-10-22-exp16-r17-p10-dmesg-gpu.txt`, not published),
r16 cache experiment (`../experiments/logs/2026-10-22-exp15-r16-cache-dmesg-gpu.txt`, not published),
same-image firmware and host exports, and the SoC power findings (`../../power/README.md`, not published).
No device access or kernel changes. Raw extracts/checks are in
reconciled evidence (`round10-reconciled-evidence.txt`, not published).

### Companion: initialized zero; the recovered nonzero path is fault recovery

**iOS host6c96be8 explicitly zeroes five qwords of host slot4b,0x28 bytes.**
The root+74 pointer addresses that object. It does not initially contain a
power-ready flag, a copied perf-state table or an IOReport-owned object.
The r17 observer prints only+00..+18 (0x1c bytes), so it does not observe
all0x28 bytes, but zero in the observed prefix is expected normal init.
6c974f0's host+450→**policy**+3c copy is a separate object; no recovered
dependency requires copying that table into companion. No prior DC command
that must populate companion before cmd8 has been established.

**Concrete firmware nonzero writer:** interrupt dispatcher100037d4's
100039b8 path tests event mask**200**. If present,10003a90..3ac4 calls
**1001a494(companion+18)**, which samples **R0+e150/e158** (the public IMG
MMU_FAULT_STATUS pair; its page-fault decoding remains revision-dependent).
10003ad4..3ae4 then writes **companion+0=2**, and branches to
100039d8→**1000d180(companion)**. This directly ties a recovered state2
writer to MMU-fault/recovery handling, not GPU power-on acknowledgement.
1000d180 also changes state1/2, clears state and updates per-engine counters
and a selected-engine field as it processes request/recovery conditions.
Its state2 path can reach1001fda0, including a deliberate terminal path.

There is also a small generic setter1000d164 for companion[0]; no direct
caller or literal function pointer was found in the three decoded firmware
regions. This is **not an exhaustive indirect-call proof**. Accordingly the
precise claim is: *the identified active nonzero writer belongs to recovery;
there is no identified normal power-on producer to emulate*. Do not seed
companion1/2 or claim that every conceivable nonzero write was enumerated.
In particular1000d138(companion==2) provides a recovery escape from MMIO
polls; forcing2 can make waits return without hardware completion.

### R17 policy10 changes the readiness diagnosis

The complete r17 lines1190–1197 are **policy+8=0,+c=0,+28=2,+2c=2,+34=1,
+38=c8**. Values2/2 at28/2c are *not* the state fields8/c. This is true
before publication as well as at timeout. The coefficient subset changed
policy evolution from r16 policy9's state2/2 without unblocking DC.

The exact two-stage normal DC readiness evaluation now gives:

1. `1000d044(policy)` accepts **policy+8<=1** immediately, independent of
   companion, timer, filters and+34. R17's0 therefore passes this predicate.
2. `10003bb4` then calls`1000d504(companion)`. For companion0 it calls
   `10003770`, returning true when **root64+830,root68+77c,root6c+75c** are
   all zero. R17 lines1257/1259/1260 explicitly show all three zero.
   Thus the observed companion0 also passes. Root64+728/root68+734 belong
   to other engine-state tests; do not substitute them for these request words.

For historical **policy2/2**,1000d044 enters its deeper+34/recovery/policy
path and returns false for that invocation, as described below. **That is
not the observed r17 predicate anymore.** No additional guessed value is
needed to make the r17 *sampled readiness predicate* true.

This does not prove the live scheduler reached that call: it may still be
inside pre-MTS1000c0d8 or a transitive synchronous helper, or servicing another
lifecycle/notification path. Nor do non-atomic CPU snapshots certify live
META cache contents. The next localization must measure entry/return, not
add another readiness bypass or label configured T1_entry_pc a live PC.

R17 timeout also has **R1+1000=1,+0=40000000,+8=0,+1004=0**, satisfying the
sampled wrapper predicates previously identified. R0's high/low/high timer is
stable and advances **8ff550→8ff59d** between samples. A completely stopped
R0 timer is contradicted; the **tuple1/AIC timer and firmware's converted time
are not validated by that observation**. Policy timestamps use the latter,
so their numerical values cannot be directly compared with raw R0 ticks.

### What1720/1728 does establish, and the remaining cache-identity boundary

The r16 experiment reached attached-context1, completed688=7/poll7→0, then
issued1720=1f and observed **1728.bit0=1 for the100ms poll interval**.
Failure prevented CL publication: scan=endpoint=producer=0 and G+720..72c=0.
This is a **pre-dispatch maintenance timeout**, not a CDM start or shader
completion test. It establishes a transaction that remains pending after
the command; it does not establish why the transaction cannot retire.

Two firmware paths define the command protocol locally:

* **1001b638**, called by the unguarded boot helper1000515c and also by
  lifecycle/maintenance helpers, first writes **R0+11f00=1**, then writes
  **688=7** and polls`(688&7)==0`; writes
  **1720=(A1GbP+44)|1f**, polls`(1728&1)==0`; next calls1001dfec for
  the separatee230/e238 operation and later issues**e138=804**, polling
  that mask clear.1728 is polled by100018c8; it has no normal wall-clock
  deadline but can exit when1000d138 reports companion2. Neither a timeout
  escape nor ALIVE alone proves all maintenance completed.
* **10019610**, used by engine/context cleanup, writes
  **1720=(A1GbP+44)|3|selection**, where engine0 selects4, engine1 selects8,
  engine2 selects10, and a nonzero all-engines argument selects1c. It polls
  the same1728.bit0 clear. Thus nominal per-engine command literals are
  **7,b,13**; all-engine is**1f**, before any global44 bits. The compute
  finish path80008cdc→8000a684→10019610 passes engine2. Its preceding
  helper8000a694→10019700 also writes11f00=1. The local initialized image
  word atGbP+44 is0, but that is not a live RAM observation or proof no
  indirect writer changes it.

**Supported classification:**1720/1728 is a synchronous, workload-selected
maintenance request/status pair with common command bits0/1 and selection
bits2/3/4. The surrounding code makes *cache maintenance* a strong working
interpretation. **The exact cache level, names of bits0/1, and internal
completion participants remain unrecovered.** No matching1720/1728 definition
was found in the consulted public IMG headers. In particular, calling it
specifically “USC instruction-cache invalidate” was not established by the
old round7 inference. It cannot be promoted to a proven SLC or MMU operation
either: the firmware performs separatee230/e238 ande138 transactions. Shared
dependencies between those blocks are possible, so separate registers do
not prove they are physically independent.

**Which block needs clocks/power?** The request/status logic and every
selected completion participant need whatever reset/clock/power condition
the operation requires. The available trace does not identify those islands
or prove that a particular USC, PDS, TPU or SLC clock bit is their enable.
A stuck pending bit is compatible with gated/reset participants, an unsupported
selection, missing command setup, or a protocol/state-ordering problem.688
completing shows one operation responded; it does not certify every other
cache or GPU island. The all-engine1f also includes non-compute participants.

**Concrete discrepancy to test before a new power claim:** my earlier host
subset omitted the firmware's **11f00=1 preamble**, and substituted
`read1720|1f` for global44|1f. Both limitations were documented, but the
failed subset must not now be treated as an exact replay.11f00's semantic
name remains unknown;10019bc4 separately writes2 there and waits bit1 clear,
so it must not be renamed a simple “shader power enable.” Restore the
recovered sequencing as a single-variable experiment, then distinguish
compute-only13 from all-engine1f. These are unexecuted diagnostic proposals.

### Reconcile6c8 and SoC power: no new exact power-on recipe follows

Power research (`../../power/README.md`, not published) independently establishes the same
three layers: **PMGR GFX gate124**, **Fender core/GPC controller**, and
**META policy/timing/lifecycle**. Linux holds the GFX runtime-PM reference;
its genpd maps the correct physical gate20e080340. Outer ACTIVE does not
prove every internal participant is clocked, but a forgotten genpd get is
not the identified omission. PMGR's published perf-domains here are SOC/CPU;
CPU/SOC APSC request registers and voltage tables are not GPU power commands.

The verified host-side order remains provider clock+568 then power+570;
R1+10000=ffff0000,+0=40000000,+1000=11 with busy-bit wait; initialize shared
roots/PIO/policy; core holdR1+20000=20 while preparing reset/FIFO/MMU/SLC;
release hold with20000=0 and requestGPC1 with1000=11; wait for boot handoff;
publish DC then kickR0+b00=10. Preserve the conditionals and intervening
setup in the power report; this is not a new list of missing writes.
Firmware1001c88c handles later performance requests through**R1+1000**.
meta6c8=0 skips that policy plus timing/accounting and wrapper-handshake work;
it does **not** gate the boot1001b638 maintenance call or all GPU initialization.

No recovered6c8-gated instruction specifically powers the1720 participant,
enables an identified XTPLUS island, or issues an additional GFX PMGR/PLL/
regulator command. GPU state1 is already acknowledged in r17. The live ADT's
five frequency/raw-second-column pairs describe inputs, not the missing
MMIO protocol. **There is no justified additional host power-on write yet.**
The two failures may share a deeper lifecycle cause, but companion0 and the
pending bit do not establish that common cause. Do not seed companion2,
force a higher GPC index or write CPU DVFS/voltage registers to test it.

### Ranked kernel experiments and exact confirming lines

Proposed lines below are log contracts, **not claims these lines already
exist**. Use separate fresh boots and retain the original timeout/quarantine
rules. Do not repeat unchanged policy9/10 or unchanged cache1 expecting a fix.

1. **Finish the round17b power/cache observation comparison.** Preserve the
   held runtime-PM reference. At the failing operation sample the existing
   PMGR observer, R1+0/8/1000/1004/20000 and R0+0/4/8/c/80/84/88/8c;
   extend the cache-timeout observer to include1720/1728 and a breadcrumbed
   gated11f00 read if needed. Confirming discrimination:
   `POWER cache-timeout PMGR gfx20e080340=<raw> target=f actual=f ...`
   alongside`CDM inv post1728=00000001 bit0_clear=0 result=-110` rejects
   simple outer-domain-off **at that sample**. AUTO can gate transiently;
   record its state, reset/disabled/parent fields and before/after samples.
   It still does not prove internal power. No new register write in this case.
2. **Policy7, restore only the omitted maintenance preamble.** Add an isolated
   cache variant: after attach, before the existing688/1720 sequence, perform
   **writel(1,R0+11f00)** with the existing ordered-MMIO/readback discipline.
   Keep1720 request1f, mappings, payload, GPC and policy unchanged. Log:
   `CDM inv variant=preamble11f00 request1720=0000001f post688=00000000 post1728=00000000 result=0`.
   That change supports a missing setup/order explanation. Then require
   actual compute`stamp=1 output=12345678`; cache completion alone is not
   shader execution. If still pending, do not replay indefinitely.
3. **Isolate the selected maintenance participants.** In a separate fresh boot
   with the same chosen preamble, replace only1f by **13** (firmware's engine2
   selection). This reproduces a command value from compute cleanup, not a
   proven normal pre-submit cache contract. Confirm:
   `CDM inv selection=compute request1720=00000013 post1728=00000000 result=0`.
   13 completing while otherwise identical1f times out localizes an extra
   selected participant; it does not identify a named cache/power island.
   Still require numerical compute completion. Preserve the global44/source
   caveat rather than invent a live private-RAM read.
4. **Localize policy10's pre-MTS versus service path with firmware breadcrumbs.**
   Add separately hashed offline-image instrumentation at pre-policy
   entry/return8000a120, policy1000c0d8 entry/exit, post-MTS readiness
   entry/result8000a240 and service dispatch8000a36c. Use explicitly reserved
   shared diagnostic RAM with verified visibility, preserve conditions and
   avoid per-iteration flooding. Exact binary patch/debug allocation still
   requires implementation review. Required distinguishing lines:
   `FWTRACE policy_enter=1 policy_return=0` localizes a transitive synchronous
   block;`FWTRACE policy_return=1 ready_result=1 service_enter=0` moves the
   boundary to scheduler/MTS continuation;`FWTRACE service_enter=1 cmd8_enter=0`
   moves it to dispatch. Repeatedready0 despite verified live0/0 and zero
   request words would require checking argument bases/cache visibility,
   not setting companion. Record counters as well as the last marker.
5. **Only after a policy wait is localized**, complete its missing input or
   lifecycle transition. If the trace points into timer/performance waits,
   compare tuple1/AIC time and private2c4 selection with the already advancing
   R0 timer; if it points into a specific table consumer, recover the host+450
   producer before changing that table. Confirm first:
   `DC ... consumer=1 producer=1` and`cache_return_epoch>0`, then real context
   and compute completion. There is presently no concrete additional
   PMGR/APSC/XTPLUS write to propose for this step.

Offline reproduction:`python3 scripts/gpu/firmware-re/round10-reconcile.py`
checks both r17 policy states0/0, zero observed companion, advancing raw timer,
r16 cache failure, the five iOS zero stores and five raw firmware anchors.
It emits the selected source/PC extracts. The incomplete MINIM decode and
unrecovered physical cache identity remain explicit limits.

## Round 10 follow-up — policy9 still starves DC; recover policy before another power claim

2026-10-22. Supplied policy9 dmesg (`../experiments/logs/2026-10-22-exp15-r16-p9-dmesg-gpu.txt`, not published)
and runner (`../experiments/logs/2026-10-22-exp15-r16-p9-runner.txt`, not published) establish
an **earlier failure**, not a CDM result: DC consumer0/producer1, no cmd8
payload/epoch/busy/request progress, no context or compute submission, park
succeeds. Policy9 reproduces the timing/table/limit subset but **does not
reproduce Apple's policy**. Do not repeat unchanged policy9 or treat this as
evidence against the code-PTE experiment currently being run under policy7.
This update supersedes the recommendation below to run unchanged policy9 first.

### The new log actually contains policy2/2 and nontrivial boot policy work

Root+70=43203040 and root+74=43203980 locate policy and companion. The
before-publish delta dump (from an initially zero snapshot) reconstructs:

```
policy+000=1  +004=16  +008=2  +00c=2  +014=384000
       +028=0 +02c=1  +030=0  +034=1  +038=0
       +6fc=400 +7cc=400 +934=1
companion+000=0
```

No later root deltas are logged through timeout. These are CPU-visible,
non-atomic observations; zero omitted words are zero in that snapshot, not
a proof firmware never touched them. policy+934 is a transition counter.
Several timestamps and policy-derived words are already nonzero. The
frequency fix therefore did not prevent all platform initialization. The
static timing-table omission was real, but **it was not the complete blocker**.

### Two gates before cmd8, with exact predicates

T0's scheduler calls10001ac8→1000c0d8 before fetching MTS, then fetches
MTS at8000a1e8 and calls10003bb4 at8000a240 before DC/service. A failed
post-MTS test cycles through8000a254..a298, including another policy call.
Thus DC consumer0 cannot distinguish a blocked pre-MTS call from repeated
post-MTS rejection. T1_entry_pc20000000 is the configured entry, not a live PC.

`10003bb4` first calls **1000d044(policy)**:

* +8<=1 accepts immediately.
* +8=2 accepts if +c=0. With private244=meta6e0=0 it also accepts+c=4;
  Linux has meta6e0=1, so that exception does not apply.
* Otherwise it requires root.policy+34 nonzero to call the next helper;
  +34=0 returns false. **+34=1 is already present and is not sufficient.**
* 1000d138 tests companion+0==2. If true,1000d044 returns false. If false,
  it calls1000c0d8 and still returns false for this invocation; side effects
  must make a subsequent invocation acceptable.

The observed **2/2,+34=1,companion0** therefore takes policy again and rejects
that invocation. Do not patch+8/+c to a passing state: they are firmware-owned
power-lifecycle states. On success10003bb4 still calls **1000d504(companion)**.
For companion0,1000d504 calls10003770 and accepts only if that helper returns0;
the helper checks request words root64+830,root68+77c,root6c+75c. Its companion1
path has additional TA/3D/global work and request handling. An UNKNOWN
instruction remains in the other-state path, so no exhaustive truth table
for every companion value is claimed. The DC source nibble0 has no TA-state
fallback; CL sources1/2 can additionally accept root64+72c=1/2/3.

Within1000c0d8, **state+8=2,+c=2 does not take the special+c=1 transition
path at1000c880**:1000c86c..c878 branches back to
the generic path1000c16c. That path checks policy+34, compares time with the
deadline at+900/+904, performs telemetry/filter arithmetic, chooses a target,
and calls transition/accounting helpers. Firmware has real scalar-float
arithmetic in this path; a frequency table cannot replace its coefficients.
The complete target/state evolution retains UNKNOWN decode boundaries; the
log does not identify the exact stalled instruction or prove an infinite loop.

### Other concrete waits skipped by6c8=0

**Performance transition1001c9b8:**1001c88c operates through **PIO tuple0,
the Fender wrapper R1**, not the RGX R0 clock register bank. It can first
wait`(R1+1000&10)==0`, stores requested state in policy+34, selects
private2c4=frequency[state], calls1001f130, then writes **R1+1000=state|10**.
Its synchronous modes1/2 wait`(R1+1000&1f)==state`.1001c9b8 also samples
stable high/low/high **R0+164/160/164** and loops at1001cabc..ccac until its
delay criterion passes. With both platform flags enabled, elapsed ticks
shifted8 are divided by private2c4; with the bypass, a different timing
factor is used. Poll-engine10007a94's timeout itself uses100223f0, so a
stopped/wrong timer can make a bounded-looking firmware wait non-progressing.
Index0 still selects frequency0; r16's observed policy+34=1 and wrapper index1
mean the old *uniformly zero frequency table* explanation is insufficient.

**Platform handshake1001cd8c**, gated by both cached6c0/6c8 flags, calls
1001c838, timestamps/accounting1000ec00, then **writes tuple0+0=40000000
and polls the whole word equal40000000**; afterward1001cce0(0) writes and
reads **R1+1004=0**, and1001cd1c reads **R1+8**, writes a nonzero sampled
word back and polls it to0. These are exact wrapper operations, **not an
identified USC-cluster power-enable protocol**.10000b0c branches to this
helper before the common boot sequence when6c0 is set; it is also called
after the ALIVE emission path at10000b8c. Therefore ALIVE is not a general
guarantee of completion of every later re-entry. However r16's after-release
snapshot already has **R1+0=40000000,+1004=0,+8=0**—and host initialization
also writes40000000. These values cannot prove firmware executed the helper,
but do not expose an unmet predicate that warrants replaying it from Linux.

**Idle/shutdown lifecycle1001cec8:**1000968c may enter it; it performs cache
maintenance, wrapper operations, accounting and can deliberately request
state0 then self-branch at **1001d074**. That is a sleep/restart lifecycle,
not a normal register wait that another kick necessarily fixes. This is
another candidate, not a measured live PC. Boot1001fef0's enabled branch
also builds per-frequency timing data and samples tuple1/time;6c8=0 skips
that branch. Do not equate any one skipped function with all shader bring-up.

### Recovered missing filters: concrete values, incomplete policy table

Same-image host initializer **6c95770→6c974f0** imports defaults/ADT and
converts them into policy words. In particular the ADT supplies power
filter313, perf filters5/50, limiter filter default5, target utilization95,
minimum duty40, base pstate default1, max pstate4 and max power3348.
Ghidra raw constant reads at5940b78/5940f80 and the tiny virtual getter
instructions independently resolve the gain/filter defaults. The three
host vtable variants'610/618/620/628 getters have identical bodies:
gain[index] at host68c, proportional[index] at69c, filter[index] at67c,
and enable byte688. Full source and arithmetic are in
policy evidence (`round10-policy-evidence.txt`, not published).

Examples of **policy offsets, not meta offsets**; float words are IEEE754
binary32 bit patterns, not integer divisions to be implemented in kernel C:

| Policy input | Recovered initialization |
|---|---|
|638,640|float312/313 and1/313: power filter pair|
|648,658|`3ca59586`,`40a90fdb`: power integral/proportional gains|
|650/654|float0/3348: power clamp pair|
|660|float(-400/3348): power-to-pstate scale|
|664/668|signed-360/0: minimum-duty offset pair|
|66c,688|400,400: pstate-scaled maxima|
|674|3348: max power|
|694,698,69c|95,1,0: utilization, enable and drop-threshold inputs|
|6a0/6a8|float4/5 and1/5: performance filter0|
|6a4/6ac|float49/50 and1/50: performance filter1|
|6b0/6b4|`40fca970`,`3e4a2121`: integral gains0/1|
|6b8/6bc|float0/95: integral clamp pair|
|6c0/6c4|`416b53d1`,`40db53d0`: proportional gains0/1|
|6c8|float300/95: base-to-max pstate scale; unrelated to meta6c8|
|6cc/6d0/6d4/6dc|100/400/100/95|
|704/708|float4/5 and1/5: limiter filter pair|

The supplied p9 root snapshot has **638=640=648=660=698=6a0=6a4=6a8=6ac=0**.
These are concrete missing iOS inputs. Some output/state words are already
nonzero because firmware ran; do not copy timeout-derived outputs into init.
The evidence script prints the complete *verified subset* as exact hex words.

**Unresolved table:**6c974f0 copies0x160 bytes from host+450 to policy+3c
through+19b, and further getter-derived fields remain outside the verified
subset (e.g.684/68c/6ec/6f8). The source table is host RAM populated before
that copy, not a constant address to copy from the firmware image. Its full
producer/contents are not recovered by the available exports. **Do not call
the coefficient subset a full policy reconstruction, invent its table, or
fill it with frequencies/pointers.** This is an explicit remaining research
dependency; no new live kernel-heap capture is requested by this analysis.

### Does skipped platform work explain policy7's idle CDM?

**Plausible common cause, not established equivalence.**6c8=0 disables boot
timing/accounting, platform wrapper handshake, performance/power transitions,
and platform event notification as well as the scheduler policy call. Any
required hardware-side effect of those omitted branches could matter after
CDM start. But shader execution bases and initial hardware invalidation are
unconditional and are already corroborated. The recovered skipped wrapper
predicates already match p9's release snapshot; **no exact USC-cluster power
switch or ready bit has been identified**. policy9 never dispatches compute,
so it cannot confirm or refute that common-cause theory. A policy7 PTE-only
success would instead show this fixed job can execute with the broad bypass;
a PTE failure leaves both fetch and platform-lifecycle hypotheses open.

### Revised ranked kernel changes

1. **Preserve the in-flight PTE-only experiment on policy7.** Do not mix
   policy/filter work into it. Confirm leaf lows1/d, bases300000000,
   4a0.COMPLETE plus actual output12345678/stamp1. This remains the clean
   next hardware comparison; unchanged policy9 is now a known earlier fail.
2. **Add a policy diagnostic snapshot before another enabled-policy run.**
   Extend the existing DC before/after/100ms/timeout observer to label
   policy8/c/14/28/2c/30/34/38,900..934; companion0..18; root64+728/+72c/+830,
   root68+734/+77c,root6c+75c, and every supplied coefficient. Under the existing
   wrapper read gate also sample **R1+0,+8,+1000,+1004** at timeout, not just
   reuse the release snapshot; include two bounded time samples of already
   validated timer registers/tuple1 to distinguish a stationary timer from
   state rejection. Do not add private META slave reads. Confirmation of
   the gate hypothesis is continued2/2/companion0 with changing time; a
   busy1000.bit4 or state0 with stationary timestamps points elsewhere.
3. **New fresh-boot policy10 based on9, adding only the verified filter
   subset above** in`g5p_dc_init_variant`, encoded with raw u32 words from
   the evidence file; retain sample16, frequencies and limits. Log the
   initialized words before release and the gate snapshot afterward. This
   is a partial-input experiment, not a ready production DVFS policy. Its
   first success criterion is **DC consumer=producer=1/cache epoch progress**;
   only then attach and test actual CDM completion. If still2/2, do not
   keep varying sample periods or forcing a higher frequency. Recover the
   host+450→policy+3c table and remaining fields before claiming equivalence.
4. **If localization is still ambiguous, instrument firmware paths in an
   offline image variant, rather than force a passing readiness state.**
   Kernel-side firmware-loader support should use a separately hashed image
   with breadcrumbs in explicitly allocated shared debug RAM at pre-policy
   entry/return, post-MTS ready-false/true and transition entry/return. Verify
   original raw byte anchors and regenerate packing/hashes; preserve register
   effects and branch conditions. This identifies pre-MTS blocking versus
   post-MTS starvation without pretending a bypass reproduced platform power.
   Exact insertion bytes/debug-RAM allocation need a separate reviewed patch;
   they are not supplied here and no firmware has been modified.

No ranking here authorizes synthetic completion, live writes to policy states,
blind R1+0/1000 replay or blanket clock forcing. Research verification:
`python3 scripts/gpu/firmware-re/round10-policy-evidence.py` reconstructs the
new policy snapshot, checks pinned constant exports and emits the field values
and source excerpts;`check-decoder.py` retains its explicit UNKNOWN limits.

## Round 10 — r16 CDM register decode and start/finish verdict

2026-10-22. Sources: supplied r16 baseline dmesg (`../experiments/logs/2026-10-22-exp15-r16-base-dmesg-gpu.txt`, not published)
and runner (`../experiments/logs/2026-10-22-exp15-r16-base-runner.txt`, not published), existing
same-image MINIM listings and round-7 byte anchors, captured iOS command words,
and the public IMG register definitions cited below. No device access, kernel
edits, commits, firmware patching or new live iOS capture. This section supersedes
round 7's *missing live base readback* qualification and its abbreviated ordering
of the fresh-start writes. It does not establish a working hardware dispatch.

### Verdict and exact uploaded state

**The job reached the recovered fresh-start register path, including the code
path that writes the start command. Hardware acceptance/fetch is unproved;
hardware context-store completion is absent. There is no established missing
second GO write, and no sampled register uniquely distinguishes never-started
from stalled.** Four seconds later, `4a0=0`, scratch tail=0/0, output=deadbeef,
stamp=0. All scheduler records pending=0/current=16/active=0 are observations
of firmware RAM, not hardware engine-idle status. They do not independently
establish an opcode101 wait or that CDM never ran.

The following state was zero before publication and matches W at timeout
(dmesg lines 711–739 versus 1073–1101):

| R0 register(s) | Timeout value | Recovered writer / meaning |
|---|---|---|
|480/484|`000000010005c000`|80008d24 upload, W70; absolute CDM control-stream VA|
|498/49c|`000000010004a020`|same, W60; absolute context-state/scratch-tail VA|
|4b8/4bc|`0000043000000420`|same, W68; terminate PDS code offset420, data offset430|
|4c0|`40200003`|same, W10; terminate PDS task control, **not GO/status**|
|4c8|`00000000`|same, W14; additional Apple state, exact semantic unresolved|
|4a8/4ac|`0003000000000400`|10018358 from W21c/220; context-store PDS code offset400, data offset30000|
|4b0|`50800005`|10018358 from W224; context-store PDS task control, **not GO/status**|

At 80008d40/54 the helper writes 4c0/4c8, then at 80008d7c the qword498;
it also uploads4b8,480 and all six indexed resource entries11040 through selector
11ff8. Those resource slots, selector and4d8 are not all in this snapshot;
do not claim their readback was verified. The observed subset exactly matches
the fresh-start recipe, including the iOS-derived420/430/40200003 pair.

**Important ordering correction:** 800073c8 calls80008d24; H.first=0 branches
through800073f4→80008f30, whose80008f3c store is **`R0+478=1`**. Then
80007400 calls trampoline8000a6f4→**10018358**, which uploads4a8/4b0.
Only afterward80007424/2c/3c records G+728=1/G+720=0/G+72c=CH. Thus matching
4a8/4b0 and G+728/+72c support execution *past* the start store. This is a
static-path inference, not a captured MMIO transaction or hardware ACK.
The firmware does not poll478 or require it to remain1. **Timeout478=0 is
compatible with a consumed/pulse-style command; its exact readback semantics
are not recovered here. Do not demand a latched1 or add a host re-kick.**
R0+490=0 is also expected on this fresh path:490=1 belongs to the nonzero-H
context-restore helper10018698, not an omitted fresh-job GO. R0+108's2→0 pulse
and context-selector byte writes precede upload as described in round 7;
their transient values were not sampled here.

### Execution bases and PDS fields: these are addresses, not power registers

**610/614,4008/400c,4010/4014,4028/402c are four little-endian qword pairs,
each exactly `0000000300000000`, before publication and at timeout.** In
particular400c=4014=402c=614=3 are their high address words. The values match
the recovered pre-ALIVE writers1001d1fc and1001d388 from meta18/meta10.
They give direct live corroboration for B=300000000 and remove any observed
base mismatch as a reason to try B=0/B=200000000. They say nothing about
USC-cluster count, cluster enablement, power acknowledgement or idle state.

The [IMG DDK header, pinned MediaTek source](https://android.googlesource.com/kernel/mediatek/+/4f43e6b499c6d194030df8d2506485db9d5165bd/drivers/staging/imgtec/rogue/hwdefs/km/rgx_cr_defs_km.h)
has two PDS task layouts, selected by **RGX_FEATURE_PDS_TEMPSIZE8**. The
[upstream IMG header](https://raw.githubusercontent.com/torvalds/linux/master/drivers/gpu/drm/imagination/pvr_rogue_cr_defs.h)
retains both layouts. The captured Apple words use bit30, outside the ordinary
layout's3fffffff mask, and fit the TEMPSIZE8 layout's7fffffff mask. Therefore
**TEMPSIZE8 is the supported family interpretation**, not an independently
recovered Apple feature declaration. Applying the ordinary Mesa layout would
mislabel bit28 and the size fields, and leave bit30 unexplained.

| TEMPSIZE8 field |50800005 at4b0|40200003 at4c0|
|---|---|---|
|PDS sequential dependency, bit30|1|1|
|USC sequential dependency, bit29|0|0|
|Target, bit28 (family 0=all,1=any)|1|0|
|Unified size, bits27:22 (encoded units)|2|0|
|Common/shared allocation selector, bit21|0|1|
|Common size, bits20:12|0|0|
|Temporary size, bits11:7|0|0|
|Data size, bits6:1 (encoded units)|2|1|
|Fence task, bit0|1|1|

These fields describe dependency/allocation/fence properties of a PDS task;
none is a hardware BUSY or GO flag. Do not clear their dependency/fence bits
to manufacture progress.50800005 matches both existing resident iOS captures.
The qword4a8 packs **code=B+400=300000400, data=B+30000=300030000**;
qword4b8 packs **code=B+420=300000420, data=B+430=300000430**. Both address
halves encode aligned offsets, rather than one64-bit absolute pointer.
These offsets land in the existing code/helper fixture pages. Correct register
addresses still do not certify page-table translation, cache attributes,
instruction fetch or actual helper execution.

### What the remaining snapshot says, and what firmware needs next

* Clocks are unchanged across publication:0/4 form`a02a82000a2a2a0a`,
  80/84 form`00000002a86aaaaa`;8/c form200 and88/8c form0. At **public
  family** shifts CDM(46),USC(20),PDS(12) controls areAUTO=2; XTPLUS
  COMPUTE(30)=AUTO=2 and PDS_SHARED(22)=ON=1. The reference CDM/USC/PDS
  status bits23/10/6 are clear. This is compatible with gated clocks at
  sampling, including an auto-gated idle or blocked pipeline; it is not a
  historical never-ran indication or proof those Apple islands are powered
  off. Reference200's bit9 must not be promoted to a new Apple readiness ACK.
* R1+1000=1 has the recovered busy10 clear and low4 index1;20000=0.
  Policy7 still bypasses the recovered performance/power state machine.
  No exact missing shader-island enable register/value follows from this
  snapshot.3c8=0 remains raw: no revision-correct idle decode was established.
* 688=0,1728=0,e238=0,e138=0 satisfy the previously recovered maintenance
  completion masks (7,1,1,804).1720/e230=0 are not evidence that their
  earlier command writes were omitted. No maintenance is visibly pending;
  stale post-attach translations/instructions remain a separate hypothesis.
* 4a0=0 means neither context-store COMPLETE(bit0) nor NEED_RESUME(bit1).
  4a4=0 is the adjacent high word, not a newly decoded status. This status
  cannot distinguish no start, blocked stream fetch, blocked shader/PDS
  execution or a missing terminate/context-store completion.
* 130 changed200→0, but has no surviving COMPUTE_FINISHED mask4 (bit2) at timeout.
  The firmware callback reads130 and ACKs138, so absence at timeout does
  not prove event4 never occurred. **GbP+70=8000a2bc is the private event
  accumulator pointer, not its contents.** The logged out-of-owned-RAM
  result correctly leaves those contents unknown; do not dereference through
  a guessed shared alias or inspect packed firmware bytes as live RAM.

Expected continuation after start32: timestamp opcode18, **opcode101 waits
for event mask4**, then004a000a,1,1 synchronously polls`(4a0&1)==1`, then
another timestamp and finish33. Finish33 clears the outstanding G fields and
stores1 through W25c; its NEED_RESUME branch validates H/scratch and may retry.
An actual event-yield records mask4 and a saved cursor; **r16 does not show
that record**. It is incorrect to state that the live firmware PC is definitely
at101. Its next *required hardware milestones* are nevertheless event4 and
4a0.COMPLETE. Timestamp helper blocking, RAM cache visibility and lifecycle
remain possible explanations for the empty sampled records. Host IRQ174 ACKs
cannot supply those milestones. Output12345678 remains a separate USC result.

### Ranked missing-step hypotheses and exact isolated kernel experiments

**No proved corrective GO/power write is available.** Rank the following as
experiments, not diagnoses. Round16 already implements these switches, so the
exact kernel deltas below require no new speculative register patch. Keep
fixture1,dependency1,profile0 and fresh boot per case. A successful case needs
**stamp=1, output=12345678 and all63 other sentinels unchanged**. No single
power/status sample is an adequate substitute.

1. **Skipped power/performance lifecycle (policy7→9), first.** Select
   `GPU_DIAG_DC_VARIANT=9 scripts/gpu/run-compute-host-r16.sh`; change only
   the existing policy initialization, preserving payload/PTE/cache variants.
   Confirm attach and the same CDM uploads first. Confirm wrapper1000.bit4=0
   after any transition, and sample clocks8/88 around publication. Under the
   family decode, an observed8.bit23=1 (`00800000` mask) would show CDM clock
   activity at that instant; it is neither required to stay set nor an Apple
   power ACK. Required completion confirmation is **4a0.bit0=1** (often raw1
   on a non-resume finish), plus the numerical result/stamp. No exact new
   PMGR register or mandatory changed frequency index can be responsibly
   prescribed. Policy9 failing before attach is not a completed comparison.
2. **Code fetch/memory attributes.** Select
   `GPU_COMPUTE_CODE_PTE=1 scripts/gpu/run-compute-host-r16.sh` under the
   chosen working policy. Exact change in`g5p_compute_prepare`: for fixture
   pages0..3 use`g5p_leaf_entry(dma,0)` instead of`g5p_leaf_entry(dma,0xc)`;
   retain data pages4..7 flags0c. Confirm leaf low bits **1 versus baseline d**,
   all four execution bases still300000000 and unchanged PDS/CDM upload.
   Confirm4a0.bit0=1 plus output/stamp. These are memory/cache attributes,
   not a proved execute-permission fix. Do not combine with base aliases.
3. **Post-attach PDS/USC maintenance.** Select
   `GPU_COMPUTE_CACHE=1 scripts/gpu/run-compute-host-r16.sh`. Exact existing
   kernel insertion after successful attach and before CL publication:
   **write688=7; bounded poll`(688&7)==0`; write1720=`pre1720|1f`;
   bounded poll`(1728&1)==0`**. For this baseline's pre1720=0 the requested
   value is1f. Confirm the two final predicates0, then4a0.bit0=1 and the
   numerical result/stamp. Baseline already reads these predicates0, so the
   *logged maintenance transaction* and subsequent progress are essential;
   zero alone cannot confirm that this missing step was the cause. Leave
   e230/e138 untouched in this isolated experiment.

**Deprioritized:** changing B (readbacks now match), adding another478/490
kick, setting/clearing4b0/4c0 bits, altering generation/stamp identity, or
forcing all clocks ON. None has a distinguishing supporting observation.
If the three isolated tests fail, the next useful kernel delta is observation:
bounded early samples of130,4a0,8/88 after publication, before the existing
four-second timeout, with no host ACK138 and no synthetic stamp.4a0.COMPLETE=1
while stamp remains0 would move the boundary toward firmware event/interpreter
continuation;4a0=0 throughout still would not distinguish no fetch from stall.
No new selector/debug-register reads are proposed without Apple evidence.

### Reproduction

`python3 scripts/gpu/firmware-re/round10-evidence.py` writes the compact
round-10 evidence (`round10-evidence.txt`, not published): log SHA256, both-stage paired-base
checks, ten expected CDM words, field arithmetic, and exact firmware
execution-PC/storage/raw-byte excerpts including the kick-before-PDS order.
It is offline and does not certify the partial MINIM decoder's UNKNOWN paths.
The public TEMPSIZE8 interpretation and clock bit names remain explicitly
separate from locally verified Apple writer addresses and values.

## Round 7 — hardware completion, execution bases, cache sequence, exp12-r14

2026-10-22. This section supersedes older statements that the absolute code
base has no recovered firmware register writer, and resolves opcode101's
previously UNKNOWN mask extraction. Kernel comparison is a **read-only snapshot
of a9/gpu at 5cfc2a756c**, mounted from `linux-a9-src:/src:ro`; round15 is
changing concurrently. Research only: no kernel/workspace commits, device
commands, PMGR changes, or firmware patching. No additional agents used.

### Immediate handoff

**Device evidence supplied by orchestrator, logs inspected:** dependency1
(`0xf` sentinel pattern in the0x280-byte object) has the same post-kick
timeout as exp12-v1: scan=endpoint=producer=1, G+728=1/G+72c=CH,
stamp=0, output=deadbeef, fault=0. See
r14 log (`../experiments/logs/2026-10-22-exp12-r14-d1-dmesg-gpu.txt`, not published).
The tested dependency initialization is not the first observed blocker;
this does not certify the whole dependency object's lifecycle.

**New offline verification:** every byte of **all eight** CPU-visible
payload pages at timeout equals `g5p-compute13-full.bin`. Page7's firstc8
bytes also equal the strict resident `one-post` iOS capture: seven28-byte
packets at0/1c/38/54/70/8c/a8, followed by80000000 at+c4. The full helper
tail was present; do not rank “append the missing helpers” for this run.
Equality does not prove hardware fetch, physical translation or coherency.
See payload checks (`round7-payload-check.txt`, not published).

**New static findings:** hardware completion is an event4 wait followed by
4a0.COMPLETE; firmware programs both code bases and performs a hardware
cache-maintenance sequence **before ALIVE**, even with meta6c8=0. Thus the
bypass skips policy transitions, but does **not** skip all GPU initialization.
Rank actual register observations ahead of code-base aliases or blanket
clock-forcing writes. Exact Apple shader-island power controls remain open.

### Exact CDM completion and event path

1. `start32 ->80007390`: call1001e7b8 to pulse **R0+108=2 then0** with
   bit1 readback waits; select context through1001838c (byte writes to
   **R0+f07a,136e,136f**);80008d24 uploads state. It writes the64-bit CDM
   stream pointer **R0+480=W70** and context-state base **R0+498=W60**,
   the context/terminate PDS state at4a8/4b0/4b8/4c0/4d8, and selected
   resources via indexed11040/11ff8.80008f30 writes **R0+478=1**.
   G+728/G+72c stores follow that kick; they do not prove hardware accepted it.
2. **Opcode101 waits for hardware event mask4.** At80005d74, raw bytes
   `24d31959` are the TRM3.5.2 extended **SHIFT to another register unit**,
   not a conditional SHIFT. Its standard-META expansion is56118c64,
   independently decoded by GNU as **`LSR D1Ar3,D0.6,#6`**. Hence
   `101>>6=4`.80005d7c..5d90 tests `(pending&4)==4`. If absent,
   800066c0 stores waiting mask4 at execution-record+c and the current SKU
   cursor at its slot entry, then yields. Once present it clears the
   satisfied bits and advances four bytes. This narrow instruction is now
   verified; other UNKNOWN instructions remain unresolved.
3. Interrupt callback **80009edc**, installed by10020604..2061c through
   100226a8, calls **10022778**. That helper samples **R0+b20**, extracts
   source kind `(value&3c)>>2`, stores low bit separately, and reads
   **R0+130**. Callback merges `(sample & private+2b8) | private+2bc` into
   **private+2bc** (80009fb0..9fd8). Boot10020224 initializes the enable
   mask **private+2b8=83ec**, which includes4. At8000a000 it writes the
   accumulated word to **R0+138** (event acknowledge), then calls100037d4.
   That dispatcher source-kind3 path10003a04..3a28 passes the initialized
   global pointer **8000a2bc** to1002712c -> **80004800**, the interpreter.
   The same pointer is saved on the interpreter stack and read by opcode101.
   Thus an acknowledged R0+130 bit may already be absent at host timeout.
   R0+130.bit2 is the public Rogue **COMPUTE_FINISHED** event; its identity
   is corroborated by the firmware's compute wait, not by host IRQ174.
4. The next packet `004a000a,1,1` polls **`(R0+4a0&1)==1`** at80005c08.
   Public Rogue definitions identify4a0 as **CDM_CONTEXT_STORE_STATUS**:
   bit0 COMPLETE, bit1 NEED_RESUME. This matches firmware80008f00 testing
   bit1 and finish33's context/retry branch. It is **not a generic
   “USC shader finished” status**. The context-store/terminate PDS programs
   and scratch must complete too. Their relative ordering inside hardware
   is not newly proved by this software trace.
5. Finish33 normally clears G+724/+728/+72c, writes
   **`*(u32*)W25c=W260`** at80007590, then1001ede0(engine2) schedules
   follow-up. If4a0.bit1 is set it validates **H=W1e8** against the GPU
   scratch tail; mismatch retries start32 using the backlink. Preserve the
   round5 H correction. Output12345678 is separately written by USC.
   Host stamp events/IRQ174 happen downstream; ACKing host IRQ174 cannot
   manufacture the internal hardware event4 or4a0.COMPLETE.

**Saved cursor caveat (round15 already accounts for it):** SKU+48 is the
event wait, +4c the4a0 poll, +6c finish33. Event-yield writes its cursor;
a synchronous later poll can block while an earlier cursor remains in RAM.
Use `record+c=4` plus pending/event/status observations, not the cursor alone,
to distinguish a yielded event wait. Global A1GbP+10c/+110 initialized values
are100164e0/10016510; interpreter event pointer is **A1GbP+70=8000a2bc**,
which is private+2bc. The former is not that hardware-event word. Do not
treat packed local code at image+24000 as a live mirror of private RAM.

### Both execution bases have real pre-ALIVE firmware writers

Boot10000adc calls **1000515c** unconditionally; the policy6c8 tests do
not guard this call. It executes the following, before ALIVE10000b80:

| Firmware call / PC | Source | R0 destination / exact store |
|---|---|---|
|100051a4 ->1001d1fc; stores1001d260..278|meta+18 qword, low word&ffffffc0|**4008,4010,4028 = B**; fc8=0 qword|
|100051b4 ->1001d388; store1001d3dc|meta+10 qword, low word&fff00000|**610 = B** qword|
|1001d3e4..440|fixed and chip-info inputs|660=1,670=1,648=1,608=1;658/6c0 from chip-info+18;700/708=1 qwords|
|100051bc ->10019c2c|fixed|640=9,698=b,630=1|

Host `...6c96be8` supplies meta+10/+18=**300000000**, and Linux
`boot-layout.h` does too. Naming610 the PDS execution base and4008 etc USC
code bases is consistent with the IMG heap contract
([primary DDK source](https://chromium.googlesource.com/chromiumos/third_party/kernel/+/refs/heads/firmware-fizz-10139.117.B-chromeos-4.4/drivers/gpu/drm/img-rogue/1.9/rgxheapconfig.h));
the **Apple numeric writer/source chain itself is verified locally**.
This closes the old static missing-writer question. **Live readback is still
missing.** Proposed expected64-bit reads610/4008/4010/4028=300000000; if
they match, aliases at B=0/200000000 have substantially less justification.
No evidence supports replacing the base with a DMA physical address.

### Cache and power: what the bypass skips, and what it retains

**Verified retained hardware maintenance:** unguarded10005184 calls
**1001b638** before the base writers. Normal non-recording branch:

```
R0+11f00 = 1
R0+688 = 7;             wait (R0+688 & 7) == 0
R0+1720 = global44 | 1f; wait (R0+1728 & 1) == 0
R0+e230 = b03;          wait (R0+e238 & 1) == 0
// host-control/catalogue bookkeeping through100179cc(1)
R0+e138 = 804;          wait (R0+e138 & 804) == 0
```

The register writer, values and predicates above are recovered, including
generic100018c8's `(read&mask)==expected` contract. The high-level names of
**688 and1720/1728 as separate PDS/USC invalidators remain inferred** in
this Apple target; do not import another Rogue revision's bitfields.
The e230/e238 pair is consistent with the SLC maintenance path. This is
hardware maintenance, unlike cmd8's META DCACHE operation. ALIVE shows the
boot code returned, though recovery1000d138 can break some waits; it is
not independent readback that every invalidate succeeded. There is **no
newly proved host per-submit USC/PDS invalidate** between context attach
and first CL kick. A late-mapped BO could justify an isolated repeat of the
recovered maintenance, but “Linux never invalidates these caches” is false.

**Verified power split:** iOS map/start6c93a9c enables provider clock+568
then power+570;6ca0f78 repeats provider operations for platform lifecycle.
PMGR gfx domain is the previously recovered20e080340/gate124. Linux holds
`pm_runtime_resume_and_get()` for gfx and retains it across the failed job;
an omitted outer runtime-PM get is not the observed gap. Both use the
Fender core request and GPC0->1 boot sequence; Linux's start helper polls
R1+20000 core request and R1+1000 performance/busy state. **Core request
20 is part of boot hold; it is not a proved “turn shader clusters on” bit.**

Firmware1000c0d8 performs performance/power transitions when6c0/6c8 are1;
1001c838 derives timing from R1+1000.low4/frequency table and1001c9b8
requests/waits for performance state through1001c88c. Policy7 skips this
state machine. Linux still omits the full policy+3c table/filter inputs,
DVFS lifecycle and a verified shader-cluster readiness condition. Policy9
restores only the recovered sample16/frequency/limit subset. Read
R1+1000/20000 and R0+0/8/80/88 before publication and at timeout; status
progress must be measured. AUTO clock controls or GPC=1 alone do not prove
USC power. **No exact Apple per-cluster PMGR island/clock-enable write or
readiness ACK has been proved in this round.** Do not invent one from the
modern Rogue masks or write all clock fields ON. The inspected CL host
submit6ca0358 publishes the ring, barrier, Fender readiness/restart checks,
then R0+b00=12; no separate unconditional code-base/cache write appears
there. Transitive lifecycle callbacks remain incompletely recovered.

### PTE0c is a mapping hypothesis, not an execute permission

Fresh Ghidra6caa6e4 confirms range attribute lookup at
`59426c8 + one_based_range*20` and mapping permission combination before
6cac314; encoder6ca9a60 is
`(flags&c)|((flags&1)<<1)|((flags&10)<<58)|1`.
The ordinary code range300000000..400000000 is range1, attribute byte0;
its derived low flag byte is0 or1 depending on mapping permission bits.
That produces low PTE bits **1 or3**, rather than Linux's uniform **d**
(`g5p_leaf_entry(...,c)`). The exact iOS code allocation's final permission
choice has not been captured, so do not assert every iOS code PTE is1.
The code-range attribute0 is evidence for testing **flags0**, while keeping
data/scratch/output mappings unchanged initially. Bits2/3 are memory/cache
attributes in the public family; no G5P execute-disable bit is recovered
here. Calling flags0c “GPU READ+EXEC” would overstate the evidence.

### Ranked one-boot kernel changes and confirming logs

All proposals below are unexecuted here. Retain dependency1, full payload,
stamps, B and generation unless that experiment explicitly changes one.
Every success requires `stamp=expected=1 output=12345678` with the existing
sentinels intact. A firmware stamp alone is insufficient.

1. **Extend round15's observation set before another construction variant.**
   Add read-only64-bit610/4008/4010/4028 and raw688/1728/e238/e138;
   R0+80/88 clock control/status and wrapper1000/20000. Keep the existing
   130/478/480/498/4a0/PDS and scheduler observations. Compare before publish
   and timeout with active wrapper gating and pre-read breadcrumb logs;
   do not ACK130/138 from the host. Also derive an event wait from the
   execution record+c=4; **GbP+10c is not private2bc**. Suggested lines:
   `CDM bases pds610=0000000300000000 usc4008=... usc4010=... usc4028=...`
   and `CDM wait mask=00000004 event130=<raw> status4a0=<raw> stream480=<raw>`.
   4a0=1 with waitmask4 surviving supports event plumbing/visibility;
   4a0=0 leaves hardware execution/terminate outstanding. An acknowledged
   event130=0 alone is inconclusive. Saved cursors cannot uniquely identify
   synchronous polls. Capture raw values even when the expected value is0.
2. **Policy9 versus policy7, same payload/profile0.** Existing runner supports
   `GPU_DIAG_DC_VARIANT=9 scripts/gpu/run-compute-host-r15.sh`.
   Before/after R1+1000 must have busy10 clear; track low4 index and
   R1+20000 plus R0 clock/status words. Log
   `CDM power policy=9 gpc1000=<before>/<after> core20000=<before>/<after>`.
   Only post-attach numerical completion isolates a useful compute change;
   policy9 stalling before cmd8 is an incomplete-policy result, not a
   CDM-power disproof. Do not force a higher performance index in parallel.
3. **Code-page mapping attributes only:** in compute_prepare, set flags0
   for pages0..3 at B/B+14000/B+28000/B+30000 (actual low PTE bits1);
   retain flags0c for pages4..7. Fresh VM/DC3 attach as before. Log raw
   catalogue/leaf entries and BO VA->DMA for all pages, with
   `CDM code-pte flags=00 low=1 data_flags=0c low=d`.
   Read610/4008 bases,130/4a0 before/after. Numerical completion with
   identical payload and power profile supports a fetch/cache-attribute
   mismatch. Failure does not prove no MMU problem. Do not mix with B aliases.
4. **After-attach hardware maintenance only**, if base/clock snapshots are
   sane and the preceding tests fail: repeat the recovered688=7/poll7->0
   and1720/1728 sequence before CL publication, preserving observed1720
   bits (`read1720|1f`); use bounded polls. The latter substitution is a
   host experiment, not proof that read1720 equals firmware global44.
   Prefer an existing validated firmware register packet path if available;
   do not add an unbounded host loop or replay all of1001b638 blindly.
   Log `CDM inv pre688=<raw> post688=<raw> mask7_clear=1 pre1720=<raw> post1728=<raw> bit0_clear=1`.
   Keep e230/e238/e138 **read-only initially** to avoid silently adding SLC/
   MMU maintenance variables. Those0 predicates establish transaction
   completion only; the confirming result is the numerical store. Exact
   PDS-versus-USC identity of the two operations remains an inference.
5. **Only if base readback disagrees with300000000:** first preserve raw
   boot/timeout register values and firmware init10/18. Mirror at the
   **observed** base in a fresh VM (existing profiles1/2 cover0/200000000),
   leaving original mappings; do not immediately overwrite live bases.
   Log `CDM base-observed pds=<...> usc=<...> alias=<...>` and full PTEs.
   Matching base reads move this behind resources/context-helper state.
   If4a0.bit1=1, dump H and the scratch-tail comparison rather than adding
   generation/stamp/channel-identity variations blindly.

No firmware evidence found here ranks changing six disabled resource entries,
generation, stamp identity or CH identity above these hardware observations.
Their existing round15 profiles remain hypotheses. No specific shader-power
register fix is ready to prescribe without additional Apple evidence.

### Reproduce and scope limits

`python3 scripts/gpu/firmware-re/round7-evidence.py` checks ten firmware byte
anchors, the independently expanded event shift, four initialized global
pointers, eight host-function exports, eight exact payload pages and the
strict captured CDM. It writes
firmware extract (`round7-firmware-extract.txt`, not published),
host extract (`round7-host-extract.txt`, not published) and
payload checks (`round7-payload-check.txt`, not published). Shared Ghidra confirmed20H350
kernelcache SHA256c6b95fcf0efe52ee9258454d01d14adc9b0cb922d94c54a04a4ce11d1ae81e0a.
Missing shared function starts6ca0358/6ca9a60 use explicitly labelled prior
same-binary auxiliary exports; five functions were freshly decompiled.
Register semantic names from public Rogue remain corroboration, not a
complete Apple feature/bitfield definition. Shader-island power sequencing,
full policy9 correctness, exact per-submit cache contract and live base/PTE
readback remain unverified. All other concurrent edits were preserved.

## Solid findings available immediately

- Exp08 (supplied by orchestrator, now in experiment logs) still has DC consumer=0,
  producer=1 with full barriers, verified boot handoff, drained ALIVE event and
  verified `{8,0,0,0,0}` slot. The tested ordering-only correction is insufficient.
- Previous cancelled agent left binutils 2.30 metag objdump, successfully runnable
  in `linux-a9-kbuild:latest`; its build script is retained.
- **Do not use ordinary objdump output to reason about this firmware.** Binutils
  `opcodes/metag-dis.c:print_insn_metag` unconditionally reads four-byte instructions.
  The firmware's LDR sets TXPRIVEXT=0x80 for both threads (MINIM enable), and entry
  bytes do not decode coherently as standard META. A MINIM decoder is required.
- `scripts/gpu/firmware-re/disassemble.py` reconstructs five address regions from
  the existing validated LDR JSON, checks each load payload hash, and generates
  standard-META disassembly as a diagnostic only. This output is **not a validated
  MINIM listing**.

## Sources being used

- Public Imagination Meta HTP instruction reference, revision 2.1.235:
  https://docs.alexrp.com/metag/meta_htp_insns.pdf
- Lauterbach's META manual explains MINIM storage vs execution addresses and
  the 1:2 mapping: https://www2.lauterbach.com/pdf/debugger_meta.pdf
- Local Linux `build/gpu/refs/linux/pvr_rogue_meta.h` names TXPRIVEXT bit7.

The findings below distinguish recovered instructions from untested hypotheses.

## New: DC dispatcher located (partial decoder, checked GP fields)

Execution PCs below are MINIM PCs, **not blob storage addresses**.
`storage = base + (PC-base)/2` in these large-MINIM windows.

- DC consumer function **10001cf4**: root from `[A1GbP+28]`; DC ctl from
  root+0x18 (10001d08), data from root+0x1c (10001d38), slot from
  consumer*20 (10001d40). Consumer updates only **after handler returns**,
  at 10001e24..10001e58. Therefore consumer=0 does NOT establish that the
  kick was ignored: cmd8 can be executing/blocking before its advance.
- Switch: cmd-3, range <=7, eight branch-table entries at 10001d78..10001db0.
  Cmd8 => index5 => **10001f08**. This clears root+0x498 and the word through
  private+0x160; calls **10021d48**, copies control+0x1020 into root+0x1480,
  conditionally copies a variable payload, tests cached control flag 0x40,
  calls **1001fea0**, then advances consumer. It is a cache/synchronization
  operation, not a guaranteed trivial NOP.
- **10021d48** can spin indefinitely when private+0x16c != 0 and the word
  addressed by private+0x160 is zero: polls META internal 0480fff8 ready,
  writes request **0x11047**, waits ready, reads 0480fff0 and repeats while
  bit0x200 is set. This is a firmware-side thread/register synchronization
  wait, not permission for ARM-side slave-port probing.
- **1001fea0** invalidates/maintains root+0x490 length0x1000 through
  1001f114, then increments **control+0x1024**. Dump this as a cmd8-completion
  breadcrumb. root+0x1480 records the copied size; an unchanged zero is inconclusive for
  the zero-payload command. Do not seed a live length field with a sentinel.

## New: actual scheduler and ALIVE are in the 80000000 region

The region previously labelled initialized data also contains executable MINIM.
The PLT at **100271ac** jumps to **8000a0a0** (storage80005050).
That function writes `{type=0, word1=8000a120}` into a stack event at
8000a0b4..8000a0d0, then calls 8000a4f4. The exact payload anchor is found.
**8000a120 is the scheduler loop PC**, deliberately sent by firmware, rather
than simply an opaque random pointer. Full trailing fields still need checking.

This discovery makes dumping the image's 8000 local backing as important as
root/shared memory. A pure T0/T1 disassembly misses the scheduler entirely.

## Scheduler path and prerequisites

The main loop at 8000a120 calls trampoline8000a4b4 -> **10001ac8**
BEFORE reading TXSTAT and before fetching an MTS item. That helper calls the
large policy routine **1000c0d8** if BOTH private+0x228 and private+0x22c are
nonzero. Init routine10003ca4 copies these from **meta+0x6c0 and +0x6c8**;
Linux explicitly sets both to1, despite leaving much of the power-policy
object zero. Thus a pre-MTS policy loop is an evidence-backed alternative to
an unrecognized kick. A complete exit-condition analysis of1000c0d8 is pending;
this report does not assert it definitely blocks on this boot.

If TXSTAT bit0 is set, scheduler calls1001061c and clears that trigger;
it also checks **boot control+0x1c** and services/clears a nonzero request
through several helpers. If TXSTAT equals1 it can loop without fetching MTS.
Otherwise it reads **R0+0xb10** via10027280 (a RAM-loaded helper wrapping
GETD). At8000a20c, it extracts **MTS_value &0xf** into D0.5;8000a214..8000a22c
clears that pending bit in private-control+0xfc. These selected AND/shift
encodings now have independent anchor checks. Thus kick0x10 selects low
nibble0; CL0x12 selects2. Both go through the service helper below, which
checks DC first. Other MTS fields remain incompletely decoded;
normal service is gated by cached control flags bit1 clear and10003bb4.
The service trampoline **8000a444 ->10002504** snapshots all four H2F producers,
then calls10001cf4 for DC FIRST, before TA/3D/CL. DC is therefore not a distinct
CL-only handler. The remaining MTS fields still need independent validation; no replacement
kick value is established by this pass.

`10003bb4(root+0x70)` calls a power/readiness helper1000d044 and may enter
1000d504 on root+0x74. The root+0x70 object is the kernel's slot0x4a policy
object, not an additional host-ready pointer. Its +8/+c and +0x34 affect the
readiness branches. Do not set +0x34=1 by guess: it also allows deeper power
policy behavior.

### Cmd8 detail and what to observe

The cmd8 switch target is **10001f08**, independently anchored by the table
for commands3..10. It is **not** case7 (10001fa4), which shares part of the
cache path but skips payload copying. Cmd8:

1. Writes zero to root byte+0x498, and through private+0x160.
2. Calls10021d48 (conditional T1/register synchronization spin described above).
3. Loads boot control+0x1020 and saves it to **root+0x1480**.
4. For a nonzero size, calls1001eae8 with destination **root+0x4a0**, source
   **boot control+0x4a0**, byte count **size<<5**. The zero-size case skips it.
5. Calls1001fea0, then publishes the DC consumer.

**1001fea0 ->1001f114 ->100006d0** implements cache/coherency maintenance
(the latter programs META cache partition controls); it increments
**boot control+0x1024** afterward. It is not an event-ACK routine.
A frozen consumer can therefore mean pre-MTS policy, missed MTS, stale producer,
T1 synchronization, or cache maintenance. The existing consumer-only timeout
cannot discriminate these cases.

## ALIVE payload interpretation

8000a0a0 reserves a stack frame, sets only event words0 and1 to0 and8000a120,
and calls8000a4f4 ->1001fb9c. The publisher copies eleven words, stride44,
using init+0x20/+0x24 and producer+0x10, then raises the host notification.
There are **no other stores to the event payload between its two explicit
initializations and the copy**. Inference: trailing words in this type0 event
are stack residue, not a defined cookie/handshake. This explains why words3/7
can contain **100271f0**, a real runtime trampoline to10000148, while words4/5
change between boots. No entropy source or cookie-generation operation was
found here. Do not claim the varying word is random merely from three values.

This also explains why only word1 is read by the recovered iOS ALIVE branch.
The explicit word1 is an execution PC; do not convert it to an ARM DMA address.

## Ranked experiments for kernel round10

All are hypotheses, one fresh boot each. No experiment below was executed.
The existing experiment09 variants can be reused; the following diagnostic
addition should precede them. Success marker remains
`DC consumed cmd=8 consumer=1 producer=1`. **A timeout must also report the
breadcrumbs below**, because a blocked handler is now a concrete possibility.

### 0. First add the two cmd8 observations (highest information gain)

Exact kernel addition: in the existing RAM-only DC snapshots, explicitly log
root+0x1480, byte+0x498, boot control+0x1020/+0x1024 and+0x1c, and META init
+0x6c0/+0x6c8. Capture before publication, after kick,100ms and timeout.
Do not initialize a live firmware-owned counter to manufacture a delta.

Suggested line:
`DC fw stage=timeout payload_units=0 root_copy_units=0 cache_epoch=<n> root_busy=<b> host_request=<n>`.

- `cache_epoch` increasing after kick proves execution reached the tail of
  cmd8's cache path (or another caller of that helper); pair it with the
  baseline and absence of other traffic. If consumer still0, investigate
  visibility/publication rather than missing initialization.
- Zero throughout is inconclusive, because zero is the legitimate cmd8 size.
  An unchanged0 root_copy_units is NOT proof of an unentered handler.
- A controlled **nonzero-payload command8** can make root_copy_units useful:
  set control+0x1020=1 and provide a validated32-byte payload atcontrol+0x4a0,
  preserving the actual host nonzero branch's slot format. This requires the
  kernel agent to check that host path first; do not assume `{8,0,0,0,0}`
  remains the correct entry for the nonzero-size path. Confirm
  `root_copy_units=1` and the copied32bytes atroot+0x4a0.

### 1. Pre-MTS policy cannot complete under Linux's incomplete policy

Evidence: every loop iteration enters10001ac8 when meta+6c0/+6c8 are both1;
Linux sets them both1 while leaving dynamic power policy zero. This is a
specific execution gate recovered from firmware, but a stall is unmeasured.

Exact isolated experiment: set **meta+0x6c8=0 before release**, leaving6c0=1
and all ring/kick fields identical. This forces the documented branch to
skip1000c0d8. It is an experimental policy bypass, not a recovered iOS default.
Log `DC fw policy meta6c0=1 meta6c8=0` before boot. Confirmation is subsequent
cache_epoch or consumer progress that the instrumented baseline lacked.
Do not enable real rendering on a policy-bypass result.

### 2. DC is fetched but cmd8 blocks synchronizing T1 / cache state

Evidence: consumer publication follows potentially unbounded10021d48 and cache
helpers; boot config contains a second firmware thread. The cmd8 zero-payload
handler still performs this synchronization. ALIVE proves T0 reached the
scheduler, not that T1 reached its loop.

Exact kernel addition: log existing init T1 PC/SP, control+0x1024, and firmware
trace buffers before/after cmd8. Do not issue new live META slave-port reads.
The same meta6c8=0 variant may bypass a relevant policy prerequisite but does
not isolate this cause. A decisive test would need a validated firmware RAM
breadcrumb immediately before/after10021d48, or safe T1 PC capture recovered
from an existing iOS path; neither is implemented here. Confirmation line
for that future instrumented variant: `DC fw cmd8 entered=1 t1_sync_done=0`.
No honest one-word host-ready fix is established yet.

### 3. META sees stale DC producer or slot data

Evidence: event traffic proves device->host stores, not the reverse direction;
firmware explicitly performs cache maintenance in cmd8. Ordinary Apple DC
aliases still match Linux. This hypothesis survives and has no proven fix.

Exact existing kernel variants: experiment09 v6 clears bit31 of init+0x1c
(data only); v1 clears it for+0x18 and+0x1c (control+data), before release.
Confirm the logged aliases and `consumer=1 producer=1`; a data-only success
has a different implication from requiring control bypass too. Neither result
proves SLC coherency or authorizes arbitrary alias changes for all objects.

### 4. Boot-control policy / missed notification

Existing experiment09 v5 sets **control+0x30=0** instead of1; the iOS host
chooses either value. Firmware meaning is not resolved here. Keep it after the
firmware-specific gate test above. Confirm log `control30=0` then DC progress.
AIC re-ACK and one delayed re-kick are weaker than these explanations;
the main loop really consumes MTS through+b10, but bit-level decoding remains
incomplete. There is still no firmware evidence supporting DC kick0x12.

## RAM words/buffers to dump around the kick

Use g5p_object() translations; offsets below are relative to the named object,
not absolute shared-array offsets. Existing round10 root/shared deltas cover
most of these, but explicit labels avoid missing unchanged zeros.

| Object | Words/range | Firmware evidence / use |
|---|---|---|
| Boot control slot0x5e | +0,+4,+0x10,+0x14,+0x18,+0x1c,+0x20 | Copied by10001328 into private+0x170..0x188; flags/request/scheduling inputs, exact field names unresolved |
| Boot control | +0x1020,+0x1024 | cmd8 copy size in32-byte units; cache-maintenance completion counter |
| Boot control | +0x102c,+0x1044,+0x1048,+0x104c | Runtime state/known backing pointers; +104c test-and-set in1001f094; names beyond that unresolved |
| Init root slot0x44 | +0x490..+0x49c,+0x1480,+0x1488/+0x148c | Cmd8 state/copy descriptor; +1488/+148c referenced by T1 cache loop |
| Init root | +0x4a0..+0x147f |32-byte-unit policy data copied by cmd8; do not assume a trace string format |
| Policy slot0x4a (root+0x70) | +0,+8,+c,+0x30,+0x34,+0x38,+0x900..+0x90c | Firmware readiness/pre-MTS policy loop inputs/state |
| Root channel objects slots47..49 | +0,+8,+0x14; tail state areas | Firmware checks busy/state before scheduler/power transitions |
| DC/event controls | +0,+0x10 and pending entries | Queue ownership; DC20-byte/event44-byte entries |
| Root+0x45c target(slot85),+0x460 target(slot4f), log controls/data slots71..77 | Entire allocated extents/deltas | Firmware logs include type2 records through root+0x28/+0x2c and trace data via+0x45c; exact trace/fault formats remain unresolved |
| Image T0 mapped data | META100139c0..10013ba0 (packed image+0x179c0...) | Global variables, scheduler pointers; cached device visibility applies |

No verified periodically increasing heartbeat was recovered. Do not label an
arbitrary changing word heartbeat. Private state lives through globals pointing
to **8000a010,8000a2c8,8000a2d0,8000a000,8000a198**. These are META local/core
addresses, not automatically CPU-readable DMA pointers. The host's packed copy
of code80000080..8000565c atimage+24000 is only a load source; **it is not a live
mirror of private8000a000 state**. No unsafe post-boot a18 fallback is proposed.

## Reproduction, validation and remaining limits

```
scripts/gpu/firmware-re/build-metag-binutils.sh
python3 scripts/gpu/firmware-re/disassemble.py
python3 scripts/gpu/firmware-re/minim.py
python3 scripts/gpu/firmware-re/check-decoder.py
```

ISA status: this is META-family GP code in MINIM mode (16-bit core and
32-bit prefixed encodings), matching the public Meta HTP/META2-family
encoding documentation. The exact implemented META core SKU is not established;
no MTX decoder was used.

The first script was reused from the cancelled agent. Native GNU output is
only a backend: the new Python MINIM expander emits standard META words with
PC-preserving padding. Original bytes, storage addresses and expanded words
are retained in *.minim.json; *.minim.dis lines carry both address spaces.
The seven independent GP anchor encodings pass. T0/T1 startup reconstructs the
exact LDR SPs8000a3c8/8000b3c8. Ring strides/indices and ALIVE anchor agree with
independent host RE/device bytes. This is useful validation, **not a complete
ISA conformance test**.

**Decoder limitations:** conditional/extended ALU/address forms, multi-register
forms, register shifts, DSP/FP and some long forms remain incomplete or
experimental. UNKNOWN is explicit where implemented checks reject a form;
other provisional decodes can still be wrong. Listings traverse data as well
as code. Do not feed them into automatic kernel changes or infer a complete
control-flow graph. The report's direct loads/stores, immediates, branch table,
relative calls and PLT targets are the manually reviewed subset. Complete MTS
bitfield decoding, exact1000c0d8 termination, and a decisive T1 progress probe
remain open.

Read-only source audit used linux-a9-src:/src:ro, /src/wt/gpu, observed head
002bc454bb0e during concurrent round10 work. Relevant boot-layout/pack bytes
still match the fields discussed here. No files in that volume were changed.
No Ghidra renames were necessary: this pass works on extracted firmware,
not the shared ARM64 kernelcache analysis. No kernel/workspace commit, push,
device access or additional subagent was used.

## Round 2 — T1 ownership, policy confirmation, CL notification endpoint

2026-10-22. This section supersedes round-1 experiment ranking. Research only:
no device access, kernel edits, git commits, or new large downloads. Source
cross-checks used the live shared Ghidra server and the kernel volume mounted
read-only. Generated listings/decompilations remain under `build/gpu/firmware-re/`.

### Immediate handoff after exp09 v7

**Device-verified (orchestrator's experiment, inspected logs):** v0 has
DC consumer0/producer1, cache_epoch0, no post-kick CPU-visible RAM delta.
v7 (`meta+6c0=1`, `meta+6c8=0` before release) reaches
`DC consumed cmd=8 consumer=1 producer=1`, cache_epoch1, and passes IRQ test.
Its log still says `T1_pc=20000000 T1_sp=8000b3c8`.
These are **configured entry/SP words**, not live thread register samples.
The log reads META init+764/+768; those fields are never intended to track PC.
See `../experiments/logs/2026-10-22-exp09-v0-dmesg-gpu.txt` and
`../experiments/logs/2026-10-22-exp09-v7-dmesg-gpu.txt`.

**New strongest CL hypothesis:** the dependency probe sends notification
`{2, channel_META, 0, 0, 0}` while publishing one pointer (`ctl+40=1`).
Firmware copies notification word2 to **ctl+30**, then scans from **ctl+20**
toward **ctl+30**, not directly toward the host's ctl+40 producer.
Zero endpoint equals zero scan index: a consumed global notification can
legitimately result in no channel entry being examined. Test **word2=1**.
This has not been run by this research agent. Do not yet call it a proven fix.

### T1: who starts it, exactly what cmd8 waits for

Recovered sequence, all addresses below are firmware data/MMIO addresses unless
explicitly called execution PCs:

1. T0 bootstrap calls `10021e0c -> 1000d648` before ALIVE. At1000d77c it
   clears **private+16c**; at1000d794..7a4 it sets private+160/+164/+168 to
   the three words at **8000a2bc/+4/+8**. `10021e0c` then ORs bit31 into
   these pointers. Private base is **8000a000**, obtained from data global
   `10013a2c` (A1GbP+6c), independently present in T1's global200016cc.
2. Work helper `1000d93c` returns immediately if root byte+498 is zero;
   otherwise it publishes work through those three local words (1000d994).
   If private+16c is zero it calls **10021f60** at1000dafc.
3. `10021f60` returns immediately if already started. Otherwise10021f98
   flushes, then it starts T1 itself:
   - write init META+768 (SP) to **0480fff0**, request **0x1003** at0480fff8;
     poll request bit31 until set (10021fd8..10022014);
   - write init META+764 (PC) to0480fff0, request **0x1005** at0480fff8;
     poll bit31 (10022018..70);
   - set private+16c=1 (100220a4), write **04801010=0x20000**
     (T1 STATUS/privilege), write **04801000=1** (T1 ENABLE,100220b4).
   This is a T0-owned on-demand start, not a missing ARM-side T1-enable step.
4. Cmd8 clears `*private[160]` and calls10021d48. That function returns if
   private+16c==0, or if `*private[160]!=0`. Otherwise it waits for
   0480fff8.bit31, sends **0x11047**, waits for bit31 again, reads0480fff0,
   and repeats while **bit0x200 is set**. Request decode is
   READ(0x10000) | THREAD1(0x1000) | REG4(0x40) | UNIT7:
   **T1 TXPOLL**, not T1 PC or an ARM mailbox. TRM register map p180 names
   trigger-unit register4 TXPOLL. The port layout also matches Linux's
   `pvr_rogue_meta.h` TXUXXRXRQ macros.
5. T1 entry200003f4 sets TXMASK=0x204; main loop200004b8 reads TXSTAT.
   At200005e4 it writes **TXSTAT=0x200**, writes04820010=2, then
   R0+0xb08=0 and loops. It tests the shared local pending word while doing
   work. This is consistent with T0 waiting for outstanding T1 trigger/work
   to drain after clearing pending. The precise trigger source wiring is
   not established here; do not invent a host interrupt ACK for it.

**Implication:** on a clean boot with no work preceding cmd8, started remains
zero and the T1 poll is skipped. v7's successful cmd8 with unchanged configured
entry/SP agrees with that. T1 not running is not, by itself, an explanation
for v0. No recovered need exists for the host to pre-start T1 to run cmd8 or
an already-satisfied dependency. For actual GPU work, preserve valid init
PC/SP and all backing/PIO maps; let the recovered T0 path start it.

### Cmd8 and T1 cache maintenance

`1001fea0` calls100006d0 with **start=root+490, length1000**; it increments
control+1024 only after return. For lengths overfff, the reviewed path uses
META cache configuration **04830028**, per-thread partition information at
**04830200 + ((TXENABLE & 0x700)>>5)** and cache-maintenance address space
based at **04400000**. Some operand-selection instructions in this path remain
UNKNOWN; a complete cache-mode/control-bit interpretation is not established.
For shorter ranges the same helper uses DCACHE on 64-byte lines.

T1200004c0 compares control+1024 against its last-seen epoch. A change flushes
64-byte lines covering **[root+490, root+1490)** and then the optional range
**[root[1488], root[1488]+root[148c])** before recording the epoch in a register.
This is a **consumer of T0's epoch**, not a RAM acknowledgement required before
T0 increments it. Root+1488 is an address and +148c is byte length; don't seed
these with arbitrary diagnostic sentinels.

DCACHE long-form decode was wrong in round1: it uses **Rs bits7:3 (five bits)**
and **Rb bits2:0 (three bits)**. Corrected against TRM3.7.5 p226:
`bd2c 4028 -> ad290028 -> DCACHE [D0FrT],D0.5` in T1, with D0.5=0;
`bd2a 4048 -> ad288048 -> DCACHE [D1Ar3],D0.5` for its optional range.
The older listing's D0.12/D1.10 operands were decoder artifacts.

Could bad mappings fault/hang? The root, control and descriptor accesses are
ordinary loads/stores and need valid firmware mappings; nonzero cmd8 payload
also copies control+4a0 to root+4a0 for units<<5 bytes. The optional T1 range
needs a valid descriptor if used. The public instruction-set TRM describes
DCACHE as a specialized cache write but does not establish this GPU's response
to an unmapped GART address; **do not claim either guaranteed fault or safety**.
The full-cache path is not equivalent to reading every byte in its input
range. v7 cache_epoch1 now verifies this cmd8 cache path can complete on the
existing baseline mappings; a cache-map defect is not the leading v0 cause.

### meta+6c8: iOS uses one; bypass loses policy behavior

**Verified iOS:** `AGXMetaFirmware_initializeSharedRootsAndControl`
(`fffffff006c96be8`) writes meta+6c0 from global007b3f402.bit1, and meta+6c8
from accelerator virtual+768. G5P allocator **...6ca8bd8** installs vtable
**...6ff6820**. Its +768 entry at **...6ff6f88** contains **...6ca8648**.
Read-memory bytes at that target are **20 00 80 52 c0 03 5f d6**:
`mov w0,#1; ret`. Thus **G5P iOS writes6c8=1**, not0. Ghidra has no function
boundary for that tiny getter; its raw bytes and vtable/allocator form the
cross-check. Do not interpret FUNCTION_NOT_FOUND as evidence it returns zero.

`...6c9edcc` calls common roots/control initialization first, fills PIO aliases
and META+764/+768 before release. `...6c9fc10` can clear T0/T1 ENABLE bit0
through the slave port while preparing hardware. `...6ca05d4` waits for ALIVE,
then calls...6c98234 (copy20 bytes, barrier, producer update), then barrier,
Fender+80 callback (R1+14=7), readiness handling, and R0+b00=10. No separate
T1 start is present in the recovered host DC submission path. The T0 sequence
above supplies the start when needed.

Firmware caches6c0/6c8 at private+228/+22c. These gate more than the one
scheduler call: timing selection10022150/1dc, boot platform initialization
1001fef0, event notification1001fd50, and policy1001cd8c also use them.
Therefore v7 is a **platform/PIO policy bypass**, not a narrow scheduler-only
switch, and DC success does not establish rendering readiness.

Pre-MTS1000c0d8 is a timer-driven power/performance state machine over
root+70 (slot4a): state+8/+c, timing+18..+2c, thresholds/index+30/+34,
telemetry/control+0x62c..+0x6fc and timestamps/counters+8f0..+934. It calls
100223f0 to sample time; that reads PIO timer tuple1 with meta+760 (1020),
and stable high/low R0+160/+164 samples, with retry loops. It can enter
10009bd8/10009db4 and power helpers for state transitions. Exact final
blocking instruction in v0 is **not localized** by the available snapshots.

The apparent zero-policy early return is irrelevant to v0: before publication
its slot4a at root+3040 has **+8=2,+c=2,+34=1**, already changed by firmware.
It takes the deeper path. v7 also avoids startup policy work: slot73's boot
producer is0 versus4 in v0. Four unconsumed records do not establish ring-full
backpressure. Dumps must include the records, not only their queue indices.
No single replacement power/clock write has been proved. Applying the bypass
and manually enabling T1 does not reproduce iOS's state machine.

### CL after the global work entry: T0 schedules, then scans dependencies

Execution-PC chain (reviewed direct loads/stores/calls):

- `10002504` services DC, snapshots work producers, then CL at10002cd0.
  Type2 invokes **10004e94(global_CL=root[6c], channel=entry[1],
  endpoint=entry[2])** at10002c7c. Only after it returns does10002cb4 advance
  the global work consumer. Thus work=1/1 proves the enqueue helper returned,
  not that a channel command ran.
- `10004e94` links channel+28/+2c into global_CL's list if channel+1c==0,
  calls10014958(global_CL+76c, channel,2), sets channel+1c=1,
  writes **channel.control+30=endpoint** at10004f40, and channel+38=1.
  These are useful entry breadcrumbs even when ctl+0 stays zero.
- Scheduler selects CL via10002dcc -> **10004f94**, which invokes10016400
  with scheduler object global_CL+76c and callback **800016a8**. There are
  resource/slot/eligibility checks before invoking that callback; inspect
  channel+24/+4c/+50/+54/+58 and global_CL+76c..end on failure.
- Callback800016a8 first calls8000a4c4 -> **10003bb4(2)**, a readiness gate.
  A zero return sets the callback's status to3 and returns without scanning.
  10003bb4 calls1000d044(root[70]) and sometimes1000d504(root[74]); it can
  also accept root[64]+72c values1/2/3. This gate survives6c8=0.
- Callback loads **ctl+30 endpoint** at80001758 and **ctl+20 scan index**
  at80001760. Equality causes the empty-scan exit. It fetches pointer-array
  entries (channel+4) and handles tag3 GPU work or tag4 dependency waits.
  Tag4 at80001a38 invalidates16 bytes, loads stamp through command+4,
  and tests signed `(command[8] - *stamp) <= 0`. A satisfied dependency
  branches to80001900, increments scan index modulo ctl+50, then
  8000180c publishes **ctl+20**. **It does not write the stamp.**

**Critical distinction:** ctl+0 (host retirement consumer), +10, +20
(firmware scan), +30 (notified endpoint), and +40 (host producer) are different
fields. A tag4 wait is not a stamp-producing job. Initial stamp1/expected1
should remain1 on success. A new stamp value requires an actual completion
record/microsequence, not this wait record. Do not make a tag4 transport test
wait for a new stamp or assume ctl+0 must advance before a later GPU job.
The exact dependency-only retirement behavior remains unverified on device.

The reviewed dependency callback runs on **T0**, through the8000 MINIM window.
The tag4 success branch does not call10021f60 or execute CDM. Missing T1 start,
context attachment, and CDM initialization are weaker explanations than the
zero notified endpoint for this probe. A real tag3 command will require the
full context/work/microsequence contract. DC cmd3 maps to1000218c and updates
an indexed control/context table using control+1044/+1048 and10019b84;
this pass does not establish a valid cmd3 payload or justify submitting a
zero/fabricated context. The dependency branch itself does not read channel+8.

### Revised ranked experiments — exact changes and confirmation lines

One fresh boot per changed variable; keep successful v7 and kick12 for CL.
No experiment below was executed here. Rank reflects evidence after v7.

1. **CL notified endpoint is zero (strongest concrete mismatch).** In
   `g5p_work_submit`'s five-word notification writer, change only word2 from0
   to the published channel endpoint **1**; leave tag4/stamp1 and queue
   pointers unchanged. Keep barriers, publish ctl+40=1 and global producer1.
   Add `CL fw notify=1 scan=<ctl20> endpoint=<ctl30> hostprod=<ctl40>`.
   Confirmation: `work=1/1 scan=1 endpoint=1 hostprod=1 stamp=00000001`.
   If scan1 but ctl0 stays0, the transport succeeded and the old retirement
   expectation was too strong; do not report real GPU completion.
2. **Readiness/resource scheduler stops CL after enqueue.** First add
   before/after/timeout RAM snapshots of channel0..60, ctl0..54, global_CL
   (slot49)0..8c0, policy(slot4a)0..938, root40..60 and TA(root64)+720..740.
   Log `CL fw linked=<ch1c> pending=<ch38> wait=<ch24> slot=<ch4c>
   outstanding=<ch58> scan=<ctl20> endpoint=<ctl30> policy=<p8>/<pc>`.
   If linked1/pending1/endpoint1 but scan0, use those bytes to distinguish
   resource eligibility from10003bb4's power gate. No validated power-register
   fix exists yet. A diagnostic firmware-only variant can force callback
   readiness success, but it must never run tag3/CDM work; no such patch is
   supplied here because bypassing the return can also skip power transitions.
3. **Dependency retired later, context/GPU completion absent.** Once scan1
   is verified, change the kernel transport-probe success predicate to track
   scan endpoint and unchanged dependency stamp separately from ctl0 retirement.
   Log `CL dependency scanned=1 retired=<ctl0==1> stamp_unchanged=1`.
   This is only a transport test. Recover a real context and tag3 completion
   sequence before claiming a DRM fence/GPU job succeeded. Do not add DC3 by
   guess. This could explain a remaining timeout after experiment1.
4. **Policy bypass removed a prerequisite for real work.** Retain v0/v7
   evidence and dump the full policy/global_CL delta. To narrow future RE,
   compare a targeted pre-MTS-call bypass with the broad6c8 bypass: expect
   original raw **14c0e598** at execution8000a120/storage80005090, replace
   exactly four storage bytes with **fe93fe93** (two MINIM NOPs), before
   load/release, keeping6c8=1. Require byte match and regenerated LDR hashes;
   do not patch a live image. Log `FW experiment skip_pre_mts=1 meta6c8=1`.
   Confirmation: DC consumption with policy/PIO initialization retained
   narrows the DC failure to the skipped call. Failure means other6c8-gated
   paths remain involved. This is a diagnostic proposal, not a shipped patch.
5. **T1 synchronization (demoted for first cmd8/tag4).** Only pursue for
   actual work or evidence that started became1. The decisive RAM fields are
   local-private+16c and pending8000a2bc, not init764. An isolated cmd8 sync
   bypass could replace raw **f4c3869b** at execution10001f30/storage10000f98
   withfe93fe93 before load. Log `FW experiment skip_cmd8_t1_sync=1` and
   require `cache_epoch=1 consumer=1` absent in its matched baseline. v7
   already rules out needing this bypass on its cmd8 path; do not combine it
   with other fixes or interpret init-PC output as evidence of T1 progress.

### Shared-word dump contract, and what cannot be seen from init

Offsets below are relative to **init root R = slot44**, with pointer notation
`[R+offset]` meaning the pointed-to object. For this layout slot44 starts at
root allocation+0. The existing full shared/root snapshots cover these words;
explicit labels are needed for unchanged zeros. This is the complete set of
**recovered relevant breadcrumbs**, not a certified enumeration of every store
in the firmware (the decoder is incomplete).

- R+40..60: TA/3D/CL ready/eligibility fields written by10002504; in particular
  +58/+5c/+60 are CL eligibility/resource/snapshot state.
- R+490/+494 and bytes+498/+499/+49a: work/trace pointer, write position,
  command/worker flags. R+1480 payload units; +1488/+148c optional cache range.
- Boot control slot5e: +1c host request, +30 policy, **+1024 epoch**,
  +1028/+1034/+1038 policy inputs, +1044/+1048 context/control table pointers,
  +104c initialization flag. +1020 is host payload size, not firmware status.
- `[R+18]` DC ctl+0/+10, `[R+20]` event ctl+0/+10 and44-byte entries;
  `[R+28]` and `[R+2c]` log ctl/data; `[R+30/+34]`, `[R+38/+3c]`
  remaining firmware output ctl/data. Dump pending entry bytes in all of
  slots70..77, not only producer/consumer. slot73 p4 vs p0 is real evidence,
  but no exact record meaning or heartbeat is established.
- `[R+6c]` global CL state slot49: +0/+4 list head/tail, +8/+c other lists,
  +10 scheduler state, +728/+72c/+758/+75c/+760 power/progress state,
  **+76c..+8bf** resource/scheduler object used by10014958/10016400.
  Keep full slot47/48 too for cross-engine power readiness.
- `[R+70]` policy slot4a: +0,+8,+c,+18..+38,+62c..+6fc,+8f0..+937;
  `[R+74]` companion policy slot4b entire28 bytes. R+44c META init:
  +6c0/+6c8 flags, +630..+6bf PIO tuples, +758/+75c/+760 IRQ/timer
  configuration, +764/+768 configured T1 PC/SP. **Label these cfg_t1_pc/sp.**
- CL probe channel at shared+54000: all0..60; ctl at shared+54100:
  **+0,+10,+20,+30,+40,+50**; pointer array+54200, command+54300,
  stamp+54340. All four indices should be printed even if zero.
- Trace/fault targets `[R+45c]`, `[R+460]`, full output ring extents and
  event types4/7/8 remain mandatory. No verified firmware heartbeat is known.

**T1 local state has no init-relative shared address.** Private base8000a000:
+160/+164/+168 pointers; +16c started; pending8000a2bc, work arguments
8000a2c0/+a2c4. T1 globals200016a4/+16a8 hold root/control pointers; +16b0..+16bc hold local counters/state.
These addresses live in META local data, not the host's packed code source.
Do not turn them into `image+24000+offset` and claim a live mirror. Any live
capture must first recover a safe local-memory read mechanism; no additional
slave-port traffic is authorized or performed by this report.

### Reproduction and confidence

```
python3 scripts/gpu/firmware-re/check-decoder.py
python3 scripts/gpu/firmware-re/minim.py
python3 scripts/gpu/firmware-re/inspect.py 10021f60 100220d4
python3 scripts/gpu/firmware-re/inspect.py 10004e94 1000501c
python3 scripts/gpu/firmware-re/inspect.py 800016a8 80001c68
```

Passed: seven original GP anchors, four independently calculated DCACHE
anchors, two reserved-encoding rejections, six raw cmd8/T1/CL image anchors.
Regenerated all four existing small listings using the existing Docker
binutils; no toolchain rebuild or large artifact was created. The DCACHE
encoding and TXPOLL identity use the [Imagination instruction TRM](https://docs.alexrp.com/metag/meta_htp_insns.pdf),
p180/p226. Function boundaries in shared Ghidra were not changed, and no
renames/save were needed for this read-only host cross-check. Exact v0 stall
PC, full policy transitions, GPU-context attachment, and dependency-only
retirement semantics remain open; the new endpoint experiment is the next
concrete discriminator.

## Round 3 — missing timing inputs, readiness gate, real compute contract

2026-10-22. Research only; no device access, kernel writes, commits, or new
downloads. Kernel round 11 owns implementation. Sources: shared j71t Ghidra,
existing auxiliary decompilations (where shared Ghidra lacks function starts),
existing MINIM listings, read-only kernel volume and saved live ADT. New small
decompilation caches are `build/gpu/firmware-re/r3-ios-*.json`.

### Transport baseline now verified on device by the orchestrator

Inspected `../experiments/logs/2026-10-22-exp10-cl2-dmesg-gpu.txt` and
`...-runner.txt`: endpoint1 with6c8=0 reaches
`CL NOP queue completion; firmware dependency only, no GPU dispatch`,
`work=1/1 ... channel=0/1 ... stamp=00000001`, complete chain rc0 and park.
Round-2 endpoint hypothesis is confirmed. Channel retirement0 is compatible
with successful tag4 scanning; it must not be used to reject this transport
test. It proves neither CDM execution nor a shader-produced stamp.

### What can and cannot be localized from v0

**Verified static:** pre-MTS chain is
`8000a120 ->8000a4b4 ->10001ac8 ->1000c0d8(policy)` with6c0/6c8=1.
However, the scheduler ALSO fetches MTS at8000a1e8 and calls
`8000a240 ->8000a4c4 ->10003bb4 ->1000d044` BEFORE service/DC dispatch.
If readiness is false,8000a254..a298 re-arms/waits and calls policy again;
service is reached through8000a36c. Thus **“v0 stalls before MTS” is not
established**. Skipping only8000a120 does not remove the readiness gate or
the second policy call at8000a298.

`1000d044`'s exact relevant predicate:

- policy+8<=1: return1;
- policy+8=2: +c=0 returns1; with private+244=0, +c=4 also returns1;
  other cases take the deeper path;
- deeper path: if root.policy+34=0, return0; otherwise test
  companion slot4b+0==2 via1000d138. That returns1 for the comparison,
  but1000d044 then returns0. If companion is not2, it calls1000c0d8
  and returns0 after it returns. The call's own side effects can allow a
  *later* attempt to pass.

Private+244 is cached meta+6e0 (Linux1). V0's policy2/2 and companion0
therefore take policy again and return0 if unchanged. DC kick low nibble0
does not get the TA-state fallback in10003bb4; CL nibble2 may also accept
TA slot47+72c=1/2/3. Companion1000d504 is another necessary test after
1000d044 succeeds. This is a concrete **post-MTS starvation condition**, not
a measured infinite wait PC.

**Observed v0 RAM:** policy+4=0, +8/+c=2/2, +34=1; +934=1; +900/+908 both
`00000000cc0d023a`, +918=`00000000cc0cfb25`, +920=`00000000cc0d2422`.
They remain unchanged through timeout. +934 is incremented at1000af38
at the tail of1000aa80; it is a transition counter, not a heartbeat.
Several timestamp stores happened before the first snapshot. This weakens
“timer never worked”, but does not prove its rate or continued operation.
CPU snapshots are non-atomic and may not expose every cached firmware store.

**Exact candidate wait sites, not live-PC claims:**

- `100221e8` (time sampler called by100223f0): tuple1 reads at
  private+268 and+8 around R0+164/160/164 reads.10022280 retries to10022250
  if the two high samples differ;100222c8 restarts at100221f4 if the
  surrounding tuple1 high samples differ. Private+268=meta+760=1020.
- `1001c9b8` (performance transition): calls1001c88c, then
 1001cabc..1001ccac loops sampling R0 time until a delay threshold passes.
  The enabled-platform branch divides elapsed ticks shifted8 by
  **private+2c4**, selected from the host frequency table. Linux supplies0.
 1001c88c uses tuple0/Fender+1000: poll `(reg&10)==0`, write `state|10`,
  and, for its synchronous modes, poll `(reg&1f)==state`. The shared
  poll engine is10007a94, loop10007b3c, with a timeout/report path which
  itself needs working time. No evidence supports substituting a PMGR ACK
  in RAM for those hardware conditions.
- `1001cec8` has an intentional terminal **self-branch1001d074** after
  platform power/shutdown work. It is not a normal polling return. Reachable
  policy transitions include1000968c ->1001cec8. Reaching that address
  would require a wake/restart lifecycle, not simply a different MTS value.

Available snapshots cannot distinguish these PCs from post-MTS readiness
starvation. A single exact live stall PC and a proven6c8=1 fix remain open.
Do not present a static candidate as a device trace.

### New verified iOS inputs: the missing sample period and frequency table

`...6c974f0` clears policy0..937, copies **0x160 bytes to policy+3c**
from firmware-host-object+450, then stores policy+4 from accelerator
virtual+988. G5P's getter...6ca8a38 is raw `ldr w0,[x0,#c6c]; ret`;
`...6c80028` fills accelerator+c6c from `gpu-power-sample-period`, or from
`gpu-pwr-sample-period-aic-clks /24000`. Saved j71t ADT supplies **16**.
The companion clock count is384000. This is an observed iOS field chain,
not a guessed ready flag. Linux's `g5p_make_init()` leaves policy+4 zero.

Firmware uses policy+4 in deadline arithmetic, e.g.1000c708 multiplies it
by24000 before setting+900/+904, and1000ac28 uses it as a divisor.
Zero makes the sample deadline degenerate. **Inference:** this is a strong
cause of invalid policy evolution; division-by-zero behavior is not proven
to be the particular v0 hang.

`...6c7be80` imports `gpu-num-perf-states=4` plus the off row from
`perf-states`; rows in the saved ADT are `(Hz, raw second column)`:
`(0,450),(340000000,675),(474000000,781),(550000000,815),(723000000,881)`.
`...6c974f0` writes meta+710=max index4, +714=count5, +718..+728=
**0,340,474,550,723**, dividing Hz by1000000. Remaining unused entries
through754 are zero for this table. Do not confuse ADT perf-state-count16
with the active five-row count or write the raw second column as a voltage.

`10003ca4` copies the sixteen meta+718..754 frequency entries to
private+26c..2a8 (meta+758/+75c/+760 separately become private+260/+264/+268).
`1001c838` reads tuple0+1000.low4 and sets
**private+2c4=frequency[index]**.10022150/100221dc select that factor when
6c0/6c8 are1. V7 instead uses the nonplatform timing factor; that is one
specific reason the broad bypass can hide the missing table.

The same iOS initializer supplies control+1028=max power, +102c/+1030=
frequency limits in hundredths of a performance index. `...6c9a74c`
converts host Q16 limits using the frequency table. With the saved ADT and
default full limits these are **3348,400,400**. Policy+7cc/+6fc are400.
Linux leaves these zero too. The filter/gain/clamp inputs at policy+630..708
are also real initialization, including fractions computed from nonzero
filter constants, not optional log fields. See existing auxiliary
`...6c95770.json` and `...6c974f0.json`; do not copy their host pointer values.

Reproduce just the verified timing/table/limit subset without device access:

```
python3 scripts/gpu/firmware-re/policy-profile.py
```

The output is deliberately **not a complete policy blob**. In particular,
the+3c table and filter coefficients are still required for faithful policy.

### iOS power ordering versus a host “power on” DC

Verified host operations, consistent with the existing round4 analysis:

1. Mapping/start...6c93a9c enables provider clock(+568) then power(+570),
   initializes Fender controls and requests Fender+1000=11, waiting bit4 clear.
2. Roots/control init, then policy...6c974f0 (tail-called from roots init),
   then...6c9edcc installs PIO aliases and configured T1 entry/SP, before release.
3. Pre-boot...6c9fc10 requests Fender core power1 via+60; implementation
   ...6ca6544 writes+20000=20 and waits bit5 and performance busy clear.
4. Prepare FIFO/reset/MMU/SLC; boot...6c9f954 requests core power0 then
   GPC state1 through Fender+10. ...6ca4ccc polls+1000.bit4 clear before
   writing(state|10). Then the host waits ALIVE.
5. First DC...6ca05d4 publishes the20-byte slot, executes a barrier,
   Fender+80/readiness or restart handling, then R0+b00=10.

No additional unconditional DC “power on” before cmd8 was found. The
explicit platform helper...6ca0f78 calls provider clock/power and Fender
core/state operations; it is not a missing sixth word in cmd8. Existing
Linux arm/release already reproduces key Fender edges, so adding another
unconditional live clock write is less justified than fixing the missing
policy input bytes.

### Ranked changes and exact confirming lines (all unexecuted here)

Start from v0 (6c0=6c8=1), preserve the exp10 endpoint fix, one fresh boot per
variant. These are research handoffs, not edits to the kernel branch.

1. **Sample-period mismatch — narrowest iOS-derived host change.** In
   `g5p_make_init`, after zeroing/building policy slot4a and before release,
   add only `g5p_put32(policy+4,16)`. Keep6c8=1. Print
   `POLICY sample_ms=16 meta6c8=1 state=<p8>/<pc> transitions=<p934>`.
   Confirmation: `DC consumed cmd=8 consumer=1 producer=1` followed by
   `cache_epoch=1`, then the existing CL NOP completion line. This isolates
   the zero sampling period, but does not validate missing DVFS coefficients.
2. **Platform time/performance factor is zero — restore the recovered
   table/limits alongside rank1.** Before release set meta710=4,714=5,
  718..728=`0,340,474,550,723`; keep later unused entries zero. Set
   control1028=3348,102c=1030=400 and policy7cc=6fc=400. Use the profile
   script output, preserving all addresses and other flags. Log
   `POLICY timing sample_ms=16 max_index=4 count=5 mhz=0,340,474,550,723 cap=400/400`.
   Confirmation: same DC/cache/CL chain with6c8=1, and advancing policy
   timestamps/transition state in RAM. This restores *known* iOS inputs,
   not full DVFS. Without+3c/filter restoration it remains an experiment;
   unrestricted workloads/thermal management are not established.
3. **Policy waits or starvation — localize without broadly disabling PIO.**
   Diagnostic image variant A: the round-2 single call patch at8000a120
   raw14c0e598 ->fe93fe93, with6c8=1. Log
   `FW skip_pre_mts=1 meta6c8=1`. DC success implicates that call but failure
   does not exclude policy (other call sites/readiness remain).
   Separately, variant B: at1000d044/storage10006822 expect05a610f0,
   replace first four storage bytes with **0608229c**
   (`MOV D0Re0,#1; MOV PC,D1RtP`); log
   `FW force_policy_ready=1 meta6c8=1`. This skips that helper's policy call
   and return predicate but retains the pre-MTS call and companion readiness.
   B success with A failure is evidence for post-MTS readiness starvation.
   Neither success pinpoints a unique live wait instruction. Do not combine
   them initially. Confirm each by DC/cache progress absent in matched v0.
   **Tag4/DC only:** neither variant establishes clock/power readiness for CDM.
4. **Performance-transition wait specifically.** If ranks1–2 fail and a
   narrower firmware discriminator is needed, at1001c9b8/storage1000e4dc
   expect1da6, replace only that first halfword with229c (immediate return).
   Log `FW skip_perf_transition=1 meta6c8=1`; confirmation is restored
   DC/cache progress. This skips the full transition routine, not just a
   timeout. It can leave clocks at the boot state, makes no perf request,
   and does not supply the missing factor/table. Do not run a tag3 job on
   this result. A real fix must restore the requested state/ack/timing contract.

All image experiments require raw-byte match, patching BEFORE image load,
updated LDR payload hashes and no live-code patching. Returning from the
intentional1001d074 halt is not proposed. No bypassed path has yet been proved
unnecessary for real Linux compute;6c8=0 is only a device-verified transport
baseline. Lack of DVFS, thermal control, power transitions and guaranteed
compute clocks remains a material risk of retaining any bypass.

### Real CL dispatch: context, work, execution and completion are separate

Use [submission-g5p.md](../submission-g5p.md) for the recovered C→D→W field
table and [command-stream.md](../../gpu-userspace/command-stream.md) for
captured CDM/PDS/USC and context-switch scratch. New firmware cross-checks:

1. **Context slot/catalogue, not context pointer zero.** Host
   ...6c8afcc calls...6c859a4 with engine2, obtains a context index and
   generation byte, then allocates dependency objects and a work object.
   ...6c859a4 publishes via accelerator+920 ->...6c81794 ->firmware+200
   (...6c98a60). The latter's raw stores are catalogue u64 to
   `[firmware+430][index]` (host object slot86), and tag u32 to
   `[firmware+420][index]` (slot84). Do not conflate these with the separate
   runtime tables control+1044(slot87)/1048(slot89).
   With accelerator+4e2 enabled iOS then emits **DC `{3,index,0,0,0}`**;
   waiting is conditional on submission flags. This recovers a real DC3
   payload, but does not prove it is always mandatory before first work.
   Firmware also has a lazy context check described next.
2. **Tag3 reads the context index before execution.**80001940 invalidates
  0x288 bytes;80001954 loads W+8 and calls8000aad4 ->**10017ac4**.
   That compares indexed control+1044 against the cache through root+458
   (slot88); differences call context/MMU helper1002700c and update the
   cache. DC3's1000218c path reads the indexed64-bit catalogue through
   control+1048, calls10019b84 (R0+e140/e148 writes), handles tags and the
   same context helper. Current zero metadata may compare equal and skip
   programming: equality is not evidence a live user VM was attached.
   Full slot84/86/87/88/89 population/ownership still needs reconciliation
   with the prepared VM path before hardware submission.
3. **Channel resources.** Preserve channel+0/+4 ring pointers, the iOS
   context META pointer at+8, +c=4, identity+14 and+18=ffffffff from
   ...6c89420. NOP never dereferenced the context field; this pass does not
   establish the complete pointed-to context layout. Real work calls
  8000aac4 ->**100156b0**, which reads **scheduler+128** and channel+0c to
   form execution metadata. Its scheduler argument is global_CL+76c,
   saved at stack-24hex, so that resource word is **global_CL+894**, NOT
   channel+128. No need to enlarge the64-byte channel follows from this
   load. Provide the iOS context backing, scheduler/resource state and
   scratch; a mapped zero page does not establish a valid context.
   Eligibility/slots at channel24/4c/50/54/58 and global CL+76c..8bf remain
   firmware-owned.
4. **Work W and microsequence.** At least0x288 readable/valid bytes:
   W0=3,W4=0,W8=context index,Wc=dependency META pointer;
   W10..14 context fields; W60/W68/W70 scratch/context/CDM fields;
   W1e0=microsequence META pointer,1e4=rounded length; context-switch
   scratch/PDS state at1ec..244; completion/control fields258..284.
   Firmware80001980 loads W+1e0, installs it in execution state, then
  80001a14 ->8000aaa4 ->**10012fac** queues the execution record. Merely
   scanning tag3 is not proof this sequence ran or a shader launched.
   iOS emits0x32(13words),80000018(5), SKU0x101 sequence,0x18(5),
  0x33(11),40000017 termination with64-byte rounded length. The constants
   at...5940cb8 are10/60 (W offsets);...5940cc0 is004a000a/1, used by
   the SKU helper. Preserve the actual relative backlink and valid stamp
   addresses; no zero microsequence is a valid replacement.
5. **GPU resources and mappings.** Live per-VM GART/catalogue, mapped CDM
   stream and its terminate address, USC/PDS code and data, argument/output
   BOs, context-switch scratch (capture uses0x6068 bytes), scratch pointers
   +4/+8/+c and packed context-switch PDS state. CDM terminator address
   goes in W+23c, not W+60. Per-core optional resources W+78 onward may be
   zero only under the recovered disabled-resource branch of...6ca90cc;
   that does not remove the mandatory context-switch scratch. Code heap B
   remains a hardware base-programming question: init+10/+18=300000000
   is verified, actual resulting USC/PDS registers are not verified here.
6. **Power and completion.** Keep valid T1 PC/SP and mappings and let
   firmware start T1 when needed. Establish readiness/compute clocks first.
   Use a real microsequence-written completion stamp and verify changed
   output bytes from the shader; global1/1, scan1 or IRQ alone is insufficient.
   Keep type4/7/8 fault monitoring and quarantine until park. Render-specific
   TA/3D parameter buffers are not established CL prerequisites by this pass.

Recommended kernel handoff order: reconcile live context tables and full
channel allocation; build and validate W/microsequence/address graph offline;
resolve policy/clock readiness; then submit a single captured or independently
validated CDM dispatch. Confirming line for that future experiment:
`CL COMPUTE stamp=<changed expected value> output=<expected bytes> faults=0`.
This line is a proposed criterion, not an observed result.

### Additional firmware-written words to dump and validation

Keep the round-2 dump contract. Add explicit labels for unchanged zeros:

- policy+4 (host timing input),+1a8 (last utilization sample),+1ac..1e8
  (per-state accumulation),+1ec..62b (state counters),+8f0..934 and especially
  **+934 transition count**. Timestamp/counter deltas may distinguish continuing
  policy activity from a frozen CPU-visible snapshot; none is a certified heartbeat.
- meta+710..754 (host frequency inputs), policy+3c..19b (host copied table),
  control+1028/+102c/+1030/+1034/+1038/+103c/+1040 (host limits/overrides).
  These are inputs, not firmware progress markers.
- All context slots84/86/87/88/89, channel0..63, CL global+894/+898
  and execution descriptors,
  W+1e0..287 and microsequence/stamp bytes. Preserve the object-relative
  names; do not turn a GPU VA into a META pointer by truncation.
- The decisive live-only time factor private+2c4 and cached timing/table
  fields private+260..2a8 live at8000a000-relative local RAM. They have no
  host image-mirror address. No new slave-port reads are authorized here.

Passed `check-decoder.py`:7 GP anchors,4 DCACHE anchors,2 reserved checks,
**14 raw firmware anchors** (8 new policy/readiness/tag3 anchors).
No ISA expansion changes were made; UNKNOWN forms and earlier limitations
remain. `policy-profile.py` reproduces the saved-device timing/table subset.
No live stall-PC sample, full policy recreation or real CDM execution was
performed. Those are explicit remaining boundaries of this round.

## Round 4 — byte-level compute handoff, captured jobs, firmware completion

2026-10-22. Research only, no device access, kernel edits or commits. Kernel
round13 owns `a9/gpu`. Supplied device baseline now includes exp11 DC3 slot1
attachment with an empty VM, in addition to exp10 tag4/endpoint1 completion.
**That does not verify the mappings or resources needed by tag3.**

The new evidence is one (`round4-one-capture.txt`, not published) and
store (`round4-store-capture.txt`, not published), generated using strict sparse reads by
`python3 scripts/gpu/firmware-re/compute-captures.py`. Both iOS jobs have
status4/error=none/rc0. `one` changes exactly one output dword from deadbeef
to12345678; `store` changes all64. The script checks the entire256-byte output.
The selected firmware extract (`round4-firmware-extract.txt`, not published) preserves65
instruction anchors with raw bytes and both address spaces.

**Capture boundary:** out5 contains the **0x1f0 userspace record**, not the
kernel-created0x288 work allocation. There is **no aligned 00000032 or
40000017 anywhere in either job's resident post pages**. Thus neither the
firmware microsequence nor a validated tag3 W can be extracted from these
dumps. A 3/0 header search has four hits per job, in resource/buffer metadata;
none establishes W. The C→W annotations below are a **static reconstruction**,
never captured kernel memory. This is the key limit on a first-try guarantee.

### Address notation and full host write map

All offsets below are hexadecimal byte offsets, little-endian, **unaligned
u64 stores are intentional**. `W` is the 32-bit META address of a0x288-byte
allocation; `C` is userspace record+38; `D` is the kext descriptor; `CH` is
channel state; `G` is global CL state (slot49). `M(x)` means the kext's
GPU/host-to-META conversion, **not truncating a GPU VA**. `S=D+100` is the
submission sequence. Addresses of CDM/PDS/scratch are GPU VAs or packed
hardware state, not META pointers.

Verified host sources: shared Ghidra `fffffff006c8a98c` and
`fffffff006ca8da4`; auxiliary `...6ca90cc`, `...6ca9218`, `...6cb68dc`
(shared database has no function start for6ca90cc). Existing text caches in
`build/gpu/round13-re/` and `build/gpu/ghidra-aux-results/` corroborate them.
Below is the union of their explicit W writes, including fields omitted in
round3. A host store is not by itself proof the firmware reads that field.

| W offset / width | iOS write / minimal-job interpretation |
|---|---|
|000 u32,004 u32|3,0|
|008 u32|D+410 context slot; exp11 establishes slot1 attachment only|
|00c u32|M(D+130 dependency object)|
|010 u32|low32 C+40 =40200003 in both captures|
|014 u32|D byte408: value2→3, value1→2, otherwise0; not supplied by C|
|060 u64|C+58 =10004a020, scratch-tail pointer|
|068 u64|C+38 =0000043000000420, packed state|
|070 u64|C+00 =10005c000, CDM stream|
|078+8*i u64|per-core resource array, count accelerator+364; if D+388=0, each entry0; otherwise `(D+380+i*(u32)(D+388)) OR ((D+38c)<<1 OR 1)`|
|1c8 u64|D+380 resource base|
|1d0 u32|low D+388 resource stride|
|1d8 u32|D+38c resource flags|
|1e0 u32|M(microsequence allocation+selected block offset), set by emitter|
|1e4 u32|microsequence length rounded to64, set by emitter|
|1e8 u32|0; mutable compute/context state, tested by start handler|
|1ec u64|C+08 =100044000 scratch base|
|1f4 u64|C+10 =100044004|
|1fc u64|C+18 =100044008|
|204 u64|C+20 =10004400c|
|20c u64|D+340; same descriptor source as W+60|
|214 u64|D+348; same descriptor source as W+23c|
|21c u64|C+28 =0003000000000400|
|224 u32|low C+30 =50800005|
|22c u64|C+48 =0003002000000740|
|234 u32|low C+50 =00a00004|
|23c u64|C+60 =10005c0c4, address of terminate word|
|244 u32|low D+338; same descriptor source as W+234|
|248 u32|0|
|24c u32|zero-extended byte D+370|
|250 u32|D+374 instrumentation identifier|
|254 u8,255 u16|D+408, D+409..40a (unaligned); scheduling/generation metadata|
|258 u32|M(first stamp-buffer base + accelerator+420) + stamp-manager offset|
|25c u32|M(second stamp-buffer base + accelerator+424) + stamp-manager offset|
|260 u64|qword returned by D virtual+d8; low32 is completion stamp value, high32 stamp identity used by manager|
|268 u32|low channel-host-object+38 (identity, not CH pointer)|
|26c u32|boolean D+48 !=0|
|270 u32|D+100 =S|
|274 u32|D+40c|
|278 u64|0, timestamp/result storage addressed by opcode18|
|280 u32,284 u32|M(D+148), M(D+150), or0 when corresponding host pointer null; optional timestamp destination pointers|

Unwritten bytes/ranges:018..05f, unfilled resource-array tail through1c7,
1d4..1d7,1dc..1df,228..22b,238..23b,257. None is proved a mandatory
nonzero input. Zeroing the allocation is a conservative Linux construction,
**not a recovered memset in this builder**. Resource count/array capacity
and all transitive firmware reads are not certified by the partial decoder.
Do not infer the entire078..1c7 range is needed simply from its size.

### Firmware reads and the actual CDM register path

Static MINIM evidence uses execution PCs. The existing decoder retains its
UNKNOWN/extended-instruction limitations; these are reviewed direct
loads/stores/calls, not a complete transitive read-set proof.

* Tag3 `80001940..80001a14` invalidates0x288 bytes, reads **W+8** for
  `10017ac4` context selection and **W+1e0** for execution-record micro-PC.
  The outer tag read is800018e0. After enqueue it also reads **W+270** at
  80001b50 for scheduler/accounting. Other W fields enter through pointers
  embedded in the microsequence rather than direct outer-handler loads.
* Opcode32 table entry80004b84→80004bc4 calls **80007390**, passing
  opcode arguments+4. It calls `80008d24(arg0,arg1)` where the host supplies
  arg0=W+10, arg1=W+60. This establishes the register chain:

| W read | MMIO write relative to firmware R0 MMIO base |
|---|---|
|010 u32,014 u32|4c0,4c8 respectively|
|060 u64|498 u64|
|068 u64|4b8 u64|
|070 u64|480 u64 (**CDM stream address**)|
|078,080,088,090,098,0a0 u64|selected-resource port11040 u64, with selector11ff8 set0,1,2,3,4,5|

  These six resource entries are read even if zero. The final entry is
  loaded at80008eac..8ecc. Hence zeroing only the first entry is insufficient.
* Opcode32 arg8 points to **W+1e8**. If its first word0, it takes the fresh
  path: `80008f30` writes **R0+478=1** (compute kick), then10018358 reads
  block+34/+38/+3c = **W+21c/+220/+224** to R0+4a8(u64)/4b0(u32).
  It sets `[G+728]=1`, `[G+720]=0`, `[G+72c]=context_slot`.
  If W+1e8 nonzero, **10018698** first loads block+54/+58/+5c =
  **W+23c/+240/+244** into4a8/4b0 and requests **R0+490=1**, polling bit0
  (with recovery escape1000d138); then follows the common state path.
  This distinguishes fresh dispatch from restore. Do not seed W+1e8 nonzero.
* `100156b0` builds execution metadata as
  `scheduler+128 OR (CH+0c <<30)`, where scheduler=**G+76c**, so the source
  is **G+894**. The enqueue allocator10012fac reads its free mask at+2c;
  if empty it sets allocator+28=16 and returns0 without allocating. This
  is **resource backpressure**, not evidence of a bad shader. Firmware owns
  this initialized pool; do not seed an isolated ready bit in a zero pool.

The0x288 layout is therefore host-complete for these builders but
**not a claim that every firmware error/restore/instrumentation branch has
been decoded**. W+c,1c8..1d8,1ec..214 and metadata tails have host provenance;
their complete transitive use remains a boundary of this pass.

### Exact uninstrumented microsequence recipe

The kext initializer6cb66d8 selects a free stream block and sets its cursor;
it emits no initial opcode. With optional instrumentation disabled, define
`w=W_META`, `g=G_META`, `ch=CH_META`, `ctx=W[8]`, `gen=D.byte409`,
`q=D.u32[40c]`, `id=stamp_descriptor.high32`, `ident=W[268]` and `s=W[270]`.
The following are **u32 words**, not GPU control-stream packets:

```
offset  words
000     00000032 w+10 w+60 g+720 ch ctx id q ident w+1e8 gen 0 s
034     80000018 w+278 w+280 w+280 s
048     00000101 004a000a 00000001 00000001
058     00000018 w+278 w+280 w+284 s
06c     00000033 g+720 ch ctx w+1e8 gen 0 s W[25c] W[260] ffffff94
098     40000017
09c..0bf padding (allocation valid/readable through0bf)
```

W+1e0 is this block's META pointer; W+1e4=c0. The type3 lookup tables at
`fffffff0059410c0/d0/e0` read280/278/284. `...5940cb8` reads10/60 and
`...5940cc0` is qword00000001004a000a, verified by a fresh24-byte
Ghidra read from5940cb8. The SKU sequence is **one-word opcode101**, then
**three-word opcode004a000a with operands1,1**; it is not one four-word
opcode101 instruction.
The finish backlink is **-0x6c =ffffff94**, from finish opcode at6c back to
start at0. The builder's expression is block_start-current_cursor+2c;
current_cursor is just after the44-byte finish packet. If optional16-byte
instrumentation packets are inserted, recompute the backlink/length.

Opcode dispatch masks `word &3f`, subtracts1, range checks<=32hex and uses
table800049fc with8-byte entries. Opcode1 (thus101) goes to80005d6c;
opcode18 to80004ef0; opcode17 to8000504c; opcode32/33 to80004bc4/4b94.
The high bit30 of40000017 removes the current execution slot and terminates;
unsupported opcode words can be skipped by advancing4 rather than yielding
a useful format error. A zero stream cannot substitute for this recipe.
Opcode101 waits on a mask in the interpreter's pending-event word and clears
the satisfied bits (80005d6c); the mask-extraction instruction remains UNKNOWN
in this decoder. The following004a000a dispatches to80005c08, which polls
`(read(R0+(opcode>>12)) & operand0)==operand1`: here **R0+4a0.bit0=1**.
On mismatch800060d0 calls recovery predicate1000d138 and, while it returns0,
loops directly to the register read at80005c34. If recovery returns nonzero,
it advances past the poll. This is an actual firmware spin, not just a
channel scan waiting for a future notification.
Thus these bytes contain **two actual wait points** before finish33; retain
both. Their words and the MMIO-poll predicate are established; the exact
interrupt-to-pending-mask plumbing is not yet fully traced.

### Captured CDM/PDS/USC and scratch

Both jobs: CDM GPU10005c000, terminate GPU10005c0c4. Their seven28-byte
packets include the argument loader, user launch, and **five driver helpers**,
including two Apple type3 packets. A two-packet Linux job omitting those
helpers is an experiment, not a capture-faithful replay.

| Job | CDM+0 loader code/data offsets | CDM+1c launch code/data offsets | USC offset | Shape |
|---|---|---|---|---|
|one|2380 /30040|2400 /2480|22c0|1 group ×1 thread|
|store|23c0 /30040|2440 /24c0|22c0|1 group ×64 threads|

All code offsets relative to common B; the capture shows the relative chain,
while absolute B/programmed base remains the prior kernel-side question.
Launch PDS data starts22c0,10 (8 USC temps), then `80000000,0` for one or
`90000000,80000000` for store. Loader data now sources **10004a068 and
10004a078**, counts2 and1 dwords, common destinations0 and2. Do not reuse
run1 k_fadd's sources10004a068/10004a088 or counts6/3 for these jobs.
The output GPU pointer100000200 is captured at CPU8d1e12068/8d3e12068.
Full decoded packet words and small PDS/USC excerpts are in the text extracts.

Both C records request scratch base100044000 and field pointers+4/+8/+c,
tail10004a020. Keep the **0x6068-byte** allocation established by the
userspace context-switch builder, including tail fields; do not use one
page. Captured context PDS data at B+30000/+30020 includes these scratch
addresses. Exact initial scratch contents across the full allocation are
not established: CPU scratch mapping is inferred and pre pages are sparse.
No claim that an all-zero full scratch allocation is iOS-identical follows.

### Firmware-side completion and retry: what to poll

**Verified store:** opcode33 passes arguments+4 to **80007500**. It reads
`R0+4a0 &2` via80008f00. When clear, it reaches80007564..7590:

```
[G+728] = 0
[G+724] = 0
[G+72c] = 0
*(u32 *)finish_arg[7] = finish_arg[8]
// with the recipe above: *(u32 *)W[25c] = W[260]
```

This is a **firmware CPU/META store**, not a USC shader store. The shader
separately writes12345678 to the output BO. Poll a valid META-visible stamp
at W+25c for expected low32 W+260, with a distinct initial value and proper
CPU/device visibility; keep W+258 separate. The recipe's opcode33 qword
arguments8/9 are copied from the **unaligned W+25c qword**, deliberately
combining the pointer at25c and value at260. Do not substitute the whole
W+260 descriptor qword or signal a fence on a ring/IRQ delta.

When R0+4a0.bit1 is set, it calls1001e368 with CH and generation, translating
and reading a GPU qword through **CH+24/+28**, comparing its low word with
**CH+2c** after masking low2 bits, and byte(highword) with **CH+30**. On a
successful comparison it also reaches the completion store. On mismatch
it calls10013df8 (which sets the W+1e8 block's first word1), clears the
G+724/+728/+72c state and returns1. The interpreter adds finish argument+24
(the signed backlink) to the current opcode PC and revisits start32.
Thus **a non-advancing stamp can mean context/retry, not just a hung USC**.
The exact CH-context setup producing those comparison inputs is not fully
recovered. CH+8=an arbitrary zero page does not implement it.

**Round-5 correction:** the paragraph above misidentifies the argument alias.
It is `H=W+1e8`, with the context-slot low byte as translation selector,
not CH and generation. See round 5 for the corrected field-by-field chain.

After normal finish it calls1001ede0(engine2), setting private pending bit2
and writing R0+b00=2, a firmware scheduling kick. This is not itself the host
completion event. The host's6c99788 type1 event consumes four bitmaps at
event+4/+8/+c/+10, selecting stamp identities0..127. Preserve/drain all44
event bytes and use actual stamp/output for completion; the full connection
from stamp identity to the firmware bitmap publisher is still unresolved.

### Failure evidence and limits on fault decoding

Capture all before publication and at timeout: ring ctl0/20/30/40, CH0..63,
G+720..72f/+76c..8bf, W0..287, microsequence0..bf, both stamp targets,
output BO, every44-byte pending event, and output/trace rings from round2.
These are RAM observations; no additional live slave-port reads proposed.

| Symptom / path | Evidence and interpretation |
|---|---|
|Global notification consumed, channel scan unchanged|Earlier transport/policy/eligibility gate, not proof tag3 entered|
|Enqueue pool mask0, allocator+28=16|10012fac has no execution slot; returns0 and outer handler leaves outstanding work; do not diagnose PDS|
|G+728=1, G+72c=slot, stamp unchanged|Start32 reached its state stores; GPU completion/restore/policy may still block|
|W+1e8 becomes1, G+728 cleared, stamp unchanged|Consistent with finish33 context/retry path; inspect CH+24..30 and backlink; not a unique fault signature|
|Stamp advances, shader output unchanged|Firmware finish reached; code/data VA/cache/launch/helper assumptions still unproved; reject shader-success claim|
|Event type4|Recovery. Host reads +c engine/index (-1→80hex default), +10 flag, +18 recovery code, +20/+24 detail into accelerator recovery state|
|Event type7|Page-request/fault handling. Host consumes raw words+4/+8/+c, reverses the two32-bit halves of the first qword for its internal queue; do not label raw event+4 as a directly usable u64 fault VA|
|Event type8, +4=0|Host labels MEMFAIL; +c identifies channel, +10 additional value|
|Event type8, +4=1|Host labels channel kill/DMKILL; +8 subtype, +c channel identity, +10 additional value|

**Unresolved:** exact firmware fault-address/status stores and event publisher
branches distinguishing unmapped CDM, missing PDS/USC page, illegal PDS and
USC timeout. The host decoder establishes the records above but does not
justify assigning a specific event8 subtype to a bad PDS. Hardware may stall
until recovery; absence of an event is not absence of a fault. Log raw packets
and all relevant RAM rather than inventing a fault code. The requested
complete error/restore read set cannot honestly be certified with this
partial MINIM decoder in this pass.

Two firmware publisher anchors supplement the host interpretation:
**80001150** constructs type8, stores event+4 from D1.7, +8 from D0Ar4,
+c/+10 from its engine object+894/+890, then calls8000a4f4→1001fb9c.
**80003264** constructs type7, obtains event+4 through8000aa04, copies
event+8 from its current channel+8 and event+c from channel+44 (then
increments that word), and publishes at800032e0. The containing routine
sets an engine-state+824 pending word before publishing and tests it
afterward: logging a page request without servicing it may leave work
blocked. Object provenance and upstream cause bits still need tracing.
The common publisher1001fb9c reserves a256-entry/44-byte ring, spins if the
next producer equals consumer (1001fbec), copies the record, publishes
ctl+10 at1001fc94, and raises host notification. **A full event ring is
itself a firmware wait condition**; drain it during the dispatch experiment.

### Handoff boundary and reproducibility

The kernel agent can now implement the exact **0xc0 uninstrumented
microsequence**, W layout and firmware stamp target, and can compare a job's
CDM/PDS/output against the supplied extracts. Still experimental: absolute
code-base programming, faithful CH/context completion mapping, pool/resource
initialization, scratch initialization, omitted Apple helper semantics and
actual compute power readiness while6c8=0. Exp11 empty-VM DC3 does not close
any of those. No first-real-dispatch success is asserted here.

Checks run offline:

```
python3 scripts/gpu/firmware-re/compute-captures.py
python3 scripts/gpu/firmware-re/check-decoder.py
python3 scripts/gpu/firmware-re/inspect.py 80004990 80004c08
python3 scripts/gpu/firmware-re/inspect.py 80007390 80007658
python3 scripts/gpu/firmware-re/inspect.py 80008d24 80008f48
python3 scripts/gpu/firmware-re/inspect.py 10018358 100183e0
python3 scripts/gpu/firmware-re/inspect.py 10018698 10018728
```

No firmware binaries copied into docs. No shared Ghidra changes/renames,
device operations, kernel/workspace commits or branch modifications.

## Round 5 — corrected retry object, scratch contents, kernel capture route

2026-10-22. Research only. No device access, kernel-branch edits, commits,
reboots, kernel patches or NVRAM/syscfg operations. Existing concurrent
workspace edits were preserved. Evidence is the same 20H350 kernelcache
(SHA256 above), shared Ghidra ARM64 analysis, generated MINIM listings, and
the strict `out5-20261022T184530Z` sparse userspace captures. **Verified** here
means static instructions or captured bytes; it does not mean Linux ran it.

Reproduce the compact round-5 evidence (`round5-evidence.txt`, not published) with
`python3 scripts/gpu/firmware-re/round5-evidence.py`. It checks seven raw
instruction anchors and both complete post scratch allocations. The existing
round-4 host W write table remains the field-by-field construction contract;
the important correction is its downstream interpretation, below.

### Resolved: the completion/retry check is against scratch, not CH

Let `F` point to the first argument **after** opcode33, so F+0=G+720,
F+4=CH, F+8=context slot, F+c=W+1e8, F+10=generation,
F+14=0, F+18=sequence, F+1c=stamp pointer, F+20=stamp value.
This follows the actual emitter `fffffff006ca8da4`; do not include the
opcode itself when indexing the handler's argument pointer.

At80007540 the handler loads D0.5 from **F+c**. At800075f0 it loads
D0Ar2 from byte **F+8**; at800075f8 it passes D0.5 as D1Ar1 to
8000a6b4→1001e368. Thus the call is:

```
H = W + 0x1e8
matches = 1001e368(H, context_slot & 0xff)
```

The old round-4 description `(CH, generation)` is wrong. The following
mapping is verified by the emitter, caller, callee and C→D→W copies:

| Callee read | Actual W field | Host provenance / meaning |
|---|---|---|
|H+24 u32 / H+28 u32|W+20c / W+210|D+340 qword = C+58, GPU scratch tail `10004a020`|
|H+2c u32|W+214|low D+348 = low C+60, CDM terminate address `0005c0c4`|
|H+30 u32, compared to byte(high result)|W+218|high D+348 = high C+60, here `1`|
|H+4 / H+8 (retry helper)|W+1ec / W+1f0|C+8, GPU scratch base `100044000`|
|H+5c u32 (retry helper)|W+244|D+338 low = C+50, context-switch packed state|

1001e368 translates the H+24/+28 GPU pointer for **8 bytes** through
1001f170, loads the qword at1001e3c0, releases/maintains the translation
through1001f86c, then tests `(low & ~3) == H[2c]` and
`(high & 0xff) == H[30]`. It does not read CH+24..30 on this path.
The second argument is a VM/context selector, not a timestamp generation.
The precise translation helper's complete error branches remain undecoded;
do not implement it as CPU dereference of a GPU VA.

On mismatch80007614..30 passes **H, CH, slot, sequence** to
10013df8. That calls1002701c→80008f48 with H and slot-low8 and then
sets H+0=1. 80008f48 translates/reads **four bytes at the scratch base**
H+4/+8 and incorporates its low bits into H+5c (`W+244`, bits12..20
after the shift/mask), unless its initial context predicate skips the update.
Start32 subsequently sees H+0=1 and restores using W+23c/+240/+244.
This adds a concrete transitive scratch read missing from round4.

**Consequence:** do not manufacture CH completion fields or prefill the
scratch tail with the expected terminate address to defeat retry. That would
turn a diagnostic into a false success. Dump the actual **8 bytes at C+58**,
the first scratch dword, H0..5f, and the normal stamp. A zero tail after a
successful non-preempted job is possible: the tail check is conditional on
R0+4a0.bit1. The existing capture's zero tail therefore does not contradict
its successful shader output. The hardware/driver-helper writer of this tail
has not been proved; a particular CDM helper must not yet be named its owner.

### Scratch, resource inputs, and which constructor owns each object

`AGX::G5::ContextSwitcher::setupComputeCommand` at userspace207cc6250
allocates **0x6068 bytes aligned32**, reserves0x6087 bytes for alignment,
calls a two-argument clear-like stub on the **first0x20 only**, and computes
tail `(GPU_base+602f)&~f`, here base+6020. Its two patchable PDS data
segments use base and base+20. Source decompilation:
`build/gpu-userspace/cs/decomp-ann/207cc6250_AGX__G5__ContextSwitcher__setupComputeCommand.c`.
The stub's exact imported identity is not established by the stripped stub
table; captured zero header bytes independently establish the result here.

**New byte verification:** both jobs' candidate CPU scratch mappings have
strictly resident **all0x6068 post bytes**, not just the head. All are zero
except dwords **+20=1,+24=1,+28=1**. All64 head bytes, including those three
ones, are also strictly resident and identical in **pre**. Pre beyond the
resident prefix is sparse, so a fully zero initial tail/body is still an
inference. CPU→GPU scratch association is still inferred from the captured
allocation/record relationship; these are not newly captured kernel pages.
Safest capture-based fresh allocation: zero0x6068, set those three dwords1,
keep the base+6020 tail zero, and retain both context-switch PDS programs.
Do not seed completion data in the tail.

Host constructors/provenance, including the separate resource input:

| Builder | Verified responsibility |
|---|---|
|fffffff006c8b280|C→D copies: C00..60 supply D2e8..348; Cb8→D408, C1a0/1a1→D409/40a, C1a3→D40b. Separate input R: R00→D380, R18 qword→D388 (stride low32, flags high32), R24 u32→D390, R08→D398, R10→D3a0. R is **not** W or C; do not infer these from a C-only capture.|
|fffffff006c8a98c|Common W writes, D2d8=CPU W / D2e0=GPU W, stamp-manager conversions, optional timestamp destinations, then virtual148 hardware fields /140 SKU emission /c0 publication. See complete round4 W table.|
|fffffff006ca90cc|W10/14,60/68/70; per-core W78 array. D388 stride0 explicitly writes each selected entry0. Nonzero stride creates address/flag entries. This does **not** allocate backing resources.|
|fffffff006ca8da4 /6ca9218 /6cb68dc|Start/finish SKU, two waits, timestamp packets, terminator and64-byte rounded length. Current SKU block CPU is `SKU.u64[28] + SKU.u32[5c]`; current cursor=SKU.u64[68], allocation stride=SKU.u64[50], channel-host+a8→SKU.|
|fffffff006c8b580|Descriptor-side dependency/control CPU object D128 (GPU D130): zero+20 qword,+28 dword,+70..270 qwords stride10, set corresponding +78..268 dwords f; set+48 qword all-ones,+50 dword all-ones, clear+80 etc through the loop expansion, clear+278, increment+30. It is **not** the GPU0x6068 scratch. Dump all0x280 rather than replace with a generic zero page.|
|fffffff006c88910 /6c89420|Allocate channel/context/ring backing and reset it. CH CPU=channel-host+58; CH GPU=host+80. CH00=M(host88), CH04=M(host90), CH08=M(host98), CH0c=4, CH14=host48 identity, CH18=ffffffff; remaining CH bytes through60 explicitly zeroed on reset. Ring control CPU=host60, pointer ring CPU=host68; ctl50=host50 capacity; ctl00/10/20/30/40=0. Firmware subsequently owns runtime mutations.|
|fffffff006c859a4 →6c81794 →6c98a60|Context-slot/tag/catalogue publication, as round3. This is independent of the corrected H scratch-comparison structure. A valid VM remains mandatory.|

The dependency initialization row is a selected explicit-store inventory,
not a complete new struct definition; retain the original host offsets and
dump the full object. Some host virtual targets are only defined in the
auxiliary analysis; `FUNCTION_NOT_FOUND` from shared Ghidra for those gaps
is not proof the function does not exist. Existing auxiliary decompilations
in `build/gpu/round13-re/` were used alongside fresh shared-server reads.

**Resource default remains conditional:** six W78..a0 qwords are read by
80008d24 regardless of being zero. D388=0 is a verified supported writer
branch, so all six zero is the conservative minimal disabled-resource
experiment. Neither that branch nor the userspace C capture proves the
minimal captured iOS job selected it. W1c8/1d0/1d8 must agree with the
selected branch; choose zero together. The firmware scheduler's initialized
execution pool at G+76c is a different object; never replace it with zero
or set a lone ready bit. Pool mask0 / allocator+28=16 means backpressure.

### Absolute code base, helper semantics, faults, and read-set boundary

**Absolute base is still inferred.** Host getter6caf7dc/range table59426d8
and init writer6c96be8 verify two init qwords10/18=`300000000` and the
4GiB VA window. No final init→USC/PDS register chain or per-context override
was verified in this round. No new execution-base MMIO write is proposed.
The superficially similar firmware boot-control+10/+18 copies at10001328
and10003ef0 are **boot-control** fields, not evidence of those code bases.
Use B=300000000 only as a labelled replay hypothesis, with all code/data
placed relative to it; leave a user-facing verification capability clear.

**Five helper packets are still required for faithful replay.** Their exact
CDM words are in round4-store-capture.txt. This pass connects scratch fields
to restore/completion but does not prove which helper writes them or the
semantics of Apple's c0000003 packet. Retain all seven captured packets
for a capture-faithful experiment; the userspace two-packet first-dispatch
artifact remains a separate minimal experiment. Do not infer helper
optionality from a successful offline byte validator.

**Fault attribution remains bounded.** Type4 recovery, type7 page request,
and type8 MEMFAIL/DMKILL records are established in round4. A bad CDM VA,
missing PDS/USC page and an illegal instruction cannot yet be uniquely
distinguished from those event types. Log raw44-byte records, context slot,
CDM/PDS/USC mappings and both stamps; preserve page-request state instead
of acknowledging a fabricated resolution. A timeout or retry is not a
unique PDS failure signature.

**Transitive read-set progress, not completeness:** round4 covers direct
tag3 W8/1e0/270, W10/14,60..a7 and fresh/restore hardware words. This round
adds H+24..30 (`W20c..21b`) and its GPU8-byte target, H+4/+8
(`W1ec..1f3`) and its GPU4-byte target, plus H+5c (`W244`) on retry.
Required external state includes context/tag/catalogue tables, CH and ring
control, G+720 state, scheduler/pool G+76c..8bf, SKU and both timestamp
destinations when enabled. Cache, MMU translation, recovery, instrumentation
and scheduling have additional branches with UNKNOWN instructions. There
is **no certified complete transitive read set**; allocating only the
direct-read subset remains unsound. Keep full W0x288, SKU rounded length,
0x6068 scratch, dependency0x280 and firmware-created scheduler extents valid.

### Read-only iOS kernel capture: supported route and limits

The source audit found a concrete tfp0 route, so kernel capture is **not
established impossible**. It is untested on this installed device.

| Candidate | Evidence / decision |
|---|---|
|palera1n tfp0|PongoOS KPF patches the pid0 rejection in task_for_pid and the task-conversion/port-to-map restrictions. Upstream ships a task_for_pid(0) test. Probe this first with appropriate entitlement and sudo. These sources do not prove the installed PongoOS revision or patches.|
|host special port4|Fallback probe only. No evidence palera1n installs kernel_task there. A returned port alone does not prove read capability.|
|jbctl / p1ctl|Audited palera1n jbinit exposes **p1ctl kbase/kslide**, not arbitrary kread/kwrite. No palera1n jbctl kernel-read CLI was found in that source tree. Dopamine's jbctl API is not evidence for this installation. Query installed help/package inventory; never invoke bootstrap/revert/reboot subcommands.|
|/dev/kmem, /dev/mem|Existence/access unknown without device access. Inventory only; this tool does not open them or scan physical memory. sudo alone does not create these devices.|
|AGXDeviceUserClient|Actual vtable6ff33d8+590 points to IOUserClient::clientMemoryForType at777a6768, returning e00002c7 unsupported. Its legacy method table getter6c9280c serves100..10a then falls back. This is **not** proof every IOGPU/AGX shared-client route is unsupported, but no validated W/SKU dump selector was recovered. No selector fuzzing.|
|IOSurface/IOAccelerator shared buffers|Useful for CDM/PDS/USC/scratch userspace captures already obtained. No demonstrated alias of kernel-created W/SKU. A GPU-visible BO is not automatically CPU-mapped into a client. The absence of W/SKU in out5 remains confirmed.|
|AGX dump/sysctl/boot args|A real dumpFirmwareRingState string exists at5944d44, xrefs6c98d8c/db4; nearby code validates ring indices. This does not expose arbitrary W bytes. No read-only sysctl yielding W was established. Enumerate names only; no sysctl writes, panic triggers or boot-arg changes.|

Pinned public sources (installation/version must still be checked):
[PongoOS tfp0 patch](https://github.com/palera1n/PongoOS/blob/e98323f8a09abd80fc4cbcd74dee023b91a1ec22/checkra1n/kpf/main.c),
[port/map patch](https://github.com/palera1n/PongoOS/blob/e98323f8a09abd80fc4cbcd74dee023b91a1ec22/checkra1n/kpf/mach_port.c),
[upstream tfp0 test](https://github.com/palera1n/PongoOS/blob/e98323f8a09abd80fc4cbcd74dee023b91a1ec22/checkra1n/unit-test/tfp0.c),
[p1ctl command table](https://github.com/palera1n/jbinit/blob/df653b8a4cc30cdc3bcec65d4aff0ed7b598da22/src/payload/p1ctl/main.c).
Do **not** run the upstream tfp0 test: it allocates/writes kernel memory.

New tool: `scripts/gpu/firmware-re/kernel-read.c`, build script and entitlement
plist beside it. Cross-compiled iOS arm64 min15 with Wall/Wextra/Werror and
signed with ldid. Imported kernel interfaces are task_for_pid,
host_get_special_port, mach_vm_region_recurse and mach_vm_read_overwrite;
there is no mach_vm_write/allocate/protect, IOKit selector call or physical
memory access. Output files use O_EXCL and0600; nothing is overwritten.

Modes: `probe` obtains a port only; `regions` enumerates **metadata only**;
`read` reads an explicit KVA, at most1MiB; `find` searches an explicitly
identified **kernel heap RAM** range, at most256MiB, for three exact slid
vtables (accelerator/channel/descriptor); `descriptor` and `channel` capture
the recovered object graphs above. find additionally rejects non-RW or
external-pager mappings and stops after eight read errors. **VM protections
and external_pager alone cannot certify RAM**: the orchestrator must identify
the heap mapping before supplying it. Do not pass a whole physical map,
MMIO mapping, guessed DMA address or `0..UINT64_MAX`. No automatic safe
heap locator or tfp0 live validation is claimed.

Exact staged orchestrator procedure (none run by this agent):

1. On the Mac, build and transfer ordinary files:

   ```sh
   sh scripts/gpu/firmware-re/build-kernel-read.sh
   scripts/ipad-ssh.sh 'mkdir -p /var/mobile/linux-a9/kernel-capture'
   IPAD_SCP=1 scripts/ipad-ssh.sh build/gpu/round5-fw-re/bin/kernel-read \
     scripts/gpu/firmware-re/kernel-read.plist \
     mobile@127.0.0.1:/var/mobile/linux-a9/kernel-capture/
   ```

2. Open an interactive device shell for sudo's password, **without putting
   the password in a command/log**. The wrapper above starts the USB tunnel;
   use `ssh -t -p 2022 mobile@127.0.0.1` for this interactive step. Run:

   ```sh
   cd /var/mobile/linux-a9/kernel-capture
   chmod 755 kernel-read
   uname -a
   command -v p1ctl jbctl ldid
   ls -l /dev/kmem /dev/mem
   dpkg-query -W '*palera1n*' '*jbinit*' '*jailbreak*'
   sudo ./kernel-read probe
   sudo ./kernel-read regions > regions.txt 2> regions-errors.txt
   ```

   Missing package/device names are inventory results, not a reason to
   install/patch anything. If the host binary was unsigned, sign only this
   file with `ldid -Skernel-read.plist kernel-read` before probing. If
   present, `p1ctl kslide` and `p1ctl kbase` provide the current boot's
   addresses. A slide of zero is legitimate; record it explicitly.

3. Verify actual read capability against the known kernel header KVA from
   that boot: `mkdir header`; `sudo ./kernel-read read "$KBASE" 0x1000
   header kernel-header`. Pull it and require feedfacf / ARM64 Mach-O;
   validate the OS build and kernelcache identity before applying offsets.
   `$KBASE`, `$SLIDE`, `$HEAP_START`, `$HEAP_SIZE` below are **measured inputs**,
   not literals supplied by this report. If base discovery or readable RAM
   classification is unavailable, stop here and return the probe/regions
   evidence; do not guess. A valid port with denied reads is a failed route.

4. With a bounded `one`/`store` Metal process and queue still alive, choose
   an independently identified RW, non-external **kernel heap RAM** region
   from the map; scan once:

   ```sh
   sudo ./kernel-read find "$HEAP_START" "$HEAP_SIZE" "$SLIDE" > candidates.txt
   mkdir sample1
   sudo ./kernel-read descriptor "$DESCRIPTOR_KVA" "$SLIDE" sample1
   sudo ./kernel-read channel "$CHANNEL_KVA" "$SLIDE" sample1
   ```

   The KVA variables come from `candidate descriptor/channel` output.
   Vtable matches are only candidates: confirm a channel's accelerator
   pointer+10/vtable, descriptor W tag3/0, W70 CDM matching the same job,
   W8/context slot and W270/sequence, channel pointer-ring entry and SKU
   embedded pointers. `descriptor` saves D0x438, W0x288 and D128 backing;
   `channel` saves host0x148, CH0x64, ctl0x80, full pointer ring and current
   SKU block through its allocation stride (cap64KiB). It does not translate
   arbitrary GPU/META pointers into KVAs. Use the captured allocator CPU base
   and offset, not truncation. If another job has advanced the SKU cursor,
   the current block may belong to another descriptor: reject that pairing.

5. Repeat in **new** directories; preserve raw addresses, OS/build, slide,
   timestamps, pre/post states and userspace output. The tool rechecks host
   objects and reports changes but cannot freeze the GPU or establish an
   atomic snapshot; even an unchanged vtable/descriptor can refer to a
   recycled slot. Tiny jobs may finish before sampling. Prefer retained
   queues and bounded repeated known jobs; do not stall them using kernel
   hooks, patched firmware, fake unsignalled dependencies or fault injection.
   A post-job W can still validate layout, but it is not a pre-execution
   initialization capture. Preserve this distinction in the manifest.

6. Still in the interactive shell, make only the new capture files readable
   by mobile (`sudo chown -R mobile sample1` and likewise for each explicitly
   named sample directory). On the Mac:

   ```sh
   IPAD_SCP=1 scripts/ipad-ssh.sh -r \
     mobile@127.0.0.1:/var/mobile/linux-a9/kernel-capture \
     build/gpu/round5-fw-re/device-capture
   ```

If tfp0 and HSP4 fail, **no working alternate read primitive was established**
in this research. Rootless palera1n is not an all-purpose kernel API promise;
shared clients/IOSurface do not close the gap without a proven alias. Return
the exact Mach errors, entitlement extraction, version inventory and region
results to the orchestrator. The safe next result is a documented blocker,
not installation of an exploit, live kernel patching or speculative MMIO reads.

### Remaining guesses: safest defaults and failure observations

Defaults below are experimental constructions, not a claimed iOS capture.
Every W field not listed as guessed here has the round4 host-source mapping;
retain its stated width, including unaligned qwords.

| Field / object | Safest default / constraint | What a failed dispatch should dump |
|---|---|---|
|W8 slot; Wc dependency; CH8 context|Use actual published VM slot and initialized dependency/context objects. No numeric zero-page substitute. Dependency initialization has nonzero sentinels above.|Slots84/86/87/88/89, CH0..63, dependency0..27f, tag/catalogue and page-table mappings|
|W14; W254..256 mode/gen|For capture replay use actual Cb8/C1a0/1a1 through D; fresh mode0 is a hypothesis. Do not confuse gen with VM selector.|C metadata, W10/14/254..257, SKU words and slot tags|
|W78 array,1c8,1d0,1d8 resource state|Disabled-resource experiment: zero all six qwords and base/stride/flags together. Keep real firmware pool initialized.|W78..1df, resource input R if available, G76c..8bf, allocator mask/+28|
|W1e8 fresh/restore|0 initially. Firmware may set1; do not force back to0 on retry.|H0..5f, scratch first dword, tail8 bytes, W23c..247, SKU PC/backlink and normal stamp|
|W1ec/1f4/1fc/204 scratch pointers; W60/20c tail|Map full0x6068, zero and set+20/+24/+28=1; pointers base,+4,+8,+c,tail+6020. Tail remains zero initially.|Full scratch before/after, raw C→W pointer qwords; H check above|
|W21c/224/22c/234,68/10 PDS state|Preserve captured packed states and matching PDS code/data. Do not zero them as unused helper state.|Both context PDS programs/data, their target USC, W10/68/21c..247, mapping permissions|
|W23c/244; W214 expected terminate|Use address **of** CDM terminate word and matching packed state; W214 is a qword, not a fence counter.|CDM through terminate, W214..21b and scratch-tail qword; retry state|
|W248,24c,250 instrumentation|0 for disabled instrumentation; emit the uninstrumented recipe. Recompute lengths/backlink when enabled.|W248..253 and full SKU including padding|
|W258/25c/260 stamps, W268 identity|Distinct valid META-visible stamp words and real manager identity/value; choose a fresh low value different from initial memory. Do not invent an event bitmap ID mapping.|Both stamp words, W258..277, every44-byte event, shader output|
|W26c optional boolean,W270 sequence,W274 host metadata|0 optional boolean only when no associated object; sequence/identity must agree throughout SKU/ring. Unknown q metadata0 remains a hypothesis.|W268..277, host descriptor metadata, queue accounting and ring pointers|
|W278 result,W280/284 optional timestamps|Result0, timestamp destination pointers0 on disabled path as host null branch allows.|W278..287 and opcode18 packets; never point optional writes at freed pages|
|W unwritten padding/tail resources|Zero entire0x288 before explicit writes, retain allocation throughout completion/recovery.|Full W before/after; do not read only changed words|
|Common USC/PDS B|300000000 inferred; maintain separate GPU mappings and adequate code/data window; no guessed register write.|VA/PTEs, executable bytes at B+offsets, CDM packets, stamp and output separately|
|Five CDM helpers|Retain all for capture-faithful replay; semantics still partly unknown.|Entire seven-packet stream, helper PDS/data/USC, scratch head/tail, event/trace rings|

Validation completed: iOS cross-compile/sign; imported-symbol audit for the
read-only helper; seven raw MINIM anchors; strict full post scratch checks
for both jobs; existing decoder anchor checks. No live kernel-memory access
or first dispatch success is asserted. Complete error/restore transitive
reads, final execution-base programming and exact fault subtype attribution
remain open rather than silently converted into defaults.

## Round 6 — exp12 localization, TA/3D envelopes, PM and render capture

### Exp12 first: the job reached start32; no firmware terminal event established

The supplied exp12 log (`../experiments/logs/2026-10-22-exp12-cdm-p7-v1-dmesg-gpu.txt`, not published)
does **not** establish a tag3 rejection or a firmware terminal event.
It establishes a **host timeout after start32**. Two distinctions change the
next experiment materially:

* `COMPUTE FAILED result=-110; terminal=1` is the driver's timeout/failure
  wrapper. The retained round13 `compute.inc` returns `-ETIMEDOUT` from its
  three-second wait, then sets `compute_terminal=true` and `failed=true`.
  `fault=0` in PARK is a separate fault latch. No raw firmware fault/event
  record accompanies this failure in the supplied log. Do not decode this
  line as MEMFAIL, DMKILL or a firmware decision to reject the job.
* The SKU has `start32.arg8=43202ea0=G+720` and
  `start32.arg12=c0254000=CH`. At **80007408..743c**, after the hardware
  state upload, fresh kick and context-PDS programming, firmware writes
  `arg8[8]=1`, `arg8[0]=0`, `arg8[c]=arg12`. These are precisely the
  observed **G720=0,G728=1,G72c=CH**. **G72c is the channel pointer here,
  not the context-slot number**; correct that interpretation in rounds3/4.
  The fresh path through80008f30 issued `R0+478=1` before these stores;
  10018358 programmed R0+4a8/4b0 from W21c/224. This proves the firmware
  attempted the hardware start; it does not prove the GPU accepted it.

The logged `start=0` reads the word pointed to by **W258=c0254940**.
That pointer occurs nowhere in the captured uninstrumented SKU: it uses
the finish pair **W25c=c0254944,W260=1**. The start32 direct stores above
target G720 state, not W258. There is no established unconditional writer
of W258 on this recipe. Therefore **W258=0 is not a start-handler marker**.
Do not add a synthetic stamp write to make this diagnostic pass.

Classification of the proposed causes, using the actual reached boundary:

| Candidate | What exp12 rules in/out |
|---|---|
|Execution-record availability / empty allocator|Not the initial blocker for this job: opcode32 ran, hence an execution record was admitted. 10012fac's empty-mask return0/+28=16 is real, but would prevent this start. Do not interpret zero at an arbitrary `pool+2c` as that allocator without resolving its argument base.|
|Zero W78..a0 GPU resource entries|All six were uploaded before the G stores. No zero-entry rejection branch exists in80008d24. Whether disabled resources are sufficient for **hardware execution** remains open. These GPU resources differ from firmware execution slots.|
|Generation/stamp identity/W14|Not a proven pre-start reject: start32 has consumed these inputs and reached its state stores. Incorrect values can still affect hardware/context helpers or later accounting. Identity0 is not evidence of a firmware error code.|
|Dependency zero256B / CH initial zero fields|Initial channel admission progressed through start32. This does not validate the zero dependency object or its later lifecycle. iOS initializes0x280 bytes with nonzero sentinels (round5); Linux's256B zero construction remains unsupported.|
|Power readiness with meta6c8=0|A pre-start scheduling gate did not stop **this** start32. The bypass does not prove working CDM clocks, hardware progress, completion IRQ routing or policy/recovery handling. It remains a post-kick hypothesis.|
|Execution base B / PTEs / helpers|No start32 software check validates B. A bad code/data mapping, packing, permissions, helper or B can stall/fault the GPU **after** the observed kick. No distinguishing fault record was captured.|

The next unresolved boundary is the microsequence **after start32**:
timestamp80000018 (destinations W280=0), then **opcode101** (scheduler
event-bit wait at80005d6c), then **004a000a,1,1**. The latter is a decoded
MMIO poll at80005c08: register offset=`word>>12=4a0`, mask1, expected1;
800060d0 retries until match or recovery escape. The final timestamp and
finish33 follow it. G728 remaining1 and retry/W1e8 remaining0 are consistent
with a wait before normal finish/retry; they do **not uniquely choose**
opcode101 versus R0+4a0 polling or a later finish helper. No captured live
META PC/execution-record cursor resolves that choice. There is no defensible
single tag3 rejection check to name from this dump.

Highest-information next observations: preserve full engine G and execution
records (including each micro-PC), interpreter pending event word, complete
SKU, G720..72f, real allocator argument+28/+2c, H=W1e8..247, scratch tail
and both stamps; log **raw44-byte events and event ring indices**, distinguishing
timeout from event failure. A cursor at SKU+48 localizes opcode101; at+4c
it localizes the4a0 poll; at+6c it localizes finish33. These offsets refer to
the captured exp12 SKU, not every possible instrumented sequence. Hardware
status must come through an already validated safe sampling mechanism;
this report does not authorize new live META slave-port reads. Preserve
all mappings through park. Do not change B, generation and power simultaneously.

### Evidence, notation and scope of the render specification

Static target is20H350 `kernelcache.release.iPad6,11_12`, SHA256
`c6b95fcf0efe52ee9258454d01d14adc9b0cb922d94c54a04a4ce11d1ae81e0a`.
Shared Ghidra identity was checked; missing virtual functions were decompiled
in the existing auxiliary project. No device access or git commit occurred.

Reproduction: `sh scripts/gpu/firmware-re/decompile-render.sh`, then
`python3 scripts/gpu/firmware-re/round6-evidence.py` (existing MINIM listings
required). The retained, source-hashed extracts are
host builders/importers/PM (`round6-host-extract.txt`, not published),
firmware code and raw bytes (`round6-firmware-extract.txt`, not published), and
every direct hardware-upload read operand (`round6-hardware-reads.txt`, not published).
The last inventory deliberately lists **all** operands of the two bounded
upload functions rather than just named/changed words. Eight firmware raw
anchors are checked. It is not a complete transitive/error read-set proof:
UNKNOWN instructions and indirect MMU/cache/recovery helpers remain.

All offsets below are hex; `W` is the appropriate work object, `D` the
host command descriptor, `CH` the engine's channel state, `M(x)` the kext's
GPU→META conversion. CPU KVA, GPU VA and META VA are separate address spaces.
Host decompilation's `uint*`/`long*` indexes scale4/8; do not paste those
indexes as byte offsets. Qword stores at unaligned offsets are intentional.

| Queue/work | Tag; firmware invalidation extent | D CPU/GPU work pointers | Microsequence pointer/length |
|---|---|---|---|
|TA|0; **560** at80002c38|aa0/aa8|W454/458|
|Normal3D|1; **8c0** at80000804|2e8/2f0|Wc/10|
|Alternate3D|2; **5f0** at80000708|2d8/2e0|W490/494|

These are verified firmware-visible extents, not allocator slab-size claims.
`D.byte630==0` selects normal3D; nonzero selects alternate. Do not call tag2
a successful PM partial-render capture: its precise high-level mode is
not independently established. In all three render envelopes **W4 is the
context slot**, unlike compute's W8. TA global notification type0 uses
firmware-host+300/+308;3D type1 uses+320/+328. Those20-byte notifications
carry converted CH and endpoint, not W inline. Pointer-ring publication
and global notification are both needed.

### TA object and firmware reads/writes

Host builder **fffffff006cb9050**, hardware filler **6ca9710**, SKU builder
**6ca9368**. The full explicit-store inventory is retained in the host
extract, including optional tag5/6 records and zero stores. Core map:

| TA W bytes | Verified source/role |
|---|---|
|00,04,08|tag0; D.cfc context slot;0|
|0c|M(D130) dependency/control GPU object|
|10,14,18|Dcb0 PM identity; M(Dcb8), M(Dcc0) PM-related control/state pointers|
|20..ff|Hardware block B: filler→80007658; **every dword** read, including six qword resource entries atd0..ff|
|28/30|D.bf8 / D.be8 qwords; these are retained as GPU state, not host pointers|
|88,90,98|Dab8&~f, Db10&~7, Dab0&~3 (qwords)|
|3a4..3c3|Hardware block A:88; `(Dbe0 | Dbda<<3 |2)`; qwords Dc98/Dca0 at3ac/3b4; Dca8 at3bc;20000 at3c0|
|444 qword,44c u32|Dbe8; low32(Dc08), checked <ffffffff|
|454,458|SKU META pointer; rounded byte length|
|464..50f|Mutable render/context state: qwords copied from Dac0..ad8 etc, then explicit zeros; preserve full extent|
|510,514;518..528|Instrumentation byte D688 and word D68c; mode bytes Dbd8/bd9/bdb/bdc/bdd expanded to dwords|
|52c,530/534|Two stamp destinations; **530 address /534 value** consumed by finish24|
|538,53c,540,544|high half of D7d4 stamp metadata; channel identity; boolean D48!=0; D970 sequence|
|548,54c,550/554,558,55c|Dcf8; mode D760==2; result qword0; optional timestamp pointers (558=M(D698) or0;55c writer not independently established)|

For exact register destinations see80007658..7d7c: A reads its eight dwords
0..1c, B reads all56 dwords0..dc. Firmware starts at **8000672c**:
select context byte through10017d6c; load A/B into hardware; call PM/state
helpers using P=M(Dcc0); then writes `[TA_G+728]=1`, `[TA_G+724]=0`,
`[TA_G+730]=ctx` (the SKU passes G+724 here). The launch path chooses
R0+400=1 versus R0+130=20 according to the pointed state; this is a real
branch, not permission to choose either for a minimal job.

Finish24 at **800068a4** clears state+4/+c, waits through80009218 on
**R0+430.bit0**, and returns R0+430&2. On normal status it runs cleanup,
increments CH+c, and stores `*(u32*)W530=W534` at80006a0c..6a1c, followed
by1001ede0(engine1). On nonzero status a mode flag selects cleanup without
normal stamp versus helper10013c80 and **return1**; the interpreter then
uses the signed backlink to revisit start23. The exact association of
430.bit1 with PM OOM versus other abort conditions is **not proved**.
Neither a TA stamp nor channel scan proves final color-buffer completion.

### 3D object, HWRT boundary and completion

Normal builder **6c7a06c**, hardware filler **6ca7ae0**, SKU **6ca7388**.
Alternate filler **6ca7e38**, SKU **6ca77a4**. Core normal3D fields:

| Normal3D W bytes | Verified source/role |
|---|---|
|00/04;0c/10|tag1/context D768; SKU pointer/length|
|14,18,1c|M(D130) dependency; M(D718),M(D720) PM-related state|
|20..57|D6b8,6d0 qwords; D6e0/6e8/6d8 dwords; unaligned qwords D6f0/6f8/700 at W3c/44/4c|
|58..13f|Hardware block B, all58 dwords0..e4 read by8000850c|
|3a0..3e7|Hardware block A, dwords0,4,8,10..44 read; +c is not read by this upload function|
|490..6d7|Copied GPU/render state; every main-builder store is retained in host extract; includes W490←D308, W4a0←D310, many render-target/PBE/depth/auxiliary fields|
|6d8..7ff|Runtime/context block, large explicit zero region; flags and qwords at7fc onward remain part of the object|
|7f4,7f8,800|Status/mode: builder clears7f8 and sets800 from D6b5; firmware tests7f4/800 on scheduler/recovery branches|
|804,814,824,834,83c,844,84c|Context helper qwords from D648/650/658/660/668/670/678|
|864,868|Instrumentation D688/D68c|
|86c..887|Expanded D6ad..6b3 flags; preserve byte packing from builder|
|888,88c/890|First stamp and normal finish destination/value (unaligned qword at88c)|
|894,898,89c,8a0,8a4,8a8|stamp metadata pair; channel identity; optional boolean; sequence; metadata D764; mode D760==2|
|8ac/8b0,8b4,8b8|result qword0; optional timestamp targets M(D698),M(D6a0) or0|

Firmware start25 at **80006afc** consumes the two state pointers and their
flags, calls8000850c(A=W3a0,B=W58), then handles the pointed PM/runtime
state before setting 3D_G+734/738 (via G+724+10/+14) and clearing G+724.
The complete hardware operand→MMIO writes are retained, with per-core
resource selectors and register sequencing. Generic zero HWRT storage is
not supported by these dereferences.

Finish26 **80007058** dereferences W878; if its+4 is nonzero it checks and
consumes scheduler event bit100 via80009290 and can return3/yield. Otherwise
it polls **R0+1090.bit0** via80007d7c. Once ready, it increments the word
pointed to by M(D720); the mode flag from `(D681==0 ? D683^1 :0)` controls
the stamp store `*(u32*)W88c=W890` at80007204..7214. It clears runtime
state and triggers engine1 pending notification. Zeroing that mode flag
without understanding it can suppress the normal stamp despite progress.
The fallback path calls10013d30 and returns1, selecting the backlink.
This is a branch description; it does not establish a complete partial-render
or surface-retirement algorithm.

Alternate3D has runtime block W498, stamp W5b4/address andW5b8/value,
sequence W5c8, optional timestamps W5e8/5ec. Finish28 at80006e84 polls
1090.bit0, clears `(G+724)+10/+14`, and normally stores W5b4/W5b8.
Start27 uploads its own A=W358,B=W10 through80007dac, **not normal3D's
850c layout**. Its full transitive upload read set is not newly certified.

**HWRT naming boundary:** W14/18/1c and TA10/14/18 establish real
kernel-created PM/runtime relationships. Userspace **whole-record+2d8/+2e0
are allocator pools8/7**, with verified80000/c0000-byte zero initialization,
not a firmware HWRT pair. The old framebuffer attribution mixed payload and
whole-record offsets; framebuffer depth copies are whole+308/+310.
Userspace round12 (`../../gpu-userspace/round12-report.md`, not published) also records a static
dummy PBE with encoded address0/third-word bit43, absent from the active
captured targets, and explains why it does not identify the pre-attach
context0 VA-zero fault or establish a dummy VA0 mapping.
No single universal `HWRT` struct with all fields named is proved in this
round. Keep all full control and per-slot structures below; do not replace
them with an assumed modern Apple/PowerVR layout.

### Microsequence word specification and TA→3D handoff

These are **META interpreter packets**, not VDM/ISP/CDM packets. Qword
fields below expand little-endian to two u32 words. Define `w=M(W_GPU)`,
`ch=M(CH_GPU)`, `g=M(engine global)`, `ctx=W4`, `s=sequence`,
`p=M(Dcc0)` for TA, `p=M(D720),p2=M(D718)` for normal3D,
`k=*(u32*)(D138+4)`. `b(x)` means byte at D+x. Exact emitted fields:

```
TA start (0x44 bytes):
23 w+3a4 w+20 p g+724 ch ctx k Dcb0 b(bd8) Dcf8 W53c
   w+464 b(bd9) b(bdb) (D760==2) s
TA finish (0x38 bytes):
24 p W14 g+724 ch ctx (b(bd8)!=0) w+464 b(bd9) b(bdb) s
   qword(W530) backlink

3D start (0x54 bytes):
25 w+3a0 w+58 p g+724 w+7f0 w+7f4 w+878 ch w ctx k D710
   b(6b5) D764 W898 w+6d8 b(6b1) b(6b3) (D760==2) s
3D finish (0x48 bytes):
26 s qword(W88c) p p2 (D681==0 ? b(683)^1 :0) g+724 w+878
   w+7f0 ch w ctx w+6d8 (D318!=0) b(6b1) b(6b3) backlink

Alternate3D start (0x34 bytes):
27 w+358 w+10 0 g+724 ch ctx k b(6b0) D764 W5c0 w+498 s
Alternate3D finish (0x2c bytes):
28 g+724 ch ctx (b(6b0)!=0) w+498 b(6b1) s qword(W5b4) backlink
```

In each recipe, channel builder first initializes SKU block/cursor through
6cb66d8. Optional instrumentation adds20 packets (TA80000020/20;
3Da0000020/20000020). When timestamp-disable is false: start packet,
`80000018 result timestamp0 timestamp0 s`, engine wait helper,
`18 result timestamp0 timestamp1 s`, finish packet. Timestamp offsets by
tag0/1/2 are result550/8ac/5dc, timestamp0558/8b4/5e8,
timestamp155c/8b8/5ec. Table59410c0/d0/e0 was read directly.

TA wait helper6ca9908 emits **801**. Normal and alternate3D helper6ca81a4
emits201, register-write/poll packets and a per-core loop (accelerator+378),
including the variable relative loop word. Full source and table bytes are
required: there is **no fixed universal render SKU size** derived here.
6cb68dc appends termination and rounds to64 bytes. Backlink is signed
`block_start - finish_opcode_PC`, recomputed after all inserted packets;
do not reuse compute's-6c or count alignment padding as executed words.

The kernel also emits explicit **tag4 dependency work**, `{4,stamp_address,
required_value,sequence&7fffffff}`. Firmware at80000a54 compares signed
`required_value-*stamp` and defers when positive; it can set a wait status2.
3D's builder orders the TA-related dependency first when it matches D968.
TA's separate context D.cfc and3D D768, both stamps, PM ID and backing,
dependency work and the same retained render descriptor establish the
handoff mechanism. **TA does not fabricate a3D command by itself**; host
publishes both jobs and their dependency. A TA completion stamp is not a
reason to free parameter data before3D has consumed it.

### PM allocation, lists, growth and error handling

Verified kernel host `P=AGXParameterManagementMeta`, size278,
vtable6ff8510. TA descriptor **D8f0=P** is proved by6cb9afc, which locks
P128, obtains PM identity Dcb0/D710, prepares PM backing and separately
acquires TA/3D context slots. It may fail with “Failed to get ID for PM
resources” or “Failed to check usage and prepare PM resources” before work.

| Host P field / function | Verified construction |
|---|---|
|6cb07f0;138,250,254,258|max blocks=capacity>>11hex (128KiB); circular available-block IDs initially0..N-1; producer254 increments; consumer250|
|6cb124c|Allocate **20000-byte** block backing, block+20=id,+28=CPU list slice,+30=M(list GPU slice),+38=memory descriptor; append backing to P200 array, increment208|
|P50,P70,P98|Slot count; CPU control base; GPU control base. Allocate **80+slots*c0** control bytes; per-slot CPU/GPU tables P88/Pb0 point to base+80+i*c0|
|P60,P68,P78,Pa0|Host page size; list stride=`4+20000/page_size*4`; CPU/GPU list allocation bases. Allocation extent=`44+max_blocks*stride`; P80/Pa8 are base+40|
|6cb13f4|Each block list+4 stores physical address>>c for each **host page**; do not insert GPU VAs or assume one entry per4KiB of actual backing|
|6cb14dc|Build initial free-page list: control20=count,24=count-1,28 qword0,30=0,10=head; chain block list+0 words and expand each host page into consecutive4KiB frame numbers|
|6cb2378|Per-slot c0 object: explicit zero qwords through48 and90; GPU addresses at a0/a8/b0 into auxiliary backing, b8=M(Pa8). Auxiliary size derived from P1c8/P1d0/P1d8 and slots|
|6cb1108|Reset control includes GPU page-list base at14 (unalignedqword), size1c,20=0,24=ffffffff,28=0,30=0,34=host page size,40=M(Pa0); limit dwords54/58; usage block (P80)20=1|
|6cb09bc|Adaptive growth prepares additional blocks, state byte P28=2, linked list of new blocks, first/last META list IDs P26c/274; updates allocated bytes P130 and limits|
|6cb0f3c|Immediate growth prepares up to10 additional blocks, obeying max/limits; new-head P260 and prior-tail P268; explicit backing preparation failure path|
|6cb17e8→6cb18bc|Release queued blocks from P240, return block IDs into P258 ring, increment254, release backing reference; host list invariants checked|

Some table names are interpretations of verified stores; it is **not** a
license to initialize all other bytes0. Preserve complete allocations and
measured slot/block limits. The host page list's initial linked entries and
control24=ffffffff alone disprove a generic zero-page PM implementation.

TA builder can prepend PM **tag5 (18 bytes)** or **tag6 (1c bytes)**:
tag5=`5,ctx,PMid,M(Dcb8),Dce8,Dcec`;
tag6=`6,ctx,PMid,M(Dcb8),Dce4,Dce8,Dcec`.
Firmware80002a78/2b3c invalidates those exact extents and routes through
100056dc/10005368. They manipulate the PM head at+10, consume linked
block-list words, copy/expand physical page entries, and update PM+20/+24;
full branches retain UNKNOWN arithmetic. These are **separate work items**,
not arbitrary opcodes to splice into start23. Retain the actual captured
mode/tag selection when replaying growth/reset.

**Fault dump contract:** preserve TA/3D W, both full SKUs/current micro-PCs,
both channels/control/pointer rings, both stamp pairs, dependency280,
P278 plus control80+slots*c0, list44+blocks*stride, per-slot runtime state,
PM head/count/tail, P28/130/138/13c/140/144..14c/250..274, all GPU-backed
parameter blocks, VDM/ISP/PDS/USC mappings, framebuffer/auxiliary targets
and raw44-byte events. A PM grow failure in the host, TA430.bit1 retry,
3D1090 not-ready, page-request and DMKILL are different observations.
The exact firmware PM-OOM interrupt→partial-render→resume transition,
bad-VDM/ISP opcode subtype and tile-fault address/status encoding remain
**unresolved**; no invented event subtype or PM OOM recovery sequence is
offered. Do not acknowledge a page request by claiming unmapped data was
supplied, or retire/free PM resources merely because a ring scan advanced.

### Kernel/userspace split for the first draw

Userspace owns compiled VS/FS/PDS/USC, VDM and helper streams, framebuffer
clear/EOT/PBE/depth/auxiliary state, context-switch GPU scratch and the
shared render record/resource declarations. Kext importers6c7b100,
6cba8a0,6cba408 copy/transform that input into D;6cbaa1c sequences the TA
import with other descriptor imports. Their source indexes are not whole
record offsets without the caller's subrecord base. The shared userspace
round8 record map is the authority for that base; this round adds the D→W
and firmware consumer sides.

Kernel owns GPU mappings/VM and context publication, host D lifetime,
full W envelopes and conversion to META pointers, SKU allocation/emission,
dependency/stamp managers, TA/3D channels and both rings, PM allocation,
physical free lists, backing residency/growth and completion/fault lifetime.
It may expose opaque render-state input to userspace, but must validate
and relocate it. Firmware owns runtime mutations, scheduler event/slot state,
TA/3D hardware programming, completion stores and retry decisions. Userspace
must not manufacture kernel KVAs, private META pointers or PM physical lists.

### Render capture tool and staged orchestrator steps

`kernel-read.c` now supports **render** and **pm**, plus TA/3D channel
vtables in `find`/`channel`. Build/sign with the existing round5 build script;
output path remains `build/gpu/round5-fw-re/bin/kernel-read`. Existing tfp0,
kernel-header/build/slide verification and independently identified heap-RAM
requirements still apply. No device calls were made in this round.

1. Run the round5 probe/header procedure in the same boot as `pass-linear`.
   Transfer the rebuilt helper, retain the Metal process/queue, and save its
   userspace pre/post manifest, shared render record, streams, target pixels,
   allocation sizes and GPU↔CPU mappings. Use bounded repeated known jobs;
   do not patch firmware, add fake dependencies or deliberately provoke OOM.
2. Scan the measured heap range once. `find` now distinguishes
   `ta-descriptor` (vtable6ff98c8,size d88), `3d-descriptor`
   (6ff9ab0,size7d8), `ta-channel` (6ff75d0,size118), `3d-channel`
   (6ff6600,size150) and `pm` (6ff8510,size278). Matches remain candidates.
3. In a fresh existing directory on the device, orchestrator runs:

   ```sh
   mkdir render-post1
   sudo ./kernel-read render "$TA_DESCRIPTOR_KVA" "$SLIDE" render-post1
   mkdir ta-channel-post1 3d-channel-post1
   sudo ./kernel-read channel "$TA_CHANNEL_KVA" "$SLIDE" ta-channel-post1
   sudo ./kernel-read channel "$THREED_CHANNEL_KVA" "$SLIDE" 3d-channel-post1
   ```

   `render` saves Dd88, WTA560, normal W3D8c0 (or alternate5f0), dependency280,
   follows verified D8f0 to P, and saves P278/control/page-list allocations
   with caps1MiB each. It prints W CPU/GPU addresses, context, SKU META/length,
   and PM counts/bases. It never follows a GPU pointer as a KVA. A separately
   verified PM candidate can be sampled using `pm KVA SLIDE DIR`.
4. Match Dcb0/D710 PM identity, TA/3D context IDs and sequences, same
   userspace VDM/target/helper addresses, both pointer-ring entries and SKU
   W-relative references. Reject other applications' or recycled jobs.
   `channel` captures the current SKU block via allocator CPU base+offset;
   it does not prove that current block is the target W's sequence. If it
   moved, dump the **known allocator CPU span** with explicit `read` and
   match W454 or Wc to the recorded GPU/META base conversion; never truncate.
5. Repeat in new directories for bounded pre/post opportunities. The tool
   rechecks D/P/channel host bytes and reports changes, but cannot freeze
   firmware or detect every recycled slot/backing mutation. A post-only
   capture is labeled post-only. PM mode does not dump all parameter backing,
   walk arbitrary linked objects, or claim to recover all HWRT state; capture
   those allocations only through verified CPU mappings and measured bounds.
6. Chown only the new named directories for mobile, pull with the round5
   SCP procedure, retain addresses/slide/build/time and hash every artifact.
   If kernel reads are unavailable, return exact errors plus userspace
   captures; no arbitrary selector probing or alternate exploit is proposed.

Validation: iOS arm64 compile with Wall/Wextra/Werror and ldid signing;
undefined imports still contain only read/region kernel interfaces (no
mach_vm_write/allocate/protect);31 host-source extracts and8 firmware byte
anchors reproduced. Live render, PM OOM recovery and complete transitive
read coverage remain unverified. This specification closes the concrete
envelope/SKU/ownership/capture boundary; it does not yet constitute an
executable minimal render job.

## Round 8 — scripted live iOS object capture

The owner/orchestrator runner and exact install/run commands are in
round8-capture.md (`round8-capture.md`, not published). Region recursion/non-progress is fixed;
Metal regions mode has a bounded600s post-completion hold; full bounded SKU
allocation/metadata and invoking-user file ownership are added. Automatic scans
use allocator-tagged heap leaves after kernel UUID/slide/AGX-byte verification.
Compute has userspace-C/W/SKU/channel/ring linkage checks; render linkage is
structural and still needs full importer/PM/target attribution. This is a
post-only capture opportunity, not a guarantee the kext retains completed W/D.
No device access or commits occurred. Binary object comparison to exp12 waits
for the owner's new capture; absent bytes will be reported as unavailable.

## Round 9 — panic containment (supersedes round 8 live capture)

The first round8 heap page faulted in `_memcpy+0x28`; tfp0's kernel-map copy
path calls unguarded memcpy. The signed helper is now **metadata-only**, with
per-page recursive region/residency queries and no kernel-copy import. Live
`--run` is disabled; `--dry-run` reports SKIP/zero copies and pulls panic logs
first. Exact typed-allocation records/sizes are extracted, but live zone-page
ownership and a verified no-fault read primitive remain unresolved. Do not use
the earlier allocator-tagged broad scan. See the incident and next-session
commands (`round8-capture.md`, not published); offline check: `scripts/gpu/firmware-re/check-capture-safety.py`.
