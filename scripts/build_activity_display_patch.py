"""Обратимый перевод подтверждённых ActivityHelp cfg-to-display потребителей."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / "source/game-formatting/activity-display-v2.lua"
METADATA = PROJECT / "source/game-formatting/activity-display-v2.json"
REVIEWED = PROJECT / "source/game-formatting/activity-display-v2.reviewed.json"
PROOF = PROJECT / "source/game-formatting/activity-display-v2.proof.json"
REVIEWED_SHA256 = "ff8026513d1de19fca4de66e32f997ba9bc4d8984da1dff172e3a50af3717ec7"
PROOF_SHA256 = "df4dfa6e8a7e41c9233b21638126620c931fa1e884306ff5a0f237c8093169f5"
CONTEXTS_SHA256 = "22ac057c63a456dde07074dbb4aa5f52fdc9630593dea65635595299662dd4b5"
PACKAGE = "10"
CONFIG_ID = "b5d856bfdf22400d"
CONFIG_SHA256 = "17257ab86965fffe791f955543ec3f0b57a05986effbfc4964efea2f8c024c9d"
PAYLOAD_MARKER = b"-- ACE_REVIEWED_RU_WORDWRAP_v2"
PATCH_BEGIN = "-- ACE_REVIEWED_RU_ACTIVITY_DISPLAY_v2 BEGIN"
PATCH_END = "-- ACE_REVIEWED_RU_ACTIVITY_DISPLAY_v2 END"
FIELD_NAMES = {2: "Title", 11: "ConfirmContent", 15: "GroupTitle"}
APPROVED_INPUTS = {
    "source/game-activity-display-proof/activity-help-queue.json": "638dd1fac30e39efae78ff7567e73515a0dcd445a10eca5958980a7e4ab15fb2",
    "source/game-activity-display-proof/manifest.json": "e45222d8345c244243460266f20a295fd023d63b3f38435944b9ddc642f565f4",
    "translations/game-inline/activity-help.ru.json": "7cb429eedd0d96e4955c7d3e4c1bf2d278a45a7805abf9dc227d69ae7d733399",
    "translations/game-inline/activity-help.qa.json": "256a7669785d8347ec9e7c03c44fff87c019747268544cde2bc77cfda19e91d3",
}
TARGETS = {
    "556806005bdb0a12": {
        "module": "UICommonHelpSystemWithImageClass",
        "original_sha256": "7ff8ab154019147e8b4e734fad11a9905087faf01ac5cc9ba9eb216c35447565",
        "hooks": ("RefreshUI", "UpdateData"),
    },
    "d1d773d80964cf4a": {
        "module": "UICommonHelpSystemVariousStyle2Class",
        "original_sha256": "f9c192dff8c65de63db90ea9112dd5e7169d1354ba96e5b8bc57f4d7a5df39e0",
        "hooks": ("InitBySystemID",),
    },
    "251491390511c74b": {
        "module": "UICommonHelpSystemVariousStylesClass",
        "original_sha256": "fedbfbdbde2b7a7c8e6412ebd27ceb06dbd74a7f55a7b1e870549f6f5881842c",
        "hooks": ("InitBySystemID",),
    },
    "25fadada8c5fc508": {
        "module": "UICommonHelpSystemGroupClass",
        "original_sha256": "75ba11d5c2104c045aecd94013bb26ca5c93731debd8d08ffffe3fddda92c2ce",
        "hooks": ("vOnInitialize",),
    },
}

# Меняются только скалярные значения на границе display. Условия, callback,
# идентификаторы, порядок вызовов и сами объекты cfg остаются исходными.
SOURCES = {
    "556806005bdb0a12": [
        ('self.widgetCommonBtn_Style1_Big:SetText(not string.isNullOrEmpty(self._cfg.ConfirmContent) and self._cfg.ConfirmContent or Localize.GetString("Confirm"))',
         'self.widgetCommonBtn_Style1_Big:SetText(not string.isNullOrEmpty(self._cfg.ConfirmContent) and wxqRuActivityDisplayV2("ConfirmContent", self._cfg.ConfirmContent) or Localize.GetString("Confirm"))', "ConfirmContent"),
        ('self.widgetCommonBtn_Style1_Big:SetText(listEnd and not string.isNullOrEmpty(self._cfg.ConfirmContent) and self._cfg.ConfirmContent or buttonTitle or Localize.GetString("Confirm"))',
         'self.widgetCommonBtn_Style1_Big:SetText(listEnd and not string.isNullOrEmpty(self._cfg.ConfirmContent) and wxqRuActivityDisplayV2("ConfirmContent", self._cfg.ConfirmContent) or buttonTitle or Localize.GetString("Confirm"))', "ConfirmContent"),
        ('self:SetTitle(cfg.Title)', 'self:SetTitle(wxqRuActivityDisplayV2("Title", cfg.Title))', "Title"),
    ],
    "d1d773d80964cf4a": [
        ('local title = major and (not string.isNullOrEmpty(major.GroupTitle) and major.GroupTitle or major.Title) or config.Title',
         'local title = major and (not string.isNullOrEmpty(major.GroupTitle) and wxqRuActivityDisplayV2("GroupTitle", major.GroupTitle) or wxqRuActivityDisplayV2("Title", major.Title)) or wxqRuActivityDisplayV2("Title", config.Title)', "GroupTitle|Title"),
        ('table.append(viewData.MenuData.MenuTree, { Title = cfg.Title })',
         'table.append(viewData.MenuData.MenuTree, { Title = wxqRuActivityDisplayV2("Title", cfg.Title) })', "Title"),
    ],
    "251491390511c74b": [
        ('local title = major and (not string.isNullOrEmpty(major.GroupTitle) and major.GroupTitle or major.Title) or\n        config.Title',
         'local title = major and (not string.isNullOrEmpty(major.GroupTitle) and wxqRuActivityDisplayV2("GroupTitle", major.GroupTitle) or wxqRuActivityDisplayV2("Title", major.Title)) or\n        wxqRuActivityDisplayV2("Title", config.Title)', "GroupTitle|Title"),
        ('table.append(viewData.MenuData.MenuTree, { Title = cfg.Title })',
         'table.append(viewData.MenuData.MenuTree, { Title = wxqRuActivityDisplayV2("Title", cfg.Title) })', "Title"),
    ],
    "25fadada8c5fc508": [
        ('local title = not string.isNullOrEmpty(major.GroupTitle) and major.GroupTitle or major.Title',
         'local title = not string.isNullOrEmpty(major.GroupTitle) and wxqRuActivityDisplayV2("GroupTitle", major.GroupTitle) or wxqRuActivityDisplayV2("Title", major.Title)', "GroupTitle|Title"),
        ('table.append(bgViewData.MenuData.MenuTree, { Title = config.Title })',
         'table.append(bgViewData.MenuData.MenuTree, { Title = wxqRuActivityDisplayV2("Title", config.Title) })', "Title"),
    ],
}


class ActivityDisplayError(ValueError):
    """Оригинал, точка display или утверждённый источник изменились."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_json(path: Path) -> dict:
    from game_lang_codec import unique_object
    return json.loads(path.read_text("utf-8-sig"), object_pairs_hook=unique_object)


