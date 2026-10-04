import tabulate
import pandas
import sys
import numpy

# the cells pyco_proc writes instead of measurements, by the run status they
# stand for
OUTCOME_CELLS = {
    "TO": "timeouts",
    "MO": "memouts",
    "ERR": "errors",
    "CRASH": "crashes",
}


def load_dataframe(path):
    """Loads from @path pandas dataframe and computes averages and medians in each metric

    :param path: path to csv file delimited by ;
    :return: averages and medians of each column, and the counts of the runs
        that produced no measurement
    """

    def transform(cell):
        if cell in OUTCOME_CELLS:
            return numpy.nan
        else:
            try:
                return float(cell)
            except ValueError:
                return cell

    try:
        df = pandas.read_csv(path, sep=";")
    except Exception as ex:
        print(f"error while reading from {path}: {ex}")
        exit(1)
    outcomes = {
        col: {cell: int(df[col].value_counts().get(cell, 0)) for cell in OUTCOME_CELLS}
        for col in df.columns
        if col.endswith("runtime")
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


if __name__ == "__main__":
    profiles = sys.argv[1:]
    if len(profiles) == 0:
        print(
            "usage: compare_profiles.py [target.csv baseline1.csv ... baselinen.csv]",
            file=sys.stderr,
        )
        sys.exit(1)

    averages, medians, outcomes = [], [], []
    columns, outcome_columns = set(), set()
    for profile in profiles:
        avg, med, outs = load_dataframe(profile)
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

    # Summary of the runs that produced no measurement; only the outcomes that
    # actually occurred are reported
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
        sys.exit(0)

    headers = ["metric"]
    for label in labels:
        headers += [f"{label} ({OUTCOME_CELLS[cell]})" for cell in reported_cells]

    data = []
    for col in sorted(outcome_columns):
        row = [col]
        for i in range(len(profiles)):
            row += [outcomes[i].get(col, {}).get(cell, 0) for cell in reported_cells]
        data.append(row)

    print(tabulate.tabulate(data, headers=headers, floatfmt=".3f"))
