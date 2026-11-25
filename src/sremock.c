/*
 * sremock.c - busybox-style dispatcher for the troubleshoot profile.
 *
 * The runtime image installs one static binary and symlinks each mocked command
 * name to it. argv[0] selects the command; explicit parsers select a fixture or
 * a command-shaped error. Resource names are validated against a table compiled
 * into the binary at build time; user input never becomes part of a file path.
 *
 * This binary is deliberately not setuid and needs no privileges. The default
 * fixture root is /var/lib/sremock/current; SREMOCK_ROOT is a host-test escape
 * hatch, not a runtime configuration interface.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define DEFAULT_ROOT "/var/lib/sremock/current"
#define ARRAY_LEN(a) (sizeof(a) / sizeof((a)[0]))

typedef struct {
    const char *key;
    const char *name;
} ValidName;

typedef struct {
    const char *fixture;
    int status;
} Selection;

/* Docker builds with -DSREMOCK_VALID_NAMES=1 -I/generated.
 * Standalone host builds without a generated table retain an empty fallback. */
#ifndef SREMOCK_VALID_NAMES
#define SREMOCK_VALID_NAMES 0
#endif

#if SREMOCK_VALID_NAMES
#include "valid_names.inc"
#else
static const ValidName valid_names[] = {
    {NULL, NULL},
};
#define VALID_NAME_COUNT \
    (sizeof(valid_names) / sizeof(valid_names[0]) - 1)
#endif

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

static int option_equals(const char *arg, const char *option, const char **value)
{
    size_t len = strlen(option);

    if (strncmp(arg, option, len) == 0 && arg[len] == '=') {
        *value = arg + len + 1;
        return 1;
    }
    return 0;
}

static size_t unit_base_len(const char *unit)
{
    size_t len = strlen(unit);

    if (len > 8 && strcmp(unit + len - 8, ".service") == 0)
        return len - 8;
    return len;
}

static int same_unit(const char *left, const char *right)
{
    size_t left_len = unit_base_len(left);
    size_t right_len = unit_base_len(right);

    return left_len == right_len && left_len > 0 &&
           strncmp(left, right, left_len) == 0;
}

static int valid_unit(const char *unit)
{
    size_t i;

    for (i = 0; i < ARRAY_LEN(valid_names); ++i) {
        if (valid_names[i].key == NULL)
            break;
        if (strcmp(valid_names[i].key, "unit") == 0 &&
            same_unit(valid_names[i].name, unit))
            return 1;
    }
    return 0;
}

