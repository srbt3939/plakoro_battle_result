"""Verify manually created AWS DynamoDB 13-table setup (手順7 §7-2・§11-1).

Also serves as the canonical schema specification shared by migration and tests.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

EXPECTED_TABLES: dict[str, dict[str, Any]] = {
    "Players": {
        "pk": ("playerId", "N"),
        "sk": None,
        "gsis": {},
        "ttl": None,
    },
    "DiscordAccounts": {
        "pk": ("discordUserId", "S"),
        "sk": None,
        "gsis": {},
        "ttl": None,
    },
    "Pokemon": {
        "pk": ("pokemonId", "N"),
        "sk": None,
        "gsis": {},
        "ttl": None,
    },
    "Seasons": {
        "pk": ("seasonId", "N"),
        "sk": None,
        "gsis": {},
        "ttl": None,
    },
    "BattleMessages": {
        "pk": ("messageId", "N"),
        "sk": None,
        "gsis": {
            "season-index": {"pk": ("seasonId", "N"), "sk": ("messageId", "N")},
        },
        "ttl": None,
    },
    "DiscordMessages": {
        "pk": ("discordMessageId", "S"),
        "sk": None,
        "gsis": {},
        "ttl": None,
    },
    "Battles": {
        "pk": ("battleId", "N"),
        "sk": None,
        "gsis": {
            "season-index": {"pk": ("seasonId", "N"), "sk": ("battleId", "N")},
        },
        "ttl": None,
    },
    "BattleMatches": {
        "pk": ("matchId", "N"),
        "sk": None,
        "gsis": {
            "season-index": {"pk": ("seasonId", "N"), "sk": ("matchId", "N")},
        },
        "ttl": None,
    },
    "Counters": {
        "pk": ("counterName", "S"),
        "sk": None,
        "gsis": {},
        "ttl": None,
    },
    "Sessions": {
        "pk": ("sessionId", "S"),
        "sk": None,
        "gsis": {},
        "ttl": "expiresAt",
    },
    "ResetRequests": {
        "pk": ("requestId", "N"),
        "sk": None,
        "gsis": {
            "status-index": {"pk": ("status", "S"), "sk": ("requestedAt", "S")},
            "player-index": {"pk": ("playerId", "N"), "sk": ("requestedAt", "S")},
        },
        "ttl": None,
    },
    "ResetHistory": {
        "pk": ("playerId", "N"),
        "sk": ("resetAt", "S"),
        "gsis": {},
        "ttl": None,
    },
    "BattleAuditLogs": {
        "pk": ("targetKey", "S"),
        "sk": ("loggedAt", "S"),
        "gsis": {},
        "ttl": None,
    },
}


def verify_tables(
    dynamodb_client: Any,
    table_prefix: str,
    *,
    require_pitr: bool = True,
    require_deletion_protection: bool = True,
) -> list[str]:
    errors: list[str] = []
    for logical_name, spec in EXPECTED_TABLES.items():
        full_name = f"{table_prefix}{logical_name}"
        try:
            desc = dynamodb_client.describe_table(TableName=full_name)["Table"]
        except Exception as exc:
            errors.append(f"{full_name}: table missing or unreadable ({exc})")
            continue

        if desc.get("TableStatus") != "ACTIVE":
            errors.append(f"{full_name}: TableStatus is {desc.get('TableStatus')!r}, expected 'ACTIVE'")

        attr_types = {
            a["AttributeName"]: a["AttributeType"]
            for a in desc.get("AttributeDefinitions", [])
        }
        key_schema = {k["KeyType"]: k["AttributeName"] for k in desc.get("KeySchema", [])}

        pk_name, pk_type = spec["pk"]
        if key_schema.get("HASH") != pk_name or attr_types.get(pk_name) != pk_type:
            errors.append(f"{full_name}: PK mismatch (expected {pk_name}:{pk_type})")

        if spec["sk"] is None:
            if "RANGE" in key_schema:
                errors.append(f"{full_name}: unexpected SK {key_schema['RANGE']}")
        else:
            sk_name, sk_type = spec["sk"]
            if key_schema.get("RANGE") != sk_name or attr_types.get(sk_name) != sk_type:
                errors.append(f"{full_name}: SK mismatch (expected {sk_name}:{sk_type})")

        actual_gsis = {g["IndexName"]: g for g in desc.get("GlobalSecondaryIndexes", [])}
        for gsi_name, gsi_spec in spec["gsis"].items():
            if gsi_name not in actual_gsis:
                errors.append(f"{full_name}: missing GSI {gsi_name!r}")
                continue
            g = actual_gsis[gsi_name]
            g_keys = {k["KeyType"]: k["AttributeName"] for k in g.get("KeySchema", [])}
            g_pk_name, g_pk_type = gsi_spec["pk"]
            g_sk_name, g_sk_type = gsi_spec["sk"]
            if g_keys.get("HASH") != g_pk_name or attr_types.get(g_pk_name) != g_pk_type:
                errors.append(f"{full_name}.{gsi_name}: GSI PK mismatch")
            if g_keys.get("RANGE") != g_sk_name or attr_types.get(g_sk_name) != g_sk_type:
                errors.append(f"{full_name}.{gsi_name}: GSI SK mismatch")
            if g.get("Projection", {}).get("ProjectionType") != "ALL":
                errors.append(f"{full_name}.{gsi_name}: ProjectionType must be ALL")

        if require_deletion_protection and not desc.get("DeletionProtectionEnabled", False):
            errors.append(f"{full_name}: DeletionProtectionEnabled is False")

        if spec["ttl"] is not None and hasattr(dynamodb_client, "describe_time_to_live"):
            ttl_desc = dynamodb_client.describe_time_to_live(TableName=full_name).get(
                "TimeToLiveDescription", {}
            )
            if (
                ttl_desc.get("TimeToLiveStatus") not in ("ENABLED", "ENABLING")
                or ttl_desc.get("AttributeName") != spec["ttl"]
            ):
                errors.append(f"{full_name}: TTL must be enabled on {spec['ttl']!r}")

        if require_pitr and hasattr(dynamodb_client, "describe_continuous_backups"):
            pitr_desc = (
                dynamodb_client.describe_continuous_backups(TableName=full_name)
                .get("ContinuousBackupsDescription", {})
                .get("PointInTimeRecoveryDescription", {})
            )
            if pitr_desc.get("PointInTimeRecoveryStatus") != "ENABLED":
                errors.append(f"{full_name}: PITR is not ENABLED")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify DynamoDB 13-table setup.")
    parser.add_argument(
        "--prefix",
        default=os.environ.get("PLAKORO_DYNAMODB_TABLE_PREFIX", "plakoro_dev_"),
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-1"))
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--skip-pitr-check", action="store_true")
    args = parser.parse_args(argv)

    import boto3

    session = (
        boto3.Session(profile_name=args.profile, region_name=args.region)
        if args.profile
        else boto3.Session(region_name=args.region)
    )
    client = session.client("dynamodb")
    errors = verify_tables(
        client,
        args.prefix,
        require_pitr=not args.skip_pitr_check,
        require_deletion_protection=not args.skip_pitr_check,
    )
    report = {"prefix": args.prefix, "ok": len(errors) == 0, "errors": errors}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
