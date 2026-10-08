#!/usr/bin/env python3

import argparse
import sys

import numpy
import pandas
import tabulate

from pyco_results import OUTCOME_CELLS


def load_dataframe(path, timeout=None, penalties=None):
    """Loads from @path pandas dataframe and computes averages and medians in each metric

    Cells carrying a run status ("TO", "MO", "ERR", "CRASH") are mapped to @penalties[cell] times
    the @timeout (PAR-2-style scoring) instead of being dropped from the average; a status absent
    from @penalties, or a missing @timeout, keeps the cell NaN as before. A timed-out (or otherwise
    failed) target therefore cannot look faster than the baseline by abandoning its slowest run.

    :param path: path to csv file delimited by ;
    :param timeout: profiling timeout in seconds used by the run; every configured penalty scales it
    :param penalties: mapping status cell ("TO", "MO", "ERR", "CRASH") to a multiple of @timeout
        charged for that status; cells without a penalty stay NaN
    :return: averages and medians of each column, and the counts of the runs that produced no measurement
    """
    penalties = penalties or {}

    def transform(cell):
        if cell in OUTCOME_CELLS:
            penalty = penalties.get(cell)
            if penalty is not None and timeout is not None:
                return penalty * timeout
            return numpy.nan
        try:
            return float(cell)
        except ValueError:
            return cell

    try:
        df = pandas.read_csv(path, sep=";")
    except Exception as ex:
        print(f"error while reading from {path}: {ex}")
        sys.exit(1)
    outcomes = {
        col: {cell: int(df[col].value_counts().get(cell, 0)) for cell in OUTCOME_CELLS}
        for col in df.columns
        if col != "name"
    }
    df = df.map(transform).drop(columns=["name"])
    avgs = df.mean(numeric_only=True, skipna=True)
    meds = df.median(numeric_only=True, skipna=True)
    return avgs, meds, outcomes


def profile_labels(profile_cnt):
    """Names the compared profiles: the first one is the target, the rest are baselines"""
    if profile_cnt == 2:
        return ["target", "baseline"]
    return ["target"] + [f"base{i}" for i in range(1, profile_cnt)]


def _parse_multiplier(value, parser, option):
    """Parses a penalty multiplier: a float, or 'off'/'nan' to drop the status from the averages."""
    lowered = str(value).strip().lower()
    if lowered in ("off", "nan"):
        return None
    try:
        return float(lowered)
    except ValueError:
        parser.error(f"{option}: invalid multiplier '{value}' (float or 'off')")


def build_penalties(args, parser):
    """Builds the per-status penalty map from --timeout, --penalty, and the --penalty-* options.

    With --timeout, every status defaults to twice the timeout (PAR-2); --penalty CELL=MULT specs
    apply left to right and the individual --penalty-<cell> options take precedence over those.
    A penalty without --timeout is an error: the multiplier has nothing to scale.
    """
    specified = args.penalty or any(
        getattr(args, f"penalty_{cell.lower()}") is not None for cell in OUTCOME_CELLS
    )
    if specified and args.timeout is None:
        parser.error("--penalty/--penalty-* require --timeout")

    penalties = (
        {cell: 2.0 for cell in OUTCOME_CELLS} if args.timeout is not None else {}
    )
    for spec in args.penalty or []:
        for pair in spec.split(","):
            cell, sep, mult = pair.partition("=")
            cell = cell.strip().upper()
            if not sep or cell not in OUTCOME_CELLS:
                parser.error(
                    f"--penalty: invalid pair '{pair.strip()}'; use CELL=MULT with CELL in "
                    f"({', '.join(cell.lower() for cell in OUTCOME_CELLS)})"
                )
            penalties[cell] = _parse_multiplier(
                mult, parser, f"--penalty {pair.strip()}"
            )
    for cell in OUTCOME_CELLS:
        value = getattr(args, f"penalty_{cell.lower()}")
        if value is not None:
            penalties[cell] = _parse_multiplier(
                value, parser, f"--penalty-{cell.lower()}"
            )
    return penalties


