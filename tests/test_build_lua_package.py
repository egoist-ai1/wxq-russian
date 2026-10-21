"""Lua-носитель пакета 10 (перенос по пробелам): правки, помощник в Lua-состоянии игры, настоящие ресурсы (только чтение)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
sys.dont_write_bytecode = True

import build_lua_package as blp  # noqa: E402
import pilot_contract as pc  # noqa: E402

GAME = Path(r"C:\Program Files\WXQLauncher\WXQ(1)")
DLL = GAME / "WXQ_Data/Plugins/x86_64/GameCore.dll"
FIXTURES = Path(__file__).resolve().parent / "lua" / "ru_space_wrap_fixtures.lua"
DIAG_FIXTURES = Path(__file__).resolve().parent / "lua" / "ru_space_wrap_diag_fixtures.lua"
INPUT = "Увеличивает силу атаки героя на 10% и скорость атаки на {0}, пока союзники находятся рядом."

SOURCE = """local __Item = import("Item")

---@class UIDemoClass_AutoBind : UIPrefabClass
local UIDemoClass_AutoBind = DeclarePrivateClass("UIDemoClass_AutoBind", _IMPORT_ARG or ClassLib.UIPrefabClass)

function UIDemoClass_AutoBind:ctor()
    ---@type Timi.TimiText
    self.title = false
    ---@type Timi.TimiText
    self.body = false
end

function UIDemoClass_AutoBind:vOnResourceUnLoaded()
    self.title = false
    self.body = false
end

-- title
---@param arg string
function UIDemoClass_AutoBind:SetTitle(arg)
    self.title.text = arg
end
-- body
---@param arg string
function UIDemoClass_AutoBind:SetBody(arg)
    self.body.text = arg
end

return UIDemoClass_AutoBind
"""
HELPER = blp.HELPER.read_text("utf-8")
# Рабочий помощник - v15 (общее покрытие 1702 модулей + 212 инлайн-ресурсов); прежние версии лежат в source/game-wordwrap/archive.
HELPER_SHA = "570058df9be28e3a674e21cdf7d0f4e329cdc20c6802a132f332f1061e47721a"
HELPER_V11_SHA = "db1e64f49b259c13cafd7199301ccbec1cd64dfdca3cf2a262a9b126c4bf2550"
HELPER_V13_SHA = "bccbf8e9ce5d58fbbbe1ac74278506d512e06c253b4dad549e911c09b14a836c"
HELPER_V13_PATH = PROJECT / "source" / "game-wordwrap" / "archive" / "ru-space-wrap.v13.lua"
HELPER_V13 = HELPER
# Синтетический общий чанк: настоящий UIPrefabClass без движка не выполнить, но помощник и выставление в _G те же.
SHARED_SOURCE = 'local UIPrefabClass = DeclareClass("UIPrefabClass")\n\nfunction UIPrefabClass:ctor()\n    self.x = false\nend\n\nreturn UIPrefabClass\n'

DIRECT_SOURCE = """---@class UIDemoCtlClass : UIDemoCtlClass_AutoBind
local UIDemoCtlClass = DeclareClass("UIDemoCtlClass", import("UIDemoCtl_AutoBind"))

function UIDemoCtlClass:vOnUninitialize()
    self.keep = nil
end

function UIDemoCtlClass:Refresh(data)
    self.title.text =Localize.GetString("Key", data.a, data.b)
    self.body.text = data.Desc -- comment
    if data.flag then
        self.body.text = ""
    end
    UIUtility:SetTextByContent(self.title, data.Name)
    self.body.text = data.Desc -- comment
    self.title.text = self.title.text .. "x"
end

return UIDemoCtlClass
"""

LOOP_SOURCE = """---@class UIDemoLoopClass : UIDemoLoopClass_AutoBind
local UIDemoLoopClass = DeclareClass("UIDemoLoopClass", import("UIDemoLoop_AutoBind"))

function UIDemoLoopClass:vOnUninitialize()
    self.keep = nil
end

function UIDemoLoopClass:ModifyContent(data)
    local texts = {self.age, self.identity}
    for i = 1, #texts do
        if #data >= i then
            if i >= 2 then
                texts[i].text = data[i].Title .. "：" .. data[i].Desc
            else
                texts[i].text = data[i].Desc
            end
        end
    end
end

