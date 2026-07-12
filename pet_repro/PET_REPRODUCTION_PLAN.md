# PET 复现计划

## 结论

可以复现，但要分两层看：

1. 论文机制已经分成两份 artifact：一个可快速测试的 user-space 模型，以及一份基于干净 Linux 6.1.44 的 clean-room 内核原型。
2. 论文性能数字必须靠裸机内核原型复现。PET 依赖 NUMA tiered memory、PTE access bit、fake page fault、`migrate_pages()` 和真实 DRAM/Optane 或 CXL-like 慢层。

我没有在公开网页检索到 PET 的 GitHub/Artifact 仓库；当前路线按论文文字重建。

## 从论文抽出的必要细节

| 项 | 论文设置 |
| --- | --- |
| 内核 | Linux 6.1.44 |
| 硬件 | Intel Xeon Platinum 8260 |
| 快层 | 4 x 16 GiB DRAM |
| 慢层 | 2 x 128 GiB Intel Optane DCPMM |
| P-block 来源 | anonymous `mmap()`/`brk()`/`mremap()` VMA，在映射成功后捕获 |
| 最大 P-block | 1024 MiB |
| temporary block | 128 MiB |
| sampling / scan | 500 ms / 10 s |
| canary ratio | 10% |
| promotion target | 3% tolerable slowdown, 8.3 us mis-demotion penalty, about 360 faults/s |
| workloads | Graph500, SPECspeed 2017, liblinear, GAPBS, Redis/YCSB, XSBench, DaCapo |

## 机制复现范围

| 机制 | 当前状态 | 下一步 |
| --- | --- | --- |
| P-block 状态机 | 已在 `pet_model.py` 和 `mm/pet.c` 实现 | 裸机验证 scan/sample 开销 |
| 多阶段 demotion | 已实现 NORMAL -> PHASE1 -> PHASE2 -> DEMOTED | 裸机验证实际 demotion/promotion 轨迹 |
| mixed P-block split | 已实现 PHASE2 hot/cold run split | 用 phase-shift microbenchmark 验证边界 |
| 访问采样 | 已实现论文 §4.2/Fig.6 的两阶段协议：interval N 清某随机页 accessed 位（arm），interval N+1 检查同一页；scan 判定要求至少一次完整 check（`samples_seen`），mmap_lock 竞争不会伪造冷信号 | 裸机验证冷检测延迟 |
| canary promotion | 已按阈值模型实现，fault handler 只计数/排队；无 canary 时 fault 路径直接跳过（`pet_have_poisoned`） | 裸机测 fake fault rate 和恢复时间 |
| file page demotion | 已在内核原型实现 inode `open_count`/cold-file processing generation；`generic_shutdown_super()` 会在 evict_inodes 前清空该 sb 的 PET 队列并等待在途处理，umount 不再遇到 PET 持有的 inode 引用 | 裸机验证文件页收益 |
| VMA 生命周期 | 已实现 successful-unmap 后 trim/split，覆盖 `mmap`/`brk`/`mremap` capture；小步扩展通过 full-VMA 补洞捕获，mremap 移动会继承 PET state/canary/fault metadata | 裸机验证 mmap/munmap/mremap churn |
| THP 行为 | 所有 page walk 不再隐式 split huge PMD：采样在 PMD 粒度读/清 accessed 位；full-range 迁移按整个 folio 隔离 PMD-mapped THP；canary-only 跳过 THP。唯一的主动 split 是在 huge PMD 上安装 canary（Thermostat 式 4KB 投毒）。残留：6.1.44 khugepaged 不跳过 PROT_NONE pte，khugepaged 活跃时 collapse 会吞掉 canary、下次 refresh 再 split，THP 实验建议关掉或 defer khugepaged | 对比 THP on/off 性能 |
| NUMA balancing 前提 | PET 复用 NUMA hint fault 的 PROT_NONE 编码；实验必须 `kernel.numa_balancing=0`，否则 canary 计数被污染且每个 hint fault 都付 PET fault 路径开销。开启 PET 时若 balancing 在开会打 pr_warn | 实验脚本里强制设置 |
| fork/exec | 子进程继承 canary PROT_NONE pte 但没有 P-block，此类 fault 由 `do_numa_page()` 恢复、不计数；PET 只跟踪父进程 mm（论文对 fork 沉默） | 文档已声明 |
| 真实性能 | 未生成 | 需要安装内核并跑 benchmark matrix |

## 建议内核路线

优先在一份干净 Linux 6.1.44 上实现 PET，不建议直接塞进当前 `deploy/kernel_patch` 的 RL/PEBS 系统；两者的 policy unit 不一样，混在一起会影响可解释性。

关键改动点：

| 模块 | 当前实现 |
| --- | --- |
| `mm/mmap.c` | 在 `mmap_region()`/`vma_expand()`/`brk` 路径捕获 anonymous P-block；使用 full-VMA gap-fill 处理小步扩展；在 unmap 成功后释放 |
| `mm/mremap.c` | 原地扩展使用 full-VMA gap-fill；移动 remap 在释放旧范围前复制 PET metadata，覆盖 `MREMAP_DONTUNMAP` |
| `include/linux/pet.h` | 暴露 PET hook；`CONFIG_PET_TIERING=n` 时提供 no-op stub |
| `mm/memory.c` | 在 NUMA hint fault 前识别 PET canary `PROT_NONE` fault 并计数 |
| `mm/pet.c` | 实现 P-block 元数据、partial-unmap trim/split、mremap state clone、`kdemoted`、`kpromoted`、canary PTE protection、阈值 promotion、file-page demotion 和 `/proc/pet/stats` |
| `mm/migrate.c` | 不改核心迁移代码，复用内核 `migrate_pages()` 做 DRAM <-> slow node migration |
| `fs/open.c`/`fs/file_table.c`/`fs/inode.c`/`include/linux/fs.h` | 实现 file page demotion 的 open count/cold list |

