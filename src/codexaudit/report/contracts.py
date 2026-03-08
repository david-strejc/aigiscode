"""Compatibility wrapper for built-in contract inventory helpers."""

from codexaudit.contracts import (
    ContractLookup,
    build_contract_inventory,
    build_contract_lookup,
    merge_contract_lookup,
)

__all__ = [
    "ContractLookup",
    "build_contract_inventory",
    "build_contract_lookup",
    "merge_contract_lookup",
]
