"""Проверенный патч общей строки заголовка; оригиналы не копируются в исходный репозиторий."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / "source/game-wordwrap/header-layout.lua"
METADATA = PROJECT / "source/game-wordwrap/header-layout.json"
PACKAGE = "10"
PAYLOAD_MARKER = b"-- ACE_REVIEWED_RU_WORDWRAP_v2"
PATCH_BEGIN = "-- ACE_REVIEWED_RU_HEADER_LAYOUT_v2 BEGIN"
PATCH_END = "-- ACE_REVIEWED_RU_HEADER_LAYOUT_v2 END"
BASE_ID = "5c97dddb5dd21ee9"
BACK_ID = "1eaf50bae2c25fe8"
MENU_ID = "2359e7634bf04fd1"
TARGETS = {
    BASE_ID: {
        "module": "UIFullScreenViewTemplateBase",
        "original_sha256": "9494783566062110c52451d11488fe6fcee4956b1bfaccdaddf0dedaba141e60",
        "hooks": ("ResetTitle", "RebuildUnderSubTitleLayout", "vOnUninitialize"),
        "inherited_hooks": ("vOnPrefabVisibleChanged",),
    },
    BACK_ID: {
        "module": "UIFullScreenViewTemplate_BackClass",
        "original_sha256": "acf4407392b926f595655a3282fe56c13a095f03601f633aa782d121f1434d92",
        "hooks": ("vOnInitialize", "RefreshContentVisibility", "RefreshOneContentVisibility",
                  "RefreshDropdownOptions", "RefreshFilter", "RefreshSearchFrame", "RefreshTick", "vOnUninitialize"),
    },
    MENU_ID: {
        "module": "CMMenuProClass",
        "original_sha256": "ca8bc8041b2c4135825f26a445dd7e1541a24260555fc80994945729a82d37bb",
        "hooks": ("Init",),
    },
}
MENU_SITE = '        self._menuMono:Init(spacing, bind(self, self.SelectedFunc), selectChangeCallback, unSelectChangedCallback)'
MENU_CALL = '''        do
            local api = rawget(_G, "_AceRuHeaderLayoutV2")
            if type(api) == "table" and api.version == 2 and type(api.menuSpacing) == "function" then
                local accepted, measured = pcall(api.menuSpacing, self, spacing, menuTopStyle)
                if accepted and type(measured) == "table" then spacing = measured end
            end
        end
'''


class HeaderLayoutError(ValueError):
    """Оригинал или структура не совпали с проверенной версией."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_metadata() -> dict:
    data = json.loads(METADATA.read_text("utf-8"))
    rows = {row["id_hex"]: row for row in data.get("resources", [])}
    if data.get("schema") != "wxq-header-layout/v1" or data.get("package") != PACKAGE or set(rows) != set(TARGETS):
        raise HeaderLayoutError("Метаданные общего заголовка не совпали с набором целей")
    if data.get("helper_sha256") != sha(HELPER.read_bytes()):
        raise HeaderLayoutError("Помощник общего заголовка изменился относительно метаданных")
    for rid, target in TARGETS.items():
        if any(rows[rid].get(field) != target[field] for field in ("module", "original_sha256")):
            raise HeaderLayoutError(f"{rid}: метаданные изменили пин оригинала или класс")
        if rows[rid].get("hooks") != list(target["hooks"]):
            raise HeaderLayoutError(f"{rid}: метаданные изменили проверенные точки обновления")
        if rows[rid].get("inherited_hooks", []) != list(target.get("inherited_hooks", ())):
            raise HeaderLayoutError(f"{rid}: метаданные изменили проверенные унаследованные события")
    return data


def _block(rid: str, ending: str) -> str:
    if rid in (BASE_ID, MENU_ID):
        content = HELPER.read_text("utf-8").replace("\r\n", "\n").replace("\n", ending)
    else:
        hooks = ", ".join(json.dumps(name) for name in TARGETS[rid]["hooks"] if name != "vOnUninitialize")
        content = (
            'do\n'
            '    local api = rawget(_G, "_AceRuHeaderLayoutV2")\n'
            '    if type(api) == "table" and api.version == 2 and type(api.install) == "function" then\n'
            f'        api.install(UIFullScreenViewTemplate_BackClass, {{ {hooks} }}, "vOnUninitialize")\n'
            '    end\n'
            'end\n'
        ).replace("\n", ending)
    return ending + PATCH_BEGIN + ending + content + PATCH_END + ending


