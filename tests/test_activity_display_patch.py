"""ActivityHelp: текущие cfg и настоящие Lua-consumers без запуска игры."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
sys.dont_write_bytecode = True

from game_lang_codec import fields, read_varint, validate_text  # noqa: E402
from validate_game_formatting import LuaState  # noqa: E402

GAME = Path(os.environ.get("WXQ_GAME_ROOT", r"C:\Program Files\WXQLauncher\WXQ(1)"))
DLL = GAME / "WXQ_Data/Plugins/x86_64/GameCore.dll"
ORIGINAL_DIR = os.environ.get("WXQ_ACTIVITY_ORIGINALS")
FIXTURE = PROJECT / "tests/lua/activity_display_v2_fixtures.lua"
CONFIG_ID = "b5d856bfdf22400d"
CONFIG_SHA = "b6577afb2715f2094d777e195a70f93cc9c10f045d96f6f93ac381236b705266"
CURRENT_SHA = {
    "556806005bdb0a12": "7ff8ab154019147e8b4e734fad11a9905087faf01ac5cc9ba9eb216c35447565",
    "d1d773d80964cf4a": "f9c192dff8c65de63db90ea9112dd5e7169d1354ba96e5b8bc57f4d7a5df39e0",
    "251491390511c74b": "fedbfbdbde2b7a7c8e6412ebd27ceb06dbd74a7f55a7b1e870549f6f5881842c",
    "25fadada8c5fc508": "75ba11d5c2104c045aecd94013bb26ca5c93731debd8d08ffffe3fddda92c2ce",
}
CLASS_NAMES = dict(zip(CURRENT_SHA, ("Image", "Style2", "Styles", "Group")))
FIELD_NAMES = {2: "Title", 11: "ConfirmContent", 15: "GroupTitle"}
TG075_TITLE = "<color=#FFFFFF>英雄皮肤</color>"
TG075_RU = "<color=#FFFFFF>Облики героев</color>"
NEW_APPROVED = {
    "<color=#FFFFFF>抉择之弈活动规则</color>": "<color=#FFFFFF>Правила события «Игра выбора»</color>",
    "<color=#FFFFFF>蔷薇加码活动规则</color>": "<color=#FFFFFF>Правила события «Бонус Розы»</color>",
    "<color=#FFFFFF>超级假期规则</color>": "<color=#FFFFFF>Правила события «Суперканикулы»</color>",
    "<color=#FFFFFF>助力王牌对决活动规则</color>": "<color=#FFFFFF>Правила события «Поддержка Дуэли асов»</color>",
    "<color=#FFFFFF>线条小狗天幕祈愿规则</color>": "<color=#FFFFFF>Правила призыва небосвода «Рисованный щенок»</color>",
    "<color=#FFFFFF>冲分钻石礼活动规则</color>": "<color=#FFFFFF>Правила события «Алмазы за рейтинг»</color>",
    "<color=#FFFFFF>超级周末规则</color>": "<color=#FFFFFF>Правила события «Супервыходные»</color>",
}
TITLE_OVERRIDES = {
    "<color=#FFFFFF>送蔷薇币活动规则</color>": "<color=#FFFFFF>Правила события «Монеты Розы»</color>",
    "<color=#FFFFFF>蔷薇之约活动说明</color>": "<color=#FFFFFF>Описание события «Обещание Розы»</color>",
    "<color=#FFFFFF>蔷薇阁活动规则</color>": "<color=#FFFFFF>Правила события «Павильон Розы»</color>",
    "<color=#FFFFFF>蔷薇挑战活动规则</color>": "<color=#FFFFFF>Правила события «Испытание Розы»</color>",
    "<color=#FFFFFF>每日蔷薇币活动规则</color>": "<color=#FFFFFF>Правила события «Ежедневные монеты Розы»</color>",
}


def lua_chunk(source: bytes | str, name: str) -> str:
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    if "]====]" in text:
        raise ValueError("Исходник содержит ограничитель тестового Lua-чанка")
    return "assert(load([====[" + text + "]====], " + json.dumps(name) + "))()"


def current_contexts() -> tuple[bytes, list, list[tuple[str, str]]]:
    raw = (Path(ORIGINAL_DIR) / f"{CONFIG_ID}.pb").read_bytes()
    records = [f for f in fields(raw) if (f.number, f.wire) == (2, 2)]
    contexts = []
    for record in records:
        for field in fields(record.payload):
            if field.number in FIELD_NAMES and field.wire == 2:
                text = field.payload.decode("utf-8")
                if re.search(r"[\u3400-\u9fff]", text):
                    contexts.append((FIELD_NAMES[field.number], text))
    return raw, records, contexts


def reviewed_current_pairs() -> dict[str, dict[str, str]]:
    """Независимый oracle: legacy-пересечение с текущим PB и точное ревью задачи."""
    queue = json.loads((PROJECT / "source/game-activity-display-proof/activity-help-queue.json").read_text("utf-8"))
    translations = json.loads((PROJECT / "translations/game-inline/activity-help.ru.json").read_text("utf-8"))
    approved = {}
    for item in queue["items"]:
        for field in item["display_fields"]:
            approved[field, item["source"]] = translations[item["id"]]
    approved.update({("Title", source): target for source, target in NEW_APPROVED.items()})
    approved.update({("Title", source): target for source, target in TITLE_OVERRIDES.items()})
    result = {field: {} for field in FIELD_NAMES.values()}
    for field, source in current_contexts()[2]:
        if (field, source) in approved:
            result[field][source] = approved[field, source]
    return result


class ActivityDisplayPatchTest(unittest.TestCase):
    def test_minimal_export_verifies_without_historical_or_game_sources(self):
        import build_activity_display_patch as patch
        with tempfile.TemporaryDirectory(prefix="wxq-activity-minimal-export-test-") as directory:
            exported = Path(directory)
            paths = {"PROJECT": exported}
            for attribute in ("HELPER", "METADATA", "REVIEWED", "PROOF"):
                source = getattr(patch, attribute)
                target = exported / source.relative_to(patch.PROJECT)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                paths[attribute] = target
            self.assertEqual(len([path for path in exported.rglob("*") if path.is_file()]), 4)
            self.assertFalse((exported / "translations").exists())
            self.assertFalse((exported / "source/game-activity-display-proof").exists())
            self.assertFalse((exported / "source/game-inline-extra").exists())
            allowed = {path.resolve() for name, path in paths.items() if name != "PROJECT"}
            original_open = Path.open

            def exported_only(path, *args, **kwargs):
                self.assertIn(path.resolve(), allowed, "Экспортная проверка читает только четыре собственных файла")
                return original_open(path, *args, **kwargs)

            with mock.patch.multiple(patch, **paths):
                with mock.patch.object(patch, "audit_historical_inputs", side_effect=AssertionError("Исторический audit должен запускаться явно")):
                    with mock.patch.object(Path, "open", exported_only):
                        metadata = patch.verify_metadata()
            self.assertEqual(metadata["current_config"]["records"], 115)
            self.assertEqual(metadata["current_config"]["display_contexts"], 128)
            for key, expected in {
                "source_strings": 108, "source_contexts": 128,
                "task_new_title_strings": 7, "task_canon_override_strings": 5, "task_reviewed_strings": 12,
            }.items():
                self.assertEqual(metadata["coverage"][key], expected)

    @unittest.skipUnless((PROJECT / "source/game-activity-display-proof/activity-help-queue.json").is_file(), "Исторический корпус доступен только для явного локального audit")
    def test_historical_source_audit_is_an_explicit_local_check(self):
        import build_activity_display_patch as patch
        self.assertEqual(patch.audit_historical_inputs(), {
            "checked_inputs": 4, "legacy_pairs": 102,
            "legacy_pairs_present_in_current": 101, "legacy_contexts_present_in_current": 121,
            "installed": False,
        })

    def test_four_current_consumers_and_nine_display_sites_are_pinned(self):
        import build_activity_display_patch as patch
        self.assertEqual(set(patch.TARGETS), set(CURRENT_SHA))
        self.assertEqual(set(patch.SOURCES), set(CURRENT_SHA))
        self.assertEqual(sum(len(swaps) for swaps in patch.SOURCES.values()), 9)
        for rid, expected in CURRENT_SHA.items():
            self.assertEqual(patch.TARGETS[rid]["original_sha256"], expected)
        patch.verify_metadata()

    def test_unknown_original_resource_and_unpatched_revert_are_refused(self):
        import build_activity_display_patch as patch
        rid = next(iter(CURRENT_SHA))
        with self.assertRaises(ValueError):
            patch.patch_resource(b"local changed = true\n", rid)
        with self.assertRaises(ValueError):
            patch.patch_resource(b"local changed = true\n", "0000000000000000")
        with self.assertRaises(ValueError):
            patch.revert_resource(b"local changed = true\n", rid)

    def test_helper_or_review_metadata_tamper_is_refused(self):
        import build_activity_display_patch as patch
        with tempfile.TemporaryDirectory(prefix="wxq-activity-metadata-test-") as directory:
            directory = Path(directory)
            helper = directory / "changed-helper.lua"
            helper.write_bytes(patch.HELPER.read_bytes() + b"\n-- changed\n")
            with mock.patch.object(patch, "HELPER", helper):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()
            metadata = json.loads(patch.METADATA.read_text("utf-8"))
            metadata["resources"][0]["original_sha256"] = "0" * 64
            changed = directory / "changed-metadata.json"
            changed.write_text(json.dumps(metadata, ensure_ascii=False), "utf-8")
            with mock.patch.object(patch, "METADATA", changed):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()
            reviewed = json.loads(patch.REVIEWED.read_text("utf-8"))
            reviewed["rows"][0]["target_ru"] = "Изменённый заголовок"
            changed_review = directory / "changed-review.json"
            changed_review.write_text(json.dumps(reviewed, ensure_ascii=False), "utf-8")
            with mock.patch.object(patch, "REVIEWED", changed_review):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()
            proof = json.loads(patch.PROOF.read_text("utf-8"))
            proof["rows"][0]["target_ru"] = "Изменённый заголовок"
            changed_proof = directory / "changed-proof.json"
            changed_proof.write_text(json.dumps(proof, ensure_ascii=False), "utf-8")
            with mock.patch.object(patch, "PROOF", changed_proof):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()
            metadata = json.loads(patch.METADATA.read_text("utf-8"))
            metadata["current_config"]["records"] = 109
            changed.write_text(json.dumps(metadata, ensure_ascii=False), "utf-8")
            with mock.patch.object(patch, "METADATA", changed):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()


@unittest.skipUnless(DLL.is_file() and ORIGINAL_DIR, "Нужны штатный Lua DLL и WXQ_ACTIVITY_ORIGINALS")
class ActivityDisplayLuaTest(unittest.TestCase):
    def lua(self) -> LuaState:
        state = LuaState(DLL)
        self.addCleanup(state.close)
        return state

    def fixture(self, cases: str, *, patched: bool = True) -> bytes:
        directory = Path(ORIGINAL_DIR)
        modules, patched_modules = [], []
        for rid, name in CLASS_NAMES.items():
            source = (directory / f"{rid}.lua").read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), CURRENT_SHA[rid])
            modules.append(name + " = " + lua_chunk(source, "activity-original-" + name))
            if patched:
                import build_activity_display_patch as patch
                payload = patch.patch_resource(source, rid)[0]
                patched_modules.append(name + " = " + lua_chunk(payload, "activity-patched-" + name))
        replacements = {
            "ORIGINAL_MODULES": "local Original = { " + ",\n".join(modules) + " }",
            "PATCHED_MODULES": "local Patched = { " + ",\n".join(patched_modules) + " }" if patched else "local Patched = Original",
            "ACTIVITY_TITLE": "local tg075Title = " + json.dumps(TG075_TITLE, ensure_ascii=False),
            "HELPER_API": "local activityDisplay = " + lua_chunk(patch._block(next(iter(CURRENT_SHA)), "\n") + "\nreturn wxqRuActivityDisplayV2", "activity-helper-api") if patched else "local activityDisplay = false",
            "EXTENDED_CASES": cases,
        }
        text = FIXTURE.read_text("utf-8")
        for name, source in replacements.items():
            token = "-- {{" + name + "}}"
            self.assertEqual(text.count(token), 1)
            text = text.replace(token, source)
        self.assertNotIn("-- {{", text)
        return text.encode("utf-8")

    def run_cases(self, cases: str, **options) -> int:
        return int(self.lua().load(self.fixture(cases, **options), "activity-display-behavior", True))

    def test_current_pb_has_115_records_128_contexts_and_exact_tg075_row(self):
        raw, records, contexts = current_contexts()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), CONFIG_SHA)
        self.assertEqual(len(records), 115)
        self.assertEqual(len(contexts), 128)
        matches = []
        for index, record in enumerate(records):
            values = fields(record.payload)
            if any((f.number, f.wire, f.payload) == (2, 2, TG075_TITLE.encode()) for f in values):
                record_id = next(read_varint(f.payload, 0)[0] for f in values if (f.number, f.wire) == (1, 0))
                matches.append((index, record_id))
        self.assertEqual(matches, [(86, 1075)])

    def test_current_config_validation_is_readonly_and_refuses_old_or_changed_source(self):
        import build_activity_display_patch as patch
        raw = current_contexts()[0]
        self.assertEqual(patch.verify_current_config(raw), {
            "source_sha256": CONFIG_SHA, "records": 115, "display_contexts": 128, "modified": False,
        })
        historical = PROJECT / "source/game-inline-extra/originals/b5d856bfdf22400d.original-config.pb"
        if historical.is_file():
            with self.assertRaises(ValueError):
                patch.verify_current_config(historical.read_bytes())
        with self.assertRaises(ValueError):
            patch.verify_current_config(raw + b"\x00")
        self.assertEqual(current_contexts()[0], raw)

    def test_original_current_consumer_reproduces_tg075(self):
        self.assertGreater(self.run_cases('''
local cfg = makeCfg(1075, tg075Title)
setDatabase({ cfg })
local view = newStyle(Original.Style2)
view:InitBySystemID(1075)
expect(view.viewData.popViewBaseParam.titleContent, tg075Title, "TG075: the real current consumer displays the original Chinese cfg title")
expect(view.refreshCalls[1][1], 1075, "The actual system ID reaches native RefreshBySystemID")
expect(cfg.Title, tg075Title, "Baseline cfg is unchanged")
''', patched=False), 0)

    def test_tg075_title_is_translated_by_real_update_and_init_consumers(self):
        self.assertGreater(self.run_cases('''
local cfg = makeCfg(1075, tg075Title)
setDatabase({ cfg })
local image = newImage(Patched.Image)
image:UpdateData(1075)
expect(image.title, "<color=#FFFFFF>Облики героев</color>", "TG075: real UpdateData translates only the displayed title")
expect(image._cfg, cfg, "UpdateData retains the exact native config object")
for _, class in ipairs({ Patched.Style2, Patched.Styles }) do
    local view = newStyle(class)
    view:InitBySystemID(1075)
    expect(view.viewData.popViewBaseParam.titleContent, "<color=#FFFFFF>Облики героев</color>", "TG075: real InitBySystemID translates the title")
end
expect(cfg.Title, tg075Title, "The original cfg scalar is never overwritten")
'''), 0)

    def test_all_nine_sites_preserve_selection_ids_callbacks_and_config(self):
        self.assertGreater(self.run_cases('''
local title2 = "<color=#FFFFFF>万象启程规则</color>"
local major = makeCfg(20, tg075Title, 20, 2)
major.GroupTitle = "交易行规则"
local minor = makeCfg(21, title2, 20, 1)
for _, pair in ipairs({ { Original.Style2, Patched.Style2 }, { Original.Styles, Patched.Styles } }) do
    local a, b = compareStyle(pair[1], pair[2], { major, minor }, 21)
    expect(a.viewData.popViewBaseParam.titleContent, "交易行规则", "Original chooses major GroupTitle")
    expect(b.viewData.popViewBaseParam.titleContent, "Правила торговой площадки", "Patched major GroupTitle")
    expect(b.viewData.MenuData.MenuTree[1].Title, "<color=#FFFFFF>Правила «Начала пути ACE»</color>", "Sorted menu title uses the same selected cfg")
    expect(b.viewData.MenuData.MenuTree[2].Title, "<color=#FFFFFF>Облики героев</color>", "Every neighbouring menu title is translated")
end
local a, b = compareGroup({ major, minor }, 21)
expect(a.viewData.popViewBaseParam.titleContent, "交易行规则", "Original grouped help chooses major GroupTitle")
expect(b.viewData.popViewBaseParam.titleContent, "Правила торговой площадки", "Grouped help title is translated")
expect(b.viewData.MenuData.MenuTree[1].Title, "<color=#FFFFFF>Правила «Начала пути ACE»</color>", "Grouped table menu title")
expect(b.viewData.MenuData.MenuTree[2].Title, "<color=#FFFFFF>Облики героев</color>", "Grouped neighbouring menu title")
'''), 0)

    def test_major_title_first_row_and_config_title_branches_are_preserved(self):
        self.assertGreater(self.run_cases('''
for _, pair in ipairs({ { Original.Style2, Patched.Style2 }, { Original.Styles, Patched.Styles } }) do
    local cfg = makeCfg(30, tg075Title, 30, 1)
    local a, b = compareStyle(pair[1], pair[2], { cfg }, 30)
    expect(b.viewData.popViewBaseParam.titleContent, "<color=#FFFFFF>Облики героев</color>", "Empty major GroupTitle selects major Title")
    expect(b.viewData.MenuData, nil, "One native row does not create a menu")
    cfg.GroupID = 99
    a, b = compareStyle(pair[1], pair[2], { cfg }, 30)
    expect(b.viewData.popViewBaseParam.titleContent, "<color=#FFFFFF>Облики героев</color>", "No group major selects first row")
    a, b = compareStyle(pair[1], pair[2], { cfg }, 30, {})
    expect(b.viewData.popViewBaseParam.titleContent, "<color=#FFFFFF>Облики героев</color>", "An empty native group falls back to config.Title")
end
local cfg = makeCfg(40, tg075Title, 99, 1)
local a, b = compareGroup({ cfg }, 40)
expect(b.viewData.popViewBaseParam.titleContent, "<color=#FFFFFF>Облики героев</color>", "Grouped help has the same first-row title fallback")
'''), 0)

    def test_confirm_content_preserves_no_page_last_page_and_button_fallback(self):
        self.assertGreater(self.run_cases('''
for _, branch in ipairs({ "empty", "last", "middle", "missing-button", "empty-confirm", "empty-confirm-no-pages" }) do
    local cfg = makeCfg(1075, tg075Title)
    cfg.ConfirmContent = (branch == "empty-confirm" or branch == "empty-confirm-no-pages") and "" or "确认"
    local a, b = compareImageRefresh(cfg, branch)
    if branch == "empty" or branch == "last" then
        expect(a.confirm.text, "确认", "Original confirm scalar")
        expect(b.confirm.text, "ОК", "Both native ConfirmContent sites translate the scalar")
    elseif branch == "middle" then
        expect(b.confirm.text, "Далее", "A middle page keeps native buttonTitle rather than cfg ConfirmContent")
    elseif branch == "missing-button" or branch == "empty-confirm-no-pages" then
        expect(b.confirm.text, "Подтвердить", "A nil native buttonTitle keeps native Confirm fallback")
    else
        expect(b.confirm.text, "Готово", "An empty cfg ConfirmContent keeps native last-page buttonTitle")
    end
end
'''), 0)

    def test_image_update_preserves_native_media_branch_and_object_identity(self):
        self.assertGreater(self.run_cases('''
for _, media in ipairs({ "image", "video", "cloud" }) do
    local cfg = makeCfg(1075, tg075Title)
    if media ~= "image" then cfg.VideoUrl = { "", "native-video-1", "native-video-2" } end
    cfg.IsCloudsVideo = media == "cloud"
    local before = clone(cfg)
    setDatabase({ cfg })
    local a = newImage(Original.Image)
    a:UpdateData(1075)
    setDatabase({ cfg })
    local b = newImage(Patched.Image)
    b:UpdateData(1075)
    expect(a.title, tg075Title, "Native media title")
    expect(b.title, "<color=#FFFFFF>Облики героев</color>", "Translated media title")
    sameData(cfg, before, "Media cfg and body are read only")
    expect(b._cfg, cfg, "Media view retains exact cfg identity")
    for _, field in ipairs({ "mode", "buttons", "_isVideoMode", "_isCloudsVideoMode", "_showIndex", "drag", "singleSelect", "playedVideo", "playedCloudsVideo" }) do
        expect(b[field], a[field], "Native media branch: " .. field)
    end
    sameData(b._imageList, a._imageList, "Native image list")
    sameData(b._videoList, a._videoList, "Native filtered video list")
    sameData(b._textList, a._textList, "Native body list")
    sameData(b.slideCalls, a.slideCalls, "Native drag branch")
    expect(b.tabData, media == "image" and b._imageList or b._videoList, "Tab control retains native list object")
end
'''), 0)

    def test_locale_marker_failure_missing_localize_and_unknown_source_are_native(self):
        self.assertGreater(self.run_cases('''
local cfg = makeCfg(1075, tg075Title)
for _, language in ipairs({ "设置", "Settings", "Язык", "Настройки " }) do
    marker = language
    local _, b = compareStyle(Original.Style2, Patched.Style2, { cfg }, 1075)
    expect(b.viewData.popViewBaseParam.titleContent, tg075Title, "Non-Russian locale is a native display")
end
marker = nil
local _, b = compareStyle(Original.Styles, Patched.Styles, { cfg }, 1075)
expect(b.viewData.popViewBaseParam.titleContent, tg075Title, "Missing locale marker is native")
marker = "Настройки"
markerError = true
_, b = compareStyle(Original.Style2, Patched.Style2, { cfg }, 1075)
expect(b.viewData.popViewBaseParam.titleContent, tg075Title, "A failing native marker is a native display")
markerError = false
local saved = Localize
for _, missing in ipairs({ false, {}, { GetString = false } }) do
    Localize = missing
    _, b = compareStyle(Original.Style2, Patched.Style2, { cfg }, 1075)
    expect(b.viewData.popViewBaseParam.titleContent, tg075Title, "Missing Localize API is a native display")
end
Localize = nil
_, b = compareStyle(Original.Style2, Patched.Style2, { cfg }, 1075)
expect(b.viewData.popViewBaseParam.titleContent, tg075Title, "Nil Localize is a native display")
Localize = saved
for _, source in ipairs({ "未知的帮助标题", "<color=#FFFFFF>英雄皮肤 </color>", "确认" }) do
    cfg.Title = source
    _, b = compareStyle(Original.Style2, Patched.Style2, { cfg }, 1075)
    expect(b.viewData.popViewBaseParam.titleContent, source, "Unreviewed or different-field source remains native")
end
'''), 0)

    def test_native_close_callbacks_and_userdata_are_not_rebound(self):
        self.assertGreater(self.run_cases('''
for _, classes in ipairs({ Original, Patched }) do
    local token = {}
    local calls = {}
    local cfg = makeCfg(1075, tg075Title, 1075, 1)
    setDatabase({ cfg })
    local group = newGroup(classes.Group)
    local argument = { SystemID = 1075, UserData = token, CloseBack = function(...) calls[#calls + 1] = table.pack(...) end }
    group:vOnInitialize(argument)
    expect(group._closeBack, argument.CloseBack, "Native CloseBack identity is retained")
    expect(group.superArgument, argument, "The original argument object reaches native super")
    expect(group.viewData.MenuData.MenuSelect, group.OnSelectTab, "The native menu selection callback is retained")
    expect(group:vOnAction(UICommonAction.CloseUI, token), true, "Native close branch result")
    expect(#calls, 1, "CloseBack is invoked once")
    expect(calls[1].n, 0, "Grouped close keeps native zero-argument callback")
    local style = newStyle(classes.Styles)
    style._closeBackData = token
    style._closeBack = argument.CloseBack
    expect(style:vOnAction(UICommonAction.CloseUI, token), true, "Native styled close branch result")
    expect(calls[2].n, 2, "Styled close preserves explicit nil and userdata")
    expect(calls[2][1], nil, "Styled close first argument stays nil")
    expect(calls[2][2], token, "Styled close preserves userdata identity")
end
'''), 0)

    def test_image_next_confirm_and_bubble_callbacks_remain_native(self):
        self.assertGreater(self.run_cases('''
for _, class in ipairs({ Original.Image, Patched.Image }) do
    local cfg = makeCfg(1075, tg075Title)
    local view = newImage(class)
    view._cfg, view._showIndex = cfg, 1
    view._imageList, view._textList = { "image-1", "image-2" }, { "body-1", "body-2" }
    local calls = {}
    local callback = function(...) calls[#calls + 1] = table.pack(...) end
    view._closeBack = callback
    view:OnWidgetCommonBtn_Style1_BigClickAction()
    expect(view._showIndex, 2, "Native next-page branch increments once")
    expect(#calls, 0, "Next page does not call CloseBack")
    expect(view._closeBack, callback, "Next page keeps callback identity")
    view:OnWidgetCommonBtn_Style1_BigClickAction()
    expect(#calls, 1, "Last page confirms once")
    expect(calls[1].n, 1, "Native image callback arity")
    expect(calls[1][1], OSProto.MsgBoxClickIndex.Confirm, "Native image callback argument")
    expect(view._closeBack, false, "Native callback is consumed")
    expect(view.closed, true, "Native close branch executes")
    expect(bubbleMessage, cfg.ClickConfirmContent, "Native post-click message is unchanged")
end
'''), 0)

    def test_finite_helper_matches_reviewed_current_intersection_and_new_titles(self):
        mapping = reviewed_current_pairs()
        contexts = current_contexts()[2]
        approved_contexts = sum(source in mapping[field] for field, source in contexts)
        self.assertEqual(sum(len(values) for values in mapping.values()), 108)
        self.assertEqual(approved_contexts, 128)
        cases = []
        for field, source in contexts:
            expected = mapping[field].get(source, source)
            cases.append("expect(activityDisplay(" + ",".join(json.dumps(x, ensure_ascii=False) for x in (field, source)) + ")," + json.dumps(expected, ensure_ascii=False) + ", 'Exact current approved field/source')")
            if source in mapping[field]:
                self.assertEqual(validate_text(source, expected), [])
        cases.extend([
            'expect(activityDisplay("Title", "确认"), "确认", "Field isolation")',
            'expect(activityDisplay("GroupTitle", tg075Title), tg075Title, "Group field isolation")',
            'expect(activityDisplay("UnknownField", tg075Title), tg075Title, "Field allowlist")',
            'expect(activityDisplay("Title", nil), nil, "Nil identity")',
            'expect(activityDisplay("Title", 19), 19, "Number identity")',
            'local token = {}; expect(activityDisplay("Title", token), token, "Object identity")',
        ])
        self.assertGreater(self.run_cases("\n".join(cases)), 0)

    def test_rose_overrides_apply_only_to_exact_russian_title_pairs(self):
        pairs = "{ " + ",\n".join("{ " + json.dumps(source, ensure_ascii=False) + ", " + json.dumps(target, ensure_ascii=False) + " }" for source, target in TITLE_OVERRIDES.items()) + " }"
        self.assertGreater(self.run_cases("local overrides = " + pairs + '''
for _, pair in ipairs(overrides) do
    local source, target = pair[1], pair[2]
    marker = "Настройки"
    expect(activityDisplay("Title", source), target, "The exact approved Rose title uses its canonical label")
    for _, field in ipairs({ "ConfirmContent", "GroupTitle", "UnknownField" }) do
        expect(activityDisplay(field, source), source, "A Rose title is never substituted in another field")
    end
    expect(activityDisplay("Title", source .. " "), source .. " ", "A changed source is not an exact approved title")
    local cfg = makeCfg(1075, source)
    setDatabase({ cfg })
    local view = newStyle(Patched.Style2)
    view:InitBySystemID(1075)
    expect(view.viewData.popViewBaseParam.titleContent, target, "Real cfg consumer displays the exact canonical Rose title")
    expect(cfg.Title, source, "The original Rose cfg scalar is retained")
    for _, language in ipairs({ "设置", "Settings", "Язык" }) do
        marker = language
        expect(activityDisplay("Title", source), source, "Non-Russian Rose title is native")
        view:InitBySystemID(1075)
        expect(view.viewData.popViewBaseParam.titleContent, source, "The real non-Russian consumer is native")
    end
end
marker = "Настройки"
local future = "<color=#FFFFFF>蔷薇新活动规则</color>"
expect(activityDisplay("Title", future), future, "A future Rose title is not translated by a substring rule")
'''), 0)

    def test_current_resource_syntax_inverse_idempotence_and_tamper(self):
        import build_activity_display_patch as patch
        for rid in CURRENT_SHA:
            with self.subTest(rid=rid):
                original = (Path(ORIGINAL_DIR) / f"{rid}.lua").read_bytes()
                payload, edits = patch.patch_resource(original, rid)
                self.assertTrue(edits)
                ending = "\r\n" if b"\r\n" in original else "\n"
                for before, after, _field in patch.SOURCES[rid]:
                    self.assertEqual(original.decode("utf-8").count(before.replace("\n", ending)), 1)
                    self.assertEqual(payload.decode("utf-8").count(after.replace("\n", ending)), 1)
                # Независимый обратный oracle снимает только объявленный prefix
                # и девять display-выражений, не вызывая production revert.
                inverse = payload.decode("utf-8")
                prefix = patch.PAYLOAD_MARKER.decode("ascii") + ending + patch._block(rid, ending)
                self.assertTrue(inverse.startswith(prefix))
                inverse = inverse[len(prefix):]
                for before, after, _field in reversed(patch.SOURCES[rid]):
                    inverse = inverse.replace(after.replace("\n", ending), before.replace("\n", ending), 1)
                self.assertEqual(inverse.encode("utf-8"), original)
                self.assertEqual(patch.revert_resource(payload, rid), original)
                self.assertEqual(patch.patch_resource(payload, rid)[0], payload)
                self.assertEqual(payload.count(patch.PATCH_BEGIN.encode()), 1)
                self.assertEqual(payload.count(patch.PATCH_END.encode()), 1)
                self.lua().load(original, "activity-original-syntax-" + rid)
                self.lua().load(payload, "activity-patched-syntax-" + rid)
                with self.assertRaises(ValueError):
                    patch.patch_resource(original + b"-- changed\n", rid)
                for tampered in (
                    payload + b"-- changed\n",
                    payload.replace(patch.PATCH_BEGIN.encode(), b"-- changed-begin", 1),
                    payload.replace(patch.PATCH_END.encode(), b"-- changed-end", 1),
                    payload.replace(patch.PATCH_BEGIN.encode(), patch.PATCH_BEGIN.encode() + b"\n-- changed", 1),
                ):
                    with self.assertRaises(ValueError):
                        patch.revert_resource(tampered, rid)
                    with self.assertRaises(ValueError):
                        patch.patch_resource(tampered, rid)
        self.assertEqual(hashlib.sha256(current_contexts()[0]).hexdigest(), CONFIG_SHA)


if __name__ == "__main__":
    unittest.main()
