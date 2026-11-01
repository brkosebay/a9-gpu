# tahang1: the desktop freeze after ~250-340 frames

Branch `a9/gpu-tahang1` on `a9/gpu-mcfold1` @ 85ad2697fd. Build, host gate and packaging:
`scripts/gpu/tahang1-build.sh test | verify A|B|C | package A|B|C` (`verify` checks the image currently
in `build/kernel-tahang1`, which now holds C).

| image | commits | entry | payload sha256 |
|---|---|---|---|
| **A (fix)** | `ed4afeb59c` TA completion rules | `tahang1a-ed4afeb59c` | `c6111e09b0f60a615db5c163f1a11589a4ec601b898df792183d0a48fe740dac` |
| **B (fix + fold)** | A + `f9190e2a6a` (= 5bdda08efb, 3D completes on its event) | `tahang1b-f9190e2a6a` | `69c423b2e658d908cd36345251c8e2715cccad87c10a7a8743ae0963b7d9e538` |
| **C (B + 3D hold)** | B + `c78c0dd899` (3D rule (c): 500 ms coherent hold) | `tahang1c-c78c0dd899` | `9500bd60303aa2b962e2b134fbeac2e510ab6028fa90044c0aac3575a39a6cf0` |

Payloads: `build/boot/m1n1-linux-<entry>.bin`. Entries: `build/gpu/tahang1/{A,B,C}/<entry>-entry`. Same
config (`kernel-gpucombined118`), cmdline and initramfs as mcfold1.

## 1. Cause: a host predicate bug, proven from the two receipts

Two freezes, same signature:
- Hang 1 (T1 first, then labwc): seq 251 (`logs/mcfold1/dmesg-ta-hang.txt:9402-9420`).
- Hang 2 (labwc only, boot 3cb354a4): seq 339 (`logs/mcfold1-desk/dmesg-hang.txt:10132-10146`).

The TA phase is done when `consumed && idle_ok && retired && pm_ok` (mcfold1 `drm-render-v3.inc:236`).
In both receipts `unsat=0x4/0x0`. 0x4 is `G5P_RU_RT_IDLE` (`render-user-retire.h:36`), built by
`g5p_rv3_ta_unsat()` (`drm-render-v3.inc:240-244`), so every other clause held at the closing sample:
- consumed: `ta_consumed_ms=2`, `ta_ring=251/251` and `83/83` (339 & 255);
- retired (`render-user-layout.h:504`): both stamps `0xfb`/`0x153` = seq, the W pointers and W+0x534, and
  all five watermarks at the job;
- PM saved (`render-layout-r22.h:210`).

The only open clause was a TA idle word, `G+0x724/0x728/0x72c` of the TA global
(`drm-render-v3.inc:215-222`). It was first seen set at 8 ms and 7 ms (`busy_ms=8`, `busy_ms=7`), and
it never cleared in 60 s. The firmware had finished the TA. Its type-1 TA completion event
(`1 1 0 0 / 0 0 43201cfc 1000e7ac / 1 10016458 10016470`, the same as on the good frames) was decoded
at +8.4 ms and +7.7 ms. Then the 60 s hard bound ended the epoch.

**It isn't a rare state, and the 3D engine already has the same problem.** In hang 1's boot, 49 of 241
good TAs showed an idle word set at 7-12 ms that cleared about 2 ms later (`RENDER-L123 ... busy=8
done=10`). On the 3D side, 50 of 199 phases after T1 were decided by the 200 ms event rule. The 3D
word left set is always `idle[0]` = G+0x724 = 1 (381 retire lines, never 0x728/0x72c). The combined107
P4R 3D timeout was this exact failure (`render-user-retire.h:7-13`). repeat109 fixed it for the 3D
(rule b); the TA phase never got the equivalent rule. start23 writes `G+0x724 = 0` at the engine's next
start (`83-ta-launch-root-cause.md` §1 row 7). That is consistent with the stuck word being
post-completion bookkeeping rather than "TA running", but doesn't show which TA word stuck.

Ruled out on the evidence:
- **Lost wakeup or IRQ race.** The TA predicate doesn't consume an event (mcfold1 `drm-render-v3.inc:225`
  sets `s->event` for the 3D only). The wait is a level-triggered RAM poll every 1-2 ms that re-reads
  the stamp at every sample. The receipt's `ta_stamp=fb/fb` is that re-read.
- **Event ring index.** The index is the consumer slot of a 256-entry ring that it shares with about
  250 ALIVE/heartbeat events per second (`boot.inc:623-687`). Nothing compares indices.
- **T1 contamination.** Hang 2 had one client and no T1. The idle words are per engine, not per slot.
- **Sequence or ring wrap.** The hangs were at seq 251 and 339, ring 251 and 83. The 3cb354a4 boot ran
  past seq 256.
