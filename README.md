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

The configuration file maps each engine to the command used to run it and can
additionally define `accepted_return_codes`, the list of return codes that mark
a run as successful (default `[0, 1]`); a run exiting with any other return
code is recorded as an error. Settings shared by all engines can be given once
in a reserved top-level `defaults` section, where per-engine values take
precedence:
```yaml
defaults:
  accepted_return_codes: [0]

my-engine:
  cmd: ./run-engine.sh $1 $2

my-solver:
  cmd: ./run-solver.sh $1
  accepted_return_codes: [0, 1]
```
