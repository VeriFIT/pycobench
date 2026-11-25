import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src import pyco_results, pycobench


class OutputTruncationTest(unittest.TestCase):
    maxDiff = None

    def measure(self, outputs):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            config = {}
            expected_compositions = {}
            for engine, (stdout, composition) in outputs.items():
                program = directory / f"program-{engine}.py"
                program.write_text(f"import sys\nsys.stdout.write({stdout!r})\n")
                config[engine] = {"cmd": f"{sys.executable} {program}"}
                expected_compositions[engine] = composition
            (directory / "config.yaml").write_text(json.dumps(config))
            headers = []
            tables = []
            for repeat in (1, 2):
                log = directory / f"rep-{repeat}.log"
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(pycobench.__file__).resolve()),
                        "--conf",
                        str(directory / "config.yaml"),
                        "--jobs",
                        "2",
                        "--timeout",
                        "10",
                        "--output",
                        str(log),
                    ],
                    input="case.mata\n",
                    text=True,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                table_path = directory / f"rep-{repeat}.csv"
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(pycobench.__file__).resolve().with_name("pyco_proc.py")),
                        "--csv",
                        "--params-num",
                        "1",
                        "--output",
                        str(table_path),
                        str(log),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                with table_path.open(newline="") as table:
                    reader = csv.DictReader(table, delimiter=";")
                    headers.append(reader.fieldnames)
                    tables.append(next(reader))

            for engine, composition in expected_compositions.items():
                prefix = f"{engine}-"
                metrics = {
                    header[len(prefix) :]
                    for header in headers[0]
                    if pyco_results._engine_of(header, expected_compositions) == engine
                }
                optional = {engine.rsplit("-", 1)[-1]} & {"trim", "reduce"}
                self.assertEqual(
                    metrics - {"runtime", "maxrss"},
                    {"composition", "states", "result"} | optional,
                )
                for row in tables:
                    self.assertEqual(float(row[prefix + "composition"]), composition)
                    self.assertEqual(row[prefix + "states"], "72")
                    self.assertEqual(row[prefix + "result"], "false")
                    for metric in optional:
                        self.assertEqual(float(row[prefix + metric]), 1.5)
            self.assertEqual(headers[0], headers[1])

    def test_truncated_output_starts_with_a_complete_metric(self):
        repeated = "composition: 2\n"
        outputs = {}
        for engine, optional in (
            ("mata-base.left", []),
            ("mata-base.left-trim", ["trim: 1.5"]),
            ("mata-base.left-reduce", ["reduce: 1.5"]),
        ):
            fragment = "n: 1.25\n"
            closing = "\n".join([*optional, "states: 72", "result: false"]) + "\n"
            count, remainder = divmod(pycobench.OUTPUT_LIMIT - len(fragment) - len(closing), len(repeated))
            stdout = "discarded: 0\n" + "compositio" + fragment + repeated * count + " " * remainder + closing
            self.assertGreater(len(stdout), pycobench.OUTPUT_LIMIT)
            outputs[engine] = (stdout, 2 * count)
        self.measure(outputs)

    def test_output_below_limit_is_unchanged(self):
        stdout = "composition: 1.25\nstates: 72\nresult: false\n"
        self.measure({"mata-base.left": (stdout, 1.25)})


class TimeoutResultTest(unittest.TestCase):
    def test_timeout_preserves_process_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            program = directory / "sleeper.py"
            program.write_text(
                "import sys\n"
                "import time\n"
                "print('stdout before timeout')\n"
                "print('stderr before timeout', file=sys.stderr)\n"
                "time.sleep(60)\n"
            )
            config = directory / "config.yaml"
            config.write_text(json.dumps({"sleeper": {"cmd": f"{sys.executable} -u {program} $1"}}))
            log = directory / "timeout.log"

            subprocess.run(
                [
                    sys.executable,
                    str(Path(pycobench.__file__).resolve()),
                    "--conf",
                    str(config),
                    "--timeout",
                    "1",
                    "--output",
                    str(log),
                ],
                input="case.mata\n",
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )

            with log.open(newline="") as output:
                row = next(row for row in csv.reader(output, delimiter=";") if row[0] == "timeout")
            self.assertEqual(row[1:3], ["sleeper", "case.mata"])
            self.assertNotEqual(row[3], "")
            self.assertEqual(row[4], "stdout before timeout")
            self.assertEqual(row[5], "stderr before timeout")
            self.assertEqual(row[6], "1")


if __name__ == "__main__":
    unittest.main()