return UIDemoLoopClass
"""


def with_targets(rid="aaaaaaaaaaaaaaaa", module="UIDemoClass_AutoBind", fields=("title", "body"), diag_field=None, direct=False):
    """Подставляет синтетический ресурс в TARGETS на время вызова patch_lua."""
    class Scope:
        def __enter__(self):
            self.saved = dict(blp.TARGETS), dict(blp.DIAG_FIELDS), blp.DIRECT
            blp.TARGETS[rid] = (module, tuple(fields))
            if direct:
                blp.DIRECT = frozenset(blp.DIRECT | {rid})
            if diag_field:
                blp.DIAG_FIELDS[rid] = diag_field
            return rid

        def __exit__(self, *exc):
            blp.TARGETS.clear()
            blp.TARGETS.update(self.saved[0])
            blp.DIAG_FIELDS.clear()
            blp.DIAG_FIELDS.update(self.saved[1])
            blp.DIRECT = self.saved[2]
    return Scope()


class coverage_scope:
    """Общее покрытие входит в рабочую сборку всегда; класс оставлен как пустая обёртка для читаемости тестов покрытия."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PatchLuaTest(unittest.TestCase):
    def test_lf_source_gets_calls_after_assignments_and_restore(self):
        with with_targets() as rid:
            payload, edits = blp.patch_lua(SOURCE.encode(), rid, False, HELPER)
        text = payload.decode()
        self.assertTrue(payload.startswith(pc.LUA_PAYLOAD_MARKER))
        self.assertIn("    self.title.text = arg\n    _AceRuWrap(self.title, arg)\n", text)
        self.assertIn("    self.body.text = arg\n    _AceRuWrap(self.body, arg)\n", text)
        self.assertIn(":vOnResourceUnLoaded()\n    pcall(_AceRuWrapRestore, self.title)\n    pcall(_AceRuWrapRestore, self.body)\n", text)
        self.assertEqual([e["id"] for e in edits], ["space-wrap-title", "space-wrap-body", "restore-own-text-fields-on-native-unload"])
        self.assertNotIn("_AceRuMark(self", text)

    def test_crlf_and_mixed_endings_keep_their_own_line_ending(self):
        crlf = SOURCE.replace("\n", "\r\n")
        mixed = SOURCE.replace("self.body.text = arg\n", "self.body.text = arg\r\n")
        for source in (crlf, mixed):
            with with_targets() as rid:
                payload, _ = blp.patch_lua(source.encode(), rid, False, HELPER)
            text = payload.decode()
            ending = "\r\n" if source is crlf else "\n"
            self.assertIn("    self.title.text = arg" + ending + "    _AceRuWrap(self.title, arg)" + ending, text)
            self.assertIn("    self.body.text = arg\r\n    _AceRuWrap(self.body, arg)\r\n", text)

    def test_diag_marker_only_in_diag_build(self):
        with with_targets(diag_field="title") as rid:
            plain, _ = blp.patch_lua(SOURCE.encode(), rid, False, HELPER)
            diag, edits = blp.patch_lua(SOURCE.encode(), rid, True, HELPER)
        self.assertNotIn(b"_AceRuMark(self", plain)
        self.assertIn("    _AceRuMark(self.title, _AceRuWrap(self.title, arg))\n", diag.decode())
        self.assertIn("diag-mark-title", [e["id"] for e in edits])
        with with_targets(fields=("body",), diag_field="title") as rid:
            diag_only, _ = blp.patch_lua(SOURCE.encode(), rid, True, HELPER)
        self.assertIn("    self.title.text = arg\n    _AceRuMark(self.title)\n", diag_only.decode())
        self.assertNotIn("_AceRuWrap(self.title", diag_only.decode())

    def test_missing_duplicate_or_foreign_assignment_refused(self):
        cases = {
            "missing": SOURCE.replace("self.body.text = arg", "self.bodyX.text = arg"),
            "duplicate": SOURCE.replace("-- body", "function UIDemoClass_AutoBind:SetBody2(arg)\n    self.body.text = arg\nend\n-- body"),
            "outside setter": SOURCE.replace("function UIDemoClass_AutoBind:SetBody(arg)", "function UIDemoClass_AutoBind:Refresh(arg)\n    self.keep = 1\nend\nlocal function helper(arg)"),
            "no unload": SOURCE.replace("vOnResourceUnLoaded", "vOnResourceOther"),
            "other module": SOURCE.replace('DeclarePrivateClass("UIDemoClass_AutoBind"', 'DeclarePrivateClass("UIOther_AutoBind"'),
            "bom": "\ufeff" + SOURCE,
        }
        for name, source in cases.items():
            with self.subTest(name), with_targets() as rid:
                with self.assertRaises(blp.LuaPackageError):
                    blp.patch_lua(source.encode(), rid, False, HELPER)

    def test_direct_controller_gets_calls_after_every_assignment_and_restore_in_uninitialize(self):
        with with_targets(module="UIDemoCtlClass", direct=True) as rid:
            payload, edits = blp.patch_lua(DIRECT_SOURCE.encode(), rid, False, HELPER)
        text = payload.decode()
        self.assertIn('Key", data.a, data.b)\n    _AceRuWrap(self.title)\n', text)
        self.assertIn("-- comment\n    _AceRuWrap(self.body)\n", text)
        self.assertIn("SetTextByContent(self.title, data.Name)\n    _AceRuWrap(self.title)\n", text)
        self.assertEqual(text.count("_AceRuWrap(self.body)"), 2)
        self.assertEqual(text.count("_AceRuWrap(self.title)"), 2)
        self.assertNotIn('= ""\n        _AceRuWrap', text)
        self.assertNotIn('.. "x"\n    _AceRuWrap', text)
        self.assertIn(":vOnUninitialize()\n    pcall(_AceRuWrapRestore, self.title)\n    pcall(_AceRuWrapRestore, self.body)\n", text)
        self.assertEqual(edits[-1]["id"], "restore-own-text-fields-on-uninitialize")

    def test_direct_controller_refused_without_site_or_hook(self):
        cases = {
            "no site": DIRECT_SOURCE.replace("self.body.text = data.Desc", "self.bodyX.text = data.Desc"),
            "no hook": DIRECT_SOURCE.replace("vOnUninitialize", "vOnOther"),
            "other module": DIRECT_SOURCE.replace('DeclareClass("UIDemoCtlClass"', 'DeclareClass("UIOther"'),
        }
        for name, source in cases.items():
            with self.subTest(name), with_targets(module="UIDemoCtlClass", direct=True) as rid:
                with self.assertRaises(blp.LuaPackageError):
                    blp.patch_lua(source.encode(), rid, False, HELPER)

    def test_loop_controller_gets_call_after_each_texts_assignment_and_restore(self):
        fields = ("age", "identity")
        with with_targets(module="UIDemoLoopClass", fields=fields, direct=True) as rid:
            saved = blp.LOOP_TEXTS
            blp.LOOP_TEXTS = frozenset(saved | {rid})
            try:
                payload, edits = blp.patch_lua(LOOP_SOURCE.encode(), rid, False, HELPER)
                text = payload.decode()
                self.assertIn("texts[i].text = data[i].Desc\n                _AceRuWrap(texts[i])\n", text)
                self.assertIn('Title .. "：" .. data[i].Desc\n                _AceRuWrap(texts[i])\n', text)
                self.assertEqual(text.count("_AceRuWrap(texts[i])"), 2)
                self.assertIn(":vOnUninitialize()\n    pcall(_AceRuWrapRestore, self.age)\n    pcall(_AceRuWrapRestore, self.identity)\n", text)
                self.assertEqual([e["id"] for e in edits], ["space-wrap-texts-loop-2", "space-wrap-texts-loop-1", "restore-own-text-fields-on-uninitialize"])
                crlf, _ = blp.patch_lua(LOOP_SOURCE.replace("\n", "\r\n").encode(), rid, False, HELPER)
                self.assertIn("_AceRuWrap(texts[i])\r\n", crlf.decode())
                cases = {
                    "no list": LOOP_SOURCE.replace("local texts = {self.age, self.identity}", "local other = {self.age, self.identity}"),
                    "no site": LOOP_SOURCE.replace("texts[i].text", "texts[i].txt"),
                    "no hook": LOOP_SOURCE.replace("vOnUninitialize", "vOnOther"),
                    "field twice": LOOP_SOURCE.replace("self.keep = nil", "self.age = nil"),
                    "other module": LOOP_SOURCE.replace('DeclareClass("UIDemoLoopClass"', 'DeclareClass("UIOther"'),
                }
                for name, source in cases.items():
                    with self.subTest(name), self.assertRaises(blp.LuaPackageError):
                        blp.patch_lua(source.encode(), rid, False, HELPER)
            finally:
                blp.LOOP_TEXTS = saved

    def test_targets_match_the_contract_allowlist(self):
        # Белый список = инлайн-цели + покрытие + общий чанк + отдельные layout/numeric/activity ресурсы.
        self.assertEqual(set(blp.ALL_IDS), set(pc.LUA_REPLACEMENT_ALLOWED[blp.PACKAGE]))
        self.assertEqual(set(blp.ALL_IDS), set(blp.TARGETS) | set(blp.COVERAGE) | set(blp.HEADER_TARGETS) | set(blp.NUMERIC_TARGETS) | set(blp.ACTIVITY_TARGETS) | {blp.SHARED_ID})
        self.assertFalse(set(blp.TARGETS) & set(blp.COVERAGE))
        families = [set(blp.TARGETS), set(blp.COVERAGE), set(blp.HEADER_TARGETS), set(blp.NUMERIC_TARGETS), set(blp.ACTIVITY_TARGETS), {blp.SHARED_ID}]
        self.assertEqual(sum(map(len, families)), len(blp.ALL_IDS))
        self.assertEqual(len(blp.TARGETS), 212)
        self.assertEqual(len(blp.ALL_IDS), 1924)
        self.assertTrue(set(blp.DIAG_FIELDS) <= set(blp.TARGETS))
        self.assertIn("title", blp.TARGETS["aeff176f093cf41d"][1])

    def test_activity_build_refuses_changed_current_config_before_patching_lua(self):
        rid = next(iter(blp.ACTIVITY_TARGETS))
        with mock.patch.object(blp.bp, "read_original", return_value=(b"changed cfg", Path("1_0.db"))) as read:
            with mock.patch.object(blp.activity_display, "patch_resource", side_effect=AssertionError("cfg must be verified first")):
                with self.assertRaisesRegex(blp.LuaPackageError, "SHA текущего ActivityHelp cfg"):
                    blp.read_resources(GAME, False, only={rid})
        read.assert_called_once_with(GAME, "1", "b5d856bfdf22400d")

    def test_covered_module_gets_short_call_and_restore_without_helper(self):
        payload, edits = blp.patch_covered(SOURCE.encode(), "aaaaaaaaaaaaaaaa", "UIDemoClass_AutoBind", ("title", "body"))
        text = payload.decode()
        self.assertTrue(payload.startswith(pc.LUA_PAYLOAD_MARKER))
        self.assertNotIn("_AceRuSpaceWrap", text)
        self.assertNotIn("local function", text)
        call = 'if rawget(_G, "_AceRuWrap") then pcall(_G._AceRuWrap, self.{}, arg) end'
        self.assertIn("    self.title.text = arg\n    " + call.format("title") + "\n", text)
        self.assertIn("    self.body.text = arg\n    " + call.format("body") + "\n", text)
        self.assertEqual(text.count("_G._AceRuWrap,"), 2)
        self.assertIn(":vOnResourceUnLoaded()\n    if rawget(_G, \"_AceRuWrapRestore\") then pcall(_G._AceRuWrapRestore, self.title) end\n", text)
        self.assertEqual([e["id"] for e in edits], ["shared-wrap-title", "shared-wrap-body", "shared-restore-on-native-unload"])
        # Обратная правка: убрать маркер и вставленные строки - оригинал побайтно.
        body = text.split("\n", 1)[1]
        kept = [line for line in body.split("\n") if "_G._AceRuWrap" not in line]
        self.assertEqual("\n".join(kept), SOURCE)

    def test_covered_crlf_keeps_its_line_ending(self):
        payload, _ = blp.patch_covered(SOURCE.replace("\n", "\r\n").encode(), "aaaaaaaaaaaaaaaa", "UIDemoClass_AutoBind", ("title",))
        text = payload.decode()
        self.assertTrue(text.startswith(pc.LUA_PAYLOAD_MARKER.decode() + "\r\n"))
        self.assertIn("self.title.text = arg\r\n    if rawget(_G, \"_AceRuWrap\") then", text)
        self.assertNotIn("\r\r", text)

    def test_covered_refuses_missing_duplicate_foreign_or_unhooked(self):
        cases = {
            "missing": SOURCE.replace("self.body.text = arg", "self.bodyX.text = arg"),
            "duplicate": SOURCE.replace("-- body", "function UIDemoClass_AutoBind:SetBody2(arg)\n    self.body.text = arg\nend\n-- body"),
            "outside setter": SOURCE.replace("function UIDemoClass_AutoBind:SetBody(arg)", "function UIDemoClass_AutoBind:Refresh(arg)\n    self.keep = 1\nend\nlocal function helper(arg)"),
            "no unload": SOURCE.replace("vOnResourceUnLoaded", "vOnResourceOther"),
            "other module": SOURCE.replace('DeclarePrivateClass("UIDemoClass_AutoBind"', 'DeclarePrivateClass("UIOther_AutoBind"'),
            "bom": "﻿" + SOURCE,
        }
        for name, source in cases.items():
            with self.subTest(name), self.assertRaises(blp.LuaPackageError):
                blp.patch_covered(source.encode(), "aaaaaaaaaaaaaaaa", "UIDemoClass_AutoBind", ("title", "body"))

    def test_shared_chunk_defines_helper_once_and_exports_it(self):
        payload = blp.patch_shared(SHARED_SOURCE.encode(), HELPER_V13)
        text = payload.decode()
        self.assertTrue(payload.startswith(pc.LUA_PAYLOAD_MARKER))
        self.assertEqual(text.count("local function _AceRuSpaceWrap("), 1)
        self.assertTrue(text.endswith(SHARED_SOURCE))
        self.assertLess(text.index('rawset(_G, "_AceRuWrap", _AceRuWrap)'), text.index("DeclareClass(\"UIPrefabClass\")"))
        self.assertLess(text.index("local function _AceRuWrap("), text.index('rawset(_G, "_AceRuWrap", _AceRuWrap)'))
        self.assertIn('rawset(_G, "_AceRuWrapRestore", _AceRuWrapRestore)', text)
        for bad in (SHARED_SOURCE.replace("UIPrefabClass", "Other"), "﻿" + SHARED_SOURCE):
            with self.assertRaises(blp.LuaPackageError):
                blp.patch_shared(bad.encode(), HELPER_V13)

    def test_shared_diag_chunk_exports_the_diag_wrapper(self):
        with coverage_scope():
            helper = blp.helper_text(True).replace(blp.DIAG_MODULE_PLACEHOLDER, "UIPrefabClass-shared").replace(blp.DIAG_RESOURCE_PLACEHOLDER, "3482bf89614b5abb")
        text = blp.patch_shared(SHARED_SOURCE.encode(), helper).decode()
        self.assertGreater(text.index('rawset(_G, "_AceRuWrap", _AceRuWrap)'), text.index("local _AceRuWrapBase = _AceRuWrap"))
        self.assertIn('local _AceRuDiagModule = "UIPrefabClass-shared"', text)

    def test_shared_chunk_is_the_base_ui_class_and_coverage_is_consistent(self):
        with coverage_scope():
            self.assertEqual(blp.SHARED_ID, "3482bf89614b5abb")
            self.assertEqual(blp.SHARED_MODULE, "UIPrefabClass")
            self.assertGreater(len(blp.COVERAGE), 1000)
            for rid, (module, fields) in blp.COVERAGE.items():
                self.assertRegex(rid, r"^[0-9a-f]{16}$")
                self.assertTrue(module.endswith("_AutoBind"))
                self.assertTrue(fields and len(set(fields)) == len(fields))
                self.assertNotRegex(module, r"(?i)^Talk|Chat|InputField")
                for field in fields:
                    self.assertNotRegex(field, r"(?i)nick|playername|rolename|username|chat|talk|input|password|token")
            self.assertEqual(set(blp.COVERAGE_PINS), set(blp.COVERAGE) | {blp.SHARED_ID})

    def test_helper_exposes_every_name_the_builder_calls(self):
        for helper in (HELPER, HELPER_V13):
            for name in ("_AceRuSpaceWrap", "_AceRuWrapRestore", "_AceRuWrap", "_AceRuMark"):
                self.assertIn(f"local function {name}(", helper)

    def test_plain_helper_is_untouched_and_keeps_the_pinned_sha(self):
        self.assertEqual(blp.helper_text(False), HELPER)
        self.assertEqual(blp.helper_sha_of(False), HELPER_SHA)
        self.assertEqual(blp.helper_sha_of(False), blp.sha_bytes(blp.HELPER.read_bytes()))
        self.assertNotIn("__AceRuDiag", HELPER)

    def test_archived_helpers_keep_their_pinned_sha(self):
        archive = HELPER_V13_PATH.parent
        self.assertEqual(blp.sha_bytes(HELPER_V13_PATH.read_bytes()), HELPER_V13_SHA)
        self.assertEqual(blp.sha_bytes((archive / "ru-space-wrap.v11.lua").read_bytes()), HELPER_V11_SHA)

    def test_diag_log_helper_is_base_plus_layer_with_placeholders(self):
        template = blp.helper_text(True)
        layer = blp.DIAG_LAYER.read_text("utf-8")
        self.assertTrue(template.startswith(HELPER.rstrip("\n")))
        self.assertTrue(template.endswith(layer.rstrip("\n") + "\n"))
        self.assertIn(blp.DIAG_MODULE_PLACEHOLDER, template)
        self.assertIn(blp.DIAG_RESOURCE_PLACEHOLDER, template)
        self.assertNotEqual(blp.helper_sha_of(True), blp.helper_sha_of(False))
        self.assertEqual(blp.helper_sha_of(True), blp.sha_bytes(template.encode("utf-8")))
        self.assertIn("ace-ru-diag.log", layer)
        self.assertNotIn("ace-ru-diag", HELPER)

    def test_diag_log_payload_gets_layer_after_helper_and_is_reversible(self):
        with with_targets() as rid:
            helper = blp.helper_text(True).replace(blp.DIAG_MODULE_PLACEHOLDER, "UIDemoClass_AutoBind").replace(blp.DIAG_RESOURCE_PLACEHOLDER, rid)
            payload, edits = blp.patch_lua(SOURCE.encode(), rid, False, helper)
        text = payload.decode()
        self.assertTrue(payload.startswith(pc.LUA_PAYLOAD_MARKER))
        self.assertNotIn("@@AceRuDiag", text)
        self.assertIn('local _AceRuDiagModule = "UIDemoClass_AutoBind"', text)
        self.assertIn(f'local _AceRuDiagResource = "{rid}"', text)
        self.assertLess(text.index("local function _AceRuSpaceWrap("), text.index("local _AceRuWrapBase = _AceRuWrap"))
        self.assertLess(text.index("local _AceRuWrapBase = _AceRuWrap"), text.index("    self.title.text = arg\n    _AceRuWrap(self.title, arg)\n"))
        self.assertEqual([e["id"] for e in edits], ["space-wrap-title", "space-wrap-body", "restore-own-text-fields-on-native-unload"])

    def test_diag_log_flag_is_separate_from_diag_marker(self):
        self.assertEqual(blp.main(["--output", str(PROJECT / "build" / "carrier-x"), "--diag-log", "--check"]), 2)

    def test_output_directory_must_be_lua_wrap_inside_build(self):
        self.assertEqual(blp.main(["--output", str(PROJECT / "build" / "carrier-x"), "--check"]), 2)
        self.assertEqual(blp.main(["--output", str(PROJECT / "x" / "lua-wrap-x"), "--check"]), 2)