def _approved_maps(data: dict) -> dict:
    from game_lang_codec import validate_text
    # Проверка сборки читает только наши экспортируемые helper/proof файлы.
    # Старые архивы доступны отдельному явному локальному аудиту ниже.
    if sha(PROOF.read_bytes()) != PROOF_SHA256:
        raise ActivityDisplayError("Самодостаточный proof ActivityHelp изменился")
    proof = _read_json(PROOF)
    if (proof.get("schema") != "wxq-activity-display-proof/v2"
            or proof.get("config_resource_id_hex") != CONFIG_ID
            or proof.get("config_source_sha256") != CONFIG_SHA256
            or proof.get("current_contexts_sha256") != CONTEXTS_SHA256
            or proof.get("source_approval_inputs") != data["approved_inputs"]
            or len(proof.get("rows", [])) != 109):
        raise ActivityDisplayError("Самодостаточный proof ActivityHelp не совпал с текущим контрактом")
    if sha(REVIEWED.read_bytes()) != REVIEWED_SHA256:
        raise ActivityDisplayError("Ревью новых заголовков ActivityHelp изменилось")
    reviews = _read_json(REVIEWED)
    if (reviews.get("schema") != "wxq-activity-display-reviewed/v2"
            or reviews.get("config_source_sha256") != CONFIG_SHA256
            or len(reviews.get("rows", [])) != 13):
        raise ActivityDisplayError("Новые заголовки ActivityHelp не совпали с текущим review")
    current = data["current_contexts"]
    reviewed = {}
    for row in reviews["rows"]:
        source, target, field = row["source_cn"], row["target_ru"], row["field"]
        if (row.get("reviewed_by") != "root" or row.get("approval_scope") != "task-translation-review"
                or row.get("source_cfg_sha256") != CONFIG_SHA256 or field != "Title"
                or row["source_context"] not in current
                or row["source_context"]["field"] != field or row["source_context"]["source_cn"] != source
                or row["source_sha256"] != sha(source.encode("utf-8"))
                or row["target_sha256"] != sha(target.encode("utf-8")) or validate_text(source, target)
                or row.get("review_kind") not in ("new-current-title", "canon-title-override")):
            raise ActivityDisplayError("Новая пара ActivityHelp нарушает текущий source/review contract")
        reviewed[(field, source)] = row
    if len(reviewed) != 13:
        raise ActivityDisplayError("Новая пара ActivityHelp повторяется в review")
    maps = {name: {} for name in FIELD_NAMES.values()}
    old_sources, old_contexts, selected_contexts, pairs = set(), 0, [], set()
    for row in proof["rows"]:
        source, field, target = row["source_cn"], row["field"], row["target_ru"]
        pair = (field, source)
        if (pair in pairs or field not in maps or not isinstance(target, str) or not target
                or row["source_sha256"] != sha(source.encode("utf-8"))
                or row["target_sha256"] != sha(target.encode("utf-8"))
                or validate_text(source, target) or re.search(r"[\u3400-\u9fff]", target)):
            raise ActivityDisplayError("Source/target пара ActivityHelp proof изменилась")
        pairs.add(pair)
        expected_contexts = [c for c in current if c["field"] == field and c["source_cn"] == source]
        if not expected_contexts or row["current_contexts"] != expected_contexts:
            raise ActivityDisplayError("ActivityHelp proof потерял точные текущие contexts")
        selected_contexts.extend(expected_contexts)
        legacy = row.get("legacy_approval")
        if legacy is not None:
            if (legacy.get("translation_id") != "ix-" + row["source_sha256"][:12]
                    or legacy.get("source_sha256") != row["source_sha256"]
                    or legacy.get("queue_sha256") != APPROVED_INPUTS["source/game-activity-display-proof/activity-help-queue.json"]
                    or legacy.get("map_sha256") != APPROVED_INPUTS["translations/game-inline/activity-help.ru.json"]):
                raise ActivityDisplayError("Provenance прежней ActivityHelp пары изменился")
            old_sources.add(source)
            old_contexts += len(expected_contexts)
        task = reviewed.get(pair)
        if task is not None:
            if (row.get("approval") != "task-reviewed-by-root" or row.get("task_review_id") != task["id"]
                    or target != task["target_ru"]):
                raise ActivityDisplayError("ActivityHelp task override отличается от точного review")
            if task["review_kind"] == "canon-title-override":
                if (legacy is None or task.get("previous_target_sha256") != legacy["target_sha256"]
                        or sha(task["previous_target_ru"].encode("utf-8")) != legacy["target_sha256"]):
                    raise ActivityDisplayError("ActivityHelp canon override потерял исходную пару")
            elif legacy is not None:
                raise ActivityDisplayError("Новый ActivityHelp Title ошибочно объявлен прежней парой")
        elif (legacy is None or row.get("approval") != "historical-reviewed-map"
                or row.get("task_review_id") is not None or legacy["target_sha256"] != row["target_sha256"]):
            raise ActivityDisplayError("Прежняя ActivityHelp пара потеряла утверждённый target")
        maps[field][source] = target
    if len(selected_contexts) != len(current):
        raise ActivityDisplayError("ActivityHelp proof покрывает не все текущие contexts")
    coverage = {"source_strings": len({row["source_cn"] for row in current}), "source_contexts": len(current),
        "historical_approved_strings_present": len(old_sources), "historical_approved_contexts_present": old_contexts,
        "task_reviewed_strings": len(reviewed), "field_contexts": dict(Counter(row["field"] for row in current)),
        "dictionary_entries": {key: len(value) for key, value in maps.items()},
        "task_new_title_strings": sum(r["review_kind"] == "new-current-title" for r in reviewed.values()),
        "task_canon_override_strings": sum(r["review_kind"] == "canon-title-override" for r in reviewed.values())}
    if data.get("coverage") != coverage or coverage["source_strings"] != 109 or coverage["source_contexts"] != 129:
        raise ActivityDisplayError("Покрытие текущих ActivityHelp display contexts изменилось")
    return maps


