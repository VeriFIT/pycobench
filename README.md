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
