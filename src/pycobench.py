#!/usr/bin/env python3
#
# pycobench.py - a small benchmarking solution
#
# A small environment for running benchmarks
#
# If you need something more elaborate, check, e.g., benchexec
#   ( https://github.com/sosy-lab/benchexec )
#
# Description:
#   * Runs benchmarks in parallel using a parameterized number of workers.
#   * One benchmark is run using different execution engines, specified in a
#     configuration file.
#     * The configuration file is a YAML where each engine is given a command
#       to run (with $1, $2, $3,... denoting input parameters).
#     * An engine can also map return codes to run statuses with
#       "return_codes", e.g. {0: finished, -6: memout, "*": error}, where "*"
#       matches every code without an entry of its own.  A code may instead be
#       given {status: finished, result: unsat} to also record the answer the
#       code stands for.  The statuses are: finished, error, timeout, memout
#       and crash; a run is classified as timeout by pycobench itself, and a
#       code left to the catch-all entry is refined into crash (killed by a
#       signal) or memout (reached the memory limit of --memout).  Settings
#       shared by all engines can be given once in a top-level "defaults"
#       section and are overridden by per-engine values.
#   * Benchmarks are provided on standard input, one per line.  Each line
#  # FIXME: semicolon-separated?
#     contains a whitespace-separated list of parameters (which are then input
#     into the engine commands in the place of $1, $2, $3, ...)
#   * The tasks to be run are written into a file and results of finished tasks
#     are appended to the file as well.
#   * When interrupted, it is possible to continue executing the unfinished
#     benchmarks from the task list file (parameter -c).
#
# TODO:
#   * better docs
#   * when restarting, check that the tasklist and YAML configuration are
#     compatible
#   * obtain time from the output of the commands
#   * prettier printing of results (csv, html, latex, ...)
#   * functionality as a converter from .tasks file to an output format
#   * check whether the output of the commands matches
#

import argparse
import csv
import glob
import os
import queue
import re
import resource
import signal
import subprocess
import sys
import threading

import psutil
import termcolor
import yaml

# thread-safe queue for distributing tasks
g_task_queue = queue.Queue()

# thread-safe queue for collecting results
g_result_queue = queue.Queue()

# a dictionary of commands to run
g_cmd_dict = {}

# timeout for subprocesses (in seconds)
g_timeout = 60

# memory limit for subprocesses (in GB)
g_memout = None

# The file that contains information about submitted and finished tasks.  This
# can be later used for restarting a prematurely stopped benchmark.
g_tasks = "pycobench.tasks"

# the command to measure CPU time; resolved through PATH, so a GNU time binary
# installed anywhere on PATH (not necessarily /usr/bin/time) is used.  When GNU
# time is available, probe_time_cmd() swaps "-p" for the format below, which
# reports the peak resident set size (in kB) next to the times.
g_time_cmd = ["time", "-p"]
TIME_FORMAT = "real %e\nuser %U\nsys %S\nmaxrss %M"

# whether the time command in use reports the peak RSS; BSD time (e.g. on
# macOS) has no "-f", in which case no memory is measured
g_time_has_maxrss = False

# limit on the size of an output on stdout and stderr
OUTPUT_LIMIT = 16384

# delimiter for EOL
g_newline_sep = "###"

# the number of tasks that are to be run
g_cnt_tasks = 0

# the number of tasks that have finished
g_cnt_finished_tasks = 0

# should we be verbose
g_verbose = False

# cpu affinity
g_cpu_affinity = list(range(os.cpu_count() or 0))  # by default, all CPUs
g_bind_to_cpu = False

# the statuses a run is classified into.  The set is closed: pyco_proc and
# compare_profiles render each of them, so an unknown status would silently
# disappear from the processed results.
STATUS_FINISHED = "finished"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_MEMOUT = "memout"
STATUS_CRASH = "crash"

# the statuses a "return_codes" configuration entry may assign; "timeout" is
# never a property of a return code, it is observed by pycobench itself
CONFIGURABLE_STATUSES = frozenset(
    {STATUS_FINISHED, STATUS_ERROR, STATUS_MEMOUT, STATUS_CRASH}
)

# how each status is reported on the terminal
STATUS_LABELS = {
    STATUS_FINISHED: ("FINISHED", "green", []),
    STATUS_ERROR: ("ERROR", "red", ["bold"]),
    STATUS_TIMEOUT: ("TIMEOUT", "yellow", []),
    STATUS_MEMOUT: ("MEMOUT", "magenta", []),
    STATUS_CRASH: ("CRASH", "red", ["bold"]),
}