def audit_historical_inputs() -> dict:
    """Явный локальный QA-аудит архива. Для import/build он не требуется."""
    from game_lang_codec import validate_text
    verify_metadata()
    documents = {}
    for path, expected in APPROVED_INPUTS.items():
        source = PROJECT / path
        if sha(source.read_bytes()) != expected:
            raise ActivityDisplayError(f"Локальный архив ActivityHelp изменился: {path}")
        documents[path] = _read_json(source)
    queue = documents["source/game-activity-display-proof/activity-help-queue.json"]
    mapping = documents["translations/game-inline/activity-help.ru.json"]
    qa = documents["translations/game-inline/activity-help.qa.json"]
    if (queue.get("resource_id_hex") != CONFIG_ID or len(queue.get("items", [])) != 102
            or len(mapping) != 102 or len(qa.get("rows", [])) != 102 or qa.get("text_validator_issues") != 0):
        raise ActivityDisplayError("Локальный архив ActivityHelp потерял состав утверждённых пар")
    legacy = {}
    for index, row in enumerate(queue["items"]):
        source, ident = row["source"], row["id"]
        target = mapping.get(ident)
        expected_review = {"index": index, "id": ident, "source_sha256": row["source_sha256"],
            "target_sha256": sha(target.encode("utf-8")), "display_fields": row["display_fields"]}
        if (ident != "ix-" + sha(source.encode("utf-8"))[:12] or row["source_sha256"] != sha(source.encode("utf-8"))
                or qa["rows"][index] != expected_review or validate_text(source, target)):
            raise ActivityDisplayError("Локальная ActivityHelp source/review пара изменилась")
        legacy[source] = (target, row["display_fields"])
    present, contexts = 0, 0
    for row in _read_json(PROOF)["rows"]:
        previous = row.get("legacy_approval")
        if previous is None:
            continue
        source = row["source_cn"]
        if (source not in legacy or row["field"] not in legacy[source][1]
                or previous["target_sha256"] != sha(legacy[source][0].encode("utf-8"))):
            raise ActivityDisplayError("Самодостаточная ActivityHelp пара не совпала с локальным архивом")
        present += 1
        contexts += len(row["current_contexts"])
    if present != 101 or contexts != 121:
        raise ActivityDisplayError("Пересечение локального архива ActivityHelp изменилось")
    return {"checked_inputs": len(documents), "legacy_pairs": len(legacy),
            "legacy_pairs_present_in_current": present, "legacy_contexts_present_in_current": contexts,
            "installed": False}


