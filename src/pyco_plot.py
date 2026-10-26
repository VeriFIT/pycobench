#!/usr/bin/env python3

"""Turns a pyco_proc result table into the plots and tables an experiment is reported with.

For one metric (wall-clock "runtime" by default) it writes, into an output directory:

  * ``scatter-<metric>-<baseline>-vs-<engine>.<fmt>`` -- one log-log scatter plot per engine
    compared against the baseline, with the runs that produced no measurement drawn on the border
    lines at the timeout,
  * ``cactus-<metric>.<fmt>`` -- the usual cactus plot: instances solved within a time limit,
  * ``summary-<metric>.{md,tex,csv}`` -- per engine, how many runs finished or failed and the
    distribution of the metric,
  * ``pairwise-<metric>.{md,tex,csv}`` -- per engine against the baseline, how often it wins and by
    how much,
  * ``summary-by-family-<metric>.{md,tex,csv}`` -- per family of instances (given by --families)
    and engine, how many runs finished and their PAR-2 score,
  * ``answers.{md,csv}`` -- per engine, how many finished runs gave each answer (when the engines
    report a "result" metric),
  * ``disagreements.csv`` -- the instances on which the engines reported different answers (when
    at least two engines report a "result" metric).

With --solved, a run that finished without one of the given answers (e.g. an SMT solver giving up
with "unknown") is counted as unsolved: it is scored and drawn like a failure, and only the answers
of solved runs are cross-checked.

The engines and their metrics are read off the table's header, so nothing here is specific to any
particular benchmark, tool or operation.
"""

import argparse
import sys
from pathlib import Path

import numpy
import pandas
import tabulate

import pyco_results
from pyco_results import STATUS_CELLS

# Written next to the figures so that a report can be regenerated with different options.
TABLE_FORMATS = {"md": "github", "tex": "latex_booktabs"}

# The metric that names the answer an engine computed, used to cross-check the engines.
RESULT_METRIC = "result"