# the "return_codes" key matching every return code without its own entry
CATCH_ALL_CODE = "*"

# classification used when neither the method nor the "defaults" configuration
# section maps the return codes (0 and 1 are both successful for backwards
# compatibility: solvers often return 1 for legitimate results)
g_default_return_codes = {
    0: STATUS_FINISHED,
    1: STATUS_FINISHED,
    CATCH_ALL_CODE: STATUS_ERROR,
}

# the share of the memory limit from which a failed run counts as a memout; the
# limit caps the address space, so a run dying on a failed allocation peaks
# somewhat below it
MEMOUT_RATIO = 0.8

# the configuration key under which default per-method settings can be given
g_defaults_key = "defaults"

#############################################


def get_cpu_affiliation(worker_idx: int) -> list[int]:
    """If g_bind_to_cpu is True, then each worker is pinned to a specific CPU;
    otherwise, all workers can use all CPUs in g_cpu_affinity
    """
    if not g_cpu_affinity:
        return []
    if not g_bind_to_cpu:
        return g_cpu_affinity
    return [g_cpu_affinity[worker_idx % len(g_cpu_affinity)]]


###########################################
# taken from https://psutil.readthedocs.io/en/latest/#kill-process-tree
def kill_proc_tree(
    pid, sig=signal.SIGTERM, include_parent=True, timeout=None, on_terminate=None
):
    """Kill a process tree (including grandchildren) with signal
    "sig" and return a (gone, still_alive) tuple.
    "on_terminate", if specified, is a callback function that is
    called as soon as a child terminates.
    """
    assert pid != os.getpid(), "won't kill myself"
    parent = psutil.Process(pid)
    children = parent.children(recursive=True)
    if include_parent:
        children.append(parent)
    for p in children:
        if p.is_running():
            p.send_signal(sig)
    gone, alive = psutil.wait_procs(children, timeout=timeout, callback=on_terminate)
    return (gone, alive)


###########################################
def remove_newlines(text):
    """remove_newlines(text) -> str

    Substitutes newline characters with a separator.
    """
    return text.replace("\n", g_newline_sep)


