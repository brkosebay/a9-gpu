# deskfold: the combined desktop image

2026-11-01. Kernel branch `a9/gpu-mcfold2` @ `f410c38330` (worktree `/src/wt/mcfold2`), the merge of:

- `a2ad7938e6` mcfold2: quiesce the GPU before a kexec or reboot (`mcfold2-kexec.md`);
- `a9/gpu-tahang1` through `c78c0dd899`: TA completion fix `ed4afeb59c`, 3D on its event `f9190e2a6a`,
  3D 500 ms coherent hold `c78c0dd899` (`tahang1.md`, image C).

Both sides sit on mcfold1 `85ad2697fd`. Not run on hardware.

| | |
|---|---|
| payload | `build/boot/m1n1-linux-deskfold-f410c38330.bin` |
| payload sha256 | `385303dca59f3f911bdb485f2ae587f6a399f8944a10369cac0eda7220bdea72` |
| entry | `deskfold-f410c38330` (`build/gpu/deskfold/deskfold-f410c38330-entry/`, per-file sha256 in `build/gpu/deskfold/entry.sha256`) |
| `Image.gz` (entry) | `11c9a33923371bb38b61b7c5caf2ebb05186e9366c1492a0c56af312de4671f4` |
| release | `7.3.0-rc1-gf410c38330b0` |
| config | byte-identical to `build/kernel-gpucombined118/config` (`DEBUG_INFO_NONE`) |
| cmdline, DTB, initramfs | mcfold1's; only `linux_a9.entry` differs. The DTB is identical, so `linux-a9-kexec` reuses the running FDT |

Script: `scripts/gpu/deskfold-build.sh verify|package|check`.

## Merge check

- `git show --remerge-diff f410c38330` is empty: git's own merge had no conflict, and the recorded
  result is that merge unchanged. (The overlap noted in `tahang1.md` §2, next to `td_hit_ns`, is more
  than 3 lines apart, so it merged textually.)
- `git diff a2ad7938e6 f410c38330` equals `git diff 85ad2697fd c78c0dd899` line for line (index and hunk
  headers aside), and `git diff c78c0dd899 f410c38330` equals `git diff 85ad2697fd a2ad7938e6`. Both
  fixes are complete and nothing from either side was dropped.
- Semantic touch points checked by hand: the quiesce's `rdev->executing` brackets `g5p_rv3_execute()`
  in `g5p_render_run()`, which tahang1 doesn't touch. The quiesce's "wedged" test reads the TYPED_V3
  latch, whose timeout/fault meaning tahang1 keeps. The unbind and `g5p_rv3_dc_wait()` are untouched by
  tahang1.
- Provenance: the image was built from the clean worktree at `f410c38330` with the worktree `.config`
  equal to combined118 (`DIRTY` empty, release string without `-dirty`). `package` reassembled the
  payload byte-identical to the one made by the previous session, so it was kept, not rebuilt.

## Gates (`deskfold-build.sh check`)

- tahang1 host gate on the merged headers: `test_tahang1` 10767 checks, 0 failures (plain and
  ASan/UBSan); all 11 header mutants caught.
- mcfold2 suite: `test_kexec_quiesce` 107/0, `test_render_bind` 177/0, `test_render_admit` 83/0.
- Register audits: `audit_kq.py` PASS (32 functions from 14 roots, the 7 justified ones as in
  `mcfold2-kexec.md` §3); `audit_ram_only.py` PASS (28 functions from 11 roots).
- checkpatch `--strict` on the four commits: 0 errors; warnings are only the `Co-Authored-By` trailer
  form and MAINTAINERS for new headers.

## Device plan (orchestrator)

Fly this only after tahang1a has passed its S1 soak. All three steps go on one boot, in this order,
under `caffeinate -i`. `SSH=scripts/omarchy/ssh.sh`.

**Boot.**
```
UPLOADFIX=1 /tmp/desk/boot-desk.sh build/boot/m1n1-linux-deskfold-f410c38330.bin deskfold-f410c38330
$SSH 'uname -r; cat /sys/module/a9_gpu_drm/parameters/kexec_quiesce'   # 7.3.0-rc1-gf410c38330b0, 2
$SSH 'echo 1 | sudo -n tee /sys/module/a9_gpu_drm/parameters/lat123_log'
```

**1. S1 touch soak (`tahang1.md` §4 S1, at least 1000 frames).** Focus foot, then
`soak-type.py 900 4` as root, the owner dragging and tapping throughout, and CPU/IRQ stress in the
second half, exactly as in `tahang1.md` §4. Collect `dmesg` and run `scripts/gpu/tahang1/soak-stats.py`.
PASS: at least 1000 labwc frames and no `complete=` other than 0. Report the `ta-done` totals, every
`how=hold`, `slow` and `3d-hold` line verbatim, and the frame gap median/p90, peak frames/s and `fin`
median/p90 next to tahang1a and the mcfold1 baseline. A `ta-done seq=N` followed by `slow seq=N engine=1`
is the firmware-side case in `tahang1.md` §4: report it, don't count it as a regression.

**2. T1 (`multi-client-design.md` §9).** With labwc still running: upload the latency101 fixture, then
`run a1; run a2; run a3`, then a4 ∥ a5, then the dmesg grep, fbprobe and `free -m`, exactly as in §9.
PASS as in §9: five `RENDER-V3 bind … ret=0` lines (`10/9` … `18/17`), every run `rc=0`, labwc's FRAME
`n` still advancing 60 s later, fbprobe unchanged.

**3. mcfold2 kexec hops (`mcfold2-kexec.md` §4, Hop 2 then Hop 3), with `deskfold` in place of
`mcfold2`.**
```
$SSH 'sudo -n systemctl stop desk-window.service desk-read.service desk-labwc.service; sleep 1'
$SSH 'echo td-kexec | sudo -n tee /sys/bus/platform/devices/201000000.gpu/command; sudo -n dmesg | grep -E "GPU-KQ|RENDER-V3 unbind|POWER gfx-off|command td-kexec"' | tee /tmp/desk/deskfold-hop2-kq.txt
UPLOADFIX=1 /tmp/desk/boot-desk.sh build/boot/m1n1-linux-deskfold-f410c38330.bin deskfold-f410c38330
```
PASS before the jump: `RENDER-V3 unbind why=kexec slots=12 verified=12 ret=0`, `GPU-KQ why=sysfs …
held=12 plan=detach+cycle skip=0 detach=0 froze=1 cycle=0`, `POWER gfx-off … off_ret=0 on_ret=0`, SSH
alive. After: SSH, `ds gen=1 mode=3 origin=warm-cycled failed=0`, labwc attached and fbprobe PASS, the
previous console in `/run/linux-a9/pstore/console-ramoops-*` shows `GPU-KQ why=reboot-notifier already
ran this boot`, and a fresh `run a1` gives `rc=0` with `bind slots=10/9 ret=0`. Then Hop 3 (automatic
quiesce into `desktop124j-c02a081b6f`) as written in `mcfold2-kexec.md` §4.

If step 1 fails, stop: report and keep the boot for the `slow` capture. If step 2 fails, skip step 3
and DFU or cold boot (STATUS rule). If the Hop 2 jump fails after three passing pre-jump lines, the
cause is not the GPU; collect Step 0 of `mcfold2-kexec.md` §4 after the DFU.