def _helper_source(maps: dict) -> bytes:
    lines = ["-- Только проверенные cfg-поля заголовков и кнопок. Конфигурация не изменяется.",
             "local wxqReviewedActivityDisplayV2 = {"]
    for field in ("Title", "ConfirmContent", "GroupTitle"):
        lines.append("    " + field + " = {")
        for source, target in sorted(maps[field].items()):
            if any(ord(c) < 32 for c in source + target):
                raise ActivityDisplayError("Неожиданный control в ActivityHelp literal")
            lines.append("        [" + json.dumps(source, ensure_ascii=False) + "] = " + json.dumps(target, ensure_ascii=False) + ",")
        lines.append("    },")
    lines.extend(["}", "",
        "local function wxqRuActivityDisplayV2(field, value)",
        '    if type(field) ~= "string" or type(value) ~= "string" then return value end',
        "    local approved = wxqReviewedActivityDisplayV2[field]",
        "    local target = approved and approved[value]",
        '    if type(target) ~= "string" then return value end',
        '    local localize = rawget(_G, "Localize")',
        '    if type(localize) ~= "table" or type(localize.GetString) ~= "function" then return value end',
        '    local ok, marker = pcall(localize.GetString, "TitleSetting")',
        '    if not ok or marker ~= "Настройки" then return value end',
        "    return target", "end", ""])
    return "\n".join(lines).encode("utf-8")


