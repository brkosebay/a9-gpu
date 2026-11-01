# G5P research: reusable findings and current boundary

The A9 GPU is a PowerVR GT7600-class G5P part using Apple's firmware ABI,
not the M1 Asahi GPU. The kernel and userspace work here is experimental.
See status (`../release/STATUS.md`, not published) and evidence (`../release/EVIDENCE.md`, not published).

The bring-up progressed from powered register windows and META firmware boot,
through device-control acknowledgement, VM/context activation and compute
retirement, to GEM-backed userspace jobs and visible compute output. Read
[firmware format](firmware-format.md), [driver design](drm-design.md),
[submission](submission-g5p.md), [UAPI](uapi.md), and the retained
[firmware research](firmware-re/) for structure/register/protocol analysis.

Useful device anchors retained as summaries:

- r19d: numerical store and retirement completed; r20 extended compute to
  user GEM BOs. These establish firmware/VM/channel operation, not rendering.
- r24-fill: exact 1536×2048 green output in a PRIME-imported simpledrm BO,
  displayed for ten seconds, then restored: 3145728/3145728 pixels.
- r24-loads: map/copy/vector addition exact over 65536 words. r24-tg:
  compute triangle and gradient exact, both offscreen and visible.
- B1 fixed-profile libg5p: 12/12 serial jobs over owned/imported BOs. B2
  FILL32/COPY32: 122/122 exact jobs, 41/41 kernel rejection checks. This
  deliberately bounded compute dialect is not a general graphics API.

TA/render still does not complete. r58 proved vertex USC execution/output;
r60–r62 did not install the expected parameter-manager lanes. r63's inserted
XFB state was traversed without issuing its canary PDS fetch. r65–r67 prefix,
stream-out and context-store probes did not unlock dispatch.

r68's injected TA_FINISHED reaches completion/PM-save, proving that route,
not natural rendering. r70's paired 3D is consumed but faults; empty region
headers contain no primitives. r71's start_present ablation reproduces the
same stall and retires that hypothesis.

Apple run18b/c captures and read-only Frida hooks on our own Metal test map
the VDM packets to their emitters: two batching draws are skipped by LINKs;
g0 is the app draw and g1 the endCommandStream driver strip. Apple clear-only
executes LINK LINK + g1 + PPP + terminate, matching Linux's stream shape.
The stress cc0 difference is context-store output; ordinary early cc0 matches
Linux. Apple's region page has real entries, while natural Linux TA still
stalls. See Frida findings (`experiments/frida-agx-findings.md`, not published) and
the r71 boundary (`experiments/r71-brief.md`, not published).

XNU source findings (`experiments/xnu-oss-8796-findings.md`, not published) explain the
unguarded memcpy behind kernel-task MMIO reads. A remap route remains a
proposal and SError can still panic. IOReport channels may supply observations;
none of this establishes a safe general MMIO reader or a completed render.

Do not resurrect a disproved hypothesis from an earlier note without its
counterevidence. A timeout is not a shader pass; an analyzer error can void a
run. Classify firmware/MMU faults and stale controls before interpreting
register deltas. Compare a known-good control and change one factor per boot.

The retained extraction, disassembly/emulation and probe tools operate on
locally obtained inputs. Firmware listings, large chronological experiment
notes, raw captures and extracted arithmetic-oracle blobs are not shipped.
Mesa/Zink/Vulkan frontend experiments remain offline; Hyprland uses llvmpipe.
