# Apple A9 (G5P) GPU on Linux: notes and tools

Notes and tools for running the GPU of the Apple A9 (S8000/S8003, iPad 5,
boards J71s/J71t/J72s/J72t) on Linux. The A9's GPU is a PowerVR Rogue
(Series 7XT, "G5P" in Apple's drivers) run by Apple's own firmware, with
Apple's own shader ISA.

**State (2026-11-02):** the Labwc desktop renders on the GPU (wlroots GLES2 ->
Zink -> our Vulkan driver -> our kernel driver) with pixel-exact output,
touch input and several GPU clients at once, each in its own GPU context with
fault isolation. Zink offers OpenGL ES 3.0 on the device; Hyprland on the GPU
is not there yet. Read [`docs/gpu/STATUS.md`](docs/gpu/STATUS.md) first.

This is experimental work by one person and an AI coding agent. Nothing here is
proposed upstream.

## The three repositories

| Repository | What |
|---|---|
| this one | notes, evidence summaries and reverse-engineering/capture tools |
| `brkosebay/linux`, branch `a9/g5p` | the kernel driver: **not published yet** |
| `brkosebay/mesa`, branch `g5p` | the Vulkan driver: **not published yet** |

The two code repositories follow once they contain nothing of Apple's and
nothing that depends on recorded Apple data. Today the Vulkan driver still
copies GPU programs recorded from iOS into every job (the helper programs
around each render), and the kernel's desktop boot still goes through lab
bring-up code. Both are being replaced by our own code first.

## Layout

- `docs/gpu/STATUS.md`: what works, what doesn't, what is being worked on.
- `docs/gpu/RESEARCH.md`, `firmware-format.md`, `fwif-comparison.md`,
  `core-configuration.md`, `agx-g5p-bringup.md`, `submission-g5p.md`: the
  firmware interface, boot and job submission.
- `docs/gpu/uapi.md`, `render-uapi.md`, `drm-design.md`,
  `multi-client-design.md`: the kernel driver's design and UAPI.
- `docs/gpu/firmware-re/`: firmware reverse-engineering notes (power words,
  wake latency, governor, TA kick).
- `docs/gpu-userspace/`: the shader ISA (`isa-g5p.md`, `ds-isa-state.md`),
  command streams and the Mesa plan.
- `docs/gpu/experiments/`: the device runs behind the current desktop
  (TA-hang fix, kexec quiesce, latency, MRT, transform feedback, WSI,
  partial redraw, depth/stencil, the arena fix, fault isolation, OpenGL ES
  3.0, the port of the A10 driver's compiler pieces, the iOS capture bundle,
  the GPU demo).
- `tools/ios-capture-bundle/`, `scripts/gpu/firmware-re/`,
  `tools/gpu-userspace/`, `scripts/gpu/mesa-a9-prod.sh`: capture, trace and
  build tools.

Paths are kept as they are in the private workspace, so cross-references
between published files work. Links to files that are not published are
marked "not published". The notes name Mesa branches by their workspace
names (`g5p-wsi`, `g5p-es3`, ...); kernel branch names like
`a9/gpu-multiclient2` or `a9/gpu-desk-next` are private lab branches. The
public code branches will fold them in.

## What is not here

- Nothing of Apple's: no firmware, kernelcache, kexts, dyld cache, IPSW
  contents, Metal/shader binaries, captured command buffers or GPU programs,
  and no decompiled code. The notes describe registers, structures and
  encodings in our own words and cite Apple code by address. A few short
  disassembly excerpts, encodings and values are kept where a finding needs
  them.
- Raw device logs, screenshots and per-device data (calibration, serials,
  MACs, syscfg/NVRAM). You need your own device and your own captures.
- The bulk of the 600+ dated lab notes; only the experiments that explain the
  current state are here.

## AI-written

The code and most of these notes were written by an AI coding agent (Claude)
working for the repository owner, who ran the device. Commits say so
(`Generated-by: LLM` in Mesa, `Co-Authored-By` in the kernel). Mesa's and
Imagination's upstream policies decline LLM-generated contributions
(`src/imagination/README.md` in Mesa), so please don't propose any of this
upstream.

## Credits

[HoolockLinux](https://github.com/HoolockLinux) (kernel and m1n1 for A7-A11),
Paul Praschl (A10 drivers carried on the kernel branch under his name), [Asahi Linux](https://asahilinux.org)
(the design reference), Imagination's open PowerVR drivers in Linux and Mesa
(the reference for Rogue encodings), and [OrchardLinux](https://github.com/OrchardLinux)
(the A10 GPU, with whom this work is shared).

## License

GPL-2.0-or-later (`LICENSE`), unless a file says otherwise. This covers our
text and tools only, not any Apple material they describe.
