/*
 * sremock.c - busybox-style dispatcher for the troubleshoot pilot.
 *
 * The runtime image installs one static binary and symlinks each mocked command
 * name to it. argv[0] selects the command; a small amount of argument parsing
 * selects the fixture when one command has several forms (journalctl, curl,
 * docker/podman and kubectl). Fixture bytes are copied to stdout unchanged.
 *
 * This binary is deliberately not setuid and needs no privileges. The default
 * fixture root is /var/lib/sremock/current; SREMOCK_ROOT is a host-test escape
 * hatch, not a runtime configuration interface.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEFAULT_ROOT "/var/lib/sremock/current"

static const char *command_name(const char *arg0)
{
    const char *slash;

    if (arg0 == NULL || *arg0 == '\0')
        return "";
    slash = strrchr(arg0, '/');
    return slash == NULL ? arg0 : slash + 1;
}

static int is_arg(const char *arg, const char *expected)
{
    return arg != NULL && strcmp(arg, expected) == 0;
}

static int nonempty(const char *arg)
{
    return arg != NULL && *arg != '\0';
}

/* journalctl has two supported forms: -xe, or a unit query with 100 lines. */
static int journalctl_fixture(int argc, char **argv, const char **fixture)
{
    int have_unit = 0;
    int have_lines = 0;
    int have_no_pager = 0;
    int i;

    if (argc == 2 && is_arg(argv[1], "-xe")) {
        *fixture = "journalctl_xe.txt";
        return 1;
    }

    for (i = 1; i < argc; ++i) {
        if (is_arg(argv[i], "-u")) {
            if (++i >= argc || !nonempty(argv[i]) || argv[i][0] == '-')
                return 0;
            have_unit = 1;
        } else if (is_arg(argv[i], "-n")) {
            if (++i >= argc || !is_arg(argv[i], "100"))
                return 0;
            have_lines = 1;
        } else if (is_arg(argv[i], "--no-pager")) {
            have_no_pager = 1;
        } else {
            return 0;
        }
    }

    if (have_unit && have_lines && have_no_pager) {
        *fixture = "journalctl_unit.txt";
        return 1;
    }
    return 0;
}

static int kubectl_fixture(int argc, char **argv, const char **fixture)
{
    if (argc < 3 || !is_arg(argv[1], "get"))
        return 0;

    if (is_arg(argv[2], "pods") && argc == 3) {
        *fixture = "kubectl_get_pods.txt";
        return 1;
    }
    if ((is_arg(argv[2], "svc") || is_arg(argv[2], "endpoints"))
        && (argc == 3 || (argc == 4 && nonempty(argv[3])))) {
        *fixture = is_arg(argv[2], "svc")
            ? "kubectl_get_svc.txt" : "kubectl_get_endpoints.txt";
        return 1;
    }
    if (is_arg(argv[2], "events") && argc == 3) {
        *fixture = "kubectl_get_events.txt";
        return 1;
    }
    if (is_arg(argv[2], "configmap") && argc == 4 && nonempty(argv[3])) {
        *fixture = "kubectl_get_configmap.txt";
        return 1;
    }
    if (is_arg(argv[2], "deploy") && argc >= 3) {
        *fixture = "kubectl_get_deploy.txt";
        return 1;
    }
    return 0;
}

static int container_fixture(const char *command, int argc, char **argv,
                             const char **fixture)
{
    if (argc == 3 && is_arg(argv[1], "ps") && is_arg(argv[2], "-a")) {
        *fixture = is_arg(command, "docker") ? "docker_ps_a.txt" : "podman_ps_a.txt";
        return 1;
    }
    if (argc >= 2 && is_arg(argv[1], "logs")) {
        *fixture = is_arg(command, "docker") ? "docker_logs.txt" : "podman_logs.txt";
        return 1;
    }
    if (argc >= 2 && is_arg(argv[1], "inspect")) {
        *fixture = is_arg(command, "docker") ? "docker_inspect.txt" : "podman_inspect.txt";
        return 1;
    }

    return 0;
}

