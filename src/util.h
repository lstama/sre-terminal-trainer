/*
 * util.h - small, bounded file/path helpers shared by the verifier.
 *
 * Every helper here is deliberately paranoid: fixed caps, explicit error
 * returns, no reliance on PATH_MAX-sized stack buffers inside callers.
 */
#ifndef SRE_UTIL_H
#define SRE_UTIL_H

#include <stddef.h>

/*
 * Read a whole file into `buf`. The buffer is always NUL-terminated.
 * Returns 0 on success, -1 if the file could not be opened, -2 if it does not
 * fit in `cap` bytes (including the NUL), -3 on read error.
 */
int read_file_bounded(const char *path, char *buf, size_t cap, size_t *out_len);

/* Create/truncate `path` (mode 0600) and write exactly `len` bytes. */
int write_file(const char *path, const char *data, size_t len);

/* Append exactly `len` bytes to `path` (created mode 0600 if absent). */
int append_file(const char *path, const char *data, size_t len);

/* 1 if `path` exists (any type), 0 otherwise. */
int file_exists(const char *path);

/* mkdir -p with an explicit mode (mode is applied to each created component). */
int mkdir_p(const char *path, unsigned int mode);

/* 1 if `s` is 1..maxlen chars of [A-Za-z0-9_-], 0 otherwise. */
int valid_id(const char *s, size_t maxlen);

/* 1 if `s` is exactly 64 lowercase/uppercase hex chars. */
int valid_hex64(const char *s);

/* Strip leading and trailing ASCII whitespace, in place. */
void trim_ws(char *s);

/* Hash a file in fixed-size chunks; writes 64 hex chars + NUL into `out`.
 * Returns 0 on success, nonzero on I/O error. */
int sha256_file_hex(const char *path, char out[65]);

#endif /* SRE_UTIL_H */
