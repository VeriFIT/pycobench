#!/usr/bin/env python3

"""Reads the ';'-delimited result tables that pyco_proc writes.

A table has one row per benchmark instance (the "name" column) and, for every engine, one column
per metric that engine reported, named "<engine>-<metric>".  Every engine always reports "runtime",
which is what identifies the engines in the header.  A run that produced no measurement fills all of
its engine's columns with the status cell of its outcome ("TO", "MO", "ERR", "CRASH"), and an engine
that was not run at all on an instance fills them with "MISSING".
"""

import sys

import numpy
import pandas

# The cells pyco_proc writes instead of measurements, by the run status they stand for.
OUTCOME_CELLS = {
    "TO": "timeouts",
    "MO": "memouts",
    "ERR": "errors",
    "CRASH": "crashes",
}

# The cell pyco_proc writes for an engine that produced no row for the instance at all.
MISSING_CELL = "MISSING"

# The metric every engine reports, and therefore the one the engine names are read off.
RUNTIME_METRIC = "runtime"


def _engine_of(column, engines):
    """Returns the engine @column belongs to: the longest engine name it is prefixed with.

    Engine names may themselves contain '-' (e.g. 'mata-words-before'), so the longest match is the
    only unambiguous one.
    """
    owner = None
    for engine in engines:
        if column.startswith(f"{engine}-") and (owner is None or len(engine) > len(owner)):
            owner = engine
    return owner


class Results:
    """One result table: the raw cells, the per-engine run statuses, and numeric views of them."""

    def __init__(self, frame, path=None):
        self.path = path
        suffix = f"-{RUNTIME_METRIC}"
        self.engines = [col[: -len(suffix)] for col in frame.columns if col.endswith(suffix)]
        if not self.engines:
            raise ValueError(f"{path}: no '<engine>-{RUNTIME_METRIC}' column, so no engine to read")

        self.instances = pandas.Index(frame["name"], name="name")
        cells = {}
        statuses = {}
        for engine in self.engines:
            columns = [col for col in frame.columns if _engine_of(col, self.engines) == engine]
            for column in columns:
                cells[(engine, column[len(engine) + 1 :])] = frame[column].to_numpy()
            runtimes = frame[f"{engine}{suffix}"]
            statuses[engine] = runtimes.where(runtimes.isin(OUTCOME_CELLS), "").to_numpy()

        self.cells = pandas.DataFrame(cells, index=self.instances)
        self.cells.columns = pandas.MultiIndex.from_tuples(
            self.cells.columns, names=["engine", "metric"]
        )
        # "" for a run that produced measurements, the status cell otherwise.
        self.status = pandas.DataFrame(statuses, index=self.instances)

    def metrics(self, engine):
        """Metrics reported by @engine, runtime first."""
        reported = list(self.cells[engine].columns)
        return [RUNTIME_METRIC] + [metric for metric in reported if metric != RUNTIME_METRIC]

    def common_metrics(self):
        """Metrics every engine reported, runtime first."""
        shared = set.intersection(*(set(self.cells[engine].columns) for engine in self.engines))
        return [RUNTIME_METRIC] + sorted(shared - {RUNTIME_METRIC})

    def numeric(self, metric, timeout=None, penalties=None):
        """The @metric of every engine as floats, indexed by instance.

        Cells carrying a run status are charged @penalties[cell] times @timeout (PAR-2-style
        scoring) when both are given and left NaN otherwise, so a failed engine cannot look fast by
        abandoning its slowest runs.  Cells that are not numbers at all (a textual metric, or
        "MISSING") are NaN.
        """
        penalties = penalties or {}

        def as_float(cell):
            if cell in OUTCOME_CELLS:
                penalty = penalties.get(cell)
                return penalty * timeout if penalty is not None and timeout is not None else numpy.nan
            try:
                return float(cell)
            except (TypeError, ValueError):
                return numpy.nan

        columns = {
            engine: self.cells[(engine, metric)].map(as_float)
            for engine in self.engines
            if (engine, metric) in self.cells.columns
        }
        return pandas.DataFrame(columns, index=self.instances)

    def text(self, metric):
        """The @metric of every engine as the raw cells, indexed by instance."""
        columns = {
            engine: self.cells[(engine, metric)]
            for engine in self.engines
            if (engine, metric) in self.cells.columns
        }
        return pandas.DataFrame(columns, index=self.instances)

    def status_counts(self):
        """Per engine, how many runs ended in each status, and how many produced measurements."""
        counts = {}
        for engine in self.engines:
            column = self.status[engine]
            engine_counts = {cell: int((column == cell).sum()) for cell in OUTCOME_CELLS}
            engine_counts["finished"] = int((column == "").sum())
            counts[engine] = engine_counts
        return pandas.DataFrame(counts).T


def load(path):
    """Reads the result table at @path, exiting with a message when it cannot be read."""
    try:
        frame = pandas.read_csv(path, sep=";", dtype=str, keep_default_na=False)
    except Exception as ex:
        print(f"error while reading from {path}: {ex}", file=sys.stderr)
        sys.exit(1)
    if "name" not in frame.columns:
        print(f"error: {path} has no 'name' column, so it is not a result table", file=sys.stderr)
        sys.exit(1)
    return Results(frame, path=path)