def main():
    parser = argparse.ArgumentParser(description="Compare performance profiles.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Timeout (in seconds) of the profiled runs; failed runs are scored as a multiple of "
        "the timeout (PAR-2), per the --penalty/--penalty-* options.",
    )
    parser.add_argument(
        "--penalty",
        action="append",
        default=None,
        metavar="CELL=MULT[,CELL=MULT...]",
        help="Charge MULT times --timeout for runs with the given status instead of dropping them "
        "from the averages. May be repeated; comma-separated pairs apply left to right. Cells: "
        + ", ".join(cell.lower() for cell in OUTCOME_CELLS)
        + "; 'off' (or 'nan') drops the status from the averages. Defaults: 2.0 for every status "
        "once --timeout is given. Individual --penalty-* options take precedence.",
    )
    for cell in OUTCOME_CELLS:
        parser.add_argument(
            f"--penalty-{cell.lower()}",
            default=None,
            metavar="MULT",
            help=f"Charge MULT times --timeout for a {cell} run instead of dropping it from the "
            f"averages; 'off' (or 'nan') drops it (default: 2.0 for every status once --timeout "
            f"is given).",
        )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Fail with exit status 1 when target/baseline average ratio exceeds this value for any metric.",
    )
    parser.add_argument(
        "profiles", nargs="+", help="target.csv baseline1.csv ... baselinen.csv"
    )
    args = parser.parse_args()

    penalties = build_penalties(args, parser)
    profiles = args.profiles
    averages, medians, outcomes = [], [], []
    columns, outcome_columns = set(), set()
    for profile in profiles:
        avg, med, outs = load_dataframe(
            profile, timeout=args.timeout, penalties=penalties
        )
        columns.update(list(avg.keys()))
        averages.append(avg)
        medians.append(med)
        outcomes.append(outs)
        outcome_columns.update(list(outs.keys()))

    labels = profile_labels(len(profiles))

    headers = ["metric"]
    for label in labels:
        headers += [f"{label} (avg)", f"{label} (med)"]

    data = []
    for col in columns:
        row = [col]
        for i in range(len(profiles)):
            row += [averages[i][col]]
            row += [medians[i][col]]
        data.append(row)
    data = sorted(data, key=lambda x: x[0])

    print(tabulate.tabulate(data, headers=headers, floatfmt=".3f"))

    print()

    # Summary of the runs that produced no measurement.
    # Only the outcomes that actually occurred are reported.
    reported_cells = [
        cell
        for cell in OUTCOME_CELLS
        if any(
            outcomes[i][col][cell]
            for i in range(len(profiles))
            for col in outcome_columns
            if col in outcomes[i]
        )
    ]

    if not reported_cells:
        print("No timeouts, memouts, errors or crashes.")
    else:
        headers = ["metric"]
        for label in labels:
            headers += [f"{label} ({OUTCOME_CELLS[cell]})" for cell in reported_cells]

        data = []
        for col in sorted(outcome_columns):
            row = [col]
            for i in range(len(profiles)):
                row += [
                    outcomes[i].get(col, {}).get(cell, 0) for cell in reported_cells
                ]
            data.append(row)

        print(tabulate.tabulate(data, headers=headers, floatfmt=".3f"))

    # Target/baseline average ratios.
    if len(profiles) == 2:
        ratio_headers = ["metric", "target/baseline (avg)"]
        data = []
        regressions = []
        for col in columns:
            baseline_avg = averages[1][col]
            target_avg = averages[0][col]
            if (
                not numpy.isfinite(target_avg)
                or not numpy.isfinite(baseline_avg)
                or baseline_avg == 0
            ):
                data.append([col, float("nan")])
                continue
            ratio = target_avg / baseline_avg
            data.append([col, ratio])
            if args.threshold is not None and ratio > args.threshold:
                regressions.append((col, ratio))
        print()
        print(tabulate.tabulate(data, headers=ratio_headers, floatfmt=".3f"))

        if regressions:
            for col, ratio in regressions:
                print(
                    f"REGRESSION: '{col}' slowed down {ratio:.3f}x past the threshold {args.threshold}."
                )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
