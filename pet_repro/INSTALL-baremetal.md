# PET bare-metal install (custom monolithic kernel)

How to build and install the PET Linux 6.1.44 kernel on a bare-metal box
and boot it **remotely and safely**, without root on the build host and
without an initramfs.

This was written for one specific server (`smicro-amd-two-numa`), but the
method is general — re-run the survey step on any target and substitute
its facts.

---

## 0. Why this shape

- **Monolithic, no initramfs.** Every boot-critical driver is built in
  (`=y`), so the kernel finds root by itself and needs no initramfs.
  Install is then just "copy one file + add one GRUB entry".
- **Built off-box in a container.** The target lacked `libelf-dev`,
  passwordless `sudo`, and Docker access, so the kernel is cross-built in
  a container on another machine and the ~12 MB `bzImage` is shipped over.
- **Remote-safe boot.** The new kernel is booted **once** via
  `grub-reboot`; a failed boot falls back to the distro kernel on the
  next power cycle. A BMC/IPMI console is the required safety net.

---

## 1. Survey the target (no root needed)

Collect the facts the kernel config depends on. On the target:

```bash
uname -r                                   # distro kernel (fallback)
[ -d /sys/firmware/efi ] && echo EFI || echo BIOS
grep -m1 'model name' /proc/cpuinfo        # CPU
nproc                                      # core count -> NR_CPUS / MAXSMP
findmnt -no SOURCE,FSTYPE /                 # root device + fs
# root disk controller (THE make-or-break driver):
cat /sys/class/scsi_host/host0/proc_name   # driver behind sda's host
lspci -nnk | grep -iA2 -E 'SATA|RAID|NVMe|Ethernet'
# UUIDs for the GRUB entry:
lsblk -no NAME,PARTUUID,UUID,MOUNTPOINT /dev/sda
grep -E 'GRUB_DEFAULT|GRUB_TIMEOUT' /etc/default/grub
```

### Facts for smicro-amd-two-numa (recorded 2026-07)

| Fact | Value | Config consequence |
| --- | --- | --- |
| CPU | AMD EPYC 9754 (2 socket, 256 core / 512 thread) | `CONFIG_MAXSMP=y` (NR_CPUS 8192), `X86_X2APIC=y` **+ `IRQ_REMAP=y`** (else only 255 threads online) |
| Firmware | EFI | GRUB loads the bzImage via its `linux` cmd |
| Root fs | ext4 on `/dev/sda3`, no LVM/mdraid/crypt | `EXT4_FS=y`, no initramfs needed |
| **Root disk controller** | **Broadcom/LSI MegaRAID 9560-8i** | **`MEGARAID_SAS=y` (else no boot)** |
| NIC (for SSH back) | Intel X710 10G, `i40e`, **bonded** (`bond0` = f0+f1) | `I40E=y` **+ `BONDING=y`** (else no network after reboot) |
| NIC naming | netplan matches `enp1s0f0np0` / `…np1` | **`.link` rename** (see gotchas — 6.1 i40e omits the `np*` suffix) |
| Console | EFI-only, no VGA/framebuffer | **serial `console=ttyS0/ttyS1`** (else black screen, no output) |
| Other storage | AHCI SATA, NVMe | `SATA_AHCI=y`, `BLK_DEV_NVME=y` |
| `/boot` | separate part `sda2`, fs-uuid `78a3fad5-7d56-42ad-9a22-5862921a76d6` | GRUB `search --fs-uuid` target |
| `/` (root) | `sda3`, **PARTUUID `4054eaa7-060f-45b1-a497-65d0cef60c32`** | `root=PARTUUID=...` (kernel-native, no initramfs) |

> `root=UUID=<fs-uuid>` needs an initramfs (udev resolves it). Without
> an initramfs use `root=PARTUUID=<partition-uuid>`, which the kernel
> parses natively.

---

## 2. Build the kernel (in a container, no root on host)

