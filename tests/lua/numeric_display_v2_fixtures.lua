-- Изолированные границы native Localize и реальные функции текущих ресурсов.
-- Никакие Unity-объекты, игровые процессы, сеть или файлы здесь не используются.
local passed = 0
local function expect(got, expected, name)
    assert(got == expected, name .. ": got " .. tostring(got) .. ", expected " .. tostring(expected))
    passed = passed + 1
end

local scaledKeys = {
    "InMillionsOfUnit", "InHundredMillionOfUnit",
    "Operation_Rank_BigNumAbbreviation_TenThousand", "GamePreviewCntFormat",
    "OperationNewbinMarkerOfW", "OperationGlobalRankItemDesc1", "SeasonJourney_KingNeed",
}
local templateSets = {
    legacy = {
        InMillionsOfUnit = "{0} дес. тыс.",
        InHundredMillionOfUnit = "{0} сот. млн",
        Operation_Rank_BigNumAbbreviation_TenThousand = "{0} дес. тыс.",
        GamePreviewCntFormat = "{0} дес. тыс.",
        OperationNewbinMarkerOfW = "{0} дес. тыс.",
        OperationGlobalRankItemDesc1 = "{0} десятков тысяч игроков",
        SeasonJourney_KingNeed = "Короли сервера: {0} дес. тыс.",
    },
    current = {
        InMillionsOfUnit = "{0}×10 тыс.",
        InHundredMillionOfUnit = "{0}×100 млн",
        Operation_Rank_BigNumAbbreviation_TenThousand = "{0}×10 тыс.",
        GamePreviewCntFormat = "{0}×10 тыс.",
        OperationNewbinMarkerOfW = "{0}×10 тыс.",
        OperationGlobalRankItemDesc1 = "{0}×10 тыс. игроков",
        SeasonJourney_KingNeed = "Короли на сервере: {0}×10 тыс.",
    },
}
local nativeTemplates = {}
local marker = "Настройки"
local markerFailure = false
local failingKey
local nativeCalls = {}
local function setTemplates(generation)
    nativeTemplates = {
        OperationGlobalRankItemDesc = "Игр.: {0}",
        SettleShareAnyTime = "Моменты Ваньсян",
        AnswerQuestionRankDesc = "Нет данных",
        FALSE_RETURN = false,
    }
    for key, value in pairs(templateSets[generation]) do nativeTemplates[key] = value end
end
setTemplates("legacy")