@unittest.skipUnless(DLL.is_file(), "GameCore.dll (Lua 5.4) нужна для синтаксиса и поведения")
class LuaStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from validate_game_formatting import LuaState
        cls.state_class = LuaState

    def lua(self):
        lua = self.state_class(DLL)
        self.addCleanup(lua.close)
        return lua

    def test_helper_behaviour_fixtures(self):
        fixture = FIXTURES.read_text("utf-8").replace("-- {{HELPER}}", HELPER)
        count = int(self.lua().load(fixture.encode(), "ru_space_wrap_fixtures", True))
        self.assertGreater(count, 100)

    def test_synthetic_module_runs_wraps_and_restores(self):
        with with_targets(diag_field="title") as rid:
            payload, _ = blp.patch_lua(SOURCE.encode(), rid, True, HELPER)
        self.assertEqual(run_module(self.lua(), payload.decode(), "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"], diag="title"), "ok")

    def covered_case(self):
        shared = blp.patch_shared(SHARED_SOURCE.encode(), HELPER_V13).decode()
        payload, _ = blp.patch_covered(SOURCE.encode(), "aaaaaaaaaaaaaaaa", "UIDemoClass_AutoBind", ("title", "body"))
        return shared, payload.decode()

    def test_covered_module_wraps_through_the_shared_helper_and_restores(self):
        shared, payload = self.covered_case()
        self.assertEqual(run_module(self.lua(), payload, "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"], shared=shared), "ok")

    def test_covered_module_preserves_native_best_fit_for_composite_captions(self):
        shared, payload = self.covered_case()
        self.assertEqual(run_module(self.lua(), payload, "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"],
                                    shared=shared, wrapped=False, native_best_fit=True), "ok")

    def test_covered_module_with_shared_diag_helper_runs(self):
        with coverage_scope():
            helper = blp.helper_text(True).replace(blp.DIAG_MODULE_PLACEHOLDER, "UIPrefabClass-shared").replace(blp.DIAG_RESOURCE_PLACEHOLDER, "3482bf89614b5abb")
        shared = blp.patch_shared(SHARED_SOURCE.encode(), helper).decode()
        _, payload = self.covered_case()
        self.assertEqual(run_module(self.lua(), payload, "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"], shared=shared), "ok")

    def test_covered_module_is_fail_closed_without_the_shared_helper(self):
        _, payload = self.covered_case()
        self.assertEqual(run_module(self.lua(), payload, "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"], wrapped=False), "ok")

    def test_covered_module_survives_a_raising_helper(self):
        _, payload = self.covered_case()
        self.assertEqual(run_module(self.lua(), payload, "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"], wrapped=False, broken=True), "ok")

    def test_diag_log_layer_fixtures(self):
        with coverage_scope():   # фикстуры рабочего помощника v15 и диагностического слоя
            template = blp.helper_text(True)   # подстановки модуля и ресурса делает сам стенд (loadChunk)
        fixture = DIAG_FIXTURES.read_text("utf-8").replace("-- {{HELPER}}", template)
        count = int(self.lua().load(fixture.encode(), "ru_space_wrap_diag_fixtures", True))
        self.assertGreater(count, 40)

    def test_synthetic_module_with_diag_log_runs_wraps_and_logs(self):
        with with_targets() as rid:
            helper = blp.helper_text(True).replace(blp.DIAG_MODULE_PLACEHOLDER, "UIDemoClass_AutoBind").replace(blp.DIAG_RESOURCE_PLACEHOLDER, rid)
            payload, _ = blp.patch_lua(SOURCE.encode(), rid, False, helper)
        self.assertEqual(run_module(self.lua(), payload.decode(), "UIDemoClass_AutoBind", ["SetTitle", "SetBody"], ["title", "body"], diag_log=True), "ok")