Uses a cross-toolchain container (here `concord-kbuild`, providing
`x86_64-linux-gnu-gcc` and `libelf-dev`). The PET source tree is the
patched `linux-6.1.44-pet` (or apply `pet-linux-6.1.44-prototype.patch`
to a clean 6.1.44 tree first).

```bash
docker run --rm -v /path/to/linux-6.1.44-pet:/src -v /tmp/out:/w \
  -w /src concord-kbuild:latest bash -c '
set -e
export ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu-
make O=/w/kb x86_64_defconfig >/dev/null
scripts/config --file /w/kb/.config \
  -e MAXSMP \
  -e NUMA -e NUMA_BALANCING -e MIGRATION -e TRANSPARENT_HUGEPAGE \
  -e PET_TIERING \
  -e SCSI -e BLK_DEV_SD -e ATA -e SATA_AHCI \
  -e MEGARAID_SAS \
  -e BLK_DEV_NVME \
  -e NETDEVICES -e ETHERNET -e NET_VENDOR_INTEL -e I40E \
  -e BONDING -e VLAN_8021Q -e BRIDGE -e VETH -e TUN -e DUMMY -e OVERLAY_FS \
  -e EXT4_FS \
  -e X86_X2APIC -e IRQ_REMAP -e AMD_IOMMU -e ACPI_NUMA \
  -e EFI -e EFI_STUB \
  --set-str LOCALVERSION "-pet"
make O=/w/kb olddefconfig >/dev/null
# Verify the boot-critical drivers are built in, not modules:
grep -E "^CONFIG_(MEGARAID_SAS|BLK_DEV_SD|SATA_AHCI|EXT4_FS|I40E|BONDING|PET_TIERING|NUMA_BALANCING)=y" /w/kb/.config
make O=/w/kb -j"$(nproc)" bzImage'
# artifact: /tmp/out/kb/arch/x86/boot/bzImage  (~12 MB)
```

`make bzImage` only compiles the `=y` set — no `make modules`, no
`modules_install`. Substitute the survey drivers if the target differs
(the one you must get right is the **root disk controller**).

> **`defconfig` does not include the network stack the distro expects.**
> `x86_64_defconfig` ships the NIC driver family but *not* `BONDING`,
> `VLAN_8021Q`, `BRIDGE`, `VETH`, `TUN`. If netplan configures the box as
> a bond (this one does: `bond0` over the two i40e ports), a kernel
> without `CONFIG_BONDING` **boots with no reachable network** — SSH never
> returns even though the NIC driver loaded. Build in every netdev type
> your `/etc/netplan/*.yaml` references. `OVERLAY_FS` is here so Docker /
> containerd on the target still work under the custom kernel.
>
> **`X86_X2APIC` alone is not enough on a >255-thread box.** Without
> `IRQ_REMAP` the kernel disables x2APIC at boot (`x2apic: IRQ remapping
> doesn't support X2APIC mode` → `x2apic disabled`), so APIC IDs above 255
> are unreachable and only 255 of 512 logical CPUs come online. Add
> `IRQ_REMAP` (which also needs `AMD_IOMMU`, already set) to get all
> threads. See the CPU-count note in §5.

---

## 3. Ship it (no root needed)

```bash
scp /tmp/out/kb/arch/x86/boot/bzImage TARGET:~/vmlinuz-6.1.44-pet
# verify integrity end to end:
shasum -a256 /tmp/out/kb/arch/x86/boot/bzImage
ssh TARGET 'sha256sum ~/vmlinuz-6.1.44-pet'   # must match
```

---

## 4. Install + boot ONCE (on the target, needs sudo)

> **Confirm BMC/IPMI console access first.** A mid-boot hang can only be
> recovered from the console. A clean boot failure self-recovers via the
> one-shot below.