static int valid_name(const char *key, const char *name)
{
    size_t i;

    for (i = 0; i < ARRAY_LEN(valid_names); ++i) {
        if (valid_names[i].key == NULL)
            break;
        if (strcmp(valid_names[i].key, key) == 0 &&
            strcmp(valid_names[i].name, name) == 0)
            return 1;
    }
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Alias tables: parsing and help deliberately share these definitions.       */
/* ------------------------------------------------------------------------- */

typedef struct {
    const char *canonical;
    const char *const *aliases;
    size_t alias_count;
    const char *fixture;
    int allow_list;
    int allow_name;
} KubectlResource;

static const char *const POD_ALIASES[] = {"pod", "pods", "po"};
static const char *const CM_ALIASES[] = {"configmap", "configmaps", "cm"};
static const char *const EP_ALIASES[] = {"endpoint", "endpoints", "ep"};
static const char *const EV_ALIASES[] = {"event", "events", "ev"};
static const char *const SVC_ALIASES[] = {"service", "services", "svc"};
static const char *const DEPLOY_ALIASES[] = {
    "deployment", "deployments", "deploy"
};

static const KubectlResource KUBECTL_RESOURCES[] = {
    {"pods", POD_ALIASES, ARRAY_LEN(POD_ALIASES), "kubectl_get_pods.txt", 1, 1},
    {"configmaps", CM_ALIASES, ARRAY_LEN(CM_ALIASES), "kubectl_get_configmap.txt", 1, 1},
    {"endpoints", EP_ALIASES, ARRAY_LEN(EP_ALIASES), "kubectl_get_endpoints.txt", 1, 1},
    {"events", EV_ALIASES, ARRAY_LEN(EV_ALIASES), "kubectl_get_events.txt", 1, 0},
    {"services", SVC_ALIASES, ARRAY_LEN(SVC_ALIASES), "kubectl_get_svc.txt", 1, 1},
    {"deployments", DEPLOY_ALIASES, ARRAY_LEN(DEPLOY_ALIASES), "kubectl_get_deploy.txt", 1, 1},
};

static const char *const CONTAINER_PS_ALIASES[] = {"ps", "ls", "list"};
static const char *const CONTAINER_LOGS_ALIASES[] = {"logs"};
static const char *const CONTAINER_INSPECT_ALIASES[] = {"inspect"};

static int alias_index(const char *const *aliases, size_t count, const char *arg)
{
    size_t i;

    for (i = 0; i < count; ++i) {
        if (is_arg(aliases[i], arg))
            return (int)i;
    }
    return -1;
}

/* ------------------------------------------------------------------------- */
/* Help                                                                      */
/* ------------------------------------------------------------------------- */

static void print_general_help(void)
{
    puts("Usage: sremock COMMAND [ARGS ...]");
    puts("       sremock -h|--help");
    puts("");
    puts("Fixture-backed SRE diagnostic commands:");
    puts("  systemctl journalctl kubectl docker podman");
    puts("  ps ss curl df free top du nginx getenforce ausearch");
    puts("  dig nslookup ping nc");
    puts("");
    puts("Run COMMAND --help for the mock-supported forms.");
}

static void print_kubectl_help(void)
{
    size_t i;
    size_t j;

    puts("Usage:");
    for (i = 0; i < ARRAY_LEN(KUBECTL_RESOURCES); ++i) {
        const KubectlResource *resource = &KUBECTL_RESOURCES[i];

        if (resource->allow_list)
            printf("  kubectl get %s\n", resource->canonical);
        if (resource->allow_name) {
            printf("  kubectl get %s NAME\n", resource->canonical);
            printf("  kubectl get %s/NAME\n", resource->canonical);
        }
    }
    puts("  kubectl describe pod NAME|pod/NAME");
    puts("  kubectl logs POD|pod/POD");
    puts("  kubectl -h|--help");
    puts("");
    puts("Aliases:");
    for (i = 0; i < ARRAY_LEN(KUBECTL_RESOURCES); ++i) {
        const KubectlResource *resource = &KUBECTL_RESOURCES[i];

        printf("  %-12s", resource->canonical);
        for (j = 0; j < resource->alias_count; ++j)
            printf(" %s", resource->aliases[j]);
        putchar('\n');
    }
    puts("  Resource names are case-insensitive; singular, plural and short forms match.");
    puts("");
    puts("Options:");
    puts("  -n, --namespace VALUE    select a namespace (fixture is unchanged)");
    puts("  --namespace=VALUE        long namespace form");
    puts("  -o, --output FORMAT      select output format (fixture is unchanged)");
    puts("  --output=FORMAT          long output form");
    puts("  -A, --all-namespaces     all namespaces (fixture is unchanged)");
    puts("  -h, --help               display this help and exit");
}

static void print_systemctl_help(void)
{
    puts("SYSTEMCTL(1)                 sremock manual                 SYSTEMCTL(1)");
    puts("");
    puts("NAME");
    puts("       systemctl - inspect one fixture-backed service unit");
    puts("");
    puts("SYNOPSIS");
    puts("       systemctl status UNIT");
    puts("       systemctl status UNIT.service");
    puts("       systemctl -h|--help");
    puts("");
    puts("DESCRIPTION");
    puts("       The .service suffix is optional. A name absent from this build's");
    puts("       compiled resource table is reported as a missing unit.");
    puts("");
    puts("OPTIONS");
    puts("       -h, --help");
    puts("              Display this help and exit.");
    puts("");
    puts("EXIT STATUS");
    puts("       0      The named unit exists and its fixture was printed.");
    puts("       4      The named unit could not be found.");
}

static void print_journalctl_help(void)
{
    puts("JOURNALCTL(1)                sremock manual                JOURNALCTL(1)");
    puts("");
    puts("NAME");
    puts("       journalctl - inspect fixture-backed systemd journals");
    puts("");
    puts("SYNOPSIS");
    puts("       journalctl -xe");
    puts("       journalctl -x -e");
    puts("       journalctl -ex");
    puts("       journalctl -u UNIT -n 100 --no-pager");
    puts("       journalctl -h|--help");
    puts("");
    puts("DESCRIPTION");
    puts("       -xe is equivalent to -x plus -e. Unit names may omit .service.");
    puts("       A valid unit without fixture entries prints -- No entries --.");
    puts("");
    puts("OPTIONS");
    puts("       -u, --unit UNIT        select a unit");
    puts("       --unit=UNIT           long unit form");
    puts("       -n, --lines 100        select the supported 100-line fixture");
    puts("       --lines=100           long line-count form");
    puts("       --no-pager            print without a pager");
    puts("       -x, -e                explanatory/journal fields (-xe combines them)");
    puts("       -h, --help            display this help and exit");
}

static void print_container_help(const char *command)
{
    size_t i;

    puts("Usage:");
    printf("  %s ps -a|--all\n", command);
    for (i = 0; i < ARRAY_LEN(CONTAINER_PS_ALIASES); ++i)
        printf("  %s container %s -a|--all\n", command,
               CONTAINER_PS_ALIASES[i]);
    for (i = 0; i < ARRAY_LEN(CONTAINER_LOGS_ALIASES); ++i) {
        printf("  %s logs CONTAINER\n", command);
        printf("  %s container %s CONTAINER\n", command,
               CONTAINER_LOGS_ALIASES[i]);
    }
    for (i = 0; i < ARRAY_LEN(CONTAINER_INSPECT_ALIASES); ++i) {
        printf("  %s inspect CONTAINER\n", command);
        printf("  %s container %s CONTAINER\n", command,
               CONTAINER_INSPECT_ALIASES[i]);
    }
    printf("  %s -h|--help\n", command);
    puts("");
    puts("Aliases:");
    printf("  %s ps = %s container ps = %s container ls = %s container list\n",
           command, command, command, command);
    printf("  %s logs = %s container logs\n", command, command);
    printf("  %s inspect = %s container inspect\n", command, command);
    puts("");
    puts("Description:");
    puts("  Mock-supported container listing, logs and inspection only.");
    puts("");
    puts("Options:");
    puts("  -a, --all     include stopped containers in the listing");
    puts("  -h, --help    display this help and exit");
}

static void print_curl_help(void)
{
    puts("Usage: curl -v URL");
    puts("       curl -I URL");
    puts("       curl -h|--help");
    puts("");
    puts("Mock-supported HTTP evidence only.");
    puts("");
    puts("Options:");
    puts("  -v         verbose connection fixture");
    puts("  -I         HEAD-response fixture");
    puts("  -h, --help display this help and exit");
}

typedef struct {
    const char *command;
    const char *usage;
    const char *description;
} SimpleHelp;

static const SimpleHelp SIMPLE_HELP[] = {
    {"ps", "ps aux", "Full process-table fixture."},
    {"ss", "ss -tlnp", "Listening TCP socket fixture."},
    {"df", "df -h", "Human-readable filesystem fixture; -h means human-readable sizes, not help."},
    {"free", "free -m", "Memory fixture in MiB."},
    {"top", "top", "Process and memory snapshot fixture."},
    {"du", "du -sh DIR", "Human-readable directory-size fixture."},
    {"nginx", "nginx -t", "Configuration-test fixture."},
    {"getenforce", "getenforce", "SELinux-mode fixture."},
    {"ausearch", "ausearch -m avc", "AVC audit fixture."},
    {"dig", "dig [ARGS ...]", "One DNS fixture for any argument list."},
    {"nslookup", "nslookup [ARGS ...]", "One DNS fixture for any argument list."},
    {"ping", "ping [ARGS ...]", "One reachability fixture for any argument list."},
    {"nc", "nc -zv HOST PORT", "Verbose TCP connection fixture."},
};

static int print_simple_help(const char *command)
{
    size_t i;

    for (i = 0; i < ARRAY_LEN(SIMPLE_HELP); ++i) {
        if (is_arg(command, SIMPLE_HELP[i].command)) {
            printf("Usage: %s\n", SIMPLE_HELP[i].usage);
            puts(SIMPLE_HELP[i].description);
            puts("");
            puts("Options:");
            if (is_arg(command, "df")) {
                puts("  -h, --human-readable  print sizes in human-readable form");
                puts("  --help                display this help and exit");
            } else {
                puts("  -h, --help display this help and exit");
            }
            return 1;
        }
    }
    return 0;
}

static int print_command_help(const char *command)
{
    if (is_arg(command, "sremock")) {
        print_general_help();
    } else if (is_arg(command, "kubectl")) {
        print_kubectl_help();
    } else if (is_arg(command, "systemctl")) {
        print_systemctl_help();
    } else if (is_arg(command, "journalctl")) {
        print_journalctl_help();
    } else if (is_arg(command, "docker") || is_arg(command, "podman")) {
        print_container_help(command);
    } else if (is_arg(command, "curl")) {
        print_curl_help();
    } else {
        return print_simple_help(command);
    }
    return 1;
}

/* --help always wins. -h also wins, except for df's real operational -h form;
 * df --help remains available and no other supported command uses bare -h. */
static int wants_help(const char *command, int argc, char **argv)
{
    int i;

    for (i = 1; i < argc; ++i) {
        if (is_arg(argv[i], "--help"))
            return 1;
    }
    if (is_arg(command, "df") && argc == 2 && is_arg(argv[1], "-h"))
        return 0;
    for (i = 1; i < argc; ++i) {
        if (is_arg(argv[i], "-h"))
            return 1;
    }
    return 0;
}

/* ------------------------------------------------------------------------- */
/* Command parsers: return 1 for a handled form, 0 for unsupported syntax.    */
/* A handled miss sets fixture to NULL and carries its command exit status;   */
/* main returns without touching the fixture root.                             */
/* ------------------------------------------------------------------------- */

static int systemctl_select(int argc, char **argv, Selection *selection)
{
    const char *unit;

    if (argc != 3 || !is_arg(argv[1], "status") || !nonempty(argv[2]) ||
        argv[2][0] == '-')
        return 0;
    unit = argv[2];
    if (!valid_unit(unit)) {
        fprintf(stderr, "Unit %s%s could not be found.\n", unit,
                strlen(unit) > 8 && strcmp(unit + strlen(unit) - 8, ".service") == 0
                    ? "" : ".service");
        selection->fixture = NULL;
        selection->status = 4;
        return 1;
    }
    selection->fixture = "systemctl_status.txt";
    return 1;
}

static int journalctl_select(int argc, char **argv, Selection *selection)
{
    const char *unit = NULL;
    const char *value = NULL;
    int have_unit = 0;
    int have_lines = 0;
    int have_no_pager = 0;
    int have_x = 0;
    int have_e = 0;
    int i;

    for (i = 1; i < argc; ++i) {
        if (is_arg(argv[i], "-xe")) {
            have_x = 1;
            have_e = 1;
        } else if (is_arg(argv[i], "-ex")) {
            have_x = 1;
            have_e = 1;
        } else if (is_arg(argv[i], "-x")) {
            have_x = 1;
        } else if (is_arg(argv[i], "-e")) {
            have_e = 1;
        } else if (is_arg(argv[i], "--no-pager")) {
            have_no_pager = 1;
        } else if (is_arg(argv[i], "-u") || is_arg(argv[i], "--unit")) {
            if (++i >= argc || !nonempty(argv[i]) || argv[i][0] == '-')
                return 0;
            unit = argv[i];
            have_unit = 1;
        } else if (option_equals(argv[i], "--unit", &value)) {
            if (!nonempty(value) || value[0] == '-')
                return 0;
            unit = value;
            have_unit = 1;
        } else if (is_arg(argv[i], "-n") || is_arg(argv[i], "--lines")) {
            if (++i >= argc || !is_arg(argv[i], "100"))
                return 0;
            have_lines = 1;
        } else if (option_equals(argv[i], "--lines", &value)) {
            if (!is_arg(value, "100"))
                return 0;
            have_lines = 1;
        } else {
            return 0;
        }
    }

    if (have_x || have_e) {
        if (!have_x || !have_e || have_unit || have_lines || have_no_pager)
            return 0;
        selection->fixture = "journalctl_xe.txt";
        return 1;
    }
    if (!have_unit || !have_lines || !have_no_pager)
        return 0;
    if (!valid_unit(unit)) {
        puts("-- No entries --");
        selection->fixture = NULL;
        return 1;
    }
    selection->fixture = "journalctl_unit.txt";
    return 1;
}

static const KubectlResource *find_kubectl_resource(const char *token,
                                                    const char **name)
{
    const char *slash = strchr(token, '/');
    size_t token_len = slash == NULL ? strlen(token) : (size_t)(slash - token);
    size_t i;
    size_t j;

    *name = NULL;
    if (slash != NULL) {
        if (slash[1] == '\0' || strchr(slash + 1, '/') != NULL)
            return NULL;
        *name = slash + 1;
    }
    for (i = 0; i < ARRAY_LEN(KUBECTL_RESOURCES); ++i) {
        const KubectlResource *resource = &KUBECTL_RESOURCES[i];

        for (j = 0; j < resource->alias_count; ++j) {
            size_t alias_len = strlen(resource->aliases[j]);

            if (alias_len == token_len &&
                strncasecmp(resource->aliases[j], token, token_len) == 0)
                return resource;
        }
    }
    return NULL;
}

static const char *kubectl_name_key(const KubectlResource *resource)
{
    if (strcmp(resource->canonical, "pods") == 0)
        return "pod";
    if (strcmp(resource->canonical, "configmaps") == 0)
        return "cm";
    if (strcmp(resource->canonical, "endpoints") == 0 ||
        strcmp(resource->canonical, "services") == 0)
        return "svc";
    if (strcmp(resource->canonical, "deployments") == 0)
        return "deploy";
    return NULL;
}

static int kubectl_name_miss(const KubectlResource *resource, const char *name)
{
    const char *key = kubectl_name_key(resource);

    if (key == NULL)
        return 0;
    if (!valid_name(key, name)) {
        fprintf(stderr, "Error from server (NotFound): %s \"%s\" not found\n",
                resource->canonical, name);
        return 1;
    }
    return 0;
}

static int kubectl_select(int argc, char **argv, Selection *selection)
{
    const char *operands[4];
    const char *value = NULL;
    const KubectlResource *resource;
    const char *name = NULL;
    int operand_count = 0;
    int i;

    for (i = 1; i < argc; ++i) {
        if (is_arg(argv[i], "-A") || is_arg(argv[i], "--all-namespaces")) {
            continue;
        } else if (is_arg(argv[i], "-n") || is_arg(argv[i], "--namespace")) {
            if (++i >= argc || !nonempty(argv[i]) || argv[i][0] == '-')
                return 0;
        } else if (option_equals(argv[i], "--namespace", &value)) {
            if (!nonempty(value) || value[0] == '-')
                return 0;
        } else if (is_arg(argv[i], "-o") || is_arg(argv[i], "--output")) {
            if (++i >= argc || !nonempty(argv[i]) || argv[i][0] == '-')
                return 0;
        } else if (option_equals(argv[i], "--output", &value)) {
            if (!nonempty(value) || value[0] == '-')
                return 0;
        } else if (argv[i][0] == '-') {
            return 0;
        } else {
            if (operand_count == (int)ARRAY_LEN(operands))
                return 0;
            operands[operand_count++] = argv[i];
        }
    }

    if (operand_count < 2)
        return 0;
    if (is_arg(operands[0], "get")) {
        if (operand_count > 3)
            return 0;
        resource = find_kubectl_resource(operands[1], &name);
        if (resource == NULL)
            return 0;
        if (operand_count == 3 && strchr(operands[1], '/') != NULL)
            return 0;
        if (name == NULL && operand_count == 3)
            name = operands[2];
        if (name != NULL && !resource->allow_name)
            return 0;
        if (name == NULL && !resource->allow_list)
            return 0;
        if (name != NULL && kubectl_name_miss(resource, name)) {
            selection->fixture = NULL;
            selection->status = 1;
            return 1;
        }
        selection->fixture = resource->fixture;
        return 1;
    }
    if (is_arg(operands[0], "describe") || is_arg(operands[0], "logs")) {
        if (operand_count > 3)
            return 0;
        if (is_arg(operands[0], "logs") && operand_count == 2 &&
            strchr(operands[1], '/') == NULL) {
            resource = &KUBECTL_RESOURCES[0];
            name = operands[1];
        } else {
            resource = find_kubectl_resource(operands[1], &name);
            if (operand_count == 3 && strchr(operands[1], '/') != NULL)
                return 0;
            if (name == NULL && operand_count == 3)
                name = operands[2];
        }
        if (resource == NULL || strcmp(resource->canonical, "pods") != 0)
            return 0;
        if (name == NULL)
            return 0;
        if (kubectl_name_miss(resource, name)) {
            selection->fixture = NULL;
            selection->status = 1;
            return 1;
        }
        selection->fixture = is_arg(operands[0], "describe")
            ? "kubectl_describe_pod.txt" : "kubectl_logs.txt";
        return 1;
    }
    return 0;
}

static int is_all_flag(const char *arg)
{
    return is_arg(arg, "-a") || is_arg(arg, "--all");
}

static int container_select(const char *command, int argc, char **argv,
                            Selection *selection)
{
    const char *name;

    if (argc == 3 && is_arg(argv[1], "ps") && is_all_flag(argv[2])) {
        selection->fixture = is_arg(command, "docker") ? "docker_ps_a.txt" : "podman_ps_a.txt";
        return 1;
    }
    if (argc == 3 && (is_arg(argv[1], "logs") || is_arg(argv[1], "inspect"))) {
        name = argv[2];
        if (!nonempty(name) || name[0] == '-')
            return 0;
        if (!valid_name("container", name)) {
            fprintf(stderr, "Error: No such container: %s\n", name);
            selection->fixture = NULL;
            selection->status = 1;
            return 1;
        }
        selection->fixture = is_arg(argv[1], "logs")
            ? (is_arg(command, "docker") ? "docker_logs.txt" : "podman_logs.txt")
            : (is_arg(command, "docker") ? "docker_inspect.txt" : "podman_inspect.txt");
        return 1;
    }
    if (argc == 4 && is_arg(argv[1], "container")) {
        if (alias_index(CONTAINER_PS_ALIASES, ARRAY_LEN(CONTAINER_PS_ALIASES),
                        argv[2]) >= 0 && is_all_flag(argv[3])) {
            selection->fixture = is_arg(command, "docker") ? "docker_ps_a.txt" : "podman_ps_a.txt";
            return 1;
        }
        if (alias_index(CONTAINER_LOGS_ALIASES, ARRAY_LEN(CONTAINER_LOGS_ALIASES),
                        argv[2]) >= 0 ||
            alias_index(CONTAINER_INSPECT_ALIASES, ARRAY_LEN(CONTAINER_INSPECT_ALIASES),
                        argv[2]) >= 0) {
            name = argv[3];
            if (!nonempty(name) || name[0] == '-')
                return 0;
            if (!valid_name("container", name)) {
                fprintf(stderr, "Error: No such container: %s\n", name);
                selection->fixture = NULL;
                selection->status = 1;
                return 1;
            }
            if (is_arg(argv[2], "logs"))
                selection->fixture = is_arg(command, "docker") ? "docker_logs.txt" : "podman_logs.txt";
            else
                selection->fixture = is_arg(command, "docker") ? "docker_inspect.txt" : "podman_inspect.txt";
            return 1;
        }
    }
    return 0;
}

static int simple_select(const char *command, int argc, char **argv,
                         Selection *selection)
{
    if (is_arg(command, "ps")) {
        if (argc == 2 && is_arg(argv[1], "aux")) {
            selection->fixture = "ps_aux.txt";
            return 1;
        }
    } else if (is_arg(command, "ss")) {
        if (argc == 2 && is_arg(argv[1], "-tlnp")) {
            selection->fixture = "ss_tlnp.txt";
            return 1;
        }
    } else if (is_arg(command, "curl")) {
        if (argc == 3 && nonempty(argv[2])) {
            if (is_arg(argv[1], "-v")) {
                selection->fixture = "curl_v.txt";
                return 1;
            }
            if (is_arg(argv[1], "-I")) {
                selection->fixture = "curl_I.txt";
                return 1;
            }
        }
    } else if (is_arg(command, "df")) {
        if (argc == 2 && is_arg(argv[1], "-h")) {
            selection->fixture = "df_h.txt";
            return 1;
        }
    } else if (is_arg(command, "free")) {
        if (argc == 2 && is_arg(argv[1], "-m")) {
            selection->fixture = "free_m.txt";
            return 1;
        }
    } else if (is_arg(command, "top")) {
        if (argc == 1) {
            selection->fixture = "top.txt";
            return 1;
        }
    } else if (is_arg(command, "du")) {
        if (argc == 3 && is_arg(argv[1], "-sh") && nonempty(argv[2])) {
            selection->fixture = "du_sh.txt";
            return 1;
        }
    } else if (is_arg(command, "nginx")) {
        if (argc == 2 && is_arg(argv[1], "-t")) {
            selection->fixture = "nginx_t.txt";
            return 1;
        }
    } else if (is_arg(command, "getenforce")) {
        if (argc == 1) {
            selection->fixture = "getenforce.txt";
            return 1;
        }
    } else if (is_arg(command, "ausearch")) {
        if (argc == 3 && is_arg(argv[1], "-m") && is_arg(argv[2], "avc")) {
            selection->fixture = "ausearch_avc.txt";
            return 1;
        }
    } else if (is_arg(command, "dig") || is_arg(command, "nslookup") ||
               is_arg(command, "ping")) {
        if (is_arg(command, "dig")) {
            selection->fixture = "dig.txt";
        } else if (is_arg(command, "nslookup")) {
            selection->fixture = "nslookup.txt";
        } else {
            selection->fixture = "ping.txt";
        }
        return 1;
    } else if (is_arg(command, "nc")) {
        if (argc == 4 && is_arg(argv[1], "-zv") && nonempty(argv[2]) &&
            nonempty(argv[3])) {
            selection->fixture = "nc_zv.txt";
            return 1;
        }
    }
    return 0;
}

static int select_fixture(int argc, char **argv, Selection *selection)
{
    const char *command = command_name(argc > 0 ? argv[0] : NULL);

    if (is_arg(command, "systemctl"))
        return systemctl_select(argc, argv, selection);
    if (is_arg(command, "journalctl"))
        return journalctl_select(argc, argv, selection);
    if (is_arg(command, "kubectl"))
        return kubectl_select(argc, argv, selection);
    if (is_arg(command, "docker") || is_arg(command, "podman"))
        return container_select(command, argc, argv, selection);
    return simple_select(command, argc, argv, selection);
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
    Selection selected = {NULL, 0};
    const char *root;

    if (wants_help(command, argc, argv)) {
        if (!print_command_help(command)) {
            fprintf(stderr, "sremock: unsupported invocation: %s\n",
                    *command == '\0' ? "(unknown)" : command);
            return 1;
        }
        return 0;
    }
    if (!select_fixture(argc, argv, &selected)) {
        fprintf(stderr, "sremock: unsupported invocation: %s\n",
                *command == '\0' ? "(unknown)" : command);
        return 1;
    }
    if (selected.fixture == NULL)
        return selected.status;
    root = getenv("SREMOCK_ROOT");
    if (root == NULL || *root == '\0')
        root = DEFAULT_ROOT;
    return print_fixture(root, selected.fixture);
}
