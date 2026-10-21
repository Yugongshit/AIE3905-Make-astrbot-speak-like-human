"""Stable account identities; QQ event self_id can change with mention metadata."""

import json
import re

QQ_OFFICIAL = {"qq_official", "qqofficial"}


def qq_account(platform_id: str, app_id=None) -> str:
    if app_id is not None and str(app_id).strip():
        return "qq-app:" + str(app_id).strip()
    # AstrBot's default QQ instance ID includes its application ID.
    match = re.fullmatch(r"default_(\d+)", platform_id)
    if match:
        return "qq-app:" + match[1]
    return "qq-platform:" + platform_id


def scope_key(platform_id, account_id, kind, target_id) -> str:
    return json.dumps(
        [str(platform_id), str(account_id), str(kind), str(target_id)], ensure_ascii=False
    )


def parse_scope(scope: str) -> tuple[str, str, str, str] | None:
    try:
        parts = json.loads(scope)
        if (
            isinstance(parts, list)
            and len(parts) == 4
            and parts[2] in ("group", "private")
            and all(type(part) in (str, int) for part in parts)
        ):
            return tuple(str(part) for part in parts)
    except (ValueError, TypeError):
        pass
    return None
