"""Общий заголовок: настоящий Lua шаблона и ограниченная модель штатной раскладки Unity."""
from __future__ import annotations

import os
import hashlib
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
sys.dont_write_bytecode = True

from validate_game_formatting import LuaState  # noqa: E402

GAME = Path(os.environ.get("WXQ_GAME_ROOT", r"C:\Program Files\WXQLauncher\WXQ(1)"))
DLL = GAME / "WXQ_Data/Plugins/x86_64/GameCore.dll"
FIXTURE = PROJECT / "tests/lua/header_layout_fixtures.lua"
ORIGINAL_DIR = os.environ.get("WXQ_HEADER_ORIGINALS")


class HeaderLayoutPatchTest(unittest.TestCase):
    def test_unknown_original_or_resource_is_refused(self):
        import build_header_layout_patch as patch
        with self.assertRaisesRegex(patch.HeaderLayoutError, "SHA оригинала"):
            patch.patch_resource(b"local changed = true\n", patch.BASE_ID)
        with self.assertRaisesRegex(patch.HeaderLayoutError, "не включён"):
            patch.patch_resource(b"local changed = true\n", "0000000000000000")

    def test_helper_source_compiles_in_shipped_lua(self):
        if not DLL.is_file():
            self.skipTest("Нет штатного Lua DLL")
        import build_header_layout_patch as patch
        state = LuaState(DLL)
        self.addCleanup(state.close)
        state.load(patch.HELPER.read_bytes(), "header-helper-syntax")