def revert_resource(payload: bytes, rid: str) -> bytes:
    if rid not in TARGETS:
        raise HeaderLayoutError("Ресурс не включён в патч заголовка")
    text = payload.decode("utf-8", "strict")
    ending = "\r\n" if "\r\n" in text else "\n"
    prefix = PAYLOAD_MARKER.decode("ascii") + ending
    block = _block(rid, ending)
    if not text.startswith(prefix) or text.count(block) != 1:
        raise HeaderLayoutError("Префикс или единственная вставка патча не найдены")
    restored = text[len(prefix):].replace(block, "", 1).encode("utf-8")
    if rid == MENU_ID:
        call = MENU_CALL.replace("\n", ending).encode("utf-8")
        if restored.count(call) != 1:
            raise HeaderLayoutError("Единственная точка native menu spacing не найдена")
        restored = restored.replace(call, b"", 1)
    if sha(restored) != TARGETS[rid]["original_sha256"]:
        raise HeaderLayoutError("Откат не восстановил закреплённый оригинал")
    return restored


def patch_resource(original: bytes, rid: str) -> tuple[bytes, list[dict]]:
    row = TARGETS.get(rid)
    if row is None:
        raise HeaderLayoutError("Ресурс не включён в патч заголовка")
    verify_metadata()
    # Повторная упаковка своего неизменного патча не добавляет обёртку второй раз.
    if original.startswith(PAYLOAD_MARKER):
        revert_resource(original, rid)
        return original, [{"id": "reviewed-header-layout-v2", "already_patched": True}]
    if sha(original) != row["original_sha256"]:
        raise HeaderLayoutError(f"{rid}: SHA оригинала не совпал с проверенной игрой 1.7.0.1")
    text = original.decode("utf-8", "strict")
    module = row["module"]
    if len(re.findall(rf'^local {module} = DeclareClass\("{module}"', text, re.M)) != 1:
        raise HeaderLayoutError(f"{rid}: объявление общего класса изменилось")
    for hook in row["hooks"]:
        if len(re.findall(rf"^function {module}:{hook}\(", text, re.M)) != 1:
            raise HeaderLayoutError(f"{rid}: точка {hook} отсутствует или неоднозначна")
    ending = "\r\n" if "\r\n" in text else "\n"
    block = _block(rid, ending)
    if rid == MENU_ID:
        if text.count(MENU_SITE) != 1:
            raise HeaderLayoutError(f"{rid}: native CMMenu.Init изменился или неоднозначен")
        text = text.replace(MENU_SITE, MENU_CALL.replace("\n", ending) + MENU_SITE, 1)
    if rid in (BASE_ID, MENU_ID):
        patched = text + block
    else:
        match = re.search(rf"^return {module}\s*\Z", text, re.M)
        if match is None:
            raise HeaderLayoutError(f"{rid}: завершающий return класса изменился")
        patched = text[:match.start()] + block + text[match.start():]
    payload = (PAYLOAD_MARKER.decode("ascii") + ending + patched).encode("utf-8")
    if revert_resource(payload, rid) != original:
        raise HeaderLayoutError(f"{rid}: вставка не откатывается побайтно")
    return payload, [{"id": "reviewed-header-layout-v2", "hooks": list(row["hooks"])}]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-dir", type=Path, required=True)
    parser.add_argument("--payload-dir", type=Path, required=True, help="Новый временный каталог, без оригиналов")
    args = parser.parse_args(argv)
    if args.payload_dir.exists():
        raise HeaderLayoutError("Каталог нагрузок уже существует; старую сборку не перезаписываем")
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
    report = {"helper_sha256": sha(HELPER.read_bytes()), "installed": False,
              "resources": [row for _, _, row in resources],
              "limitations": ["Реальная иерархия prefab и экран игры должны быть проверены отдельно; стенд не доказывает их геометрию"]}
    (args.payload_dir / "header-layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HeaderLayoutError, OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
