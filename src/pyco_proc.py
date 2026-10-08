#!/usr/bin/env python3


import argparse
import csv
import datetime
import io
import sys
from enum import Enum
from pathlib import Path

from pyco_statistics import StatisticsParser, StatsFormat
from tabulate import tabulate

#  fmt = 'text'
fmt = "csv"


class StatsDestination(Enum):
    """Output destination for statistics."""

    OUTPUT_FILE = "output_file"
    SEPARATE_FILES = "separate_files"


class RunResult(Enum):
    """Result of a benchmark instance run."""

    FINISHED = 1
    ERROR = 2
    TIMEOUT = 3
    MEMOUT = 4
    CRASH = 5


# How a run that produced no measurements is rendered in every cell of its engine.
# "finished" is the only status with data to print instead.
RUN_RESULT_CELLS = {
    RunResult.ERROR: "ERR",
    RunResult.TIMEOUT: "TO",
    RunResult.MEMOUT: "MO",
    RunResult.CRASH: "CRASH",
}

# The status written by pycobench for each unsuccessful run.
STATUS_RUN_RESULTS = {
    "error": RunResult.ERROR,
    "timeout": RunResult.TIMEOUT,
    "memout": RunResult.MEMOUT,
    "crash": RunResult.CRASH,
}


class InnerBlockType(Enum):
    """Type of parsed inner block."""

    MODEL = "model"
    STATISTICS = "statistics"