/* Return the flat fixture name for a supported invocation, else NULL. */
static const char *select_fixture(int argc, char **argv)
{
    const char *command = command_name(argc > 0 ? argv[0] : NULL);
    const char *fixture = NULL;

    if (is_arg(command, "systemctl")) {
        if (argc == 3 && is_arg(argv[1], "status") && nonempty(argv[2]))
            return "systemctl_status.txt";
    } else if (is_arg(command, "journalctl")) {
        if (journalctl_fixture(argc, argv, &fixture))
            return fixture;
    } else if (is_arg(command, "kubectl")) {
        if (argc == 4 && is_arg(argv[1], "describe") &&
            is_arg(argv[2], "pod") && nonempty(argv[3]))
            return "kubectl_describe_pod.txt";
        if (argc == 3 && is_arg(argv[1], "logs") && nonempty(argv[2]))
            return "kubectl_logs.txt";
        if (kubectl_fixture(argc, argv, &fixture))
            return fixture;
    } else if (is_arg(command, "docker") || is_arg(command, "podman")) {
        if (container_fixture(command, argc, argv, &fixture))
            return fixture;
    } else if (is_arg(command, "ps")) {
        if (argc == 2 && is_arg(argv[1], "aux"))
            return "ps_aux.txt";
    } else if (is_arg(command, "ss")) {
        if (argc == 2 && is_arg(argv[1], "-tlnp"))
            return "ss_tlnp.txt";
    } else if (is_arg(command, "curl")) {
        if (argc >= 3 && nonempty(argv[2])) {
            if (is_arg(argv[1], "-v"))
                return "curl_v.txt";
            if (is_arg(argv[1], "-I"))
                return "curl_I.txt";
        }
    } else if (is_arg(command, "df")) {
        if (argc == 2 && is_arg(argv[1], "-h"))
            return "df_h.txt";
    } else if (is_arg(command, "free")) {
        if (argc == 2 && is_arg(argv[1], "-m"))
            return "free_m.txt";
    } else if (is_arg(command, "top")) {
        if (argc == 1)
            return "top.txt";
    } else if (is_arg(command, "du")) {
        if (argc >= 3 && is_arg(argv[1], "-sh") && nonempty(argv[2]))
            return "du_sh.txt";
    } else if (is_arg(command, "nginx")) {
        if (argc == 2 && is_arg(argv[1], "-t"))
            return "nginx_t.txt";
    } else if (is_arg(command, "getenforce")) {
        if (argc == 1)
            return "getenforce.txt";
    } else if (is_arg(command, "ausearch")) {
        if (argc == 3 && is_arg(argv[1], "-m") && is_arg(argv[2], "avc"))
            return "ausearch_avc.txt";
    } else if (is_arg(command, "dig")) {
        return "dig.txt";
    } else if (is_arg(command, "nslookup")) {
        return "nslookup.txt";
    } else if (is_arg(command, "ping")) {
        return "ping.txt";
    } else if (is_arg(command, "nc")) {
        if (argc >= 2 && is_arg(argv[1], "-zv"))
            return "nc_zv.txt";
    }

    return NULL;
}

static int fixture_path(char **out, const char *root, const char *name)
{
    size_t root_len = strlen(root);
    size_t name_len = strlen(name);
    size_t total;
    char *path;

    if (root_len > (size_t)-1 - name_len - 2) {
        errno = ENAMETOOLONG;
        return -1;
    }
    total = root_len + 1 + name_len + 1;
    path = malloc(total);
    if (path == NULL)
        return -1;

    memcpy(path, root, root_len);
    path[root_len] = '/';
    memcpy(path + root_len + 1, name, name_len + 1);
    *out = path;
    return 0;
}

static int print_fixture(const char *root, const char *name)
{
    char *path = NULL;
    unsigned char buffer[16384];
    FILE *file;
    size_t count;
    int saved_errno;

    if (fixture_path(&path, root, name) != 0) {
        fprintf(stderr, "sremock: cannot build fixture path: %s\n", strerror(errno));
        return 1;
    }

    file = fopen(path, "rb");
    if (file == NULL) {
        saved_errno = errno;
        fprintf(stderr, "sremock: cannot read %s: %s\n", path, strerror(saved_errno));
        free(path);
        return 1;
    }

    while ((count = fread(buffer, 1, sizeof(buffer), file)) != 0) {
        if (fwrite(buffer, 1, count, stdout) != count) {
            saved_errno = errno != 0 ? errno : EIO;
            fprintf(stderr, "sremock: cannot write fixture output: %s\n", strerror(saved_errno));
            fclose(file);
            free(path);
            return 1;
        }
    }
    if (ferror(file)) {
        saved_errno = errno != 0 ? errno : EIO;
        fprintf(stderr, "sremock: cannot read %s: %s\n", path, strerror(saved_errno));
        fclose(file);
        free(path);
        return 1;
    }
    if (fclose(file) != 0) {
        saved_errno = errno != 0 ? errno : EIO;
        fprintf(stderr, "sremock: cannot close %s: %s\n", path, strerror(saved_errno));
        free(path);
        return 1;
    }
    if (fflush(stdout) != 0) {
        saved_errno = errno != 0 ? errno : EIO;
        fprintf(stderr, "sremock: cannot write fixture output: %s\n", strerror(saved_errno));
        free(path);
        return 1;
    }

    free(path);
    return 0;
}

int main(int argc, char **argv)
{
    const char *command = command_name(argc > 0 ? argv[0] : NULL);
    const char *fixture = select_fixture(argc, argv);
    const char *root = getenv("SREMOCK_ROOT");

    if (fixture == NULL) {
        fprintf(stderr, "sremock: unsupported invocation: %s\n",
                *command == '\0' ? "(unknown)" : command);
        return 1;
    }
    if (root == NULL || *root == '\0')
        root = DEFAULT_ROOT;
    return print_fixture(root, fixture);
}
