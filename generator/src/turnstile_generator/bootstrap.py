"""Create the registry through the admin endpoints. Safe to run any number of times."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import partial

from turnstile_generator import registry
from turnstile_generator.api_models import GateUpsert
from turnstile_generator.client import ParkingClient
from turnstile_generator.sender import call_with_retry

log = logging.getLogger("turnstile_generator.bootstrap")
STUDENT_CHUNK = 1000


@dataclass
class BootstrapReport:
    lots_created: int = 0
    gates_created: int = 0
    students_created: int = 0
    students_unchanged: int = 0


def bootstrap(
    client: ParkingClient,
    *,
    seed: int,
    students: int,
    today: date,
    sleeper: Callable[[float], None],
) -> BootstrapReport:
    report = BootstrapReport()
    existing = call_with_retry(client.get_lots, sleeper=sleeper)
    lots = registry.desired_lots(registry.anexo_change_date(existing, today))
    for lot in lots:
        put_lot = partial(client.put_lot, lot.lot_id, lot.name, lot.capacity_periods())
        _, created = call_with_retry(put_lot, sleeper=sleeper)
        report.lots_created += created
        for direction, gate_id, label in (
            ("entry", lot.entry_gate, "Entrada"),
            ("exit", lot.exit_gate, "Saída"),
        ):
            body = GateUpsert.model_validate(
                {"lot_id": lot.lot_id, "direction": direction, "name": f"{lot.name} - {label}"}
            )
            _, created = call_with_retry(partial(client.put_gate, gate_id, body), sleeper=sleeper)
            report.gates_created += created
    refs = registry.student_refs(seed, students)
    for i in range(0, len(refs), STUDENT_CHUNK):
        result = call_with_retry(
            partial(client.upsert_students, refs[i : i + STUDENT_CHUNK]), sleeper=sleeper
        )
        report.students_created += result.created
        report.students_unchanged += result.unchanged
    log.info("bootstrap done", extra=vars(report))
    return report