def run_module(lua, payload: str, module: str, setters: list[str], fields: list[str], diag: str | None = None, diag_log: bool = False,
               shared: str | None = None, wrapped: bool = True, broken: bool = False, others: list[str] | None = None,
               native_best_fit: bool = False, uninitialize: bool = False) -> str:
    """Выполняет настоящий Lua-модуль с заглушками движка: setters переносят текст, выгрузка возвращает исходный вид.

    io закрыт (журнал помощника не пишет в настоящий %TEMP%); диагностический слой уходит в заглушку CS.UnityEngine.Debug.Log.
    shared - нагрузка общего чанка, выполняется до модуля (выставляет _G._AceRuWrap); wrapped=False - ждём штатный текст (помощника в _G нет);
    broken - вместо помощника в _G лежит функция, которая бросает исключение (вызов обязан остаться безопасным)."""
    shared_load = ""
    if shared is not None:
        shared_load = 'DeclareClass = function(name) return { __name = name } end\nassert(load([=====[' + shared + ']=====], "UIPrefabClass"))()'
    if broken:
        shared_load = 'rawset(_G, "_AceRuWrap", function() error("boom") end)\nrawset(_G, "_AceRuWrapRestore", function() error("boom") end)'
    elif shared is None:
        shared_load = 'rawset(_G, "_AceRuWrap", nil)\nrawset(_G, "_AceRuWrapRestore", nil)'
    wrap_checks = '''for _, name in ipairs(names) do
    local node = self[name]
    assert(node.text:find("\\n", 1, true), name .. " was not wrapped: " .. tostring(node.text))
    assert(node.horizontalOverflow == 1 and node.resizeTextForBestFit == false, name .. " style")
    assert(widthOf((node.text:gsub("·%w?·?$", ""))) <= 199, name .. " fits the line")
end''' if wrapped else '''for _, name in ipairs(names) do
    local node = self[name]
    assert(node.text == INPUT and node.horizontalOverflow == 0 and node.resizeTextForBestFit == NATIVE_BEST_FIT, name .. " keeps the native text")
end'''
    names = ", ".join(f'"{f}"' for f in fields)
    others_lua = ", ".join(f'"{f}"' for f in (others or []))
    calls = "\n".join(f'    cls.{s}(self, INPUT)' for s in setters)
    uninitialize_check = f'''
assert(type(cls.vOnUninitialize) == "function", "uninitialize hook is missing")
local previousSuper = rawget(_G, "super")
super = {{ vOnUninitialize = function() end }}
self.DestroyAllChildPrefabClass = function() end
self._prefab = stub()
cls.vOnUninitialize(self)
for _, name in ipairs(names) do
    local node = self[name]
    assert(node.text == INPUT and node.horizontalOverflow == 0 and node.resizeTextForBestFit == NATIVE_BEST_FIT, name .. " restored on uninitialize")
end
{calls}
{wrap_checks}
rawset(_G, "super", previousSuper)
''' if uninitialize else ""
    log_check = f'''
local sawChunk, sawCall = false, false
for _, line in ipairs(diagLines) do
    assert(line:find("^%[ace%-ru%-diag%] ") ~= nil, "diag line prefix")
    if line:find(" chunk {module} ", 1, true) then sawChunk = true end
    if line:find(" call {module} ", 1, true) then sawCall = true end
    assert(not line:find("Увеличивает", 1, true), "diag log must not carry field text")
end
assert(sawChunk and sawCall, "diag log has chunk and call lines of {module}")
''' if diag_log else ""
    script = f'''
INPUT = "{INPUT}"
NATIVE_BEST_FIT = {str(native_best_fit).lower()}
Localize = {{ GetString = function(key) return "Настройки" end }}
CS = {{ UnityEngine = {{ HorizontalWrapMode = {{ Wrap = 0, Overflow = 1 }}, UI = {{ LayoutRebuilder = {{ ForceRebuildLayoutImmediate = function() end }} }} }} }}
ClassLib = {{ UIPrefabClass = {{}} }}
io = nil
print = function() end
-- состояние диагностического слоя общее на весь Lua-state (счётчики лимитов, последний ключ повтора): каждый модуль начинает с чистого
for _, key in ipairs({{ "__AceRuDiagEnv", "__AceRuDiagOpened", "__AceRuDiagFolder", "__AceRuDiagChunkN", "__AceRuDiagCallN", "__AceRuDiagErrN",
    "__AceRuDiagProbeN", "__AceRuDiagExpN", "__AceRuDiagRepN", "__AceRuDiagSweepN", "__AceRuDiagLastKey", "__AceRuDiagRepeat", "__AceRuDiagSweepLast",
    "__AceRuDiagSweepAt", "__AceRuDiagSweepOffset", "__AceRuDiagAssetDone", "__AceRuDiagExpDone", "__AceRuDiagProbed", "__AceRuDiagProbeTries" }}) do
    rawset(_G, key, nil)
end
local diagLines = {{}}
CS.UnityEngine.Debug = {{ Log = function(line) diagLines[#diagLines + 1] = line end }}
_IMPORT_ARG = nil
import = function() return {{}} end
DeclarePrivateClass = function(name, base) return {{ __name = name }} end
EnumBegin = function() end
NextEnum = function() return 0 end
EnumEnd = function(value) return value end
auto_bind = function() end
local function widthOf(text)
    text = text:gsub("<[^>]*>", "")
    local width, maximum = 0, 0
    for _, code in utf8.codes(text) do
        if code == 10 then maximum = math.max(maximum, width); width = 0 elseif code == 32 then width = width + 4 else width = width + 8 end
    end
    return math.max(maximum, width)
end
local function newNode()
    local values = {{ text = "", font = {{}}, fontSize = 20, fontStyle = 0, textStyle = 2, lineSpacing = 1, horizontalOverflow = 0, resizeTextForBestFit = NATIVE_BEST_FIT }}
    local node = {{ rect = {{ width = 200, height = 600 }} }}
    function node:GetRectTransform() return {{ rect = self.rect, owner = self }} end
    node.rectTransform = {{ rect = node.rect, owner = node }}
    return setmetatable(node, {{
        __index = function(n, key)
            if key == "preferredWidth" then return widthOf(values.text) end
            if key == "preferredHeight" then local _, lines = values.text:gsub("\\n", ""); return (lines + 1) * 22 end
            return values[key]
        end,
        __newindex = function(n, key, value) values[key] = value end,
    }})
end
{shared_load}
local chunk = assert(load([=====[{payload}]=====], "{module}"))
local cls = chunk()
assert(type(cls) == "table" and type(cls.vOnResourceUnLoaded) == "function", "module did not return its class")
local self = setmetatable({{}}, {{ __index = cls }})
local names = {{ {names} }}
for _, name in ipairs(names) do self[name] = newNode() end
-- остальные поля модуля (кнопки, узлы без обёртки): заглушка, принимающая любые обращения
local function stub() return setmetatable({{}}, {{ __index = function() return stub() end, __call = function() return stub() end }}) end
for _, name in ipairs({{ {others_lua} }}) do self[name] = stub() end
{calls}
{wrap_checks}
{('assert(self[%r].text:sub(-2) == "··" or self[%r].text:sub(-2) == "·" or self[%r].text:find("·", 1, true), "diagnostic mark")' % (diag, diag, diag)) if diag else ''}
{uninitialize_check}
local kept = {{}}
for _, name in ipairs(names) do kept[name] = self[name] end
cls.vOnResourceUnLoaded(self)
for _, name in ipairs(names) do
    assert(self[name] == false, name .. " reference cleared by the original code")
    assert(kept[name].text == INPUT and kept[name].horizontalOverflow == 0 and kept[name].resizeTextForBestFit == NATIVE_BEST_FIT, name .. " restored")
end
{log_check}
return "ok"
'''
    return lua.load(script.encode(), module, True)