###########################################
def proc_res(fd, args):
    """proc_res(fd, args) -> _|_

    processes results of pycobench.py from file descriptor 'fd' using command line
    arguments 'args'
    """
    reader = csv.reader(
        fd, delimiter=";", quotechar='"', doublequote=False, quoting=csv.QUOTE_MINIMAL
    )

    engines = []
    engines_outs = {}
    results = {}

    current_time = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    if args.stats and args.stats == StatsDestination.SEPARATE_FILES:
        Path(f"./stats/{current_time}/").mkdir(parents=True, exist_ok=True)

    for row in reader:
        assert len(row) >= 1 + 1 + args.params_num  # status + engine name + params
        status, eng = row[0], row[1]
        params = tuple(row[2 : (args.params_num + 2)])
        row_tail = row[(args.params_num + 2) :]
        if params not in results:
            results[params] = {}
        if eng not in engines:
            engines.append(eng)
            engines_outs[eng] = []
            # engines_outs[eng] = ["result"]

        # we don't have some results twice
        assert eng not in results[params]

        if status == "finished":
            retcode, out, err, runtime = (
                row_tail[0],
                row_tail[1],
                row_tail[2],
                row_tail[3],
            )

            eng_res = {}
            eng_res["runtime"] = runtime
            eng_res["retcode"] = retcode
            eng_res["error"] = err
            eng_res["output"] = {}
            eng_res["run_result"] = RunResult.FINISHED
            name = ""

            # out_lines = [out]
            out_lines = []
            lines = out.split("###")
            for line in lines:
                if "WARNING" in line:
                    continue
                out_lines.append(line)
            inner_block_type: None | InnerBlockType = None
            inner_block = ""
            for line in out_lines:
                # TODO: Extract into user-provided function for parsing output lines, so that it can be easily adapted
                #  to other engines and output formats.
                # FIXME: From mata-comparison.
                # spl = line.split(":", 1)
                # if len(spl) != 2:  # jump over lines not in the format
                #     continue
                # name, val = spl[0], spl[1]
                # if name in eng_res["output"]:
                #     try:
                #         # If there is already defined value and it is number, we sum it
                #         val_res = float(eng_res["output"][name])
                #         val_res += float(val)
                #         val = float(val_res)
                #     except ValueError as e:
                #         print(
                #             f"warning non-float {name} already parsed (skipping): {e}",
                #             file=sys.stderr,
                #         )
                if inner_block_type:
                    inner_block += line + "\n"

                    if line.endswith(")"):
                        if inner_block_type == InnerBlockType.STATISTICS:
                            engine_stats_name = "stats"
                            assert engine_stats_name not in eng_res["output"]
                            if engine_stats_name not in engines_outs[eng]:
                                engines_outs[eng].append(engine_stats_name)
                            eng_res["output"][engine_stats_name] = StatisticsParser(
                                inner_block
                            ).stats
                        elif inner_block_type == InnerBlockType.MODEL:
                            # TODO: Add model block handling.
                            # print("model:")
                            # print(inner_block)
                            pass

                        inner_block = ""
                        inner_block_type = None
                elif line.startswith("(:"):
                    inner_block_type = InnerBlockType.STATISTICS
                    inner_block += line + "\n"
                elif line == "(":
                    inner_block_type = InnerBlockType.MODEL
                    inner_block += line + "\n"
                else:
                    spl = line.split(":", 1)
                    if len(spl) != 2:  # jump over lines not in the format
                        continue
                    name, val = spl[0].strip(), spl[1].strip()

                    # A program may run the same operation several times; the
                    # timings of the repeats add up to the time spent in it,
                    # while repeated non-numeric values are kept side by side
                    # so a disagreement stays visible.
                    previous = eng_res["output"].get(name)
                    if previous is not None:
                        try:
                            val = repr(float(previous) + float(val))
                        except ValueError:
                            val = f"{previous},{val}"
                    if name not in engines_outs[eng]:
                        engines_outs[eng].append(name)
                    eng_res["output"][name] = val

            # The peak memory, when the time command of the run reported it.
            maxrss = row_tail[4] if len(row_tail) > 4 else ""
            if maxrss:
                if "maxrss" not in engines_outs[eng]:
                    engines_outs[eng].append("maxrss")
                eng_res["output"]["maxrss"] = maxrss

            # The answer the return code stands for, as configured in "return_codes".
            # What the engine printed itself wins over it.
            mapped_result = row_tail[5] if len(row_tail) > 5 else ""
            if mapped_result:
                printed_result = eng_res["output"].get("result")
                if printed_result is None:
                    if "result" not in engines_outs[eng]:
                        engines_outs[eng].append("result")
                    eng_res["output"]["result"] = mapped_result
                elif printed_result != mapped_result:
                    sys.stderr.write(
                        f"Warning: in {params} and {eng}: the engine printed result "
                        f"'{printed_result}' while its return code maps to "
                        f"'{mapped_result}'; keeping the printed one\n"
                    )

            results[params][eng] = eng_res
        elif status in STATUS_RUN_RESULTS:
            results[params][eng] = {}
            results[params][eng]["run_result"] = STATUS_RUN_RESULTS[status]
        elif status != "execute":
            # "execute" rows are the task list pycobench writes before running anything.
            # Everything else is a status this version cannot render.
            sys.stderr.write(
                f"Warning: in {params} and {eng}: unknown run status '{status}'\n"
            )

    list_ptrns = []
    for bench in results:
        all_engs = True
        ls = list(bench)
        for eng in engines:
            out_len = len(engines_outs[eng]) + 1  # +1 = time
            if eng in results[bench]:
                bench_res = results[bench][eng]
                for out in engines_outs[eng]:
                    if out == "stats" and "output" in bench_res:
                        bench_res["output"][out] = StatisticsParser.stats_formatter(
                            bench_res["output"][out], args.stats_format
                        )

                if bench_res["run_result"] in RUN_RESULT_CELLS:
                    # the run produced no measurements, so every column of the
                    # engine says why
                    cell = RUN_RESULT_CELLS[bench_res["run_result"]]
                    for i in range(out_len):
                        ls.append(cell)
                else:
                    assert type(bench_res) == dict
                    assert "output" in bench_res

                    ls.append(bench_res["runtime"])
                    for out in engines_outs[eng]:
                        if out in bench_res["output"]:
                            out_data = bench_res["output"][out]

                            if out == "stats":
                                if args.stats == StatsDestination.SEPARATE_FILES:
                                    stats_file_name = f"{eng}-{bench[0].replace('/', '_').replace('.', '_')}-stats.json"
                                    with open(
                                        f"./stats/{current_time}/{stats_file_name}", "w"
                                    ) as f:
                                        f.write(out_data)
                                elif args.stats == StatsDestination.OUTPUT_FILE:
                                    if args.csv:
                                        out_data = out_data.replace("\n", "###")
                                    ls.append(out_data)
                            else:
                                ls.append(out_data)

                        else:
                            sys.stderr.write(
                                f"Warning: in {bench} and {eng}: element {out} not in "
                                f"{bench_res['output']}\n"
                            )
                            ls.append("MISSING")
                            # assert False
            else:
                all_engs = False
                for i in range(out_len):
                    ls.append("MISSING")

        # prepend with status of the benchmark
        if args.tick:
            if all_engs:
                ls = ["T"] + ls
            else:
                ls = ["F"] + ls

        list_ptrns.append(ls)

    header = []
    if args.tick:
        header += ["T"]

    header += ["name"]
    for eng in engines:
        header += [eng + "-runtime"]
        for out in engines_outs[eng]:
            if out == "stats" and args.stats != StatsDestination.OUTPUT_FILE:
                continue
            header += [eng + "-" + out]
            # header += [eng + "-result"]

    fmt = "text"
    if args.csv:
        fmt = "csv"
    if args.text:
        fmt = "text"
    if args.html:
        fmt = "html"

    if fmt == "html":
        return tabulate(list_ptrns, header, tablefmt="html")
    elif fmt == "text":
        return tabulate(list_ptrns, header, tablefmt="text")
    elif fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(
            output,
            delimiter=";",
            quotechar='"',
            escapechar="\\",
            doublequote=False,
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writerow(header)
        writer.writerows(list_ptrns)
        return output.getvalue()
    else:
        raise Exception(f'Invalid output format: "{fmt}"')


def parse_args():
    parser = argparse.ArgumentParser(
        description="Processes results of benchmarks from pycobench.py"
    )
    parser.add_argument(
        "result_file",
        nargs="?",
        help="file with results (output of pycobench.py) (default: '<stdin>')",
        type=argparse.FileType("r"),
        default=sys.stdin,
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file to print the parsed results (default: '<stdout>')",
        nargs="?",
        type=argparse.FileType("w"),
        default=sys.stdout,
    )
    parser.add_argument("--csv", action="store_true", help="output in CSV")
    parser.add_argument("--text", action="store_true", help="output in text")
    parser.add_argument("--html", action="store_true", help="output in HTML")
    parser.add_argument(
        "--tick",
        action="store_true",
        help="tick finished benchmarks (usable for filtering)",
    )
    parser.add_argument(
        "--params-num",
        type=int,
        default=1,
        help="tells pyco_proc how many parameters original file had",
    )
    parser.add_argument(
        "--stats-format",
        choices=[format_option.name.lower() for format_option in StatsFormat],
        default=StatsFormat.JSON.value,
        help="Which format to use for printing statistics (default: '%(default)s')",
    )
    parser.add_argument(
        "--stats",
        nargs="?",
        choices=[destination_option.name.lower() for destination_option in StatsDestination],
        const="output_file",
        default=None,
        help="Whether to output statistics and where (default: skipping stats, flag without \
                              argument: '%(const)s')",
    )
    args = parser.parse_args()

    if args.stats:
        args.stats = StatsDestination[args.stats.upper()]
    if args.stats_format:
        args.stats_format = StatsFormat[args.stats_format.upper()]

    return args


###############################
if __name__ == "__main__":
    args = parse_args()
    processed_results = proc_res(args.result_file, args)
    args.output.write(processed_results)
