"""Числовые подписи: реальные текущие Lua-ресурсы в отдельном штатном Lua 5.4."""
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

from validate_game_formatting import LuaState, function_span  # noqa: E402

GAME = Path(os.environ.get("WXQ_GAME_ROOT", r"C:\Program Files\WXQLauncher\WXQ(1)"))
DLL = GAME / "WXQ_Data/Plugins/x86_64/GameCore.dll"
ORIGINAL_DIR = os.environ.get("WXQ_NUMERIC_ORIGINALS")
FIXTURE = PROJECT / "tests/lua/numeric_display_v2_fixtures.lua"
LOCALIZE_ID = "06831ac11637ca73"
SHARE_ID = "b7039a45af1c9df7"
PROGRESS_ID = "58f61988158bf8fa"
CURRENT_SHA = {
    LOCALIZE_ID: "d99c2caa0fc56dd508752e3005b1c3bc73aa88d2ba91cd22e0264b3225f43de8",
    SHARE_ID: "870361dbabf57789e9c12463560824ab2ce92b0d9a38bfdddd46308fed4f7825",
}


def lua_chunk(source: bytes | str, name: str) -> str:
    text = source.decode("utf-8") if isinstance(source, bytes) else source
    if "]====]" in text:
        raise ValueError("Исходник содержит ограничитель тестового Lua-чанка")
    return "assert(load([====[" + text + "]====], " + json.dumps(name) + "))()"


class NumericDisplayPatchTest(unittest.TestCase):
    def test_display_policy_pins_exact_integer_fallback_and_unknown_fast_path(self):
        import build_numeric_display_patch as patch
        policy = {
            "compact_fraction_digits_max": 2,
            "trim_trailing_fraction_zeros": True,
            "overflow_fraction": "exact-grouped-base-integer",
            "group_separator": " ",
            "rounding_added": False,
            "native_share_quantum": 100,
            "unknown_key_fast_path": True,
        }
        self.assertEqual(patch.DISPLAY_POLICY, policy)
        metadata = patch.verify_metadata()
        self.assertEqual(metadata["display_policy"], policy)
        with tempfile.TemporaryDirectory(prefix="wxq-numeric-policy-test-") as directory:
            changed = Path(directory) / "changed-policy.json"
            for field, value in {
                "compact_fraction_digits_max": 3,
                "group_separator": "\u00a0",
                "rounding_added": True,
                "native_share_quantum": 1,
                "unknown_key_fast_path": False,
            }.items():
                with self.subTest(field=field):
                    altered = dict(metadata, display_policy=dict(policy, **{field: value}))
                    changed.write_text(json.dumps(altered, ensure_ascii=False), "utf-8")
                    with mock.patch.object(patch, "METADATA", changed):
                        with self.assertRaises(ValueError):
                            patch.verify_metadata()

    def test_only_two_current_resource_pins_are_authorized(self):
        import build_numeric_display_patch as patch
        self.assertEqual(set(patch.TARGETS), set(CURRENT_SHA))
        for rid, expected in CURRENT_SHA.items():
            self.assertEqual(patch.TARGETS[rid]["original_sha256"], expected)
        patch.verify_metadata()

    def test_unknown_original_or_resource_is_refused(self):
        import build_numeric_display_patch as patch
        with self.assertRaises(ValueError):
            patch.patch_resource(b"local changed = true\n", LOCALIZE_ID)
        with self.assertRaises(ValueError):
            patch.patch_resource(b"local changed = true\n", "0000000000000000")
        with self.assertRaises(ValueError):
            patch.revert_resource(b"local changed = true\n", LOCALIZE_ID)

    def test_changed_helper_or_metadata_is_refused_without_editing_project_files(self):
        import build_numeric_display_patch as patch
        with tempfile.TemporaryDirectory(prefix="wxq-numeric-metadata-test-") as directory:
            directory = Path(directory)
            helper = directory / "changed-helper.lua"
            helper.write_bytes(patch.HELPER.read_bytes() + b"\n-- changed\n")
            with mock.patch.object(patch, "HELPER", helper):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()
            metadata = json.loads(patch.METADATA.read_text("utf-8"))
            metadata["resources"][0]["original_sha256"] = "0" * 64
            changed_metadata = directory / "changed-metadata.json"
            changed_metadata.write_text(json.dumps(metadata, ensure_ascii=False), "utf-8")
            with mock.patch.object(patch, "METADATA", changed_metadata):
                with self.assertRaises(ValueError):
                    patch.verify_metadata()

    @unittest.skipUnless(DLL.is_file(), "Нужен штатный Lua DLL")
    def test_helper_compiles_with_native_lua_54(self):
        import build_numeric_display_patch as patch
        state = LuaState(DLL)
        self.addCleanup(state.close)
        self.assertEqual(state.load(b"return _VERSION", "numeric-lua-version", True), "Lua 5.4")
        state.load(patch.HELPER.read_bytes(), "numeric-helper-syntax")