def _import_pyplot():
    """Imports matplotlib with a non-interactive backend, which is all a report needs."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as pyplot
    except ImportError:
        print(
            "error: plotting needs matplotlib; install it or pass --no-plots",
            file=sys.stderr,
        )
        sys.exit(1)
    return pyplot


def _limit(values, timeout):
    """The value a run that produced no measurement is drawn at."""
    if timeout is not None:
        return timeout
    finite = values[numpy.isfinite(values)]
    # Without a timeout, put the failures an order of magnitude past the slowest measurement so
    # that they stay visibly off the cloud instead of mixing into it.
    return float(finite.max()) * 10 if len(finite) else 1.0


def _floor(values):
    """A positive lower bound for the log axes; measurements of 0 would otherwise be dropped."""
    positive = values[numpy.isfinite(values) & (values > 0)]
    return float(positive.min()) / 2 if len(positive) else 1e-6


def scatter(results, metric, baseline, engine, timeout, outdir, formats, title):
    """Writes the log-log scatter plot of @engine against @baseline."""
    pyplot = _import_pyplot()

    values = results.numeric(metric)
    status = results.status
    both = pandas.DataFrame(
        {
            "x": values[baseline],
            "y": values[engine],
            "x_failed": status[baseline] != "",
            "y_failed": status[engine] != "",
        }
    )
    limit = _limit(numpy.concatenate([both["x"].to_numpy(), both["y"].to_numpy()]), timeout)
    floor = _floor(numpy.concatenate([both["x"].to_numpy(), both["y"].to_numpy()]))
    both["x"] = both["x"].fillna(limit).clip(lower=floor, upper=limit)
    both["y"] = both["y"].fillna(limit).clip(lower=floor, upper=limit)

    figure, axes = pyplot.subplots(figsize=(5.2, 5.0))
    diagonal = numpy.array([floor, limit])
    axes.plot(diagonal, diagonal, color="black", linewidth=0.8, zorder=1)
    for factor in (10, 100):
        axes.plot(diagonal, diagonal / factor, color="grey", linewidth=0.5, linestyle=":", zorder=1)
        axes.plot(diagonal, diagonal * factor, color="grey", linewidth=0.5, linestyle=":", zorder=1)

    solved = ~both["x_failed"] & ~both["y_failed"]
    axes.scatter(
        both.loc[solved, "x"], both.loc[solved, "y"],
        s=12, alpha=0.6, edgecolors="none", color="tab:blue", label="both finished", zorder=3,
    )
    for mask, color, marker, label in (
        (both["x_failed"] & ~both["y_failed"], "tab:green", "<", f"{baseline} failed"),
        (~both["x_failed"] & both["y_failed"], "tab:red", "^", f"{engine} failed"),
        (both["x_failed"] & both["y_failed"], "tab:grey", "x", "both failed"),
    ):
        if mask.any():
            axes.scatter(
                both.loc[mask, "x"], both.loc[mask, "y"],
                s=18, alpha=0.8, color=color, marker=marker, label=label, zorder=2,
            )

    axes.set_xscale("log")
    axes.set_yscale("log")
    axes.set_xlim(floor, limit * 1.1)
    axes.set_ylim(floor, limit * 1.1)
    axes.set_xlabel(f"{baseline} -- {metric} [s]")
    axes.set_ylabel(f"{engine} -- {metric} [s]")
    axes.set_title(title or f"{engine} vs {baseline}")
    axes.grid(True, which="major", linewidth=0.3, alpha=0.5)
    axes.legend(loc="lower right", fontsize="small", framealpha=0.9)
    figure.tight_layout()

    written = []
    for fmt in formats:
        path = outdir / f"scatter-{metric}-{baseline}-vs-{engine}.{fmt}"
        figure.savefig(path, dpi=200)
        written.append(path)
    pyplot.close(figure)
    return written


def cactus(results, metric, timeout, outdir, formats, title):
    """Writes the cactus plot: per engine, the instances it finished sorted by the metric."""
    pyplot = _import_pyplot()

    figure, axes = pyplot.subplots(figsize=(6.0, 4.2))
    values = results.numeric(metric)
    for engine in results.engines:
        finished = values.loc[results.status[engine] == "", engine].dropna()
        ordered = numpy.sort(finished.to_numpy())
        axes.plot(numpy.arange(1, len(ordered) + 1), ordered, linewidth=1.4, label=engine)
    if timeout is not None:
        axes.axhline(timeout, color="black", linewidth=0.8, linestyle="--", label="timeout")

    axes.set_yscale("log")
    axes.set_xlabel("instances finished")
    axes.set_ylabel(f"{metric} [s]")
    axes.set_title(title or f"cactus plot ({metric})")
    axes.grid(True, which="major", linewidth=0.3, alpha=0.5)
    axes.legend(loc="upper left", fontsize="small", framealpha=0.9)
    figure.tight_layout()

    written = []
    for fmt in formats:
        path = outdir / f"cactus-{metric}.{fmt}"
        figure.savefig(path, dpi=200)
        written.append(path)
    pyplot.close(figure)
    return written


def summary_table(results, metric, timeout):
    """Per engine: how the runs ended and how the metric is distributed over the finished ones."""
    values = results.numeric(metric)
    counts = results.status_counts()
    penalised = results.numeric(metric, timeout=timeout, penalties={cell: 2.0 for cell in STATUS_CELLS})
    rows = []
    for engine in results.engines:
        finished = values.loc[results.status[engine] == "", engine].dropna()
        row = {
            "engine": engine,
            "instances": len(values),
            "finished": int(counts.loc[engine, "finished"]),
        }
        row.update(
            {STATUS_CELLS[cell]: int(counts.loc[engine, cell]) for cell in STATUS_CELLS}
        )
        row.update(
            {
                "mean": finished.mean(),
                "median": finished.median(),
                "max": finished.max(),
                "total": finished.sum(),
                "PAR2": penalised[engine].mean() if timeout is not None else numpy.nan,
            }
        )
        rows.append(row)
    table = pandas.DataFrame(rows)
    # Columns nobody failed in only add noise.
    return table.drop(columns=[name for name in STATUS_CELLS.values() if (table[name] == 0).all()])


def family_table(results, metric, timeout, families):
    """Per family of instances and engine: how many runs finished and their PAR-2 score."""
    penalised = results.numeric(metric, timeout=timeout, penalties={cell: 2.0 for cell in STATUS_CELLS})
    family = families.reindex(results.instances).fillna("?")
    rows = []
    for name in dict.fromkeys(family):
        members = (family == name).to_numpy()
        row = {"family": name, "instances": int(members.sum())}
        for engine in results.engines:
            row[f"{engine} finished"] = int((results.status.loc[members, engine] == "").sum())
            if timeout is not None:
                row[f"{engine} PAR2"] = penalised.loc[members, engine].mean()
        rows.append(row)
    return pandas.DataFrame(rows)


def answers_table(results):
    """Per engine: how many finished runs gave each answer, the answers of a run counted apart."""
    answers = results.text(RESULT_METRIC)
    if answers.empty:
        return None
    counts = {}
    for engine in answers.columns:
        # A run that failed has no answers; one that is merely unsolved still has.
        given = answers[engine][~results.status[engine].isin(pyco_results.OUTCOME_CELLS)]
        counts[engine] = given.str.split(",").explode().value_counts()
    table = pandas.DataFrame(counts).T.fillna(0).astype(int)
    return table.rename_axis("engine").reset_index()


def pairwise_table(results, metric, baseline, timeout):
    """Per engine against @baseline: who finishes what, and the speed-up where both finish."""
    values = results.numeric(metric)
    rows = []
    for engine in results.engines:
        if engine == baseline:
            continue
        base_ok = results.status[baseline] == ""
        eng_ok = results.status[engine] == ""
        both = base_ok & eng_ok & values[baseline].notna() & values[engine].notna()
        # A speed-up of 0 s over 0 s is not informative; the resolution of the clock bounds it.
        comparable = both & (values[baseline] > 0) & (values[engine] > 0)
        speedup = values.loc[comparable, baseline] / values.loc[comparable, engine]
        rows.append(
            {
                "engine": engine,
                "both finished": int(both.sum()),
                f"only {engine}": int((eng_ok & ~base_ok).sum()),
                f"only {baseline}": int((base_ok & ~eng_ok).sum()),
                "faster": int((speedup > 1).sum()),
                "slower": int((speedup < 1).sum()),
                "median speed-up": speedup.median(),
                "min speed-up": speedup.min() if len(speedup) else numpy.nan,
                "max speed-up": speedup.max() if len(speedup) else numpy.nan,
                # Of the summed metric, dominated by the hard instances the median overlooks.
                "total speed-up": (
                    values.loc[both, baseline].sum() / values.loc[both, engine].sum()
                    if values.loc[both, engine].sum() > 0 else numpy.nan
                ),
            }
        )
    return pandas.DataFrame(rows)


def disagreements(results):
    """The instances on which the engines that report an answer did not report the same one."""
    answers = results.text(RESULT_METRIC)
    # An engine that never printed an answer (one that cannot, or that timed out on every
    # instance) has no column: the others are still compared.
    if answers.shape[1] < 2:
        return None
    # Only runs that finished have an answer to compare.
    comparable = answers.where(results.status[answers.columns] == "")
    differing = comparable.apply(lambda row: row.dropna().nunique() > 1, axis=1)
    return answers[differing]


def write_table(table, outdir, stem, floatfmt=".4g"):
    """Writes @table as CSV and as every format a report embeds."""
    written = [outdir / f"{stem}.csv"]
    table.to_csv(written[0], index=False)
    for extension, tablefmt in TABLE_FORMATS.items():
        path = outdir / f"{stem}.{extension}"
        path.write_text(
            tabulate.tabulate(table, headers="keys", showindex=False, tablefmt=tablefmt, floatfmt=floatfmt)
            + "\n"
        )
        written.append(path)
    return written


def parse_args():
    parser = argparse.ArgumentParser(
        description="Plots and tabulates a pycobench result table.",
    )
    parser.add_argument("results", help="the ';'-delimited .csv written by process_pyco.sh")
    parser.add_argument(
        "-m", "--metric", action="append", default=None,
        help="metric to report; repeatable, defaults to 'runtime'. 'all' reports every metric "
        "that all engines share.",
    )
    parser.add_argument(
        "-t", "--timeout", type=float, default=None,
        help="timeout (in seconds) of the run; draws the limit and scores failures as PAR-2",
    )
    parser.add_argument(
        "-b", "--baseline", default=None,
        help="engine the others are compared against (default: the first one in the table)",
    )
    parser.add_argument(
        "-e", "--engines", default=None,
        help="comma-separated engines to report, in this order (default: all of them)",
    )
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="where to write the report (default: '<results>-report' next to the table)",
    )
    parser.add_argument(
        "-f", "--formats", default="pdf,png", help="figure formats to write (default: pdf,png)"
    )
    parser.add_argument(
        "-s", "--solved", default=None,
        help="comma-separated answers (of the 'result' metric) a finished run must give to count as "
        "solved, e.g. 'sat,unsat' (default: every finished run counts)",
    )
    parser.add_argument(
        "--families", default=None,
        help="';'-delimited .csv with a 'name' and a 'family' column; adds a per-family summary",
    )
    parser.add_argument("--title", default=None, help="title of the figures")
    parser.add_argument("--no-plots", action="store_true", help="only write the tables")
    parser.add_argument("--no-tables", action="store_true", help="only write the figures")
    return parser.parse_args(), parser


def main():
    args, parser = parse_args()
    results = pyco_results.load(args.results)

    if args.engines:
        selected = [engine.strip() for engine in args.engines.split(",") if engine.strip()]
        unknown = [engine for engine in selected if engine not in results.engines]
        if unknown:
            parser.error(f"no such engine in {args.results}: {', '.join(unknown)} "
                         f"(have: {', '.join(results.engines)})")
        results.engines = selected
    baseline = args.baseline or results.engines[0]
    if baseline not in results.engines:
        parser.error(f"no such engine in {args.results}: {baseline} "
                     f"(have: {', '.join(results.engines)})")
    if args.solved:
        results.require_answers({answer.strip() for answer in args.solved.split(",")}, RESULT_METRIC)
    families = None
    if args.families:
        frame = pandas.read_csv(args.families, sep=";", dtype=str, keep_default_na=False)
        if not {"name", "family"} <= set(frame.columns):
            parser.error(f"{args.families} needs a 'name' and a 'family' column")
        families = frame.drop_duplicates("name").set_index("name")["family"]

    metrics = args.metric or [pyco_results.RUNTIME_METRIC]
    if "all" in metrics:
        metrics = results.common_metrics()

    outdir = Path(args.output_dir or f"{Path(args.results).with_suffix('')}-report")
    outdir.mkdir(parents=True, exist_ok=True)
    formats = [fmt.strip() for fmt in args.formats.split(",") if fmt.strip()]

    written = []
    for metric in metrics:
        reporting = [engine for engine in results.engines if (engine, metric) in results.cells.columns]
        if len(reporting) < len(results.engines):
            missing = set(results.engines) - set(reporting)
            print(f"warning: skipping metric '{metric}', not reported by {', '.join(sorted(missing))}",
                  file=sys.stderr)
            continue

        if not args.no_tables:
            written += write_table(summary_table(results, metric, args.timeout), outdir, f"summary-{metric}")
            if len(results.engines) > 1:
                written += write_table(
                    pairwise_table(results, metric, baseline, args.timeout), outdir, f"pairwise-{metric}"
                )
            if families is not None:
                written += write_table(
                    family_table(results, metric, args.timeout, families), outdir,
                    f"summary-by-family-{metric}",
                )
        if not args.no_plots:
            written += cactus(results, metric, args.timeout, outdir, formats, args.title)
            for engine in results.engines:
                if engine != baseline:
                    written += scatter(
                        results, metric, baseline, engine, args.timeout, outdir, formats, args.title
                    )

    answers = answers_table(results)
    if answers is not None and not args.no_tables:
        written += write_table(answers, outdir, "answers")

    differing = disagreements(results)
    if differing is not None and not differing.empty:
        path = outdir / "disagreements.csv"
        differing.to_csv(path)
        written.append(path)
        print(f"warning: {len(differing)} instance(s) with differing answers, see {path}",
              file=sys.stderr)

    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