def verify_metadata() -> dict:
    data = _read_json(METADATA)
    resources = data.get("resources", [])
    rows = {row["id_hex"]: row for row in resources}
    if (data.get("schema") != "wxq-activity-display/v2" or data.get("package") != PACKAGE
            or len(resources) != len(TARGETS) or set(rows) != set(TARGETS)):
        raise ActivityDisplayError("Метаданные ActivityHelp не совпали с набором целей")
    for rid, target in TARGETS.items():
        if (any(rows[rid].get(field) != target[field] for field in ("module", "original_sha256"))
                or rows[rid].get("hooks") != list(target["hooks"])):
            raise ActivityDisplayError(f"{rid}: метаданные изменили модуль, SHA или точки ActivityHelp")
    expected_config = {"package": "1", "id_hex": CONFIG_ID, "source_sha256": CONFIG_SHA256,
        "bytes": 26242, "records": 116, "display_contexts": 129}
    if (data.get("current_config") != expected_config or data.get("current_contexts_sha256") != CONTEXTS_SHA256
            or sha(_canonical(data.get("current_contexts"))) != CONTEXTS_SHA256):
        raise ActivityDisplayError("Текущий source-bound cfg ActivityHelp изменился")
    if data.get("approved_inputs") != [{"path": path, "sha256": pin} for path, pin in APPROVED_INPUTS.items()]:
        raise ActivityDisplayError("Источники прежнего ActivityHelp review изменились")
    if data.get("task_reviewed_input") != {"path": "source/game-formatting/activity-display-v2.reviewed.json", "sha256": REVIEWED_SHA256}:
        raise ActivityDisplayError("Источник нового ActivityHelp review изменился")
    if data.get("proof_snapshot") != {"path": "source/game-formatting/activity-display-v2.proof.json", "sha256": PROOF_SHA256}:
        raise ActivityDisplayError("Источник самодостаточного ActivityHelp proof изменился")
    helper = HELPER.read_bytes()
    if data.get("helper_sha256") != sha(helper) or helper != _helper_source(_approved_maps(data)):
        raise ActivityDisplayError("Помощник ActivityHelp не воспроизводится из проверенных пар")
    return data


def verify_current_config(original: bytes) -> dict:
    """Read-only source proof для сборки; cfg не входит в заменяемые payload."""
    from game_lang_codec import fields
    data = verify_metadata()
    if sha(original) != CONFIG_SHA256:
        raise ActivityDisplayError("SHA текущего ActivityHelp cfg не совпал с проверенным оригиналом")
    records = [field for field in fields(original) if (field.number, field.wire) == (2, 2)]
    contexts = []
    for index, record in enumerate(records):
        seen = Counter()
        for field in fields(record.payload):
            occurrence = seen[field.number]
            seen[field.number] += 1
            if field.number not in FIELD_NAMES:
                continue
            if field.wire != 2:
                raise ActivityDisplayError("ActivityHelp display scalar изменил wire type")
            source = field.payload.decode("utf-8", "strict")
            if re.search(r"[\u3400-\u9fff]", source):
                contexts.append({"record_index": index, "field": FIELD_NAMES[field.number],
                    "wire_path": f"root.field2[{index}].field{field.number}[{occurrence}]",
                    "source_cn": source, "source_sha256": sha(source.encode("utf-8"))})
    if len(records) != 116 or contexts != data["current_contexts"]:
        raise ActivityDisplayError("Текущие ActivityHelp field contexts не совпали с проверенными")
    return {"source_sha256": sha(original), "records": len(records), "display_contexts": len(contexts), "modified": False}


def _block(rid: str, ending: str) -> str:
    if rid not in TARGETS:
        raise ActivityDisplayError("Ресурс не включён в ActivityHelp display-патч")
    helper = HELPER.read_text("utf-8").replace("\r\n", "\n").replace("\n", ending)
    return ending + PATCH_BEGIN + ending + helper + PATCH_END + ending