###########################################
def run_subproc(cmd):
    """run_subproc(cmd) -> dict()

    Runs a command as a subprocess and collects results.
    """
    rusage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    # proc = subprocess.run(cmd, timeout=g_timeout, capture_output=True)
    proc = subprocess.run(
        cmd, timeout=g_timeout, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    rusage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = {}
    result["retcode"] = proc.returncode
    result["stdout"] = proc.stdout.decode().strip()
    result["stderr"] = proc.stderr.decode().strip()

    result["time"] = rusage_after.ru_utime - rusage_before.ru_utime
    return result


###########################################
def limit_virtual_memory(cpu_affinity):
    if g_memout is not None:
        # The tuple below is of the form (soft limit, hard limit). Limit only
        # the soft part so that the limit can be increased later (setting also
        # the hard limit would prevent that).
        # When the limit cannot be changed, setrlimit() raises ValueError.
        resource.setrlimit(
            resource.RLIMIT_AS, (g_memout * 1024 * 1024 * 1024, resource.RLIM_INFINITY)
        )

    p = psutil.Process()
    p.cpu_affinity(cpu_affinity)


###########################################
def probe_time_cmd():
    """probe_time_cmd() -> None

    Switches the time command over to the GNU "time -f" format when it is
    supported, so that the peak resident set size is measured next to the CPU
    time.  BSD time (e.g. on macOS) has no "-f"; plain "time -p" stays in use
    there and no memory is reported.
    """
    global g_time_cmd, g_time_has_maxrss
    try:
        probe = subprocess.run(
            ["time", "-f", "maxrss %M", "true"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception:
        return

    if probe.returncode == 0 and re.search(r"maxrss\s+\d+", probe.stderr.decode()):
        g_time_cmd = ["time", "-f", TIME_FORMAT]
        g_time_has_maxrss = True


###########################################
def extract_measurements(result):
    """extract_measurements(dict) -> bool

    Moves the summary printed by the time command out of stderr into the result
    ("time" in seconds and, with GNU time, "maxrss" in kB).  The notes the time
    command adds about how the command ended are taken out of stderr too, and
    the signal that killed the command is recorded in "signal".  Returns False
    when the summary is not there, which happens when the command was killed
    before the time command could report.
    """
    line_cnt = 4 if g_time_has_maxrss else 3
    stderr_split = result["stderr"].splitlines()
    measured = {}
    for line in stderr_split[-line_cnt:]:
        mtch = re.search(r"user\s+(?P<time>\d+\.\d+)", line)
        if mtch:
            measured["time"] = float(mtch.group("time"))
        mtch = re.search(r"maxrss\s+(?P<maxrss>\d+)", line)
        if mtch:
            measured["maxrss"] = int(mtch.group("maxrss"))

    if "time" not in measured:
        return False

    result.update(measured)

    # the time command reports how the command ended just above its summary;
    # that note belongs to the time command, not to the benchmarked program
    stderr_split = stderr_split[:-line_cnt]
    while stderr_split:
        mtch = re.fullmatch(
            r"Command (?:exited with non-zero status \d+"
            r"|terminated by signal (?P<signal>\d+))\.?",
            stderr_split[-1].strip(),
        )
        if not mtch:
            break
        if mtch.group("signal") is not None:
            result["signal"] = int(mtch.group("signal"))
        stderr_split.pop()

    result["stderr"] = "\n".join(stderr_split)
    return True


###########################################
def reached_memory_limit(result):
    """reached_memory_limit(dict) -> bool

    Tells whether the run got close enough to the configured memory limit to be
    called a memout.  The limit is enforced on the address space, so a process
    that dies on a failed allocation can peak slightly below it.
    """
    if g_memout is None or "maxrss" not in result:
        return False
    return result["maxrss"] * 1024 >= MEMOUT_RATIO * g_memout * 1024**3


###########################################
def classify_return_code(return_codes, retcode):
    """classify_return_code(dict, int) -> (str, str | None, bool)

    Returns the status and the result configured for the return code, together
    with whether the return code was matched by an entry of its own (as opposed
    to the catch-all entry).
    """
    entry = return_codes.get(retcode)
    if entry is None:
        return return_codes[CATCH_ALL_CODE]["status"], None, False
    return entry["status"], entry["result"], True


###########################################
def run_subproc_systime(cmd, cpu_affinity, return_codes):
    """run_subproc_systime(cmd, cpu_affinity, return_codes) -> dict()

    Runs a command as a subprocess and collects results.  The time consumed is
    measured using the system "time" command.  The run is classified into one of
    the statuses according to "return_codes"; a return code matched only by the
    catch-all entry is refined into "crash" when the command died on a signal
    and into "memout" when it reached the memory limit.
    """
    cmd = g_time_cmd + cmd
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # preexec_fn is a callable object that will be called in the child process
        # just before the child is executed.
        preexec_fn=lambda: limit_virtual_memory(cpu_affinity),
    )
    result = {}
    try:
        outs, errs = proc.communicate(timeout=g_timeout)
    except subprocess.TimeoutExpired:
        try:
            kill_proc_tree(proc.pid, sig=signal.SIGKILL, include_parent=True)
        except Exception:
            pass  # do not care

        raise

    result["retcode"] = proc.returncode
    result["stdout"] = outs.decode().strip()
    result["stderr"] = errs.decode().strip()
    if g_verbose:
        print("==== results for {} ====".format(" ".join(cmd)))
        print("======= stdout =======")
        print(result["stdout"])
        print("======= stderr =======")
        print(result["stderr"])
        print("======= ^^^^^^ =======")

    # limit too big outputs
    result["stdout"] = result["stdout"][-OUTPUT_LIMIT:]
    result["stderr"] = result["stderr"][-OUTPUT_LIMIT:]

    measured = extract_measurements(result)
    status, run_result, explicit = classify_return_code(return_codes, result["retcode"])

    if not explicit:
        # the command did not end in a way the configuration describes, so look
        # at how it died before calling it a plain error
        if reached_memory_limit(result):
            status = STATUS_MEMOUT
        elif result.get("signal") is not None or result["retcode"] < 0:
            # the time command reports the signal that killed the command; a
            # negative return code carries it when no time command is in use
            status = STATUS_CRASH

    if status == STATUS_FINISHED and not measured:
        status = STATUS_ERROR
        result["stderr"] = remove_newlines(
            "Could not extract measured time\n" + result["stderr"]
        )

    result["status"] = status
    if run_result is not None:
        result["result"] = run_result

    return result


def substitute_cmd(in_cmd, in_params):
    cmd = in_cmd[:]
    # substitute $1, $2, etc. with the real parameters
    for i in range(len(cmd)):
        if len(cmd[i]) == 2 and cmd[i][0] == "$" and cmd[i][1] != "(":
            try:
                x = int(cmd[i][1])
            except Exception:
                raise Exception(
                    'invalid placeholder "' + cmd[i] + '" in the command to run'
                )

            if x > len(in_params):
                raise Exception(
                    "parameter "
                    + cmd[i]
                    + " not provided (only "
                    + str(len(in_params))
                    + " parameters passed) in `"
                    + " ".join(cmd)
                    + "`"
                )
            else:
                cmd[i] = in_params[x - 1]
    return cmd[:]


###########################################
def execute_benchmark(params, cpu_affinity):
    """execute_benchmark(params, cpu_affinity) -> None

    Executes one benchmark.
    """
    global g_cmd_dict
    name = params["method"]
    in_params = params["params"]
    # in_params = in_params.split()
    cmd = substitute_cmd(g_cmd_dict[name]["cmd"].split(), in_params)

    try:
        # result = run_subproc(cmd)
        executed_cmd = []
        for c in cmd:
            if "*" in c:
                executed_cmd.extend(sorted(glob.glob(c)))
            else:
                executed_cmd.append(c)
        result = run_subproc_systime(
            executed_cmd, cpu_affinity, g_cmd_dict[name]["return_codes"]
        )
        return result
    except subprocess.TimeoutExpired:
        return {"status": STATUS_TIMEOUT}


###########################################
def merge_two_dicts(x, y):
    z = x.copy()  # start with x's keys and values
    z.update(y)  # modifies z with y's keys and values & returns None
    return z


###########################################
def worker(cpu_affinity):
    """worker(cpu_affinity) -> None

    Main function of a thread for processing tasks.
    """
    while True:
        item = g_task_queue.get()
        if item is None:  # None signals end of processing
            g_result_queue.put(None)  # signal termination of worker
            break
        res = execute_benchmark(item, cpu_affinity)
        g_result_queue.put(merge_two_dicts(item, res))
        g_task_queue.task_done()


###########################################
def process_result(writer, task_file, result):
    """process_result(writer, task_file, result) -> None

    Processes one obtained result (writes it using writer [and flushes, as a good
    christian]).  Every row has the same shape:

      status;method;params...;retcode;stdout;stderr;time;maxrss;result
    """
    status = result.get("status", STATUS_ERROR)
    str_stdout = remove_newlines(result.get("stdout") or "")
    str_stderr = remove_newlines(result.get("stderr") or "")
    if status == STATUS_TIMEOUT:
        # the run was cut off, so the time it got is the one worth recording
        time_str = f"{g_timeout}"
    else:
        time_str = "" if result.get("time") is None else str(result["time"])
    maxrss_str = "" if result.get("maxrss") is None else str(result["maxrss"])

    writer.writerow(
        [status, result["method"]]
        + result["params"]
        + [
            result.get("retcode", ""),
            str_stdout,
            str_stderr,
            time_str,
            maxrss_str,
            result.get("result", ""),
        ]
    )
    task_file.flush()

    label, colour, attrs = STATUS_LABELS[status]
    res_string = termcolor.colored(label, colour, attrs=attrs)
    if status == STATUS_FINISHED:
        res_string += f"\tResult: {result['retcode']}\tTime: {time_str}"
        if result.get("result"):
            res_string += f"\tAnswer: {result['result']}"

    global g_cnt_finished_tasks
    g_cnt_finished_tasks += 1
    print(
        str(
            "{}/{}\t{}\t{}:\t{}".format(
                g_cnt_finished_tasks,
                g_cnt_tasks,
                result["method"],
                f"[{' '.join(substitute_cmd(g_cmd_dict[result['method']]['cmd'].split(), result['params']))}]",
                res_string,
            )
        )
    )


###########################################
def create_writer(opened_file):
    """create_writer(opened_file) -> csv.Writer"""
    writer = csv.writer(
        opened_file,
        delimiter=";",
        quotechar='"',
        doublequote=False,
        escapechar="\\",
        quoting=csv.QUOTE_MINIMAL,
    )
    return writer


###########################################
def parse_return_codes(meth, raw_codes):
    """parse_return_codes(str, dict) -> dict

    Normalises the "return_codes" configuration of one method into a mapping
    from a return code (or the catch-all key) to a {"status", "result"} entry.
    A code may be given either a bare status or a mapping that also names the
    answer the code stands for, e.g. {status: finished, result: unsat}.
    """
    if not isinstance(raw_codes, dict):
        raise Exception(
            '"return_codes" for method "{}" must be a mapping from return codes '
            "to statuses".format(meth)
        )

    parsed = {}
    for code, spec in raw_codes.items():
        if code != CATCH_ALL_CODE and not isinstance(code, int):
            raise Exception(
                'invalid return code "{}" for method "{}"; use an integer or '
                '"{}"'.format(code, meth, CATCH_ALL_CODE)
            )
        if isinstance(spec, str):
            spec = {"status": spec}
        if not isinstance(spec, dict) or "status" not in spec:
            raise Exception(
                'return code "{}" of method "{}" needs a status'.format(code, meth)
            )

        unknown_keys = set(spec) - {"status", "result"}
        if unknown_keys:
            raise Exception(
                'return code "{}" of method "{}" has unknown key(s): {}'.format(
                    code, meth, ", ".join(sorted(unknown_keys))
                )
            )
        if spec["status"] not in CONFIGURABLE_STATUSES:
            raise Exception(
                'invalid status "{}" for return code "{}" of method "{}"; use one '
                "of: {}".format(
                    spec["status"], code, meth, ", ".join(sorted(CONFIGURABLE_STATUSES))
                )
            )

        parsed[code] = {"status": spec["status"], "result": spec.get("result")}

    if CATCH_ALL_CODE not in parsed:
        parsed[CATCH_ALL_CODE] = {"status": STATUS_ERROR, "result": None}

    return parsed


###########################################
def process_conf_file(conf_file):
    """process_conf_file(conf_file) -> None

    Processes the configuration file.
    """
    global g_cmd_dict
    config = yaml.load(conf_file, Loader=yaml.FullLoader)

    # values in the "defaults" section apply to every method that does not
    # override them itself
    defaults = config.pop(g_defaults_key, {}) or {}

    g_cmd_dict = config
    for meth in g_cmd_dict:
        x = g_cmd_dict[meth]
        if x is None:
            raise Exception('Missing configuration for method "{}"'.format(meth))
        merged = {**defaults, **x}
        if "cmd" not in merged:
            raise Exception('Missing "cmd" value for method "{}"'.format(meth))
        # a method that maps return codes replaces the default mapping as a
        # whole, so that it cannot silently inherit a status it does not want
        merged["return_codes"] = parse_return_codes(
            meth, merged.get("return_codes", g_default_return_codes)
        )
        g_cmd_dict[meth] = merged


###########################################
def prepare_list_of_tasks_from_tasklist(tasklist_filename):
    """prepare_list_of_tasks_from_tasklist(str tasklist_filename) -> list

    Checks the file 'tasklist_filename' and extracts from it tasks that have not
    been finished yet.  These are then returned in a list.
    """
    executed_tasks = set()
    finished_tasks = set()
    with open(tasklist_filename, "r") as tasklist_file:
        reader = csv.reader(
            tasklist_file, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL
        )
        try:
            for row in reader:
                # create two sets
                if row[0] == "execute":
                    executed_tasks.add((row[1], row[2]))
                elif row[0] == "finished":
                    finished_tasks.add((row[1], row[2]))
        except Exception as ex:
            print(
                "Error reading a task list at line "
                + str(reader.line_num)
                + ": "
                + str(ex)
            )
            sys.exit(1)

    unfinished_business = executed_tasks - finished_tasks
    list_of_tasks = [{"method": x, "params": y} for (x, y) in unfinished_business]

    return list_of_tasks


###########################################
def run_main(args):
    """run_main(args) -> None

    Runs the main program according to the arguments obtained from the parser.
    """
    assert len(args.conf) == 1
    with open(args.conf[0], "r") as conf_file:
        process_conf_file(conf_file)

    # if there were specified methods to run
    global g_cmd_dict
    if args.methods:
        methods = args.methods.split(";")
        methods = [meth for meth in methods if meth != ""]
        new_cmd_dict = dict()
        for m in methods:
            if m not in g_cmd_dict:
                raise Exception("Invalid method selected: {}".format(m))

            new_cmd_dict[m] = g_cmd_dict[m]

        g_cmd_dict = new_cmd_dict

    if args.exclude:
        methods = args.exclude.split(";")
        methods = [meth for meth in methods if meth != ""]
        new_cmd_dict = dict()
        for m in g_cmd_dict:
            if m not in methods:
                new_cmd_dict[m] = g_cmd_dict[m]

        g_cmd_dict = new_cmd_dict

    # process additional program parameters
    global g_timeout
    g_timeout = args.timeout
    global g_memout
    g_memout = args.memout
    global g_tasks
    g_tasks = args.output_file
    global g_verbose
    g_verbose = args.verbose
    global g_cpu_affinity
    if args.cpu_affinity is not None:
        g_cpu_affinity = args.cpu_affinity
    global g_bind_to_cpu
    g_bind_to_cpu = args.bind_to_cpu

    # pick the richest time command the system offers before running anything
    probe_time_cmd()

    list_of_tasks = []  # these are the tasks that are to be procecessed
    if args.tasklist:  # we want to continue in a tasklist
        list_of_tasks = prepare_list_of_tasks_from_tasklist(args.tasklist)
    else:  # take the tasks from stdin
        # processing the input

        # if False:  # changed to CSV reader
        #     for line in sys.stdin:
        #         line = line.rstrip()
        #         for k in g_cmd_dict:
        #             list_of_tasks.append({'method': k, 'params': line})

        reader = csv.reader(args.input, delimiter=";")
        for line in reader:
            for k in g_cmd_dict:
                list_of_tasks.append({"method": k, "params": line})

        # write into task_file what we're executing
        with open(g_tasks, "w") as task_file:
            writer = create_writer(task_file)
            for task in list_of_tasks:
                writer.writerow(["execute", task["method"]] + task["params"])

    global g_cnt_tasks
    g_cnt_tasks = len(list_of_tasks)

    # set the number of workers
    num_worker_threads = args.jobs
    if num_worker_threads is None:
        num_worker_threads = 1

    # no more workers than number of jobs
    num_worker_threads = min(num_worker_threads, g_cnt_tasks)

    # start the workers
    threads = []
    for i in range(num_worker_threads):
        t = threading.Thread(target=worker, args=(get_cpu_affiliation(i),))
        t.start()
        threads.append(t)

    # queue the tasks
    for task in list_of_tasks:
        g_task_queue.put(task)

    # send the END OF TASKS message
    for t in threads:
        g_task_queue.put(None)

    with open(g_tasks, "a") as task_file:
        writer = create_writer(task_file)

        # processing the results
        finished_workers = 0
        while finished_workers < len(threads):
            result = g_result_queue.get()
            if result is None:
                print("worker terminated")
                finished_workers += 1
                continue
            else:
                process_result(writer, task_file, result)

    # a barrier
    for t in threads:
        t.join()


###########################################
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="pycobench: Executes benchmarks given using a configuration file on cases given in input."
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=os.cpu_count(),
        help="The number of jobs (workers) to run concurrently (default: %(default)s)",
    )
    parser.add_argument(
        "-f",
        "--finish",
        metavar="TASKLIST",
        dest="tasklist",
        help="""Specifying this argument continues execution "
                        "of unfinished tasks from %(metavar)s. "
                        "No input is read.""",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_FILE",
        dest="output_file",
        default=g_tasks,
        help="The output file (default: %(default)s)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        metavar="TIMEOUT",
        type=int,
        dest="timeout",
        default=g_timeout,
        help="The timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--memout",
        metavar="MEMOUT",
        type=int,
        dest="memout",
        help="The memory limit in GB (no limit if not given)",
    )
    parser.add_argument(
        "-m",
        "--methods",
        metavar="METHODS",
        type=str,
        dest="methods",
        help="Which methods from the configuration file to "
        "execute, separated by ';' (default: all)",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        metavar="METHODS",
        type=str,
        dest="exclude",
        help="Which methods from the configuration file will be ignored,"
        "separated by ';' (default=None)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    parser.add_argument(
        "-c",
        "--conf",
        metavar="config.yaml",
        nargs=1,
        required=True,
        help="configuration file (in YAML)",
    )
    parser.add_argument(
        "--cpu-affinity",
        type=int,
        nargs="+",
        help="Set CPU affinity to the given list of CPUs.",
    )
    parser.add_argument(
        "--bind-to-cpu",
        action="store_true",
        default=False,
        help="Pin each worker thread to a specific CPU.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="input file with the tasks in CSV (default: %(default)s)",
        type=argparse.FileType("r"),
        default=sys.stdin,
    )

    args = parser.parse_args()
    run_main(args)
