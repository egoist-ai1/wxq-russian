"""Обратимый числовой display-патч для проверенных оригиналов игры 1.6.0.1."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / "source/game-formatting/ru-display-v2.lua"
METADATA = PROJECT / "source/game-formatting/numeric-display-v2.json"
PACKAGE = "10"
PAYLOAD_MARKER = b"-- ACE_REVIEWED_RU_WORDWRAP_v2"
PATCH_BEGIN = "-- ACE_REVIEWED_RU_NUMERIC_DISPLAY_v2 BEGIN"
PATCH_END = "-- ACE_REVIEWED_RU_NUMERIC_DISPLAY_v2 END"
LOCALIZE_ID = "06831ac11637ca73"
SHARE_ID = "b7039a45af1c9df7"
TARGETS = {
    LOCALIZE_ID: {
        "module": "Localize",
        "original_sha256": "d99c2caa0fc56dd508752e3005b1c3bc73aa88d2ba91cd22e0264b3225f43de8",
        "hooks": ("GetString",),
    },
    SHARE_ID: {
        "module": "UITalkOSHProgitShareContentClass",
        "original_sha256": "870361dbabf57789e9c12463560824ab2ce92b0d9a38bfdddd46308fed4f7825",
        "hooks": ("FormatDamage",),
    },
}
SCALED_KEYS = {
    "InMillionsOfUnit": {"scale": 10000, "output_kind": "number"},
    "InHundredMillionOfUnit": {"scale": 100000000, "output_kind": "number"},
    "Operation_Rank_BigNumAbbreviation_TenThousand": {"scale": 10000, "output_kind": "number"},
    "GamePreviewCntFormat": {"scale": 10000, "output_kind": "number"},
    "OperationNewbinMarkerOfW": {"scale": 10000, "output_kind": "number"},
    "OperationGlobalRankItemDesc1": {"scale": 10000, "output_kind": "players"},
    "SeasonJourney_KingNeed": {"scale": 10000, "output_kind": "server_kings"},
}
DISPLAY_POLICY = {
    "compact_fraction_digits_max": 2,
    "trim_trailing_fraction_zeros": True,
    "overflow_fraction": "exact-grouped-base-integer",
    "group_separator": " ",
    "rounding_added": False,
    "native_share_quantum": 100,
    "unknown_key_fast_path": True,
}


class NumericDisplayError(ValueError):
    """Оригинал, помощник или проверенная схема изменились."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_metadata() -> dict:
    data = json.loads(METADATA.read_text("utf-8"))
    resources = data.get("resources", [])
    rows = {row["id_hex"]: row for row in resources}
    if (data.get("schema") != "wxq-numeric-display/v2" or data.get("package") != PACKAGE
            or len(resources) != len(TARGETS) or set(rows) != set(TARGETS)):
        raise NumericDisplayError("Метаданные числовых подписей не совпали с набором целей")
    if data.get("helper_sha256") != sha(HELPER.read_bytes()):
        raise NumericDisplayError("Помощник числовых подписей изменился относительно метаданных")
    if data.get("display_policy") != DISPLAY_POLICY:
        raise NumericDisplayError("Правило точного числового отображения изменилось")
    for rid, target in TARGETS.items():
        if any(rows[rid].get(field) != target[field] for field in ("module", "original_sha256")):
            raise NumericDisplayError(f"{rid}: метаданные изменили SHA оригинала или модуль")
        if rows[rid].get("hooks") != list(target["hooks"]):
            raise NumericDisplayError(f"{rid}: метаданные изменили проверенную точку форматирования")
    keys = data.get("scaled_keys", [])
    key_rows = {row["key"]: row for row in keys}
    if len(keys) != len(SCALED_KEYS) or set(key_rows) != set(SCALED_KEYS):
        raise NumericDisplayError("Набор подтверждённых масштабируемых ключей изменился")
    for key, target in SCALED_KEYS.items():
        if any(key_rows[key].get(field) != value for field, value in target.items()):
            raise NumericDisplayError(f"{key}: масштаб или вид подписи изменился")
    return data


def _block(rid: str, ending: str) -> str:
    if rid == LOCALIZE_ID:
        content = HELPER.read_text("utf-8").replace("\r\n", "\n").replace("\n", ending)
    elif rid == SHARE_ID:
        content = '''do
    local original = UITalkOSHProgitShareContentClass.FormatDamage
    UITalkOSHProgitShareContentClass.FormatDamage = function(self, ...)
        if select("#", ...) == 1 then
            local api = rawget(_G, "_AceRuNumericDisplayV2")
            if type(api) == "table" and api.version == 2 and type(api.tryShare) == "function" then
                local accepted, text = pcall(api.tryShare, ...)
                if accepted and type(text) == "string" then return text end
            end
        end
        return original(self, ...)
    end
end
'''.replace("\n", ending)
    else:
        raise NumericDisplayError("Ресурс не включён в числовой display-патч")
    return ending + PATCH_BEGIN + ending + content + PATCH_END + ending


