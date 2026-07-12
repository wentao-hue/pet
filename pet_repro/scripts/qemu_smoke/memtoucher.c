// Minimal PET smoke workload: populate one anon P-block, go idle so it
// demotes, then re-touch so canary faults drive promotion.
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(void)
{
	size_t sz = 256UL << 20;
	char *p = mmap(NULL, sz, PROT_READ | PROT_WRITE,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	int round;
	size_t off;

	if (p == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	memset(p, 1, sz);
	printf("MEMTOUCHER: populated %zu MiB, going idle\n", sz >> 20);
	fflush(stdout);
	sleep(20);
	printf("MEMTOUCHER: re-touching\n");
	fflush(stdout);
	for (round = 0; round < 40; round++) {
		for (off = 0; off < sz; off += 4096)
			p[off]++;
		usleep(250000);
	}
	printf("MEMTOUCHER: done, sleeping\n");
	fflush(stdout);
	for (;;)
		sleep(60);
}
