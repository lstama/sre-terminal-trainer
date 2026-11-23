/*
 * sha256.h - self-contained SHA-256 (FIPS 180-4) for the SRE trainer verifier.
 *
 * Deliberately dependency-free (no OpenSSL) so the verifier can be linked
 * statically and stay tiny inside the container image.
 */
#ifndef SRE_SHA256_H
#define SRE_SHA256_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t h[8];              /* running chaining state */
    uint64_t total;             /* total bytes fed in, for the length field */
    unsigned char buf[64];      /* partial block */
    size_t buflen;              /* bytes currently held in buf */
} sha256_ctx;

void sha256_init(sha256_ctx *c);
void sha256_update(sha256_ctx *c, const void *data, size_t len);
void sha256_final(sha256_ctx *c, unsigned char out[32]);

/* Convenience: digest `len` bytes and write 64 lowercase hex chars + NUL. */
void sha256_hex(const void *data, size_t len, char out[65]);

#endif /* SRE_SHA256_H */
