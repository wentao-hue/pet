// SPDX-License-Identifier: GPL-2.0
/*
 * Hot-set shifting microbenchmark for PET reactiveness experiments.
 *
 * The PET paper uses eight threads, one 1 GiB hot set per thread, one-minute
 * phases, and a 120 GiB total working set.  Defaults here are smaller so the
 * binary can be smoke-tested on ordinary machines; pass the paper settings
 * explicitly on the target tiered-memory host.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define CACHELINE 64UL
#define GIB (1024UL * 1024UL * 1024UL)
#ifndef MAP_POPULATE
#define MAP_POPULATE 0
#endif

struct bench_cfg {
	size_t total_bytes;
	size_t hot_bytes;
	unsigned int threads;
	unsigned int phase_sec;
	unsigned int phases;
};

/* Cacheline-aligned so per-worker byte counters do not false-share. */
struct worker {
	pthread_t thread;
	struct bench_cfg *cfg;
	uint8_t *base;
	unsigned int id;
	uint64_t seed;
	_Atomic unsigned int *phase;
	atomic_bool *stop;
	_Atomic uint64_t bytes;
} __attribute__((aligned(CACHELINE)));

static uint64_t now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static uint64_t xorshift64(uint64_t *state)
{
	uint64_t x = *state;

	x ^= x << 13;
	x ^= x >> 7;
	x ^= x << 17;
	*state = x;
	return x;
}

static void *worker_fn(void *arg)
{
	struct worker *w = arg;
	struct bench_cfg *cfg = w->cfg;
	size_t lines_per_hot = cfg->hot_bytes / CACHELINE;
	size_t total_hot_sets = cfg->total_bytes / cfg->hot_bytes;
	uint64_t seed = w->seed;
	uint64_t bytes = 0;

	while (!atomic_load_explicit(w->stop, memory_order_relaxed)) {
		unsigned int phase = atomic_load_explicit(w->phase,
							  memory_order_relaxed);
		size_t set = (phase * cfg->threads + w->id) % total_hot_sets;
		size_t base_off = set * cfg->hot_bytes;
		size_t line = xorshift64(&seed) % lines_per_hot;
		volatile uint64_t *ptr;

		ptr = (volatile uint64_t *)(w->base + base_off +
					    line * CACHELINE);
		*ptr += 1;
		bytes += CACHELINE;
		/* Single writer: a relaxed store is enough for the reader. */
		atomic_store_explicit(&w->bytes, bytes, memory_order_relaxed);
	}
	w->seed = seed;
	return NULL;
}

static unsigned long parse_ul(const char *s, const char *name)
{
	char *end = NULL;
	unsigned long v;

	errno = 0;
	v = strtoul(s, &end, 0);
	if (errno || !end || *end) {
		fprintf(stderr, "invalid %s: %s\n", name, s);
		exit(2);
	}
	return v;
}

static void usage(const char *prog)
{
	fprintf(stderr,
		"usage: %s [--total-gb N] [--hot-gb N] [--threads N] "
		"[--phase-sec N] [--phases N]\n",
		prog);
}

int main(int argc, char **argv)
{
	struct bench_cfg cfg = {
		.total_bytes = 8UL * GIB,
		.hot_bytes = 1UL * GIB,
		.threads = 8,
		.phase_sec = 10,
		.phases = 8,
	};
	struct worker *workers;
	_Atomic unsigned int phase = 0;
	atomic_bool stop = false;
	uint8_t *base;
	unsigned int i;

	for (i = 1; i < (unsigned int)argc; i++) {
		if (!strcmp(argv[i], "--total-gb") && i + 1 < (unsigned int)argc)
			cfg.total_bytes = parse_ul(argv[++i], "total-gb") * GIB;
		else if (!strcmp(argv[i], "--hot-gb") && i + 1 < (unsigned int)argc)
			cfg.hot_bytes = parse_ul(argv[++i], "hot-gb") * GIB;
		else if (!strcmp(argv[i], "--threads") && i + 1 < (unsigned int)argc)
			cfg.threads = parse_ul(argv[++i], "threads");
		else if (!strcmp(argv[i], "--phase-sec") && i + 1 < (unsigned int)argc)
			cfg.phase_sec = parse_ul(argv[++i], "phase-sec");
		else if (!strcmp(argv[i], "--phases") && i + 1 < (unsigned int)argc)
			cfg.phases = parse_ul(argv[++i], "phases");
		else {
			usage(argv[0]);
			return 2;
		}
	}

	if (!cfg.threads || !cfg.hot_bytes || cfg.total_bytes < cfg.hot_bytes ||
	    cfg.total_bytes / cfg.hot_bytes < cfg.threads ||
	    cfg.total_bytes / GIB > (1UL << 20)) {
		fprintf(stderr, "invalid geometry\n");
		return 2;
	}
	/*
	 * set = (phase * threads + id) % total_hot_sets shifts by `threads`
	 * sets per phase; if total_hot_sets divides threads the hot set
	 * never moves and the run silently measures a static workload.
	 */
	if (cfg.phases > 1 &&
	    cfg.threads % (cfg.total_bytes / cfg.hot_bytes) == 0) {
		fprintf(stderr,
			"hot sets would never shift: need total-gb > hot-gb * threads\n");
		return 2;
	}

	base = mmap(NULL, cfg.total_bytes, PROT_READ | PROT_WRITE,
		    MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
	if (base == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	workers = calloc(cfg.threads, sizeof(*workers));
	if (!workers) {
		perror("calloc");
		return 1;
	}

	for (i = 0; i < cfg.threads; i++) {
		workers[i].cfg = &cfg;
		workers[i].base = base;
		workers[i].id = i;
		workers[i].seed = 0x9e3779b97f4a7c15ULL ^ (uint64_t)i;
		workers[i].phase = &phase;
		workers[i].stop = &stop;
		if (pthread_create(&workers[i].thread, NULL, worker_fn,
				   &workers[i])) {
			perror("pthread_create");
			return 1;
		}
	}

	printf("second,phase,throughput_gbytes_per_sec\n");
	for (i = 0; i < cfg.phases; i++) {
		uint64_t before = 0;
		uint64_t after = 0;
		uint64_t start;
		unsigned int t;

		for (t = 0; t < cfg.threads; t++)
			before += atomic_load_explicit(&workers[t].bytes,
						       memory_order_relaxed);
		atomic_store_explicit(&phase, i, memory_order_relaxed);
		start = now_ns();
		sleep(cfg.phase_sec);
		for (t = 0; t < cfg.threads; t++)
			after += atomic_load_explicit(&workers[t].bytes,
						      memory_order_relaxed);
		printf("%u,%u,%.6f\n", (i + 1) * cfg.phase_sec, i,
		       (double)(after - before) /
			       ((double)(now_ns() - start) / 1e9) / 1e9);
		fflush(stdout);
	}

	atomic_store_explicit(&stop, true, memory_order_relaxed);
	for (i = 0; i < cfg.threads; i++)
		pthread_join(workers[i].thread, NULL);

	munmap(base, cfg.total_bytes);
	free(workers);
	return 0;
}
