"""Seeded synthetic inputs and slot-derived gold; does not import the production merger."""

import random
from typing import Any


def base(
    name: str = "Atlas",
    payment: int = 30,
    notice: int = 30,
    renewal: bool = True,
    liability: str = "limited to annual fees",
) -> str:
    return (
        f"Master Services Agreement: {name}\n"
        f"Parties: {name} Customer Example LLC | {name} Supplier Example LLC\n"
        "Effective date: 2025-01-01\n\n"
        "1. Renewal\n"
        f"This Agreement {'does' if renewal else 'does not'} automatically renew.\n"
        "The renewal term is 12 months.\n\n"
        "2. Payment\n"
        f"Invoices are payable within {payment} days.\n\n"
        "3. Termination\n"
        f"Either party may terminate for convenience with {notice} days notice.\n\n"
        "4. Governing Law\n"
        "This Agreement is governed by the laws of California.\n\n"
        "5. Liability\n"
        f"Aggregate liability is {liability}.\n\n"
        "6. Assignment\n"
        "Assignment requires prior written consent.\n"
    )


def amendment(
    name: str = "Atlas",
    number: int = 1,
    when: str = "2025-06-01",
    operation: str = 'In Section 2, replace "30 days" with "60 days".',
) -> str:
    return (
        f"Amendment {number}: {name}\n"
        f"Parties: {name} Customer Example LLC | {name} Supplier Example LLC\n"
        f"Effective date: {when}\nAmends agreement effective: 2025-01-01\n\n{operation}\n"
    )


def generate(seed: int = 17, families: int = 30) -> tuple[dict[str, str], dict[str, Any]]:
    rng = random.Random(seed)
    documents = {}
    gold = {}
    for i in range(families):
        name = f"Synthetic {seed} {i}"
        payment, changed, notice = (
            rng.choice([15, 30, 45]),
            rng.choice([60, 75, 90]),
            rng.choice([30, 90]),
        )
        renewal = rng.choice([True, False])
        documents[f"base-{i}"] = base(name, payment, notice, renewal)
        documents[f"amendment-{i}"] = amendment(
            name, operation=(f'In Section 2, replace "{payment} days" with "{changed} days".')
        )
        gold[name] = {
            "before_payment": payment,
            "after_payment": changed,
            "notice_days": notice,
            "auto_renewal": renewal,
        }
    return documents, gold