## 当前验证状态

| 验证 | 状态 |
| --- | --- |
| PET 相关对象编译 | passed（docker concord-kbuild x86_64 交叉编译，defconfig+PET_TIERING+THP，0 warning）: `mm/pet.o`, `mm/mmap.o`, `mm/mremap.o`, `mm/memory.o`, `fs/super.o`, `fs/open.o`, `fs/file_table.o`, `fs/inode.o` |
| THP=n 下 `mm/pet.o` | passed（`CONFIG_TRANSPARENT_HUGEPAGE=n`，验证 pmd_entry 的 `#ifdef` 路径） |
| PET_TIERING=n stub 编译 | passed（`fs/super.c` 等经由 `pet.h` no-op stub 编译全部 hook 文件） |
| patch dry-run | passed against clean Linux 6.1.44，且打完后与 `linux-6.1.44-pet` 树逐字节一致 |
| checkpatch --strict | 0 errors, 0 warnings；2 个 CHECK 为对局部 `spinlock_t *ptl` 变量的误报（内核 mm 代码惯用写法）；raw diff 仅缺 commit message 元数据 |
| user-space model tests | passed（模型本身即论文两阶段采样语义,内核修复后与模型一致） |
| hot-set microbenchmark smoke test | passed |
| full `vmlinux` | passed（docker concord-kbuild 交叉编译，defconfig+PET_TIERING+THP，`-d NETFILTER_XT_TARGET_TCPMSS` 因 macOS 大小写不敏感文件系统，与历史构建一致） |

## 剩余工程风险

| 风险 | 当前处理 |
| --- | --- |
| P-block 并发生命周期 | 已增加 P-block 引用计数、per-P-block mutex、`dying` 标记；`kdemoted`/`kpromoted` 的 page walk 和 migration 在全局 PET mutex 外执行，`exit_mmap()` 会等待在途 P-block 操作结束；bulk scan 使用 id 上界避免同轮处理 split 新块 |
| promotion fault window | 已改为 atomic generation，同步 fault handler 和 reset；global threshold 触发时即使某些 demoted P-block 正忙也会按当前窗口计数排队，避免漏 promote |
| file-page reopen/disable race | cold inode 迁移前从队列转入 processing，记录 generation；迁移期间 reopen 会触发 promote-back/requeue；运行时关闭 `file_demote_enabled`/`enabled`（proc 或 sysfs）会清空 cold-file 队列并释放 inode 引用，开关写入与 requeue 判定在同一把 `pet_cold_lock` 下序列化 |
| umount 生命周期 | `generic_shutdown_super()` 在 `evict_inodes()` 前调用 `pet_sb_shutdown()`：清空该 sb 的排队 inode、等待 processing/游离引用归零，避免 busy-inode 告警和 superblock 释放后的 iput UAF |
| canary PTE notifier | PROT_NONE install/restore 包裹 mmu-notifier range，降低 secondary MMU stale permission 风险 |
| THP canary-only 迁移 | canary-only 跳过 THP（canary 是 4KB 页）；隐式 PMD split 已消除，仅 canary 安装会主动 split 该 PMD；khugepaged 与 canary 的 collapse/re-split 交互见上表 |
| 全局锁面 | open/close 走独立 `pet_cold_lock` 自旋锁；fault 路径在无 canary 时零开销跳过；`pet_lock` 内不再做 `iput()`。fault 路径命中 canary 块时仍是全局锁+线性扫描，多进程高 fake-fault 压力下的开销需裸机确认 |

## 实验矩阵

| Claim | Reviewer question | Evidence needed | Benchmark | Baselines | Metrics | Status |
| --- | --- | --- | --- | --- | --- | --- |
| PET 在工作集超过快层时减少 slowdown | 是否优于非 proactive 和旧 proactive 方法 | 1.5x fast-memory capacity 配置 | Graph500, SPEC, liblinear, GAPBS, Redis | Base, Base+, AutoTiering, TPP, MGLRU-PD, DAMON, Thermostat | slowdown vs Fast-only, fast-memory usage | planned |
| PET 在工作集可放入快层时节省 DRAM | 是否能低开销主动腾快层 | 约 45 GiB footprints | 同上 + XSBench | Base+PD, Thermostat, DAMON | fast-memory saving, slowdown vs Base | planned |
| P-block tracking 响应相变 | hot set 变化后是否及时 promote | 8 threads, 8 x 1 GiB hot sets, 120 GiB total | Base+, Base+PD, DAMON | reaction time, throughput recovery | native runner ready |
| 多阶段 demotion 有必要 | PHASE1/PHASE2/canary 是否降低误迁 | ablation | synthetic + liblinear/GAPBS | no phase split, no canary, page-granularity | false demotion, fault count, slowdown | planned |
| adversarial case 边界 | Java heap 无 allocation-unit locality 时会怎样 | DaCapo H2 | Base+PD, DAMON, PET | slowdown, fast-memory usage | planned |

No experimental result has been generated here.  All paper-level numeric cells
must be filled from user-run experiments or verified matching artifacts.