def revert_resource(payload: bytes, rid: str) -> bytes:
    if rid not in TARGETS:
        raise ActivityDisplayError("Ресурс не включён в ActivityHelp display-патч")
    text = payload.decode("utf-8", "strict")
    ending = "\r\n" if "\r\n" in text else "\n"
    prefix = PAYLOAD_MARKER.decode("ascii") + ending + _block(rid, ending)
    if not text.startswith(prefix) or text.count(_block(rid, ending)) != 1:
        raise ActivityDisplayError("Префикс или единственная вставка ActivityHelp не найдены")
    restored = text[len(prefix):]
    for before, after, _field in reversed(SOURCES[rid]):
        before, after = before.replace("\n", ending), after.replace("\n", ending)
        if restored.count(after) != 1:
            raise ActivityDisplayError("ActivityHelp inverse statement отсутствует или неоднозначен")
        restored = restored.replace(after, before, 1)
    original = restored.encode("utf-8")
    if sha(original) != TARGETS[rid]["original_sha256"]:
        raise ActivityDisplayError("Откат ActivityHelp не восстановил закреплённый оригинал")
    return original


def patch_resource(original: bytes, rid: str) -> tuple[bytes, list[dict]]:
    target = TARGETS.get(rid)
    if target is None:
        raise ActivityDisplayError("Ресурс не включён в ActivityHelp display-патч")
    verify_metadata()
    if original.startswith(PAYLOAD_MARKER):
        revert_resource(original, rid)
        return original, [{"id": "reviewed-activity-display-v2", "already_patched": True}]
    if sha(original) != target["original_sha256"]:
        raise ActivityDisplayError(f"{rid}: SHA оригинала ActivityHelp не совпал с проверенной игрой 1.7.0.1")
    text = original.decode("utf-8", "strict")
    module = target["module"]
    if len(re.findall(rf'^local {module} = DeclareClass\("{module}"', text, re.M)) != 1:
        raise ActivityDisplayError(f"{rid}: объявление ActivityHelp модуля изменилось")
    for hook in target["hooks"]:
        if len(re.findall(rf"^function {module}:{hook}\(", text, re.M)) != 1:
            raise ActivityDisplayError(f"{rid}: ActivityHelp точка {hook} отсутствует или неоднозначна")
    ending = "\r\n" if "\r\n" in text else "\n"
    result, edits = text, []
    for before, after, field in SOURCES[rid]:
        before, after = before.replace("\n", ending), after.replace("\n", ending)
        if text.count(before) != 1 or after in text:
            raise ActivityDisplayError(f"{rid}: cfg-to-display statement отсутствует или неоднозначен")
        result = result.replace(before, after, 1)
        edits.append({"id": "reviewed-activity-display-v2", "field": field,
                      "source_line": text.count("\n", 0, text.index(before)) + 1})
    payload = (PAYLOAD_MARKER.decode("ascii") + ending + _block(rid, ending) + result).encode("utf-8")
    if revert_resource(payload, rid) != original:
        raise ActivityDisplayError(f"{rid}: ActivityHelp не откатывается побайтно")
    return payload, edits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--payload-dir", type=Path, required=True, help="Новый временный каталог")
    parser.add_argument("--audit-historical", action="store_true", help="Явно проверить старый локальный архив утверждённых пар")
    args = parser.parse_args(argv)
    if args.payload_dir.exists():
        raise ActivityDisplayError("Каталог нагрузок уже существует; старую сборку не перезаписываем")
    config_proof = verify_current_config((args.original_dir / f"{CONFIG_ID}.pb").read_bytes())
    historical_audit = audit_historical_inputs() if args.audit_historical else None
    resources = []
    for rid, target in TARGETS.items():
        original = (args.original_dir / f"{rid}.lua").read_bytes()
        payload, edits = patch_resource(original, rid)
        resources.append((rid, payload, {"package": PACKAGE, "id_hex": rid, "kind": "lua-script",
            "module": target["module"], "original_sha256": sha(original), "sha256": sha(payload),
            "bytes": len(payload), "roundtrip": revert_resource(payload, rid) == original, "edits": edits}))
    args.payload_dir.mkdir(parents=True)
    for rid, payload, _ in resources:
        (args.payload_dir / f"{rid}.lua").write_bytes(payload)
    report = {"helper_sha256": sha(HELPER.read_bytes()), "metadata_sha256": sha(METADATA.read_bytes()),
        "task_reviewed_sha256": sha(REVIEWED.read_bytes()), "proof_sha256": sha(PROOF.read_bytes()),
        "current_config_proof": config_proof, "historical_audit": historical_audit,
        "coverage": verify_metadata()["coverage"], "installed": False,
        "resources": [row for _, _, row in resources], "live_layout_verified": False}
    (args.payload_dir / "activity-display-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ActivityDisplayError, OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
