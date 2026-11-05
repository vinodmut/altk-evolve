"""Run one fresh interactive arm; preserve the original experiment and guideline store."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/demo")
import demo
import services

demo.configure_process()
parser = argparse.ArgumentParser()
parser.add_argument("trial", type=int, choices=[1, 2, 3])
parser.add_argument("arm", choices=["baseline", "guided", "compare"])
args = parser.parse_args()
batch = Path(__file__).parent
source = services.load_state()
trial = batch / f"trial-{args.trial}"
services.STATE = trial / "state.json"
demo.RESULTS = trial
demo.RUNTIME = services.RUNTIME / "three-trials" / batch.name / f"trial-{args.trial}"
state = services.load_state()
state["checked_at"] = source["checked_at"]
if args.arm == "guided":
    if not state["sessions"].get("baseline", {}).get("captured"):
        raise RuntimeError("Complete this pair's fresh baseline first.")
    expected = json.loads((batch / "plan.json").read_text())["selected_candidates"]
    if source["promoted"] != expected:
        raise RuntimeError("The reviewed guidelines changed after the trial plan was frozen.")
    state["promoted"] = expected
    state["learned"] = True
services.save_state(state)
if args.arm == "compare":
    demo.compare()
else:
    demo.task(args.arm)