- **pm80 = 0x1a.** pm80 is `S+0`, the first runtime word that the PM bank-0 save writes into PM record 0
  (`ds-r38-a-pm-state-static.md` §0). In 3cb354a4, the good jobs seq 7 and 8 already read `0x1a`. The
  early jobs read 3, 4 and 0xc as the scene grows. It is the steady desktop value, not a leak.
  `g5p_rv3_pm_rebuild()` (`drm-render-v3.inc:1115`) rebuilds the pool, the list and F before every job
  ≥ 2. The firmware has no freelist reader, grow request or OOM consumer
  (`dsx-agx-pm-freelist-recovery-annotated.md` §1, §4.1-4.3). Nothing can accumulate over a context's
  lifetime.

**Still unproven:**
- Which of the three TA words stuck. The receipt printed only the 3D sample's idle words
  (`drm-render-v3.inc:1088-1092`); image A now prints both.
- Why the firmware sometimes doesn't clear the word.
- The owner's touch correlation. Touch is IRQ and CPU load on the host. It may change when the firmware
  services its bookkeeping. No mechanism is shown, and the fix doesn't depend on one.

## 2. Fix (image A, `ed4afeb59c`)

The TA is complete when the target is coherent (ring consumed, `g5p_ru_retired()`) with the PM state
saved, and any one of these holds (`render-user-retire.h`, `g5p_ru_ta_step()`):
- (a) the idle words are zero (the old predicate);
- (b) a type-1 TA completion event was decoded since this TA's publish;
- (c) the target has stayed coherent for 500 ms without a break.

The stamps carry the job's identity. The TA event carries none, so it never completes a job whose
stamps haven't landed. Rule (c) is the safety net the owner asked for: a lost or late event can't hold
a frame whose stamps landed.

The TA latch works like the 3D one:
- `g5p_rv3_event_arm(d, 0, 0)` runs before the producer store;
- `g5p_drm_event()` sets `ta_hit_ns` under `status_lock` on whichever CPU decodes the event;
- the wait samples it.

A real stall (no stamp) still times out exactly as before.

New RAM-only lines:
- `RENDER-V3 ta-done seq= how=event|hold idle=a,b,c pm80= ... rules=clear/event/hold`: for each TA
  that completed with an idle word set. Rate-limited for `event`, always printed for `hold`.
- `RENDER-V3 slow seq= engine= ms=...`: printed once by any phase still pending 1 s after its publish.
  It carries the ring, stamps, watermarks, all six idle words, pm80, the clauses, the event time and
  the waiter state.
- `RENDER-V3 retire ... ta_how= ta_unsat= ta_coherent_ms= ta_event_ms= ta_idle=`: the receipt (the
  first 8 jobs of each context, and every failure).

Host gate (`tahang1-build.sh test`, `scripts/gpu/tahang1/test_tahang1.c`): 10767 checks on image C's
headers, plain and ASan/UBSan. It runs the real headers in the simulated `g5p_rv3_wait()` loop with the ALIVE stream and
covers:
- the seq 251 and 339 timelines (legacy: timeout at 60 s with unsat 0x4; new: done at the event);
- a lost event (done at the hold);
- an event before the stamps, a stale event, a late 3D event, and the PM not yet saved;
- a real stall (still a timeout);
- the hold-run reset;
- latch arm order (an arm-after-drain mutant loses the event);
- ring, grid and 32-bit wraps;
- 2000 random timelines;
- the 3D rules (image C, §3): a lost 3D event, a TA event or the previous job's 3D event during the
  3D wait, a real 3D stall, settle above the hold, the hold-run reset, and 600 random 3D timelines,
  each also run as image B (no hold) for comparison.

All 11 header mutants are caught (7 TA, 4 3D).

**Recovery limits.**
- A real TA stall (stamps never land) is still terminal for the epoch: the firmware keeps the job, and
  per STATUS a wedged firmware needs DFU. Failing only that job would need a firmware-side cancel we
  don't have.
- The failed job's fence is signaled with the error (`drm-render.inc:1059-1062`). Labwc then hanging in
  a futex is Mesa/Zink device-lost handling, not the kernel.

**Overlap with mcfold2:**
- Files: `a9-gpu-drm.c` (two fields after `td_hit_ns` in `struct g5p_drm`, and two lines in
  `g5p_drm_event()`), `drm-render-v3.{h,inc}` (sample, done, latch, wait, observe, execute, receipt) and
  `render-user-retire.h` (appended).
- Teardown doesn't use the latches. A merge conflict can only be textual, next to `td_hit_ns`.
- Nothing here needs unbind.

## 3. Fold (image B, `f9190e2a6a` = 5bdda08efb)

The 3D completes at the first coherent sample after its event, with settle 0 instead of 200 ms. The
cherry-pick conflicted only in `g5p_rv3_done()`'s comment. The retire header's "The TA phase does not
use this file" now points to the TA rules. Expected effect: the idle-stuck 3D frames (`done - ev` ≈
200 ms in a quarter to half of the frames) finish at their event.