def revert_resource(payload: bytes, rid: str) -> bytes:
    if rid not in TARGETS:
        raise NumericDisplayError("Ресурс не включён в числовой display-патч")
    text = payload.decode("utf-8", "strict")
    ending = "\r\n" if "\r\n" in text else "\n"
    prefix = PAYLOAD_MARKER.decode("ascii") + ending
    block = _block(rid, ending)
    if not text.startswith(prefix) or text.count(block) != 1:
        raise NumericDisplayError("Префикс или единственная числовая вставка не найдены")
    restored = text[len(prefix):].replace(block, "", 1).encode("utf-8")
    if sha(restored) != TARGETS[rid]["original_sha256"]:
        raise NumericDisplayError("Откат числовой вставки не восстановил закреплённый оригинал")
    return restored


def patch_resource(original: bytes, rid: str) -> tuple[bytes, list[dict]]:
    row = TARGETS.get(rid)
    if row is None:
        raise NumericDisplayError("Ресурс не включён в числовой display-патч")
    verify_metadata()
    if original.startswith(PAYLOAD_MARKER):
        revert_resource(original, rid)
        return original, [{"id": "reviewed-numeric-display-v2", "already_patched": True}]
    if sha(original) != row["original_sha256"]:
        raise NumericDisplayError(f"{rid}: SHA оригинала не совпал с проверенной игрой 1.6.0.1")
    text = original.decode("utf-8", "strict")
    ending = "\r\n" if "\r\n" in text else "\n"
    block = _block(rid, ending)
    if rid == LOCALIZE_ID:
        if (len(re.findall(r"^Localize\s*=", text, re.M)) != 1
                or len(re.findall(r"^\s+GetString = function\(key, \.\.\.\)", text, re.M)) != 1
                or text.count("local CSLuaCallCSharpInteraction = CS.LuaCallCSharpInteraction") != 1):
            raise NumericDisplayError(f"{rid}: точка native Localize изменилась")
        patched = text + block
    else:
        module = row["module"]
        if (len(re.findall(rf'^local {module} = DeclareClass\("{module}"', text, re.M)) != 1
                or len(re.findall(rf"^function {module}:FormatDamage\(damage\)", text, re.M)) != 1):
            raise NumericDisplayError(f"{rid}: точка FormatDamage изменилась")
        match = re.search(rf"^return {module}\s*\Z", text, re.M)
        if match is None:
            raise NumericDisplayError(f"{rid}: завершающий return класса изменился")
        patched = text[:match.start()] + block + text[match.start():]
    payload = (PAYLOAD_MARKER.decode("ascii") + ending + patched).encode("utf-8")
    if revert_resource(payload, rid) != original:
        raise NumericDisplayError(f"{rid}: числовая вставка не откатывается побайтно")
    return payload, [{"id": "reviewed-numeric-display-v2", "hooks": list(row["hooks"])}]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--payload-dir", type=Path, required=True, help="Новый временный каталог")
    args = parser.parse_args(argv)
    if args.payload_dir.exists():
        raise NumericDisplayError("Каталог нагрузок уже существует; старую сборку не перезаписываем")
    resources = []
    for rid, row in TARGETS.items():
        original = (args.original_dir / f"{rid}.lua").read_bytes()
        payload, edits = patch_resource(original, rid)
        resources.append((rid, payload, {"package": PACKAGE, "id_hex": rid, "kind": "lua-script",
            "module": row["module"], "original_sha256": sha(original), "sha256": sha(payload),
            "bytes": len(payload), "roundtrip": revert_resource(payload, rid) == original, "edits": edits}))
    args.payload_dir.mkdir(parents=True)
    for rid, payload, _ in resources:
        (args.payload_dir / f"{rid}.lua").write_bytes(payload)
    report = {"helper_sha256": sha(HELPER.read_bytes()), "display_policy": DISPLAY_POLICY, "installed": False,
              "resources": [row for _, _, row in resources],
              "limitations": ["Проверка Lua и точного отката не заменяет визуальный просмотр в игре"]}
    (args.payload_dir / "numeric-display-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (NumericDisplayError, OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
