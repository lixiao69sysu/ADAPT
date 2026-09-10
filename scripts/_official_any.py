"""Official metrics for any checkpoint (offline, no API calls)."""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent.vitabench_bootstrap as bootstrap
bootstrap.enable_vitabench_utf8()

from vita.data_model.simulation import AgentInfo, Info, Results, SimulationRun, UserInfo
from vita.environment.environment import EnvironmentInfo
from vita.data_model.tasks import Task
from vita.metrics.agent_metrics import compute_metrics


def summarize(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    raw_sims = [s for s in d["simulations"] if (s.get("reward_info") or {}).get("reward") is not None]
    unscored = len(d["simulations"]) - len(raw_sims)
    sims = [SimulationRun.model_validate(s) for s in raw_sims]
    tasks = [Task.model_construct(id=t, domain="personalization") for t in d["tasks"]]
    info = Info(
        git_commit="unknown",
        num_trials=int(d["info"].get("num_trials") or 1),
        max_steps=int(d["info"].get("max_steps") or 100),
        max_errors=10,
        user_info=UserInfo(implementation="personalization_user", llm=d["info"].get("llm_user")),
        agent_info=AgentInfo(implementation=str(d["info"].get("agent_kind")), llm=d["info"].get("llm_agent")),
        environment_info=EnvironmentInfo(implementation="vitabench", domain_name="personalization"),
        seed=d["info"].get("seed"),
    )
    m = compute_metrics(Results(info=info, tasks=tasks, simulations=sims))
    users = sorted({s.task_id for s in sims})
    return (
        f"users={len(users):2d} sims={len(sims):3d} unscored={unscored:2d} "
        f"subtask_avg={m.subtask_average_at_n} pass@k={m.subtask_pass_at_n} "
        f"pass^k={m.subtask_pass_hat_ks} units={m.subtask_num_units} "
        f"task_avg={m.avg_reward:.4f}"
    )


for name in sys.argv[1:]:
    path = Path(name)
    if not path.exists():
        print(f"{path.name:52} MISSING")
        continue
    try:
        print(f"{path.name:52} {summarize(path)}")
    except Exception as exc:  # noqa: BLE001
        print(f"{path.name:52} ERROR {type(exc).__name__}: {exc}")