Image B's 3D phase has no hold rule: an idle-stuck 3D completes only on its event, so a lost 3D
event still times out at 60 s.

**Image C (`c78c0dd899`) closes that gap.** It is a separate commit on B. The 3D phase gets rule (c),
like the TA's: it completes once its target (ring, both stamps, W words, watermarks) has stayed
coherent, unbroken, for 500 ms, or for `settle_ms` when that is longer. Rules (a) and (b) are
unchanged.
- PM needs no clause on the 3D: the 3D publish waits for the TA phase, and that phase already required
  the PM state saved. The 3D's own `pm80 == pm_serial + 1` stays a receipt only, because the receipts'
  pm80 sequence (3, 3, 4, 4, 4, c, 1a, 1a) doesn't step by one per job.
- A 3D decided by the hold prints one `RENDER-V3 3d-hold seq= idle=... coherent_ms= decided_ms=`
  line, and the retire receipt shows `how=hold`.
- In the host gate, image B times out at 60 s on a lost 3D event or a foreign one (a TA event, or the
  previous job's 3D event). Image C completes at coherent + 500 ms. A real stall, where the stamps
  never land, still times out on both.

mcfold1 baseline from hang 1's boot (`soak-stats.py`): frame gap median 0.571 s, p90 1.03 s, peak 8
frames/s; per-frame `fin` median 76 ms, p90 274 ms.

## 4. Device plan (orchestrator)

Tools:
- `scripts/gpu/tahang1/soak-type.py SECONDS [KEYS/S]`, run as root on the iPad: a uinput keyboard that
  types into the focused foot window. It never presses Enter, and Ctrl+U clears the line every 60
  characters. **It is untested on the device.** Before counting a soak, confirm labwc sees it:
  `libinput list-devices` shows "tahang1 soak keyboard", or text appears in foot within about 5 s.
- `scripts/gpu/tahang1/soak-stats.py dmesg.txt`, on the Mac: frame count, first failure seq, TA rule
  totals, `slow` lines, frame gap and peak, per-frame `fin`, and the PASS/FAIL verdict.

Before each soak:
- Use the usual desktop environment (`PVR_G5P_GROUPS=last-read`, `WLR_SCENE_DEBUG_DAMAGE=rerender`).
- Focus foot.
- Run `echo 1 > /sys/module/a9_gpu_drm/parameters/lat123_log` (one `RENDER-L123` line per job; needed
  for `fin`).

1. **S1: single client, touch-heavy, image A.**
   - Boot `tahang1a-ed4afeb59c` by the usual route (warm hop or cold), then start the desktop as on mcfold1.
   - Run `soak-type.py 900 4` (about 3600 keystrokes).
   - The owner drags and taps on the screen throughout (real N1 IRQ bursts), and especially in the first
     10 minutes.
   - Second half: CPU/IRQ stress, e.g. `for i in 1 2; do (while :; do :; done) & done` plus
     `ping -f -c 200000 <mac>`. Kill the stress afterwards.
   - PASS: at least 1000 labwc frames and no `complete=` other than 0.
   - Report the `ta-done` totals. `how=event` lines are expected, and they are the proof that the fix
     caught frames which would have frozen before. Report any `how=hold` or `slow` line verbatim, and
     the failure seq if there is one.
2. **S2: T1 then desktop, image A, the last test of its boot.**
   - On a fresh boot of the same entry, run T1 first: 3 fixture contexts on 10/9, 12/11 and 14/13.
   - Then run S1's soak (at least 1000 frames). The same PASS rule applies.
   - Afterwards, DFU or cold boot, with no warm kexec (STATUS rule, until mcfold2 has flown).
3. **S3: fps and soak, image C** (B's fold plus the 3D hold; it replaces B).
   - Run S1 again on `tahang1c-c78c0dd899` with the same script and rate.
   - Add a 60 s burst at 15 keys/s for peak fps.
   - Report the frame gap median/p90, the peak frames/s and `fin` median/p90 next to image A and the
     baseline above.
   - PASS: S1's rule, plus `fin` median and p90 below image A's.
   - Report any `3d-hold` line verbatim: it means a 3D event was not seen, and on image B that frame
     would have frozen.

A failure mode the fix doesn't cover: if a TA idle word left set means the firmware itself still
sees the TA as busy, the 3D published right after a rule (b)/(c) TA may stall. The signature is
`ta-done seq=N how=event|hold` followed by `slow seq=N engine=1`. If that pair appears, report
`ta_idle` and whether the 3D stamp and `retired` landed. That would be a separate firmware-side
bug, not a regression of this fix.

If a freeze still happens on A, B or C:
- The watcher's capture plus the `slow` line at +1 s shows which clause and which word is stuck.
- If it's the stamps (`retired=0`), it's a real stall, and DFU is needed.