local function nativeGetter(key, ...)
    nativeCalls[#nativeCalls + 1] = table.pack(key, ...)
    if type(key) ~= "string" then error("native-invalid-key", 0) end
    if key == "TitleSetting" then
        if markerFailure then error("native-marker-error", 0) end
        return marker
    end
    if failingKey == key then error("native-value-error", 0) end
    local template = nativeTemplates[key]
    if template == false then return false end
    if template == nil then return nil end
    local args = table.pack(...)
    return (template:gsub("{(%d+)}", function(index) return tostring(args[tonumber(index) + 1]) end))
end
CS = { LuaCallCSharpInteraction = { GetLocalizeString = nativeGetter } }

-- {{ORIGINAL_LOCALIZE_MODULE}}
local originalGetString = Localize.GetString
local function loadPatchedLocalize()
    -- {{PATCHED_LOCALIZE_MODULE}}
end

UITalkOSHProgitShareContentClass = {}
-- {{ORIGINAL_SHARE_FUNCTION}}
local originalDamage = UITalkOSHProgitShareContentClass.FormatDamage
-- {{SHARE_PATCH_BLOCK}}
-- {{INITIAL_HELPER_LOAD}}

UIOperationServerLaunchProgressMainPageClass = {}
-- {{PROGRESS_SOURCE}}

local function decorate(key, text)
    if key == "OperationGlobalRankItemDesc1" then return text .. " игроков" end
    if key == "SeasonJourney_KingNeed" then return "Короли на сервере: " .. text end
    return text
end

local function numericPart(key, text)
    if key == "OperationGlobalRankItemDesc1" then
        local suffix = " игроков"
        assert(text:sub(-#suffix) == suffix, "The native players label is retained")
        return text:sub(1, -#suffix - 1)
    end
    if key == "SeasonJourney_KingNeed" then
        local prefix = "Короли на сервере: "
        assert(text:sub(1, #prefix) == prefix, "The native server label is retained")
        return text:sub(#prefix + 1)
    end
    return text
end

-- Независимый обратный parser проверяет значение уже готовой подписи.
-- Все операции строковые: даже большие целые не проходят через double.
local function parseDisplayedInteger(text)
    assert(type(text) == "string", "Display must be a string")
    local power = 0
    for suffix, exponent in pairs({ [" тыс."] = 3, [" млн."] = 6, [" млрд."] = 9 }) do
        if text:sub(-#suffix) == suffix then
            text, power = text:sub(1, -#suffix - 1), exponent
            break
        end
    end
    local negative = text:sub(1, 1) == "-"
    if negative then text = text:sub(2) end
    local digits
    if text:find(" ", 1, true) then
        assert(power == 0 and not text:find(",", 1, true), "Grouped fallback is a base integer")
        local groups = {}
        for group in text:gmatch("[^ ]+") do
            assert(group:match("^%d+$"), "Grouping contains only ASCII digits")
            assert(#group == 3 or #groups == 0 and #group >= 1 and #group <= 3, "Thousands groups have exactly three digits")
            groups[#groups + 1] = group
        end
        assert(table.concat(groups, " ") == text, "Grouping uses single ASCII spaces")
        assert(#groups[1] == 1 or groups[1]:sub(1, 1) ~= "0", "Grouping has no leading zeroes")
        digits = table.concat(groups)
    else
        local whole, fraction = text:match("^(%d+),(%d+)$")
        if whole == nil then
            assert(text:match("^%d+$"), "Unrecognized numeric display")
            whole, fraction = text, ""
        end
        assert(#fraction <= power, "A compact decimal must reconstruct an integer base")
        digits = whole .. fraction .. string.rep("0", power - #fraction)
    end
    digits = digits:gsub("^0+", "")
    if digits == "" then return "0" end
    return (negative and "-" or "") .. digits
end

-- Fallback проверяет настоящий результат/ошибку оригинала и точный вызов C#.
local function same(key, ...)
    local args = table.pack(...)
    local expected = table.pack(pcall(originalGetString, key, table.unpack(args, 1, args.n)))
    nativeCalls = {}
    local actual = table.pack(pcall(Localize.GetString, key, table.unpack(args, 1, args.n)))
    expect(actual.n, expected.n, "Native fallback return arity")
    for index = 1, expected.n do expect(actual[index], expected[index], "Native fallback result or error") end
    local call = nativeCalls[#nativeCalls]
    assert(call, "Native fallback must call the original C# getter")
    expect(call.n, args.n + 1, "Native argument arity includes nil values")
    expect(call[1], key, "Native key identity")
    for index = 1, args.n do
        if type(args[index]) == "number" and args[index] ~= args[index] then
            expect(type(call[index + 1]), "number", "Native NaN argument type")
            expect(string.pack("d", call[index + 1]), string.pack("d", args[index]), "Native NaN argument representation")
        else
            expect(call[index + 1], args[index], "Native argument identity")
        end
    end
end

local function sameShare(...)
    local args = table.pack(...)
    local expected = table.pack(pcall(originalDamage, UITalkOSHProgitShareContentClass, table.unpack(args, 1, args.n)))
    local actual = table.pack(pcall(UITalkOSHProgitShareContentClass.FormatDamage, UITalkOSHProgitShareContentClass, table.unpack(args, 1, args.n)))
    expect(actual.n, expected.n, "Native Share return arity")
    for index = 1, expected.n do
        -- Местоположение Lua-чанка в native ошибке допустимо отличается;
        -- типичный native текст ошибки сохраняется вместе со статусом.
        if index == 2 and expected[1] == false then
            local expectedMessage = tostring(expected[index]):gsub("^.-:%d+: ", "")
            local actualMessage = tostring(actual[index]):gsub("^.-:%d+: ", "")
            expect(actualMessage, expectedMessage, "Native Share error")
        else
            expect(actual[index], expected[index], "Native Share result")
        end
    end
end

-- {{EXTENDED_CASES}}
return tostring(passed)