```bash
# 4a. place the kernel on /boot
sudo cp ~/vmlinuz-6.1.44-pet /boot/vmlinuz-6.1.44-pet

# 4b. add a GRUB entry (values filled for smicro-amd-two-numa).
#     The serial console params are REQUIRED on this EFI-only box: with no
#     framebuffer, tty0 shows nothing, so a stall is undiagnosable without
#     ttyS0/ttyS1 streaming to the BMC's Serial-over-LAN.
sudo tee -a /etc/grub.d/40_custom >/dev/null <<'EOF'

menuentry 'Linux 6.1.44-pet (PET tiering)' {
    insmod gzio
    insmod part_gpt
    insmod ext2
    search --no-floppy --fs-uuid --set=root 78a3fad5-7d56-42ad-9a22-5862921a76d6
    echo 'Loading Linux 6.1.44-pet ...'
    linux /vmlinuz-6.1.44-pet root=PARTUUID=4054eaa7-060f-45b1-a497-65d0cef60c32 ro numa_balancing=disable pet.fast_node=0 pet.slow_node=1 console=tty0 console=ttyS0,115200 console=ttyS1,115200 earlyprintk=serial,ttyS1,115200 ignore_loglevel
}
EOF

# 4b-bis. pin the NIC names netplan expects (see gotchas). The 6.1 i40e
#     driver names the ports enp1s0f0 / enp1s0f1, but netplan's bond0
#     matches enp1s0f0np0 / …np1 -> without this, no interface matches and
#     the box comes up with no network. Match by PERMANENT MAC (bonding
#     rewrites the runtime MAC of both slaves to a shared address).
sudo tee /etc/systemd/network/10-pet-nic0.link >/dev/null <<'EOF'
[Match]
PermanentMACAddress=90:5a:08:0a:bf:7c
[Link]
Name=enp1s0f0np0
EOF
sudo tee /etc/systemd/network/10-pet-nic1.link >/dev/null <<'EOF'
[Match]
PermanentMACAddress=90:5a:08:0a:bf:7d
[Link]
Name=enp1s0f1np1
EOF
# dry-run check the rename resolves before committing to a reboot:
sudo udevadm test-builtin net_setup_link /sys/class/net/enp1s0f0 2>&1 | grep -i 'Name\|ID_NET_NAME'

# 4c. enable one-shot selection, regenerate grub
sudo sed -i 's/^GRUB_DEFAULT=.*/GRUB_DEFAULT=saved/' /etc/default/grub
sudo update-grub
sudo grub-set-default 0          # persistent default stays the distro kernel
grep -c 'PET tiering' /boot/grub/grub.cfg   # expect 1

# 4d. boot the PET kernel exactly once, then reboot
sudo grub-reboot 'Linux 6.1.44-pet (PET tiering)'
sudo reboot
```

If SSH does not return within a few minutes, power-cycle from the BMC:
the one-shot is spent, so it boots the distro kernel again.

---

## 5. Verify after reboot

```bash
uname -r                                # 6.1.44-pet
nproc                                   # 255 without IRQ_REMAP, 512 with it (see below)
cat /proc/sys/kernel/numa_balancing     # 0
cat /proc/pet/enabled                   # 0  (off by default, safe)
ls /proc/pet/                           # enabled, stats
numactl -H                              # 2 nodes; confirm node1 has the memory PET will use
```

> **CPU count & a useful side effect.** The first working build omitted
> `IRQ_REMAP`, so x2APIC was disabled and only 255/512 threads came up —
> and *all* of socket 1's cores were the offline ones. That left **node 1
> as a CPU-less, memory-only node**, which is actually a *better* emulated
> slow tier: every access to it is remote, with no local CPU to
> "accidentally" keep its pages warm. If you want all 512 threads instead,
> add `IRQ_REMAP=y` (§2) and node 1 regains its cores. Either is fine for
> PET; just don't size a workload for 512 threads when only 255 are
> online.

## 6. Run PET

```bash
echo 1 | sudo tee /proc/pet/enabled     # fast_node=0 / slow_node=1 already on cmdline
cat /proc/pet/stats                     # captured_pblocks, demoted_pages, ...
# tunables live under /sys/module/pet/parameters/ (also set via cmdline pet.*)
```