@unittest.skipUnless(DLL.is_file() and ORIGINAL_DIR, "Нужны штатный Lua DLL и WXQ_NUMERIC_ORIGINALS")
class NumericDisplayLuaTest(unittest.TestCase):
    def lua(self) -> LuaState:
        state = LuaState(DLL)
        self.addCleanup(state.close)
        return state

    def fixture(self, cases: str, *, patched: bool = True, late_helper: bool = False) -> bytes:
        import build_numeric_display_patch as patch
        directory = Path(ORIGINAL_DIR)
        localize = (directory / f"{LOCALIZE_ID}.lua").read_bytes()
        share = (directory / f"{SHARE_ID}.lua").read_text("utf-8")
        progress = (directory / f"{PROGRESS_ID}.lua").read_text("utf-8")
        for name, source in (("share", share), ("progress", progress)):
            self.assertNotIn("]====]", source, name)
        share_header = "function UITalkOSHProgitShareContentClass:FormatDamage("
        progress_header = "function UIOperationServerLaunchProgressMainPageClass:RefreshKingPersonCnt("
        self.assertEqual(share.count(share_header), 1)
        self.assertEqual(progress.count(progress_header), 1)
        constants = []
        for name in ("KING_CNT_DISPLAY_LIMIT", "TEN_THOUSAND"):
            matches = re.findall(r"^local " + name + r"\s*=\s*\d+[^\r\n]*$", progress, re.M)
            self.assertEqual(len(matches), 1, name)
            constants.extend(matches)
        patched_localize = patch.patch_resource(localize, LOCALIZE_ID)[0] if patched else localize
        blocks = {
            "ORIGINAL_LOCALIZE_MODULE": lua_chunk(localize, "numeric-original-localize"),
            "PATCHED_LOCALIZE_MODULE": lua_chunk(patched_localize, "numeric-patched-localize"),
            "ORIGINAL_SHARE_FUNCTION": function_span(share, share_header),
            "SHARE_PATCH_BLOCK": patch._block(SHARE_ID, "\n") if patched else "",
            "PROGRESS_SOURCE": "\n".join(constants) + "\n" + function_span(progress, progress_header),
            "INITIAL_HELPER_LOAD": "" if late_helper else "loadPatchedLocalize()",
            "EXTENDED_CASES": cases,
        }
        text = FIXTURE.read_text("utf-8")
        for placeholder, source in blocks.items():
            token = "-- {{" + placeholder + "}}"
            self.assertEqual(text.count(token), 1, placeholder)
            text = text.replace(token, source)
        self.assertNotIn("-- {{", text)
        return text.encode("utf-8")

    def run_cases(self, cases: str, **options) -> int:
        return int(self.lua().load(self.fixture(cases, **options), "numeric-display-behavior", True))

    def test_original_reproduces_scaled_units_and_share_w(self):
        self.assertGreater(self.run_cases('''
expect(Localize.GetString("InMillionsOfUnit", 300), "300 дес. тыс.", "TG068: original ten-thousand unit")
expect(Localize.GetString("OperationGlobalRankItemDesc1", 200), "200 десятков тысяч игроков", "TG071: original cap unit")
expect(UITalkOSHProgitShareContentClass:FormatDamage(20500), "2.05w", "TG046: original damage unit")
''', patched=False), 0)

    def test_seven_keys_reconstruct_base_counts_in_both_known_template_generations(self):
        self.assertGreater(self.run_cases('''
for _, generation in ipairs({ "legacy", "current" }) do
    setTemplates(generation)
    for _, key in ipairs(scaledKeys) do
        local hundred = key == "InHundredMillionOfUnit"
        expect(Localize.GetString(key, 1.5), decorate(key, hundred and "150 млн." or "15 тыс."), generation .. ": positive " .. key)
        expect(Localize.GetString(key, -2.05), decorate(key, hundred and "-205 млн." or "-20,5 тыс."), generation .. ": negative " .. key)
        expect(Localize.GetString(key, 0), decorate(key, "0"), generation .. ": zero " .. key)
        expect(Localize.GetString(key, "0.0001"), decorate(key, hundred and "10 тыс." or "1"), generation .. ": small count " .. key)
    end
end
expect(Localize.GetString("InMillionsOfUnit", 123.4), "1 234 000", "Caller count uses exact grouped base instead of a three-digit unit fraction")
expect(Localize.GetString("InHundredMillionOfUnit", "1.23456789"), "123 456 789", "Hundred-million scale keeps every integer digit")
expect(Localize.GetString("GamePreviewCntFormat", 9999), "99,99 млн.", "Caller cap is preserved")
expect(Localize.GetString("OperationNewbinMarkerOfW", 0.05), "500", "Small scaled count has no unit")
'''), 0)

    def test_decimal_strings_shift_exactly_without_double_or_rounding(self):
        self.assertGreater(self.run_cases('''
expect(Localize.GetString("InMillionsOfUnit", "900719925474.0991"), "9 007 199 254 740 991", "String count keeps every integer digit without a long compact fraction")
expect(Localize.GetString("InMillionsOfUnit", "  +0001.20000  "), "12 тыс.", "Whitespace, sign and harmless trailing zeroes")
expect(Localize.GetString("InMillionsOfUnit", "+00000123.40000"), "1 234 000", "Grouped fallback removes leading and trailing zeroes without rounding")
expect(Localize.GetString("InMillionsOfUnit", "-00000123.40000"), "-1 234 000", "Grouped fallback retains the sign")
expect(Localize.GetString("InMillionsOfUnit", "-0.0000"), "0", "There is no negative zero")
expect(Localize.GetString("InMillionsOfUnit", "100000.0000000"), "1 млрд.", "Trailing fractional zeroes do not change the base")
expect(Localize.GetString("InHundredMillionOfUnit", "0.00000001"), "1", "Hundred-million scale permits one base unit")
for _, value in ipairs({ "1e2", "1E-2", "1,5", "0x10", "nan", "inf", "", " ", ".5", "1.", "--1", "+", "1 2", "0.00001", "1.234500001", string.rep("9", 65) }) do
    same("InMillionsOfUnit", value)
end
same("InHundredMillionOfUnit", "0.000000001")
'''), 0)

    def test_numeric_input_requires_finite_near_integer_safe_base(self):
        self.assertGreater(self.run_cases('''
expect(Localize.GetString("InMillionsOfUnit", 0.0001), "1", "One exact base unit")
expect(Localize.GetString("InMillionsOfUnit", 0.000100000005), "1", "Only a sub-tolerance floating residue is accepted")
expect(Localize.GetString("InMillionsOfUnit", 900719925474.0991), "9 007 199 254 740 991", "Largest representable safe integer base is not rounded upward")
for _, value in ipairs({ 0.000100001, 0.00015, 1e308, -1e308, math.huge, -math.huge, 0/0, 900719925475 }) do
    same("InMillionsOfUnit", value)
end
'''), 0)

    def test_two_fraction_digits_remain_compact_and_longer_values_are_exact(self):
        self.assertGreater(self.run_cases('''
local cases = {
    { "0", "0" }, { "0.0999", "999" },
    { "0.1", "1 тыс." }, { "0.1001", "1 001" },
    { "0.101", "1,01 тыс." }, { "0.1011", "1 011" },
    { "1.505", "15,05 тыс." }, { "1.5099", "15 099" },
    { "2.05", "20,5 тыс." }, { "100", "1 млн." },
    { "101", "1,01 млн." }, { "100.1", "1 001 000" },
    { "123", "1,23 млн." }, { "123.4", "1 234 000" },
    { "199.9999", "1 999 999" }, { "100000", "1 млрд." },
    { "101000", "1,01 млрд." }, { "100100", "1 001 000 000" },
}
for _, row in ipairs(cases) do
    expect(Localize.GetString("InMillionsOfUnit", row[1]), row[2], "Boundary between a two-digit unit fraction and exact grouped base")
    local negative = row[2] == "0" and "0" or "-" .. row[2]
    expect(Localize.GetString("InMillionsOfUnit", "-" .. row[1]), negative, "The same exact boundary applies to negative numbers and zero")
end
'''), 0)

    def test_all_seven_displays_parse_back_to_exact_source_base_without_double(self):
        self.assertGreater(self.run_cases('''
local cases = {
    { "0", "0", "0" },
    { "0.1001", "0.00001001", "1001" },
    { "0.101", "0.0000101", "1010" },
    { "1.5099", "0.00015099", "15099" },
    { "123", "0.0123", "1230000" },
    { "123.4", "0.01234", "1234000" },
    { "199.9999", "0.01999999", "1999999" },
    { "100100", "10.01", "1001000000" },
    { "900719925474.0991", "90071992.54740991", "9007199254740991" },
}
for _, generation in ipairs({ "legacy", "current" }) do
    setTemplates(generation)
    for _, key in ipairs(scaledKeys) do
        for _, row in ipairs(cases) do
            for _, sign in ipairs({ "", "-" }) do
                local value = sign .. row[key == "InHundredMillionOfUnit" and 2 or 1]
                local before = value
                local shown = Localize.GetString(key, value)
                local base = row[3] == "0" and "0" or sign .. row[3]
                expect(parseDisplayedInteger(numericPart(key, shown)), base, "Display is exactly equivalent to the source base integer")
                expect(value, before, "Display does not mutate or replace the caller value")
            end
        end
    end
end
'''), 0)

    def test_unrelated_and_nonstring_keys_never_enter_scaled_helper(self):
        self.assertGreater(self.run_cases('''
local api = rawget(_G, "_AceRuNumericDisplayV2")
local nativeTry = api.tryScaled
local calls = 0
api.tryScaled = function(...) calls = calls + 1; return nativeTry(...) end
local token = {}
for _, key in ipairs({ "UNKNOWN", "TitleSetting", "OperationGlobalRankItemDesc", "SettleShareAnyTime", "FALSE_RETURN", "MISSING" }) do
    same(key, 1.5, nil, token)
end
same(nil, 1.5)
same(false, 1.5)
same(true, 1.5)
same(123, 1.5)
same(token, 1.5)
same(function() end, 1.5)
failingKey = "UNKNOWN"
same("UNKNOWN", 1.5)
failingKey = nil
expect(calls, 0, "Unknown and nonstring keys bypass pcall/tryScaled entirely")
api.tryScaled = nativeTry
'''), 0)

    def test_each_known_key_enters_helper_once_even_when_native_fallback_is_required(self):
        self.assertGreater(self.run_cases('''
local api = rawget(_G, "_AceRuNumericDisplayV2")
local nativeTry = api.tryScaled
local calls = 0
api.tryScaled = function(...) calls = calls + 1; return nativeTry(...) end
local token = {}
for _, key in ipairs(scaledKeys) do
    local before = calls
    local hundred = key == "InHundredMillionOfUnit"
    expect(Localize.GetString(key, 1.5), decorate(key, hundred and "150 млн." or "15 тыс."), "Known key display still works")
    expect(calls - before, 1, "Each known key enters helper exactly once")
    before = calls
    same(key, 1.5, nil, token)
    expect(calls - before, 1, "Known key extraargs keep one helper call and native args identity")
    before = calls
    same(key, nil)
    expect(calls - before, 1, "Known key nil fallback still enters helper once")
    before = calls
    failingKey = key
    same(key, 1.5)
    failingKey = nil
    expect(calls - before, 1, "Known key native errors are preserved after exactly one helper call")
end
api.tryScaled = nativeTry
'''), 0)

    def test_unknown_locale_or_template_preserves_exact_native_args_and_results(self):
        self.assertGreater(self.run_cases('''
local sentinelTable = { value = 1 }
local sentinelFunction = function() end
for _, language in ipairs({ "设置", "Settings", "Выбор языка", "Настройки " }) do
    marker = language
    for _, key in ipairs(scaledKeys) do
        same(key, 1.5)
        same(key, sentinelTable)
        same(key, 1.5, nil, sentinelFunction)
    end
end
marker = "Настройки"
for _, key in ipairs(scaledKeys) do
    local template = nativeTemplates[key]
    nativeTemplates[key] = "Другая схема {0} единиц"
    same(key, 1.5)
    nativeTemplates[key] = nil
    same(key, 1.5)
    nativeTemplates[key] = false
    same(key, 1.5)
    nativeTemplates[key] = template
    same(key)
    same(key, nil)
    same(key, 1.5, "extra")
    same(key, 1.5, nil)
    for _, value in ipairs({ true, false, sentinelTable, sentinelFunction }) do same(key, value) end
end
same("OperationGlobalRankItemDesc", 2000000)
same("SettleShareAnyTime", "万象时刻", nil, sentinelTable)
same("UNKNOWN", 1.5)
same("FALSE_RETURN")
same("MISSING")
same(nil, 1)
same(false, 1)
same(sentinelTable, 1)
'''), 0)

    def test_native_guard_errors_missing_marker_and_native_errors_do_not_recurse(self):
        self.assertGreater(self.run_cases('''
for _, key in ipairs(scaledKeys) do
    markerFailure = true
    same(key, 1.5)
    markerFailure = false
    marker = nil
    same(key, 1.5)
    marker = false
    same(key, 1.5)
    marker = "Настройки"
    failingKey = key
    same(key, 1.5)
    failingKey = nil
end
nativeCalls = {}
expect(Localize.GetString("TitleSetting"), "Настройки", "Native guard key delegates once")
expect(#nativeCalls, 1, "Native guard never recurses")
nativeCalls = {}
expect(Localize.GetString("InMillionsOfUnit", 1.5), "15 тыс.", "Normal guarded display")
expect(#nativeCalls, 2, "Only locale and known-template probes run")
local api = rawget(_G, "_AceRuNumericDisplayV2")
local before = Localize.GetString
api.attach(Localize, nativeGetter)
api.attach(Localize, nativeGetter)
expect(Localize.GetString, before, "Repeated attach never stacks wrappers")
'''), 0)

    def test_helper_failure_falls_back_to_original_native_behavior(self):
        self.assertGreater(self.run_cases('''
local api = rawget(_G, "_AceRuNumericDisplayV2")
local scaled, share = api.tryScaled, api.tryShare
api.tryScaled = function() error("unsupported-runtime-shape", 0) end
same("InMillionsOfUnit", 1.5)
same("InMillionsOfUnit", nil, "extra")
api.tryScaled = scaled
api.tryShare = function() error("unsupported-runtime-shape", 0) end
sameShare(20500)
api.tryShare = share
expect(Localize.GetString("InMillionsOfUnit", 1.5), "15 тыс.", "A transient helper failure never replaces native data")
expect(UITalkOSHProgitShareContentClass:FormatDamage(20500), "20,5 тыс.", "The next independent display call still works")
'''), 0)

    def test_actual_share_formatter_preserves_native_hundred_quantum(self):
        self.assertGreater(self.run_cases('''
for _, row in ipairs({ { 10000, "10 тыс." }, { 15000, "15 тыс." }, { 15099, "15 тыс." }, { 20500, "20,5 тыс." }, { 20599, "20,5 тыс." }, { 99999, "99,9 тыс." }, { 1000000, "1 млн." }, { 1999999, "1 999 900" } }) do
    expect(UITalkOSHProgitShareContentClass:FormatDamage(row[1]), row[2], "Share base number retains native floor(damage/100)*100")
end
for _, damage in ipairs({ -10000, -1, 0, 999, 9999, 9999.9 }) do sameShare(damage) end
sameShare(20500, "extra")
sameShare(20500, nil)
sameShare("20500")
sameShare(nil)
sameShare(true)
sameShare(math.huge)
sameShare(-math.huge)
sameShare(0/0)
for _, language in ipairs({ "设置", "Settings", "Выбор языка" }) do
    marker = language
    sameShare(20500)
    sameShare(15099)
end
'''), 0)

    def test_share_display_parses_to_native_hundred_quantum_without_mutating_damage(self):
        self.assertGreater(self.run_cases('''
for _, row in ipairs({ { 15099, "15000" }, { 20599, "20500" }, { 99999, "99900" }, { 1999999, "1999900" }, { 123456789, "123456700" }, { 2147483647, "2147483600" } }) do
    local damage = row[1]
    local shown = UITalkOSHProgitShareContentClass:FormatDamage(damage)
    expect(parseDisplayedInteger(shown), row[2], "The displayed value equals the original hundred quantum exactly")
    expect(damage, row[1], "Share display never rewrites the original damage counter")
end
'''), 0)

    def test_share_wrapper_looks_up_helper_at_call_time(self):
        self.assertGreater(self.run_cases('''
expect(rawget(_G, "_AceRuNumericDisplayV2"), nil, "Share may be imported before Localize helper")
sameShare(20500)
loadPatchedLocalize()
expect(UITalkOSHProgitShareContentClass:FormatDamage(20500), "20,5 тыс.", "A later helper load fixes an already imported Share class")
local api = rawget(_G, "_AceRuNumericDisplayV2")
rawset(_G, "_AceRuNumericDisplayV2", { version = 99 })
sameShare(20500)
rawset(_G, "_AceRuNumericDisplayV2", nil)
sameShare(20500)
rawset(_G, "_AceRuNumericDisplayV2", api)
expect(UITalkOSHProgitShareContentClass:FormatDamage(20500), "20,5 тыс.", "Returning the known helper restores display")
''', late_helper=True), 0)

    def test_actual_current_server_progress_method_keeps_native_threshold(self):
        self.assertGreater(self.run_cases('''
local counter = 2000001
local view = setmetatable({}, { __index = UIOperationServerLaunchProgressMainPageClass })
function view:GetKingRankCnt() return counter end
function view:SetMember(text) self.member = text end
view:RefreshKingPersonCnt()
expect(view.member, "2 млн. игроков+", "TG071: above cap uses scaled known label")
expect(counter, 2000001, "The server counter is never rewritten")
counter = 2000000
view:RefreshKingPersonCnt()
expect(view.member, "Игр.: 2000000", "At the exact cap the actual caller retains the unscaled native key")
counter = 1999999
view:RefreshKingPersonCnt()
expect(view.member, "Игр.: 1999999", "Below the cap the actual caller retains the unscaled native key")
counter = nil
view:RefreshKingPersonCnt()
expect(view.member, "Нет данных", "The native missing-counter branch survives")
marker = "设置"
counter = 2000001
view:RefreshKingPersonCnt()
expect(view.member, originalGetString("OperationGlobalRankItemDesc1", 2000000 / 10000) .. "+", "A non-Russian guard keeps original rendered text even on the cap branch")
'''), 0)

    def test_complete_current_resources_compile_roundtrip_and_patch_once(self):
        import build_numeric_display_patch as patch
        for rid in CURRENT_SHA:
            with self.subTest(rid=rid):
                original = (Path(ORIGINAL_DIR) / f"{rid}.lua").read_bytes()
                self.assertEqual(hashlib.sha256(original).hexdigest(), CURRENT_SHA[rid])
                payload, edits = patch.patch_resource(original, rid)
                self.assertTrue(edits)
                self.assertEqual(patch.revert_resource(payload, rid), original)
                self.assertEqual(patch.patch_resource(payload, rid)[0], payload)
                self.assertEqual(payload.count(patch.PATCH_BEGIN.encode()), 1)
                self.assertEqual(payload.count(patch.PATCH_END.encode()), 1)
                state = self.lua()
                state.load(original, "numeric-original-syntax-" + rid)
                state.load(payload, "numeric-patched-syntax-" + rid)
                with self.assertRaises(ValueError):
                    patch.patch_resource(original + b"-- changed\n", rid)
                for tampered in (
                    payload + b"-- changed\n",
                    payload.replace(patch.PATCH_BEGIN.encode(), b"-- unknown-patch-begin", 1),
                    payload.replace(patch.PATCH_END.encode(), b"-- unknown-patch-end", 1),
                    payload.replace(patch.PATCH_BEGIN.encode(), patch.PATCH_BEGIN.encode() + b"\n-- changed", 1),
                ):
                    with self.assertRaises(ValueError):
                        patch.revert_resource(tampered, rid)
                    with self.assertRaises(ValueError):
                        patch.patch_resource(tampered, rid)


if __name__ == "__main__":
    unittest.main()
