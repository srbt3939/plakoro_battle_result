"""Faithful in-memory low-level DynamoDB simulator for contract & integration tests.

Supports:
  - 13 tables with HASH/RANGE keys, GSIs, TTL, PITR, DeletionProtection metadata
  - get_item, put_item, update_item (SET, REMOVE, ADD, ReturnValues='UPDATED_NEW')
  - query (with GSI, ScanIndexForward, pagination PageSize, and simulated GSI lag)
  - scan (with pagination PageSize)
  - batch_get_item
  - transact_write_items (atomic rollback on ConditionExpression failure + ClientRequestToken idempotency)
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.dynamodb_serde import deserialize_item, from_attr, to_attr
from scripts.verify_aws_setup import EXPECTED_TABLES


class ConditionalCheckFailedException(Exception):
    def __init__(self, message: str = "The conditional request failed"):
        super().__init__(message)
        self.response = {"Error": {"Code": "ConditionalCheckFailedException", "Message": message}}


class TransactionCanceledException(Exception):
    def __init__(self, reasons: list[dict[str, str]]):
        super().__init__("Transaction cancelled")
        self.response = {
            "Error": {"Code": "TransactionCanceledException", "Message": "Transaction cancelled"},
            "CancellationReasons": reasons,
        }


class InMemoryDynamoDBClient:
    def __init__(self, prefix: str = "test_", *, page_size: int = 500):
        self.prefix = prefix
        self.page_size = page_size
        self.tables: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[Any, dict[str, Any]]] = {}
        self._tokens: dict[str, bool] = {}
        self.hidden_from_gsi: set[tuple[str, Any]] = set()
        self.gsi_lag_countdown: dict[str, int] = {}
        self._init_expected_tables(prefix)

    def _init_expected_tables(self, prefix: str) -> None:
        for logical, spec in EXPECTED_TABLES.items():
            full_name = f"{prefix}{logical}"
            pk_name, pk_type = spec["pk"]
            attrs = {pk_name: pk_type}
            key_schema = [{"AttributeName": pk_name, "KeyType": "HASH"}]
            if spec["sk"]:
                sk_name, sk_type = spec["sk"]
                attrs[sk_name] = sk_type
                key_schema.append({"AttributeName": sk_name, "KeyType": "RANGE"})

            gsis = []
            for gname, gspec in spec["gsis"].items():
                gpk, gpkt = gspec["pk"]
                gsk, gskt = gspec["sk"]
                attrs[gpk] = gpkt
                attrs[gsk] = gskt
                gsis.append(
                    {
                        "IndexName": gname,
                        "KeySchema": [
                            {"AttributeName": gpk, "KeyType": "HASH"},
                            {"AttributeName": gsk, "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                        "IndexStatus": "ACTIVE",
                    }
                )

            self.tables[full_name] = {
                "TableName": full_name,
                "TableStatus": "ACTIVE",
                "AttributeDefinitions": [
                    {"AttributeName": k, "AttributeType": v} for k, v in attrs.items()
                ],
                "KeySchema": key_schema,
                "GlobalSecondaryIndexes": gsis,
                "DeletionProtectionEnabled": True,
                "_spec": spec,
            }
            self.items[full_name] = {}

    def describe_table(self, *, TableName: str) -> dict[str, Any]:
        if TableName not in self.tables:
            raise KeyError(f"ResourceNotFoundException: {TableName}")
        t = {k: v for k, v in self.tables[TableName].items() if not k.startswith("_")}
        return {"Table": copy.deepcopy(t)}

    def describe_time_to_live(self, *, TableName: str) -> dict[str, Any]:
        spec = self.tables[TableName]["_spec"]
        if spec["ttl"]:
            return {
                "TimeToLiveDescription": {
                    "TimeToLiveStatus": "ENABLED",
                    "AttributeName": spec["ttl"],
                }
            }
        return {"TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}}

    def describe_continuous_backups(self, *, TableName: str) -> dict[str, Any]:
        _ = self.tables[TableName]
        return {
            "ContinuousBackupsDescription": {
                "ContinuousBackupsStatus": "ENABLED",
                "PointInTimeRecoveryDescription": {"PointInTimeRecoveryStatus": "ENABLED"},
            }
        }

    def _extract_pk(self, table_name: str, av_map: dict[str, Any]) -> Any:
        spec = self.tables[table_name]["_spec"]
        pk_name = spec["pk"][0]
        pk_val = from_attr(av_map[pk_name])
        if spec["sk"]:
            sk_name = spec["sk"][0]
            return (pk_val, from_attr(av_map[sk_name]))
        return pk_val

    @staticmethod
    def _resolve_name(token: str, names: dict[str, str] | None) -> str:
        token = token.strip()
        if names and token in names:
            return names[token]
        return token

    def _eval_single_cond(
        self,
        expr: str,
        existing_py: dict[str, Any] | None,
        names: dict[str, str] | None,
        values: dict[str, Any] | None,
    ) -> bool:
        expr = expr.strip()
        if expr.startswith("(") and expr.endswith(")"):
            inner = expr[1:-1].strip()
            if " OR " in inner:
                parts = inner.split(" OR ")
                return any(self._eval_single_cond(p, existing_py, names, values) for p in parts)

        m_not = re.fullmatch(r"attribute_not_exists\(\s*([^)]+)\s*\)", expr)
        if m_not:
            attr = self._resolve_name(m_not.group(1), names)
            return existing_py is None or attr not in existing_py

        m_ex = re.fullmatch(r"attribute_exists\(\s*([^)]+)\s*\)", expr)
        if m_ex:
            attr = self._resolve_name(m_ex.group(1), names)
            return existing_py is not None and attr in existing_py

        m_cmp = re.fullmatch(r"(\S+)\s*(=|<>)\s*(\S+)", expr)
        if m_cmp:
            lhs = self._resolve_name(m_cmp.group(1), names)
            op = m_cmp.group(2)
            rhs_tok = m_cmp.group(3).strip()
            rhs_val = from_attr(values[rhs_tok])
            has_lhs = existing_py is not None and lhs in existing_py
            lhs_val = existing_py.get(lhs) if existing_py else None
            if op == "=":
                return has_lhs and lhs_val == rhs_val
            return (not has_lhs) or lhs_val != rhs_val

        raise ValueError(f"Unsupported ConditionExpression clause: {expr!r}")

    def _eval_condition(
        self,
        cond_expr: str | None,
        existing_av: dict[str, Any] | None,
        names: dict[str, str] | None = None,
        values: dict[str, Any] | None = None,
    ) -> bool:
        if not cond_expr:
            return True
        existing_py = deserialize_item(existing_av) if existing_av is not None else None
        # Split top-level AND outside parentheses
        clauses: list[str] = []
        depth = 0
        buf: list[str] = []
        tokens = re.split(r"(\s+AND\s+)", cond_expr)
        for tok in tokens:
            if tok.strip() == "AND" and depth == 0:
                clauses.append("".join(buf))
                buf = []
            else:
                depth += tok.count("(") - tok.count(")")
                buf.append(tok)
        if buf:
            clauses.append("".join(buf))

        if len(clauses) == 1 and " OR " in clauses[0] and not clauses[0].strip().startswith("("):
            or_parts = clauses[0].split(" OR ")
            return any(self._eval_single_cond(p, existing_py, names, values) for p in or_parts)

        return all(self._eval_single_cond(c, existing_py, names, values) for c in clauses)

    def get_item(
        self,
        *,
        TableName: str,
        Key: dict[str, Any],
        ConsistentRead: bool = False,
    ) -> dict[str, Any]:
        _ = ConsistentRead
        pk = self._extract_pk(TableName, Key)
        found = self.items[TableName].get(pk)
        return {"Item": copy.deepcopy(found)} if found is not None else {}

    def batch_get_item(self, *, RequestItems: dict[str, Any]) -> dict[str, Any]:
        responses: dict[str, list[dict[str, Any]]] = {}
        for tname, req in RequestItems.items():
            out = []
            for k in req.get("Keys", []):
                pk = self._extract_pk(tname, k)
                found = self.items[tname].get(pk)
                if found is not None:
                    out.append(copy.deepcopy(found))
            responses[tname] = out
        return {"Responses": responses, "UnprocessedKeys": {}}

    def put_item(
        self,
        *,
        TableName: str,
        Item: dict[str, Any],
        ConditionExpression: str | None = None,
        ExpressionAttributeNames: dict[str, str] | None = None,
        ExpressionAttributeValues: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pk = self._extract_pk(TableName, Item)
        existing = self.items[TableName].get(pk)
        if not self._eval_condition(
            ConditionExpression, existing, ExpressionAttributeNames, ExpressionAttributeValues
        ):
            raise ConditionalCheckFailedException()
        self.items[TableName][pk] = copy.deepcopy(Item)
        return {}

    def _apply_update_in_place(
        self,
        TableName: str,
        Key: dict[str, Any],
        UpdateExpression: str,
        names: dict[str, str] | None,
        values: dict[str, Any] | None,
    ) -> dict[str, Any]:
        pk = self._extract_pk(TableName, Key)
        current = copy.deepcopy(self.items[TableName].get(pk) or Key)
        updated_attrs: dict[str, Any] = {}

        # Parse clauses (ADD, SET, REMOVE) in order
        pattern = re.compile(r"\b(SET|REMOVE|ADD)\b")
        matches = list(pattern.finditer(UpdateExpression))
        for idx, m in enumerate(matches):
            verb = m.group(1)
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(UpdateExpression)
            body = UpdateExpression[start:end].strip()

            if verb == "SET":
                for assign in body.split(","):
                    lhs_raw, rhs_raw = assign.split("=", 1)
                    attr = self._resolve_name(lhs_raw, names)
                    val_av = copy.deepcopy(values[rhs_raw.strip()])
                    current[attr] = val_av
                    updated_attrs[attr] = val_av
            elif verb == "REMOVE":
                for rem in body.split(","):
                    attr = self._resolve_name(rem, names)
                    current.pop(attr, None)
            elif verb == "ADD":
                for add_part in body.split(","):
                    parts = add_part.strip().split()
                    attr = self._resolve_name(parts[0], names)
                    inc = int(from_attr(values[parts[1].strip()]))
                    prev = int(from_attr(current[attr])) if attr in current else 0
                    new_av = to_attr(prev + inc)
                    current[attr] = new_av
                    updated_attrs[attr] = new_av

        self.items[TableName][pk] = current
        return updated_attrs

    def update_item(
        self,
        *,
        TableName: str,
        Key: dict[str, Any],
        UpdateExpression: str,
        ConditionExpression: str | None = None,
        ExpressionAttributeNames: dict[str, str] | None = None,
        ExpressionAttributeValues: dict[str, Any] | None = None,
        ReturnValues: str = "NONE",
    ) -> dict[str, Any]:
        pk = self._extract_pk(TableName, Key)
        existing = self.items[TableName].get(pk)
        if not self._eval_condition(
            ConditionExpression, existing, ExpressionAttributeNames, ExpressionAttributeValues
        ):
            raise ConditionalCheckFailedException()
        updated = self._apply_update_in_place(
            TableName, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues
        )
        if ReturnValues == "UPDATED_NEW":
            return {"Attributes": updated}
        return {}

    def transact_write_items(
        self,
        *,
        TransactItems: list[dict[str, Any]],
        ClientRequestToken: str | None = None,
    ) -> dict[str, Any]:
        if ClientRequestToken and ClientRequestToken in self._tokens:
            return {}

        # Phase 1: check all conditions against snapshot
        reasons: list[dict[str, str]] = []
        any_failed = False
        for op in TransactItems:
            if "Put" in op:
                p = op["Put"]
                pk = self._extract_pk(p["TableName"], p["Item"])
                existing = self.items[p["TableName"]].get(pk)
                ok = self._eval_condition(
                    p.get("ConditionExpression"),
                    existing,
                    p.get("ExpressionAttributeNames"),
                    p.get("ExpressionAttributeValues"),
                )
            elif "Update" in op:
                u = op["Update"]
                pk = self._extract_pk(u["TableName"], u["Key"])
                existing = self.items[u["TableName"]].get(pk)
                ok = self._eval_condition(
                    u.get("ConditionExpression"),
                    existing,
                    u.get("ExpressionAttributeNames"),
                    u.get("ExpressionAttributeValues"),
                )
            else:
                raise ValueError(f"Unsupported TransactItem: {op!r}")

            if ok:
                reasons.append({"Code": "None"})
            else:
                reasons.append({"Code": "ConditionalCheckFailed"})
                any_failed = True

        if any_failed:
            raise TransactionCanceledException(reasons)

        # Phase 2: apply all mutations atomically
        for op in TransactItems:
            if "Put" in op:
                p = op["Put"]
                pk = self._extract_pk(p["TableName"], p["Item"])
                self.items[p["TableName"]][pk] = copy.deepcopy(p["Item"])
            elif "Update" in op:
                u = op["Update"]
                self._apply_update_in_place(
                    u["TableName"],
                    u["Key"],
                    u["UpdateExpression"],
                    u.get("ExpressionAttributeNames"),
                    u.get("ExpressionAttributeValues"),
                )

        if ClientRequestToken:
            self._tokens[ClientRequestToken] = True
        return {}

    def scan(
        self,
        *,
        TableName: str,
        ExclusiveStartKey: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        all_entries = list(self.items[TableName].items())
        start_idx = 0
        if ExclusiveStartKey is not None:
            start_pk = self._extract_pk(TableName, ExclusiveStartKey)
            for idx, (pk, _) in enumerate(all_entries):
                if pk == start_pk:
                    start_idx = idx + 1
                    break
        page = all_entries[start_idx : start_idx + self.page_size]
        resp: dict[str, Any] = {"Items": [copy.deepcopy(av) for _, av in page]}
        if start_idx + self.page_size < len(all_entries) and page:
            last_pk, last_av = page[-1]
            _ = last_pk
            spec = self.tables[TableName]["_spec"]
            lek = {spec["pk"][0]: copy.deepcopy(last_av[spec["pk"][0]])}
            if spec["sk"]:
                lek[spec["sk"][0]] = copy.deepcopy(last_av[spec["sk"][0]])
            resp["LastEvaluatedKey"] = lek
        return resp

    def query(
        self,
        *,
        TableName: str,
        IndexName: str | None = None,
        KeyConditionExpression: str,
        ExpressionAttributeValues: dict[str, Any],
        ScanIndexForward: bool = True,
        ExclusiveStartKey: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        m = re.fullmatch(r"(\w+)\s*=\s*(:\w+)", KeyConditionExpression.strip())
        if not m:
            raise ValueError(f"Unsupported KeyConditionExpression: {KeyConditionExpression!r}")
        pk_attr = m.group(1)
        target_val = from_attr(ExpressionAttributeValues[m.group(2)])

        spec = self.tables[TableName]["_spec"]
        if IndexName:
            gsi_spec = spec["gsis"][IndexName]
            sk_attr = gsi_spec["sk"][0]
        else:
            sk_attr = spec["sk"][0] if spec["sk"] else spec["pk"][0]

        lag_active = False
        if IndexName and self.gsi_lag_countdown.get(TableName, 0) > 0:
            self.gsi_lag_countdown[TableName] -= 1
            lag_active = True

        matched: list[tuple[Any, Any, dict[str, Any]]] = []
        for base_pk, av in self.items[TableName].items():
            if IndexName and (TableName, base_pk) in self.hidden_from_gsi and lag_active:
                continue
            py = deserialize_item(av) or {}
            if pk_attr in py and sk_attr in py and py[pk_attr] == target_val:
                matched.append((py[sk_attr], base_pk, av))

        matched.sort(key=lambda t: t[0], reverse=not ScanIndexForward)

        start_idx = 0
        if ExclusiveStartKey is not None:
            start_base_pk = self._extract_pk(TableName, ExclusiveStartKey)
            for idx, (_, base_pk, _) in enumerate(matched):
                if base_pk == start_base_pk:
                    start_idx = idx + 1
                    break

        page = matched[start_idx : start_idx + self.page_size]
        resp: dict[str, Any] = {"Items": [copy.deepcopy(av) for _, _, av in page]}
        if start_idx + self.page_size < len(matched) and page:
            _, _, last_av = page[-1]
            lek = {
                spec["pk"][0]: copy.deepcopy(last_av[spec["pk"][0]]),
                pk_attr: copy.deepcopy(last_av[pk_attr]),
                sk_attr: copy.deepcopy(last_av[sk_attr]),
            }
            if spec["sk"]:
                lek[spec["sk"][0]] = copy.deepcopy(last_av[spec["sk"][0]])
            resp["LastEvaluatedKey"] = lek
        return resp