@unittest.skipUnless(DLL.is_file() and ORIGINAL_DIR, "Нужны штатный Lua DLL и WXQ_HEADER_ORIGINALS")
class HeaderLayoutLuaTest(unittest.TestCase):
    def fixture(self, patched: bool = False, extended: str = "") -> str:
        directory = Path(ORIGINAL_DIR)
        base = (directory / "5c97dddb5dd21ee9.lua").read_bytes()
        back = (directory / "1eaf50bae2c25fe8.lua").read_bytes()
        if patched:
            import build_header_layout_patch as patch
            base = patch.patch_resource(base, "5c97dddb5dd21ee9")[0]
            back = patch.patch_resource(back, "1eaf50bae2c25fe8")[0]
        # return Back ограничен вложенным чанком, как при штатном import игры.
        code = FIXTURE.read_text("utf-8")
        for placeholder, source in (("BASE", base), ("BACK", back)):
            wrapped = "assert(load(" + "[====[" + source.decode("utf-8") + "]====], '" + placeholder + "'))()"
            code = code.replace("-- {{" + placeholder + "_MODULE}}", wrapped)
        return code.replace("-- {{EXTENDED_CASES}}", extended)

    def lua(self) -> LuaState:
        state = LuaState(DLL)
        self.addCleanup(state.close)
        return state

    def test_real_original_reproduces_tg41(self):
        with self.assertRaisesRegex(RuntimeError, "TG41: Russian header overlaps"):
            self.lua().load(self.fixture().encode(), "header-original", True)

    def test_common_template_keeps_gap_and_native_typography(self):
        self.assertEqual(self.lua().load(self.fixture(True).encode(), "header-patched", True), "3")

    def run_cases(self, cases: str) -> int:
        return int(self.lua().load(self.fixture(True, cases).encode(), "header-patched-cases", True))

    def test_repeated_refresh_is_bounded_and_has_no_width_drift(self):
        count = self.run_cases('''
local api = rawget(_G, "_AceRuHeaderLayoutV1")
local beforeWidth = stack.rect.width
for _ = 1, 30 do
    local before = nativeCalls
    view:RefreshContentVisibility(EmFullScreenContentType.dropdown | EmFullScreenContentType.filter, nil, 0)
    check(nativeCalls - before <= 2, "A nested content refresh has at most two native rebuilds")
    check(stack.rect.width == beforeWidth and gap(view, dropdown) >= 24, "Refresh never accumulates extra width")
end
local beforeMethod = Back.RefreshContentVisibility
api.install(Back, { "RefreshContentVisibility" }, "vOnUninitialize")
api.install(Back, { "RefreshContentVisibility" }, "vOnUninitialize")
check(Back.RefreshContentVisibility == beforeMethod, "The same class is hooked only once")
check(marks > 0, "Native dirty layout is scheduled without a timer")
''')
        self.assertEqual(count, 65)

    def test_neighbour_filter_search_and_tick_share_the_layout_contract(self):
        count = self.run_cases('''
for _, mask in ipairs({ EmFullScreenContentType.filter, EmFullScreenContentType.tick, EmFullScreenContentType.searchFrame }) do
    local v, r, s = newView()
    -- Штатный поиск создаётся отдельно; в этом тесте показывается уже созданный Rect.
    function v:InitInputSearchUI() end
    refresh(v, "Список лучших игроков", mask)
    check(s.rect.width >= v.title.preferredWidth, "A neighbouring header reserves the measured title width")
    check(r.components.HorizontalLayoutGroup.spacing >= 24, "A neighbouring control keeps a readable gap")
    check(v.title.resizeTextForBestFit and v.title.fontSize == 36, "Neighbour typography is untouched")
end
''')
        self.assertEqual(count, 12)

    def test_native_geometry_and_typography_return_on_uninitialize(self):
        count = self.run_cases('''
view:vOnUninitialize()
check(stack.components.LayoutElement.preferredWidth == 180 and stack.components.LayoutElement.minWidth == 180,
    "Unload restores the original LayoutElement")
check(root.components.HorizontalLayoutGroup.spacing == 8 and stack.rect.width == 180, "Unload rebuilds the original layout")
check(view.title.text == "Матчи мастеров" and view.title.fontSize == 36 and view.title.resizeTextForBestFit,
    "Unload never edits text or font")
refresh(view, "Чемпионаты мастеров")
check(gap(view, dropdown) >= 24, "The same pooled screen can be used again")
check(stack.rect.width == view.title.preferredWidth, "A reused screen uses its new measured width")
''')
        self.assertEqual(count, 8)

    def test_original_language_or_non_cyrillic_header_restores_native_layout(self):
        count = self.run_cases('''
Localize.GetString = function() return "设置" end
refresh(view, "Матчи мастеров")
check(stack.components.LayoutElement.preferredWidth == 180 and root.components.HorizontalLayoutGroup.spacing == 8,
    "A Chinese locale restores the native values")
Localize.GetString = function() return "Настройки" end
refresh(view, "Master matches")
check(stack.rect.width == 180 and root.components.HorizontalLayoutGroup.spacing == 8,
    "A non-Cyrillic title keeps the native layout")
refresh(view, "大师对局")
check(stack.rect.width == 180 and view.title.text == "大师对局", "Chinese bytes survive pooled reuse")
refresh(view, "Матчи мастеров")
check(gap(view, dropdown) >= 24, "Russian layout returns after pooled locale reuse")
''')
        self.assertEqual(count, 7)

    def test_missing_horizontal_group_or_element_is_a_safe_noop(self):
        count = self.run_cases('''
local api = rawget(_G, "_AceRuHeaderLayoutV1")
for _, kind in ipairs({ "no-group", "vertical-group", "no-element", "separate-control", "no-title-rect" }) do
    local v, r, s, d = newView()
    if kind == "no-group" then r.components.HorizontalLayoutGroup = nil
    elseif kind == "vertical-group" then
        r.components.VerticalLayoutGroup = r.components.HorizontalLayoutGroup
        r.components.HorizontalLayoutGroup = nil
    elseif kind == "no-element" then s.components.LayoutElement = nil
    elseif kind == "separate-control" then d.parent = rect(nil, 1400, false)
    elseif kind == "no-title-rect" then v.title.rectTransform = nil end
    refresh(v, "Матчи мастеров", EmFullScreenContentType.dropdown)
    check(not s.components.LayoutElement or s.components.LayoutElement.preferredWidth == 180,
        "Missing layout binding never fabricates a geometry override: " .. kind)
    check(v.title.fontSize == 36 and v.title.resizeTextForBestFit and v.title.text == "Матчи мастеров",
        "A safe fallback retains the native field: " .. kind)
end
''')
        self.assertEqual(count, 13)

    def test_slow_native_measurement_uses_rebuild_instead_of_a_fixed_delay(self):
        count = self.run_cases('''
local v, r, s, d = newView()
function v:SetTitle(text)
    self.title.text = text
    self.title.preferredWidth = 0
    r.afterRebuild = function() self.title.preferredWidth = utf8.len(text) * 22 end
end
refresh(v)
check(s.rect.width >= v.title.preferredWidth and gap(v, d) >= 24, "A deferred native measure is read after rebuild")
check(v.title.fontSize == 36 and v.title.resizeTextForBestFit, "Slow rendering never changes the font")
''')
        self.assertEqual(count, 5)

    def test_large_header_does_not_push_controls_outside_the_available_row(self):
        count = self.run_cases('''
local api = rawget(_G, "_AceRuHeaderLayoutV1")
local v, r, s = newView()
refresh(v, string.rep("Мастера ", 15))
check(s.components.LayoutElement.preferredWidth == 180 and r.components.HorizontalLayoutGroup.spacing == 8,
    "A title larger than available space does not fabricate an offscreen layout")
check(api.refresh(v) == "no-space" and v.title.fontSize == 36 and v.title.resizeTextForBestFit,
    "An unsupported width fails closed without shrinking the font")
local v2, r2, s2, d2 = newView()
refresh(v2, "Матчи мастеров")
check(gap(v2, d2) >= 24 and s2.rect.width < r2.rect.width, "The next screen has its own independent geometry")
''')
        self.assertEqual(count, 6)

    def test_native_override_is_not_lost_when_our_values_are_restored(self):
        count = self.run_cases('''
local api = rawget(_G, "_AceRuHeaderLayoutV1")
stack.components.LayoutElement.preferredWidth = 444
api.restore(view)
check(stack.components.LayoutElement.preferredWidth == 444 and stack.rect.width == 444,
    "A new width from the game survives restore")
check(stack.components.LayoutElement.minWidth == 180 and root.components.HorizontalLayoutGroup.spacing == 8,
    "Only unchanged values owned by the patch are restored")
''')
        self.assertEqual(count, 5)

    def test_wrapper_keeps_multiple_return_values_and_original_errors(self):
        count = self.run_cases('''
local api = rawget(_G, "_AceRuHeaderLayoutV1")
local class = {
    update = function(self, x) self.received = x; return "first", nil, "last" end,
    fail = function() error("native-test-error", 0) end,
}
api.install(class, { "update", "fail" }, "unload")
local object = setmetatable({}, { __index = class })
local result = table.pack(object:update(42))
check(result.n == 3 and result[1] == "first" and result[2] == nil and result[3] == "last" and object.received == 42,
    "Native multi-return and arguments survive")
local ok, message = pcall(object.fail, object)
check(not ok and message == "native-test-error", "A native error is not suppressed or replaced")
''')
        self.assertEqual(count, 5)

    def test_real_resources_roundtrip_and_patch_idempotence(self):
        import build_header_layout_patch as patch
        for rid, row in patch.TARGETS.items():
            with self.subTest(rid=rid):
                original = (Path(ORIGINAL_DIR) / f"{rid}.lua").read_bytes()
                self.assertEqual(hashlib.sha256(original).hexdigest(), row["original_sha256"])
                payload, _ = patch.patch_resource(original, rid)
                self.assertEqual(patch.revert_resource(payload, rid), original)
                self.assertEqual(patch.patch_resource(payload, rid)[0], payload)
                with self.assertRaises(patch.HeaderLayoutError):
                    patch.revert_resource(payload + b"-- changed\n", rid)
                self.lua().load(payload, "header-resource-syntax-" + rid)


if __name__ == "__main__":
    unittest.main()
