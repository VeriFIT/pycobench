#!/usr/bin/env python3
"""Records what a benchmark run measured, and on what, as JSON.

A result table only names its engines; the manifest ties every engine to what
it runs (e.g. the commit a solver was built from) and keeps the settings, the
hashes of the input files, the revisions of the repositories and the machine a
run depends on, so that the run can be read and repeated without its command
line.

usage: pyco_manifest.py -o manifest.json
           [--engine <name> <key>=<value>... ]... [--set <key> <value>]...
           [--file <path>]... [--repository <name> <path>]... [--cpus <cpu>...]
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import subprocess


def run(*cmd, cwd=None):
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


def repo_state(path):
    """The commit of the repository at @path, preferring jj, which also sees its working copy."""
    commit = run("jj", "--ignore-working-copy", "log", "--no-graph", "-r", "@", "-T", "commit_id", cwd=path)
    if commit is not None:
        return {"commit": commit, "vcs": "jj"}
    commit = run("git", "-C", path, "rev-parse", "HEAD")
    if commit is None:
        return None
    dirty = run("git", "-C", path, "status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "vcs": "git", "dirty": bool(dirty)}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fd:
        for chunk in iter(lambda: fd.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cpu_model():
    try:
        with open("/proc/cpuinfo") as fd:
            for line in fd:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def governors(cpus):
    result = {}
    for cpu in cpus:
        try:
            with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_governor") as fd:
                result[cpu] = fd.read().strip()
        except OSError:
            result[cpu] = None
    return result


def engine(values, parser):
    name, *pairs = values
    info = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            parser.error(f"--engine {name}: expected <key>=<value>, got '{pair}'")
        info[key] = value
    return name, info


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", required=True, help="the JSON file to write")
    parser.add_argument("--engine", nargs="+", action="append", default=[], metavar=("NAME", "KEY=VALUE"),
                        help="an engine of the run and what describes it, e.g. 'z3 rev=v4.13 commit=3a1f'")
    parser.add_argument("--set", nargs=2, action="append", default=[], metavar=("KEY", "VALUE"),
                        help="a setting of the run, e.g. 'timeout 60'")
    parser.add_argument("--file", action="append", default=[],
                        help="a file the run depends on (configuration, inputs), recorded with its hash")
    parser.add_argument("--repository", nargs=2, action="append", default=[], metavar=("NAME", "PATH"),
                        help="a git or jj repository the run depends on, recorded with its commit")
    parser.add_argument("--cpus", nargs="+", default=[], help="the CPUs the run is pinned to")
    args = parser.parse_args()

    manifest = {
        "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "settings": dict(args.set),
        "engines": dict(engine(values, parser) for values in args.engine),
        "files": {path: sha256(path) for path in args.file},
        "repositories": {name: repo_state(path) for name, path in args.repository},
        "machine": {
            "host": platform.node(),
            "system": " ".join(platform.uname()[i] for i in (0, 2, 4)),
            "cpu": cpu_model(),
            "cpu_count": os.cpu_count(),
            "cpus": args.cpus,
            "governors": governors(args.cpus),
            "python": platform.python_version(),
        },
    }
    with open(args.output, "w") as fd:
        json.dump(manifest, fd, indent=2)
        fd.write("\n")


if __name__ == "__main__":
    main()