def setter_fields(text: str, module: str, field_names) -> tuple[list[str], list[str]]:
    """Setters модуля, присваивающие нужные поля, и все поля, которые такие setters трогают."""
    setters, fields = [], []
    for match in re.finditer(r"^function " + re.escape(module) + r":(Set\w+)\(arg\)\r?\n(.*?)^end", text, re.M | re.S):
        used = re.findall(r"self\.(\w+)\.text = arg", match.group(2))
        if set(used) & set(field_names):
            setters.append(match.group(1))
            fields += [f for f in used if f not in fields]
    return setters, fields


REAL_GAME_OK = (GAME / "WXQ_Data/Package/builtin/10/10_0.db").is_file() and DLL.is_file()


class _RealResources:
    """Все настоящие ресурсы кандидата: SHA оригиналов, обратимость правок, синтаксис и выполнение с заглушками движка.

    Основа для двух наборов: рабочая сборка 0.6.2 (v11, 211 инлайн-ресурсов) и архивная v12c/v13 с общим покрытием (coverage_scope)."""

    scope = None

    @classmethod
    def setUpClass(cls):
        from validate_game_formatting import LuaState
        cls._scope = cls.scope() if cls.scope else None
        if cls._scope:
            cls._scope.__enter__()
        try:
            cls.lua = LuaState(DLL)
            cls.plain = {r["id_hex"]: r for r in blp.read_resources(GAME, False)}
            # Диагностические сборки отличаются только у инлайн-целей и общего чанка: остальное (покрытие) читать повторно незачем.
            inline = set(blp.TARGETS) | ({blp.SHARED_ID} if blp.SHARED_ID else set())
            cls.diag = {r["id_hex"]: r for r in blp.read_resources(GAME, True, only=inline)}
            cls.dlog = {r["id_hex"]: r for r in blp.read_resources(GAME, False, True, only=inline)}
        except BaseException:
            if cls._scope:
                cls._scope.__exit__(None, None, None)
            raise

    @classmethod
    def tearDownClass(cls):
        cls.lua.close()
        if cls._scope:
            cls._scope.__exit__(None, None, None)

    def test_all_targets_are_present_with_pinned_originals(self):
        self.assertEqual(set(self.plain), set(blp.ALL_IDS))
        for rid, row in self.plain.items():
            self.assertEqual(row["original_sha256"], pc.LUA_REPLACEMENT_ALLOWED["10"][rid])
            self.assertTrue(row["payload"].startswith(pc.LUA_PAYLOAD_MARKER))
            self.assertNotIn(b"_AceRuMark(self", row["payload"])

    def test_activity_current_config_proof_is_read_only_and_separate_from_wrap_targets(self):
        expected = {"source_sha256": "b6577afb2715f2094d777e195a70f93cc9c10f045d96f6f93ac381236b705266",
                    "records": 115, "display_contexts": 128, "modified": False}
        self.assertFalse(blp.ACTIVITY_TARGETS & (set(blp.TARGETS) | set(blp.COVERAGE)))
        for rid in blp.ACTIVITY_TARGETS:
            row = self.plain[rid]
            self.assertEqual(row["activity_current_config_proof"], expected)
            self.assertEqual(row["fields"], [])
            self.assertNotIn(b"_AceRuWrap(self", row["payload"])
            self.assertEqual(blp.activity_display.revert_resource(row["payload"], rid), row["original"])
        self.assertEqual({row["package"] for row in self.plain.values()}, {"10"})
        self.assertNotIn("b5d856bfdf22400d", self.plain)

    def test_diag_build_marks_only_the_two_known_fields(self):
        for rid, row in self.diag.items():
            marks = row["payload"].count(b"_AceRuMark(self.")
            self.assertEqual(marks, 1 if rid in blp.DIAG_FIELDS else 0, rid)
        self.assertIn(b"_AceRuMark(self.title, _AceRuWrap(self.title, arg))", self.diag["aeff176f093cf41d"]["payload"])

    def test_diag_log_build_has_layer_with_own_module_and_no_marker(self):
        self.assertEqual(set(self.dlog), set(blp.TARGETS) | ({blp.SHARED_ID} if blp.SHARED_ID else set()))
        for rid, row in self.dlog.items():
            payload = row["payload"].decode("utf-8")
            self.assertNotIn("@@AceRuDiag", payload, rid)
            self.assertIn(f'local _AceRuDiagModule = "{blp.SHARED_MODULE + "-shared" if rid == blp.SHARED_ID else row["module"]}"', payload, rid)
            self.assertIn(f'local _AceRuDiagResource = "{rid}"', payload, rid)
            self.assertNotIn("_AceRuMark(self", payload, rid)
            self.assertNotEqual(row["sha256"], self.plain[rid]["sha256"], rid)
            self.assertEqual(row["original_sha256"], self.plain[rid]["original_sha256"], rid)
            self.lua.load(row["payload"], rid + ".dlog.lua")

    def test_every_diag_log_payload_wraps_restores_and_logs(self):
        for rid, row in self.dlog.items():
            if rid in blp.DIRECT or rid == blp.SHARED_ID:
                continue
            with self.subTest(resource=rid):
                text = row["payload"].decode("utf-8")
                setters, fields = setter_fields(text, row["module"], row["fields"])
                self.assertTrue(setters, "setter не найден")
                self.assertEqual(run_module(self.lua, text, row["module"], setters, fields, diag_log=True), "ok")

    def test_every_payload_and_original_compiles(self):
        for rows in (self.plain, self.diag):
            for rid, row in rows.items():
                self.lua.load(row["payload"], rid + ".lua")
                self.lua.load(row["original"], rid + ".orig.lua")

    def test_every_payload_wraps_and_restores_its_fields(self):
        for build, rows in (("plain", self.plain), ("diag", self.diag)):
            for rid, row in rows.items():
                if rid not in blp.TARGETS:
                    continue   # общий чанк и покрытые модули проверяют тесты покрытия
                with self.subTest(build=build, resource=rid):
                    text = row["payload"].decode("utf-8")
                    if rid in blp.DIRECT:
                        for field in row["fields"]:
                            if rid in blp.LOOP_TEXTS:
                                self.assertIn("_AceRuWrap(texts[i])", text)
                            else:
                                self.assertIn(f"_AceRuWrap(self.{field})", text)
                            self.assertIn(f"pcall(_AceRuWrapRestore, self.{field})", text)
                        continue
                    setters, fields = setter_fields(text, row["module"], row["fields"])
                    self.assertTrue(setters, "setter не найден")
                    self.assertTrue(set(row["fields"]) <= set(fields))
                    diag = blp.DIAG_FIELDS.get(rid) if build == "diag" else None
                    self.assertEqual(run_module(self.lua, text, row["module"], setters, fields, diag if diag in fields else None), "ok")

    def test_selected_voice_setter_uninitialize_reuse_and_native_unload_are_symmetric(self):
        rid = "f2e5aa01735bafb3"
        row = self.plain[rid]
        payload, edits = blp.patch_lua(row["original"], rid, False, HELPER)
        self.assertEqual(payload, row["payload"])
        self.assertEqual({edit["id"] for edit in edits}, {
            "space-wrap-voiceContent", "restore-own-text-fields-on-native-unload",
            "restore-own-text-fields-on-uninitialize",
        })
        ending = "\r\n" if b"\r\n" in row["original"] else "\n"
        prefix = blp.MARKER + ending + HELPER.replace("\n", ending) + ending
        self.assertTrue(payload.decode().startswith(prefix))
        body = payload.decode()[len(prefix):]
        for call, count in (("_AceRuWrap(self.voiceContent, arg)", 1), ("pcall(_AceRuWrapRestore, self.voiceContent)", 2)):
            line = "    " + call + ending
            self.assertEqual(body.count(line), count)
            body = body.replace(line, "")
        self.assertEqual(body.encode(), row["original"])
        self.assertEqual(run_module(self.lua, payload.decode(), row["module"], ["SetVoiceContent"], ["voiceContent"],
                                    others=["uiPersonaliseVoiceUseItem", "tuoDong", "deleted"], uninitialize=True), "ok")


