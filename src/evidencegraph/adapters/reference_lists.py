"""Dated reference lists yield network evidence, never authenticated actor identities."""

import ipaddress
from pathlib import Path

from evidencegraph.adapters.base import AdapterOutput, Builder
from evidencegraph.refs import json_citation
from evidencegraph.schema import Witness
from evidencegraph.strict_json import load_strict_json


class ReferenceLists:
    def describe(self, path: Path) -> dict:
        return {
            "kind": "reference_list",
            "coverage_claim": "Reference-list snapshot at acquisition; historical applicability requires dated evidence",
        }

    def ingest(self, path: Path, witness: Witness) -> AdapterOutput:
        data = load_strict_json(path, max_bytes=64 * 1024 * 1024)
        if not isinstance(data, dict):
            raise ValueError("reference list must be an object")
        builder = Builder(witness)
        prefixes = []
        for index, value in enumerate(data.get("values", [])):
            for prefix in value.get("properties", {}).get("addressPrefixes", []):
                prefixes.append((prefix, f"$.values[{index}]", value))
        for index, value in enumerate(data.get("prefixes", [])):
            prefix = value.get("ipv4Prefix") or value.get("ipv6Prefix")
            if prefix:
                prefixes.append((prefix, f"$.prefixes[{index}]", value))
        if data.get("startAddress") and data.get("endAddress"):
            for network in ipaddress.summarize_address_range(
                ipaddress.ip_address(data["startAddress"]), ipaddress.ip_address(data["endAddress"])
            ):
                prefixes.append((str(network), "$", data))
        if not prefixes:
            raise ValueError("no supported Azure, OpenAI prefix or RDAP address ranges")
        refs = {}
        for prefix, locator, value in prefixes:
            network = str(ipaddress.ip_network(prefix, strict=False))
            if locator not in refs:
                refs[locator] = json_citation(witness.witness_id, locator, value)
            ref = refs[locator]
            builder.entity(
                "identity",
                "network_prefix",
                network,
                ref,
                {
                    "network": network,
                    "authenticated": False,
                    "reference_date": data.get("creationTime") or data.get("changeNumber"),
                    "origin": witness.origin,
                },
            )
            yield from builder.drain()
