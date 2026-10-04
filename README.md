# pycobench
A small Python framework for running benchmarks.

Contents:
* `src/pycobench.py`: The main script for running benchmarks
* `src/pyco_proc.py`: The script for processing the output of `pycobench.py` into a table
* `src/compare_profiles.py`: Compares averages/medians/timeouts across one or more result CSVs
* `examples/ba-compl.yaml`: Example of a YAML file for configuring what to run
* `examples/ba-all.input`: Example of a file with input benchmarks

Setup (dependencies are managed by [uv](https://docs.astral.sh/uv/)):
```
uv sync
```

How to run:
```
cat benchmarks.input | uv run src/pycobench.py -c config.yaml -j JOBS -t TIMEOUT -o OUTPUT_FILE
```
When the benchmarks finish, you can process the results by
```
cat OUTPUT_FILE | uv run src/pyco_proc.py --csv > OUTPUT_CSV
```

The configuration file maps each engine to the command used to run it. Settings
shared by all engines can be given once in a reserved top-level `defaults`
section, where per-engine values take precedence:
```yaml
defaults:
  return_codes:
    0: finished
    "*": error

my-engine:
  cmd: ./run-engine.sh $1 $2

my-solver:
  cmd: ./run-solver.sh $1
  return_codes:
    10: { status: finished, result: sat }
    20: { status: finished, result: unsat }
    "*": error
```

### Run statuses

Every run ends in exactly one status, recorded in the output file and rendered
by `pyco_proc.py` in every column of the engine:

| status     | cell    | meaning                                                      |
| ---------- | ------- | ------------------------------------------------------------ |
| `finished` |         | the run produced measurements                                 |
| `error`    | `ERR`   | the run ended in a way that does not count as an answer       |
| `timeout`  | `TO`    | the run exceeded `--timeout` and was killed                   |
| `memout`   | `MO`    | the run reached the memory limit given by `--memout`          |
| `crash`    | `CRASH` | the run was killed by a signal (segfault, abort, ...)         |

`return_codes` maps a return code to the status it stands for, with `"*"`
matching every code without an entry of its own (the default mapping is
`{0: finished, 1: finished, "*": error}`, since solvers often return 1 for a
legitimate result). A per-engine mapping replaces the default one as a whole.
`timeout` is observed by pycobench and cannot be assigned to a return code; a
code left to the catch-all entry is refined automatically into `crash` when the
command died on a signal, or into `memout` when its peak memory reached
`--memout`.

### Answers of the engines

An entry may also name the answer a return code stands for, as in the
`my-solver` example above, which records `sat`/`unsat` in a `<engine>-result`
column while keeping the run `finished` with its measured time. Engines that
print their answer instead (e.g. `result: unsat` on standard output, like any
other `key: value` line that `pyco_proc.py` turns into a column) need no
configuration; when both are present, the printed answer wins and the
disagreement is reported on standard error.

With GNU time available, the peak resident set size of every run is measured
and reported in a `<engine>-maxrss` column (in kB). BSD time (e.g. on macOS)
does not support the required format, in which case no memory is reported.