@unittest.skipUnless(REAL_GAME_OK, "нужна установка WXQ(1) (только чтение)")
class RealResourcesTest(_RealResources, unittest.TestCase):
    """Помощник один раз в общем чанке, остальные 1702 модуля получают короткий вызов; 211 инлайн-ресурсов держат свою копию."""

    def test_helper_is_defined_once_in_the_shared_chunk_only(self):
        owners = [rid for rid, row in self.plain.items() if b"local function _AceRuSpaceWrap(" in row["payload"]]
        self.assertEqual(sorted(owners), sorted(set(blp.TARGETS) | {blp.SHARED_ID}))
        self.assertEqual(sum(row["payload"].count(b'rawset(_G, "_AceRuWrap", _AceRuWrap)') for row in self.plain.values()), 1)
        self.assertIn(b'rawset(_G, "_AceRuWrap", _AceRuWrap)', self.plain[blp.SHARED_ID]["payload"])
        for rid in blp.COVERAGE:
            self.assertNotIn(b"_AceRuSpaceWrap", self.plain[rid]["payload"], rid)

    def test_every_covered_module_has_one_call_and_one_restore_per_field_and_only_inserts(self):
        for rid, (module, fields) in blp.COVERAGE.items():
            row = self.plain[rid]
            text = row["payload"].decode("utf-8")
            original = row["original"].decode("utf-8")
            self.assertEqual(row["fields"], list(fields))
            for field in fields:
                self.assertEqual(text.count(blp.COVER_CALL.format(field=field)), 1, f"{rid} {field}")
                self.assertEqual(text.count(blp.COVER_RESTORE.format(field=field)), 1, f"{rid} {field}")
            body = text.split("\n", 1)[1]
            kept = [line for line in body.split("\n") if "_G._AceRuWrap" not in line]
            self.assertEqual("\n".join(kept), original, rid)

    def test_every_covered_payload_compiles_and_runs_through_the_shared_helper(self):
        shared = blp.patch_shared(SHARED_SOURCE.encode(), HELPER_V13).decode()
        for rid, (module, fields) in blp.COVERAGE.items():
            with self.subTest(resource=rid):
                text = self.plain[rid]["payload"].decode("utf-8")
                self.lua.load(self.plain[rid]["payload"], rid + ".lua")
                setters, used = setter_fields(text, module, fields)
                self.assertTrue(setters, "setter не найден")
                self.assertTrue(set(fields) <= set(used))
                # Проверяем только покрытые поля; прочие поля класса (в т.ч. не TimiText с `.text`) получают заглушки.
                declared = re.findall(r"^[ \t]+self\.(\w+)[ \t]*= false", text, re.M)
                others = [f for f in dict.fromkeys(declared + used) if f not in fields]
                self.assertEqual(run_module(self.lua, text, module, setters, list(fields), shared=shared, others=others), "ok")
                self.assertEqual(run_module(self.lua, text, module, setters, list(fields), wrapped=False, others=others), "ok")


if __name__ == "__main__":
    unittest.main()
