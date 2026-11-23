/*
 * util.c - bounded file/path helpers for the verifier.
 */
#include "util.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "sha256.h"

/* O_NOFOLLOW keeps a hostile symlink from redirecting a state-file read/write
 * even though the state dir itself is root-only. Defined by POSIX on Linux. */
#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0
#endif

int read_file_bounded(const char *path, char *buf, size_t cap, size_t *out_len)
{
    int fd;
    size_t total = 0;
    ssize_t n;

    if (cap == 0)
        return -2;
    buf[0] = '\0';

    fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
        return -1;

    while (total < cap - 1) {
        n = read(fd, buf + total, cap - 1 - total);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            close(fd);
            buf[total] = '\0';
            return -3;
        }
        if (n == 0)
            break;
        total += (size_t)n;
    }

    /* If we filled the buffer, probe for one more byte to detect truncation
     * instead of silently returning a half-read file. */
    if (total == cap - 1) {
        char probe;
        n = read(fd, &probe, 1);
        if (n > 0) {
            close(fd);
            buf[total] = '\0';
            return -2;
        }
    }

    close(fd);
    buf[total] = '\0';
    if (out_len)
        *out_len = total;
    return 0;
}

static int write_all(int fd, const char *data, size_t len)
{
    while (len > 0) {
        ssize_t n = write(fd, data, len);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            return -1;
        data += (size_t)n;
        len -= (size_t)n;
    }
    return 0;
}

int write_file(const char *path, const char *data, size_t len)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0)
        return -1;
    if (write_all(fd, data, len) != 0) {
        close(fd);
        return -1;
    }
    if (close(fd) != 0)
        return -1;
    return 0;
}

int append_file(const char *path, const char *data, size_t len)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0)
        return -1;
    if (write_all(fd, data, len) != 0) {
        close(fd);
        return -1;
    }
    if (close(fd) != 0)
        return -1;
    return 0;
}

int file_exists(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0;
}

int mkdir_p(const char *path, unsigned int mode)
{
    char tmp[4096];
    size_t len, i;

    len = strlen(path);
    if (len == 0 || len >= sizeof(tmp))
        return -1;
    memcpy(tmp, path, len + 1);

    for (i = 1; i < len; i++) {
        if (tmp[i] != '/')
            continue;
        tmp[i] = '\0';
        if (mkdir(tmp, (mode_t)mode) != 0 && errno != EEXIST)
            return -1;
        tmp[i] = '/';
    }
    if (mkdir(tmp, (mode_t)mode) != 0 && errno != EEXIST)
        return -1;
    return 0;
}

int valid_id(const char *s, size_t maxlen)
{
    size_t i, n;

    if (s == NULL)
        return 0;
    n = strlen(s);
    if (n == 0 || n > maxlen)
        return 0;
    for (i = 0; i < n; i++) {
        unsigned char ch = (unsigned char)s[i];
        if (!(isalnum(ch) || ch == '_' || ch == '-'))
            return 0;
    }
    return 1;
}

int valid_hex64(const char *s)
{
    size_t i;

    if (s == NULL || strlen(s) != 64)
        return 0;
    for (i = 0; i < 64; i++) {
        if (!isxdigit((unsigned char)s[i]))
            return 0;
    }
    return 1;
}

void trim_ws(char *s)
{
    char *start, *end;

    if (s == NULL)
        return;
    start = s;
    while (*start && isspace((unsigned char)*start))
        start++;
    if (start != s)
        memmove(s, start, strlen(start) + 1);

    end = s + strlen(s);
    while (end > s && isspace((unsigned char)end[-1]))
        end--;
    *end = '\0';
}

int sha256_file_hex(const char *path, char out[65])
{
    static unsigned char chunk[65536];
    sha256_ctx ctx;
    unsigned char digest[32];
    static const char hexdigits[] = "0123456789abcdef";
    int fd, i;

    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -1;

    sha256_init(&ctx);
    for (;;) {
        ssize_t n = read(fd, chunk, sizeof(chunk));
        if (n < 0) {
            if (errno == EINTR)
                continue;
            close(fd);
            return -1;
        }
        if (n == 0)
            break;
        sha256_update(&ctx, chunk, (size_t)n);
    }
    close(fd);

    sha256_final(&ctx, digest);
    for (i = 0; i < 32; i++) {
        out[i * 2] = hexdigits[digest[i] >> 4];
        out[i * 2 + 1] = hexdigits[digest[i] & 0x0f];
    }
    out[64] = '\0';
    return 0;
}