Node 1 is a second DRAM socket used as an **emulated** slow tier (~1.5–2×
latency, interconnect-limited bandwidth), not a genuinely slow medium
(CXL/Optane). Mechanism and relative trends reproduce; absolute
fast-memory-saving / slowdown magnitudes are understated versus the
paper's Optane tier.

---

## 7. Verified: demotion + promotion on bare metal

A minimal anonymous-memory workload confirms both directions of the PET
cycle end to end. Bind 8 GB to the fast node, keep it hot, let it go
idle (demotion), then re-touch it (promotion):

```bash
# hog: allocate N GB on node0, hot H s, idle I s, then re-hot R s.
numactl --membind=0 --cpunodebind=0 ./pet-hog 8 20 60 40 &
# poll while it runs:
watch -n5 "grep -E 'demoted_pages|promoted_pages|fake_faults|n' /proc/pet/stats; \
           numactl -H | grep 'node 1 free'"
```

Observed (8 GB workload, `canary_ratio=10`, fast=0 / slow=1):

| Phase | Trigger | Signature in stats |
| --- | --- | --- |
| **Demote** | ~30 s into idle | `demoted_pages` +2,097,160 (≈8 GB); `node 1 free` −8192 MB; `canary_pages` first jumps ~10 % (PHASE2 pre-demote) |
| **Promote** | re-touching demoted pages | `fake_faults` climbs (canary/PROT_NONE PTE hits), then `promoted_pages` +2,097,806 (≈8 GB); `node 1 free` +8192 MB back |
| **Release** | `munmap` | `node 1 free` returns to baseline |

Both migrations match the 8 GB workload byte-for-byte with
`migration_failures=0`. Demotion is driven by going cold; promotion is
driven by fake faults on canary PTEs when the block is re-accessed —
exactly the paper's §4 mechanism. A source `pet-hog.c` (hot/idle/re-hot
phases) is the whole harness; PET reacts within ~one `scan_interval_ms`
(default 10 s) of each phase change.

---

## Notes / gotchas

- **The root disk controller is the one driver you cannot get wrong.**
  Here it is behind a MegaRAID HBA (`megaraid_sas`), not plain AHCI —
  `lsblk` shows `sda` but the controller is the RAID card. Miss it and
  the kernel boots then panics with "unable to mount root fs".
- `NR_CPUS` default (64) silently wastes a big machine — set MAXSMP.
- **A monolithic kernel must also carry the network *topology* drivers,
  not just the NIC.** `defconfig` gives you `i40e` but a distro configured
  for a bond/bridge/VLAN needs `BONDING`/`BRIDGE`/`VLAN_8021Q` built in
  too, or it boots with the NIC up but no reachable address. Symptom:
  clean boot on the serial console, SSH never returns. Cross-check against
  `/etc/netplan/*.yaml`.
- **NIC predictable-naming can differ between kernels.** 6.1's `i40e`
  names ports `enp1s0f0` (no `np0` suffix); 6.8+ adds `np0` via
  `phys_port_name`. If netplan matches the `np*` name, pin it with a
  `systemd/network/*.link` file — and match on **`PermanentMACAddress`**,
  because bonding overwrites each slave's runtime MAC with a shared one.
  Validate with `udevadm test-builtin net_setup_link` *before* rebooting.
- **On an EFI-only server there is no console until you ask for one.** No
  VGA/framebuffer means `tty0` is blank; add `console=ttyS0,115200`
  (+`ttyS1`) so boot streams to the BMC's Serial-over-LAN. Without it a
  boot stall looks identical to a dead machine.
- `i40e`/`megaraid_sas` need no external firmware; a monolithic kernel
  boots this box with no `/lib/firmware` and no initramfs.
- Fully reversible up to step 4: building and shipping change nothing on
  the target's boot path. Only step 4 touches `/boot` and GRUB, and the
  distro kernel entry is left intact as the default. Recovery from a bad
  boot is a BMC power-cycle (the one-shot is spent → distro kernel);
  `reset /system1/pwrmgtsvc1` on the Insyde SMASH-CLP shell, or the web
  iKVM "Power Reset".
