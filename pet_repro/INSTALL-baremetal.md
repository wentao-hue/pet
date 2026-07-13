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
| CPU | AMD EPYC 9754 (2 socket, 256 core / 512 thread) | `CONFIG_MAXSMP=y` (NR_CPUS 8192), `X86_X2APIC=y` |
| Firmware | EFI | GRUB loads the bzImage via its `linux` cmd |
| Root fs | ext4 on `/dev/sda3`, no LVM/mdraid/crypt | `EXT4_FS=y`, no initramfs needed |
| **Root disk controller** | **Broadcom/LSI MegaRAID 9560-8i** | **`MEGARAID_SAS=y` (else no boot)** |
| NIC (for SSH back) | Intel X710 10G, `i40e` | `I40E=y` (else no network after reboot) |
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
  -e EXT4_FS \
  -e X86_X2APIC -e AMD_IOMMU -e ACPI_NUMA \
  -e EFI -e EFI_STUB \
  --set-str LOCALVERSION "-pet"
make O=/w/kb olddefconfig >/dev/null
# Verify the boot-critical drivers are built in, not modules:
grep -E "^CONFIG_(MEGARAID_SAS|BLK_DEV_SD|SATA_AHCI|EXT4_FS|I40E|BLK_DEV_NVME|PET_TIERING|NUMA_BALANCING)=y" /w/kb/.config
make O=/w/kb -j"$(nproc)" bzImage'
# artifact: /tmp/out/kb/arch/x86/boot/bzImage  (~12 MB)
```

`make bzImage` only compiles the `=y` set — no `make modules`, no
`modules_install`. Substitute the survey drivers if the target differs
(the one you must get right is the **root disk controller**).

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

# 4b. add a GRUB entry (values filled for smicro-amd-two-numa)
sudo tee -a /etc/grub.d/40_custom >/dev/null <<'EOF'

menuentry 'Linux 6.1.44-pet (PET tiering)' {
    insmod gzio
    insmod part_gpt
    insmod ext2
    search --no-floppy --fs-uuid --set=root 78a3fad5-7d56-42ad-9a22-5862921a76d6
    echo 'Loading Linux 6.1.44-pet ...'
    linux /vmlinuz-6.1.44-pet root=PARTUUID=4054eaa7-060f-45b1-a497-65d0cef60c32 ro numa_balancing=disable pet.fast_node=0 pet.slow_node=1
}
EOF

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
nproc                                   # 512  (MAXSMP took effect)
cat /proc/sys/kernel/numa_balancing     # 0
cat /proc/pet/enabled                   # 0  (off by default, safe)
ls /proc/pet/                           # enabled, stats
```

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

## Notes / gotchas

- **The root disk controller is the one driver you cannot get wrong.**
  Here it is behind a MegaRAID HBA (`megaraid_sas`), not plain AHCI —
  `lsblk` shows `sda` but the controller is the RAID card. Miss it and
  the kernel boots then panics with "unable to mount root fs".
- `NR_CPUS` default (64) silently wastes a big machine — set MAXSMP.
- `i40e`/`megaraid_sas` need no external firmware; a monolithic kernel
  boots this box with no `/lib/firmware` and no initramfs.
- Fully reversible up to step 4: building and shipping change nothing on
  the target's boot path. Only step 4 touches `/boot` and GRUB, and the
  distro kernel entry is left intact as the default.
