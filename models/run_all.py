"""Run the whole modelling stage in order, then build the HTML report.

    .venv/bin/python models/run_all.py
"""
import runpy
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
STEPS = ["m1_location_ilp.py", "m2_capacity_lp.py", "m3_simulation.py",
         "m4_goal_programming.py", "m5_decision_analysis.py",
         str(HERE.parent / "report" / "build_report.py")]

if __name__ == "__main__":
    for s in STEPS:
        t = time.time()
        print(f"\n=== {Path(s).name}")
        runpy.run_path(str(HERE / s) if not s.startswith("/") else s, run_name="__main__")
        print(f"=== {Path(s).name} done in {time.time() - t:.0f}s")
