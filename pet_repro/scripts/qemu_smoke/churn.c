// Lifecycle stress for PET: mmap/mremap/munmap churn plus periodic fork
// (children touch inherited memory, including any canary PROT_NONE PTEs).
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define SLOTS 8
#define MIB (1UL << 20)

static unsigned long rs = 0x9e3779b9UL;
static unsigned long rnd(void)
{
	rs ^= rs << 13;
	rs ^= rs >> 7;
	rs ^= rs << 17;
	return rs;
}

int main(int argc, char **argv)
{
	struct { char *p; size_t sz; } slot[SLOTS] = { { 0 } };
	int secs = argc > 1 ? atoi(argv[1]) : 20;
	time_t end = time(NULL) + secs;
	unsigned long iter = 0;
	size_t o;
	int i, j;

	while (time(NULL) < end) {
		i = rnd() % SLOTS;
		if (!slot[i].p) {
			size_t sz = (2 + rnd() % 15) * 2 * MIB; /* 4..32 MiB */
			char *p = mmap(NULL, sz, PROT_READ | PROT_WRITE,
				       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
			if (p == MAP_FAILED)
				continue;
			memset(p, 1, sz);
			slot[i].p = p;
			slot[i].sz = sz;
		} else {
			switch (rnd() % 4) {
			case 0:
				munmap(slot[i].p, slot[i].sz);
				slot[i].p = NULL;
				break;
			case 1: /* tail-half unmap: exercises trim */
				if (slot[i].sz >= 8 * MIB) {
					munmap(slot[i].p + slot[i].sz / 2,
					       slot[i].sz / 2);
					slot[i].sz /= 2;
				}
				break;
			case 2: { /* grow, possibly moving: mremap inherit */
				char *np = mremap(slot[i].p, slot[i].sz,
						  slot[i].sz * 2,
						  MREMAP_MAYMOVE);
				if (np != MAP_FAILED) {
					slot[i].p = np;
					slot[i].sz *= 2;
					memset(np, 2, slot[i].sz);
				}
				break;
			}
			case 3:
				for (o = 0; o < slot[i].sz; o += 4096)
					slot[i].p[o]++;
				break;
			}
		}
		if (++iter % 64 == 0) {
			pid_t pid = fork();

			if (pid == 0) {
				for (j = 0; j < SLOTS; j++)
					if (slot[j].p)
						for (o = 0; o < slot[j].sz;
						     o += 4096)
							slot[j].p[o]++;
				_exit(0);
			}
			if (pid > 0)
				waitpid(pid, NULL, 0);
		}
		usleep(10000);
	}
	printf("CHURN: done after %lu iterations\n", iter);
	return 0;
}
