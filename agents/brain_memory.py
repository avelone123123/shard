import os
import json
import time
BRAIN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shard_brain.json")
DEFAULT_BRAIN = {
    "project": "shard",
    "version": "1.0.0",
    "cycles_completed": 0,
    "last_cycle_timestamp": 0,
    "agents_active_count": 50,
    "learned_heuristics": [
        "AVX2 FMA dot products run at 18+ GFLOPS with 8-fold unrolling across ymm0-ymm7.",
        "Win32 MapViewOfFile eliminates RAM duplication for model weights.",
        "Zero empty lines and zero comments policy enforces maximum code density.",
        "SVG vector graphics replace raster emojis for enterprise dashboards."
    ],
    "evolution_milestones": [
        {"milestone": "Tri-Language Architecture Initialized", "status": "completed", "timestamp": int(time.time())},
        {"milestone": "CLI Entry Points and PyPI Packaging Ready", "status": "completed", "timestamp": int(time.time())},
        {"milestone": "50-Agent Autonomous Dev Studio Active", "status": "in_progress", "timestamp": int(time.time())}
    ],
    "benchmark_records": [
        {"kernel": "MASM x64 AVX2 SIMD", "iterations": 100000, "latency_us": 45179, "gflops": 18.13}
    ],
    "agent_contributions": {}
}
def load_brain():
    if not os.path.exists(BRAIN_FILE):
        save_brain(DEFAULT_BRAIN)
        return DEFAULT_BRAIN
    try:
        with open(BRAIN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_BRAIN
def save_brain(data):
    try:
        with open(BRAIN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
def record_cycle(agent_id, action, result_summary, heuristic=None, benchmark=None):
    brain = load_brain()
    brain["cycles_completed"] = brain.get("cycles_completed", 0) + 1
    brain["last_cycle_timestamp"] = int(time.time())
    contribs = brain.setdefault("agent_contributions", {})
    agent_data = contribs.setdefault(agent_id, {"tasks_completed": 0, "actions": []})
    agent_data["tasks_completed"] += 1
    agent_data["actions"].append({
        "action": action,
        "summary": result_summary,
        "timestamp": int(time.time())
    })
    if heuristic and heuristic not in brain.setdefault("learned_heuristics", []):
        brain["learned_heuristics"].append(heuristic)
    if benchmark:
        brain.setdefault("benchmark_records", []).append(benchmark)
    save_brain(brain)
    return brain