#!/usr/bin/env python3
"""Merges the result tables of repeated runs of one job into a single table.

A cell measured in every repeat keeps its minimum: the least disturbed run is
the closest to what the code costs. A cell that is not a number in some repeat
(a timeout, an error, an answer) keeps the first such value, so a run that
failed once is not reported as finished.

usage: pyco_merge.py -o merged.csv rep-1.csv rep-2.csv ...
"""

import argparse
import csv
import sys


def number(cell):
    try:
        return float(cell)
    except ValueError:
        return None


def read(path):
    with open(path, newline="") as fd:
        rows = list(csv.reader(fd, delimiter=";"))
    if not rows:
        sys.exit(f"error: {path} is empty")
    header, body = rows[0], rows[1:]
    # The header names one 'name' column, while a row spells out every
    # parameter of the instance before the metrics.
    metrics = len(header) - 1
    table = {}
    for row in body:
        key = tuple(row[: len(row) - metrics])
        table[key] = row[len(row) - metrics :]
    return header, table, [tuple(row[: len(row) - metrics]) for row in body]


def merge(cells):
    numbers = [number(cell) for cell in cells]
    if all(n is not None for n in numbers):
        best = min(range(len(cells)), key=lambda i: numbers[i])
        return cells[best]
    return next(cell for cell, n in zip(cells, numbers) if n is None)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("tables", nargs="+")
    args = parser.parse_args()

    header, first, order = read(args.tables[0])
    tables = [first]
    for path in args.tables[1:]:
        other_header, table, _ = read(path)
        if other_header != header:
            sys.exit(f"error: {path} has a different header than {args.tables[0]}")
        if table.keys() != first.keys():
            sys.exit(f"error: {path} measured different instances than {args.tables[0]}")
        tables.append(table)

    with open(args.output, "w", newline="") as fd:
        writer = csv.writer(fd, delimiter=";", lineterminator="\n")
        writer.writerow(header)
        for key in order:
            columns = zip(*(table[key] for table in tables))
            writer.writerow(list(key) + [merge(list(cells)) for cells in columns])


if __name__ == "__main__":
    main()
