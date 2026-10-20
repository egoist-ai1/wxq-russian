local assertions = 0
local function check(value, message)
    assertions = assertions + 1
    assert(value, message)
end
local locale = "Настройки"
Localize = { GetString = function(key) assert(key == "TitleSetting"); return locale end }
-- Лог диагностики не должен писать в настоящий %TEMP% во время тестов: io закрыт, заглушку подставляют только тесты лога.
local realIo, realOs, realDebug = io, os, debug
io = nil
local rebuildCalls = 0
CS = { UnityEngine = { HorizontalWrapMode = { Wrap = 0, Overflow = 1 }, VerticalWrapMode = { Truncate = 0, Overflow = 1 }, UI = { LayoutRebuilder = { ForceRebuildLayoutImmediate = function(rt)
    rebuildCalls = rebuildCalls + 1
    if rt.onRebuild then rt.onRebuild() end
    if rt.owner and rt.owner.autoHeight then rt.rect.height = rt.owner.preferredHeight end
end } } } }
-- {{HELPER}}

-- size: кегль узла (по умолчанию 20); ширина линейна по кеглю.
local function widthOf(text, size)
    text = text:gsub("<[^>]*>", "")
    local scale = (size or 20) / 20
    local width, maximum = 0, 0
    for _, code in utf8.codes(text) do
        if code == 10 or code == 13 then maximum = math.max(maximum, width); width = 0
        elseif code == 32 then width = width + 4
        elseif code == 9 then width = width + 16
        elseif code == 0x200b then width = width
        elseif code == 0x00a0 then width = width + 4
        elseif code >= 0x0400 and code <= 0x052f then width = width + 10
        elseif code == string.byte("i") or code == string.byte(".") then width = width + 3
        else width = width + 8 end
    end
    return math.max(maximum, width) * scale
end
local function newNode(width, height)
    local values = { text = "", font = {}, fontSize = 20, fontStyle = 0, textStyle = 2, lineSpacing = 1, horizontalOverflow = 0, verticalOverflow = 0, resizeTextForBestFit = false }
    local node = { values = values, rect = { width = width or 160, height = height or 400 }, accesses = 0 }
    -- parent: родитель с той же коробкой; пересчёт раскладки родителя (rebuild) растит коробку поля с autoHeight.
    local function makeRt(owner) return { rect = owner.rect, owner = owner, parent = { rect = owner.rect, owner = owner } } end
    function node:GetRectTransform() return makeRt(self) end
    -- Vendor-код читает .rectTransform; помощник берёт его первым, GetRectTransform остаётся запасным путём.
    node.rectTransform = makeRt(node)
    return setmetatable(node, {
        __index = function(n, key)
            if key == "preferredWidth" then
                n.accesses = n.accesses + 1
                if n.widthError then error("mock getter rejected") end
                if n.nanWidth then return 0/0 end
                if n.zeroWidth then return 0 end
                -- literalTags: движок рисует непрозрачные теги буквально (их ширина входит в строку).
                if n.literalTags then return widthOf((values.text:gsub("<", "x")), values.fontSize) end
                return widthOf(values.text, values.fontSize)
            elseif key == "preferredHeight" then
                if n.heightError then error("mock height getter rejected") end
                -- heightNative / heightOurs: штатная высота (перенос движка) и высота явных строк, независимо от числа LF.
                if n.heightNative and values.horizontalOverflow == 0 then return n.heightNative end
                if n.heightOurs and values.horizontalOverflow == 1 then return n.heightOurs end
                local _, lines = values.text:gsub("\n", "")
                return (lines + 1) * 22
            end
            return values[key]
        end,
        __newindex = function(n, key, value)
            if key == "horizontalOverflow" and n.enumError then error("mock enum setter rejected") end
            values[key] = value
        end,
    })
end
local function sourceSet(node, text)
    node.text = text
    return _AceRuSpaceWrap(node, text)
end
local function assertBreaksOnSpaces(input, output)
    local i, j = 1, 1
    while j <= #output do
        if output:sub(j,j) == input:sub(i,i) then i = i + 1; j = j + 1
        elseif output:sub(j,j) == "\n" and input:sub(i,i) == " " and output:sub(j+1,j+1) ~= " " then
            -- Разрыв вместо пробела (запасная ветка при ширине с хвостовым пробелом): пробел заменён на LF.
            i = i + 1; j = j + 1
        else
            check(output:sub(j,j) == "\n", "Only LF may be added")
            check(output:sub(j-1,j-1) == " " or output:sub(j+1,j+1) == " ", "Added LF must touch original ASCII space")
            j = j + 1
        end
    end
    check(i == #input + 1, "Original bytes must survive in sequence")
end
-- v15: штатный BestFit у компактных подписей должен оставаться владельцем типографики.
do
    local managed = newNode(100, 120)
    managed.resizeTextForBestFit = true
    local caption = "Оконный режим"
    local managedOk, managedReason = sourceSet(managed, caption)
    check(not managedOk and managed.text == caption and managed.resizeTextForBestFit == true
        and managed.horizontalOverflow == 0 and managed.fontSize == 20,
        "A multiword BestFit caption keeps its native settings: " .. tostring(managedReason))
end
local n = newNode(160, 400)
local input = "Увеличивает силу атаки героя на 10% и скорость атаки на {0}."
local ok, reason, calls = sourceSet(n, input)
check(ok and reason == "wrapped", "Natural Russian prose should wrap")
check(calls <= 800, "Bounded preferredWidth calls")
check(n.horizontalOverflow == 1 and n.resizeTextForBestFit == false, "Explicit lines disable native inside-word wrapping")
check(n.fontSize == 20 and n.fontStyle == 0 and n.textStyle == 2, "Native font/style unchanged")
check(widthOf(n.text) <= 159, "Every rendered fixture line fits")
check(n.text:find("{0}",1,true) and n.text:find("10%",1,true), "Runtime placeholder/numbers unchanged")
assertBreaksOnSpaces(input, n.text)
local cached = n.text
ok, reason, calls = sourceSet(n, input)
check(ok and reason == "cached" and calls == 0 and n.text == cached, "Repeated same field uses bounded cache")
n.rect.width = 230
ok, reason, calls = sourceSet(n, input)
check(ok and reason ~= "cached" and calls > 0, "Geometry change invalidates cache")
_AceRuWrapRestore(n)
check(n.text == input and n.horizontalOverflow == 0 and n.resizeTextForBestFit == false, "Native unload restores reviewed field")

for _, text in ipairs({
    "Первая строка\r\nВторая длинная строка описания\n\nПоследний абзац.",
    "<c #FFCC00>Боевая сила героя и <b>скорость атаки</b></c> увеличиваются на 25%.",
    "<color=#AABBCC><size=20>Описание навыка с числами 10/20/30</size></color> и {value}.",
    "  Сохраняются  все   исходные пробелы  ",
    "Русские слова\tи табуляция сохраняются вместе.",
    "Урон\226\128\139 героя равен 10%.",
}) do
    local node = newNode(230, 800)
    ok, reason, calls = sourceSet(node, text)
    check(ok, "Reviewed rich/plain fixture: " .. tostring(reason))
    assertBreaksOnSpaces(text, node.text)
    check(node.preferredHeight <= node.rect.height and widthOf(node.text) <= node.rect.width - 1, "Native model width/height both fit")
    _AceRuWrapRestore(node)
end
local literal = "Короткая подпись"
local short = newNode(230, 100)
ok, reason = sourceSet(short, literal)
check(ok and reason == "unchanged" and short.text == literal, "Already-fitting label remains exact")
locale = "设置"
sourceSet(short, "Настройки китайского интерфейса")
check(short.text == "Настройки китайского интерфейса" and short.horizontalOverflow == 0 and short.resizeTextForBestFit == false, "Locale change restores only styles, preserving new caller text")
locale = "Настройки"
for _, text in ipairs({ "<c #FFFFFF>Без закрытия", "<b><i>Неверно</b></i>", "Текст\0ошибка" }) do
    local node = newNode(70, 800)
    ok = sourceSet(node, text)
    check(not ok and node.text == text and node.horizontalOverflow == 0 and node.resizeTextForBestFit == false, "Unbalanced known markup and control bytes fail closed")
end
-- Цельный атом шире поля не получает дефис, LF или уменьшенный кегль, в том числе с тегами/NBSP.
for _, text in ipairs({
    "Это сверхдлинноесловобезпробелов в описании",
    "Это неразрывное\194\160слово в описании",
    "<sprite=1>Описаниеумениягероя и ещё слова",
    "<b>Сверхдлинное</b><i>словобезпробелов</i> и конец",
    "Магическая защита",
}) do
    local node = newNode(100, 800)
    ok, reason = sourceSet(node, text)
    check(not ok and tostring(reason):find("unbreakable-word", 1, true), "A whole atom wider than the field is refused: " .. tostring(reason))
    check(node.text == text and node.fontSize == 20 and node.horizontalOverflow == 0 and node.lineSpacing == 1,
        "An unbreakable atom preserves text and typography")
end
for _, fault in ipairs({ "widthError", "heightError", "nanWidth", "zeroWidth", "enumError" }) do
    local node = newNode(160, 800)
    node[fault] = true
    ok = sourceSet(node, input)
    check(not ok and node.text == input and node.resizeTextForBestFit == false, "Native fault rollback: " .. fault)
end
local tiny = newNode(160, 30)
ok = sourceSet(tiny, input)
check(not ok and tiny.text == input and tiny.horizontalOverflow == 0, "Too-tall text restores original instead of clipping")
local invalidRect = newNode(10, 100)
ok = sourceSet(invalidRect, literal)
check(not ok and invalidRect.text == literal and invalidRect.horizontalOverflow == 0, "Unknown tiny geometry remains native")
for _, text in ipairs({ string.rep("Русское слово ", 400), string.rep("я", 6500), "English only" }) do
    local node = newNode(160, 800)
    ok, reason, calls = sourceSet(node, text)
    check(not ok and node.text == text and node.accesses == 0, "Oversize/atom/no-Cyrillic guard precedes measurement")
end
local changed = newNode(230, 100)
sourceSet(changed, literal)
sourceSet(changed, "<b>Новое значение</unbalanced>")
check(changed.text == "<b>Новое значение</unbalanced>" and changed.horizontalOverflow == 0, "Reject after prior success never resurrects stale caption")
local narrow = newNode(96, 400)
local closeFit = "Сила атаки героя"
ok = sourceSet(narrow, closeFit)
check(ok and widthOf(narrow.text) <= 95, "Trailing-space-sensitive generator can place separator on next line")
assertBreaksOnSpaces(closeFit, narrow.text)
check(narrow.text == "Сила атаки\nгероя", "The separator space is replaced by LF and does not start the next line: " .. narrow.text:gsub("\n", "|"))
check(not narrow.text:find("\n ", 1, true), "No line begins with a space")

-- The cache stores only a bounded set of these exact native fields.
_AceRuWrapCache = setmetatable({}, { __mode = "k" })
local retained = {}
for i = 1, 128 do
    retained[i] = newNode(160,400)
    check(sourceSet(retained[i], input), "At most 128 live source-bound fields may be cached")
end
local excess = newNode(160,400)
ok, reason = sourceSet(excess, input)
check(not ok and reason == "node-cache-budget" and excess.text == input and excess.horizontalOverflow == 0 and excess.resizeTextForBestFit == false, "129th live field rolls back exactly")
_AceRuWrapRestore(retained[1])
ok = sourceSet(excess, input)
check(ok, "A released reviewed field permits a new cache entry")
_AceRuWrapCache = setmetatable({}, { __mode = "k" })
local heavy = newNode(30,4096)
local heavyText = string.rep("я ", 96)
ok, reason, calls = sourceSet(heavy, heavyText)
check(calls <= 800 and heavy.accesses <= 800, "Worst-case narrow panel never exceeds the measurement budget")
check(ok or heavy.text == heavyText, "Budget exhaustion preserves complete original source")
-- ===== v15: реальные границы, целые слова и неизменная типографика =====
_AceRuWrapCache = setmetatable({}, { __mode = "k" })
for _, height in ipairs({ 0, 10, 22, 30, 66 }) do
    for _, vertical in ipairs({ 0, 1 }) do
        local compact = newNode(160, height)
        compact.verticalOverflow = vertical
        compact.heightNative = 200 -- старый v14 ошибочно считал это разрешением выйти за реальный rect.
        local before = rebuildCalls
        ok, reason = sourceSet(compact, input)
        check(not ok and tostring(reason):find("native-height-overflow", 1, true), "A fixed/unknown box is bounded by its actual height")
        check(compact.text == input and compact.horizontalOverflow == 0 and compact.fontSize == 20
            and compact.verticalOverflow == vertical and compact.rect.height == height and compact.lineSpacing == 1,
            "A compact field does not spill into the next row")
        check(rebuildCalls == before, "Unknown/fixed layout is never rebuilt speculatively")
    end
end
for _, caption in ipairs({ "Новички", "Сверхдлинноеслово", "<b>Сверхдлинное</b>", "Ддддддддддддд", string.rep("ж", 40) }) do
    for _, height in ipairs({ 22, 60, 400 }) do
        local word = newNode(100, height)
        local before = word.accesses
        ok, reason = sourceSet(word, caption)
        check(not ok and reason == "single-word" and word.text == caption, "Single atoms have no legal inserted line break")
        check(word.fontSize == 20 and word.horizontalOverflow == 0 and word.accesses == before,
            "A single atom never shrinks, hyphenates or measures")
    end
end
do
    local calls, realGet = 0, Localize.GetString
    Localize.GetString = function(key) calls = calls + 1; return realGet(key) end
    local progress, one = newNode(100, 22), newNode(100, 22)
    for _ = 1, 5 do _AceRuWrap(progress, "75 %"); _AceRuWrap(one, "Новички") end
    check(calls == 1 and progress.accesses == 0 and one.accesses == 0, "Cheap guards do not repeatedly query the locale or engine")
    Localize.GetString = realGet
end
-- Штатный Overflow означает, что поле не согласилось на многострочный текст.
local nowrap = newNode(160, 400)
nowrap.horizontalOverflow = 1
ok, reason = sourceSet(nowrap, input)
check(not ok and reason == "native-nowrap" and nowrap.text == input and nowrap.horizontalOverflow == 1,
    "Native horizontal Overflow is not converted to multiline")
-- Любая попытка записать типографику должна падать: все успешные и неуспешные ветки обходятся без таких записей.
for _, caption in ipairs({ input, "Магическая защита", "Сверхдлинное", "Короткая подпись" }) do
    local rigid = newNode(160, 400)
    local meta, set = getmetatable(rigid), getmetatable(rigid).__newindex
    local forbiddenWrites = {}
    meta.__newindex = function(n, key, value)
        if key == "fontSize" or key == "font" or key == "lineSpacing" or key == "resizeTextForBestFit"
            or key == "verticalOverflow" or key == "fontStyle" or key == "textStyle" then
            forbiddenWrites[key] = true
            error("Typography setter is forbidden")
        end
        set(n, key, value)
    end
    sourceSet(rigid, caption)
    _AceRuWrapRestore(rigid)
    check(next(forbiddenWrites) == nil, "Success, refusal and unload never attempt to write typography")
end

local wrapNode = newNode(160, 400)
wrapNode.text = input
ok, reason = _AceRuWrap(wrapNode, input)
check(ok == true and reason == "wrapped" and wrapNode.text:find("\n", 1, true), "_AceRuWrap returns the helper decision")
check(select("#", _AceRuWrap(nil, "Русский текст")) == 2 and _AceRuWrap(nil, "Русский текст") == false, "nil node is declined, not raised")
check(_AceRuWrap(newNode(160, 400), nil) == false, "nil content without node text is declined, not raised")
check(select("#", _AceRuWrap(nil)) == 2 and _AceRuWrap(nil) == false and select(2, _AceRuWrap(nil)) == "missing-node", "Single-argument nil node (nil field at the call site) is declined quietly")
local oneArg = newNode(160, 400)
oneArg.text = input
ok, reason = _AceRuWrap(oneArg)
check(ok == true and reason == "wrapped" and oneArg.text:find("\n", 1, true), "Single-argument call reads node.text and wraps it")
local hostileRead = setmetatable({}, { __index = function() error("boom") end })
check(select(1, pcall(_AceRuWrap, hostileRead)) == true and _AceRuWrap(hostileRead) == false, "Node whose text read raises is declined by the single-argument call")
local hostile = setmetatable({}, { __index = function() error("boom") end, __newindex = function() error("boom") end })
check(select(1, pcall(_AceRuWrap, hostile, input)) == true and _AceRuWrap(hostile, input) == false, "Hostile node never raises out of _AceRuWrap")
local localeOnly = newNode(160, 400)
locale = "设置"
localeOnly.text = input
ok, reason = _AceRuWrap(localeOnly, input)
check(ok == false and reason == "locale-guard" and localeOnly.text == input, "Foreign locale leaves the field untouched")
check(rawget(_G, "__AceRuWrapGuardLogged") == true, "A locale-guard refusal is logged once per session (flag set)")
locale = "Опции"
local shortLocale = newNode(160, 400)
shortLocale.text = input
ok, reason = _AceRuWrap(shortLocale, input)
check(ok == true and shortLocale.text:find("\n", 1, true), "Short Russian form of TitleSetting (Опции) keeps wrapping on: " .. tostring(reason))
locale = "Settings"
local latinLocale = newNode(160, 400)
latinLocale.text = input
ok, reason = _AceRuWrap(latinLocale, input)
check(ok == false and reason == "locale-guard" and latinLocale.text == input, "A Latin TitleSetting still refuses")
locale = "Настройки"

-- Re-entrancy: a text hook that calls the helper again must not loop or corrupt the outer wrap.
local reentrant = newNode(160, 400)
local depth, maxDepth = 0, 0
local inner = getmetatable(reentrant)
local plainNewIndex = inner.__newindex
inner.__newindex = function(n, key, value)
    if key == "text" then
        depth = depth + 1
        maxDepth = math.max(maxDepth, depth)
        _AceRuWrap(n, value)
        depth = depth - 1
    end
    plainNewIndex(n, key, value)
end
reentrant.text = input
ok = _AceRuWrap(reentrant, input)
check(ok == true and maxDepth <= 200 and reentrant.text:find("\n", 1, true), "Text hook calling the helper again does not recurse")
_AceRuWrapRestore(reentrant)
check(reentrant.text == input and reentrant.horizontalOverflow == 0 and _AceRuWrapCache[reentrant] == nil,
    "Unload does not let a text hook reapply the helper while restoring its native text")

-- Chinese and mixed text.
local chinese = newNode(160, 400)
chinese.text = "提升英雄的攻击力"
ok, reason = _AceRuWrap(chinese, "提升英雄的攻击力")
check(ok == false and reason == "no-cyrillic" and chinese.accesses == 0 and chinese.horizontalOverflow == 0 and chinese.resizeTextForBestFit == false, "Chinese text is not touched or measured")
local numeric = newNode(160, 400)
ok, reason = _AceRuWrap(numeric, "12345 / 678")
check(ok == false and reason == "no-cyrillic" and numeric.accesses == 0, "Numbers without Cyrillic are not touched")
local mixed = newNode(230, 400)
local mixedText = "Герой 英雄 получает щит на 120 единиц и усиливает союзников рядом"
ok = sourceSet(mixed, mixedText)
check(ok, "Mixed Cyrillic and CJK text is wrapped")
assertBreaksOnSpaces(mixedText, mixed.text)

-- A word wider than the line is cut only inside itself; a word that needs too many measurements keeps the field native.
local wide = newNode(100, 400)
local wideText = "Это сверхдлинноесловобезпробелов в описании"
wide.text = wideText
ok, reason = _AceRuWrap(wide, wideText)
check(ok == false and wide.horizontalOverflow == 0 and wide.text == wideText, "Too-wide word is preserved whole")
local huge = newNode(30, 4096)
huge.text = "Это " .. string.rep("я", 400)
ok, reason = _AceRuWrap(huge, huge.text)
check(ok == false and huge.text == "Это " .. string.rep("я", 400) and huge.horizontalOverflow == 0, "Word needing more than 40 measures is refused whole")

-- Links and inline graphics (<a href=N>, <quad>, <sprite>) are accepted; broken or foreign tags still refuse the field.
local caoCao = "<a href=11><color=#ffffff>\226\149\172<b>Подготовка</b></color></a>: активирует <a href=1><color=#ffffff>\226\149\168<b>Выход на поле</b></color></a> 1 случайного героя «Троецарствия»."
local linkNode = newNode(230, 800)
ok, reason = sourceSet(linkNode, caoCao)
check(ok and reason == "wrapped", "Link markup of the Cao Cao card wraps: " .. tostring(reason))
assertBreaksOnSpaces(caoCao, linkNode.text)
check(widthOf(linkNode.text) <= 229 and linkNode.text:find("случайного", 1, true), "Link text fits the line, words stay whole")
check(select(2, linkNode.text:gsub("<a href=11>", "")) == 1 and select(2, linkNode.text:gsub("</a>", "")) == 2, "Link tags are kept")
_AceRuWrapRestore(linkNode)
check(linkNode.text == caoCao and linkNode.horizontalOverflow == 0, "Link field restores")
for _, text in ipairs({
    "<a>Ссылка на описание</a> и ещё немного слов для переноса строки",
    "Значок <quad material=1 size=20 x=0 y=0 width=1 height=1/> перед длинным описанием умения героя",
    "Значок <sprite name=gold> перед длинным описанием умения героя в окне",
    "Значок <quad path=ArtHub/Icon/Item/ZiChan/icon_huobi_zuanshi size=40> перед длинным описанием умения героя",
    "Значок <quad width=2.5 scale=1.5 path=12 type=0> перед длинным описанием умения героя в окне",
    "Значок <quad path=Emoji_3 width=1.2 yoff=-3> перед длинным описанием умения героя в окне",
    "Условия <a href=https://game.qq.com/tencent_other_contract.shtml>соглашения</a> и ещё слова для переноса",
}) do
    local node = newNode(230, 800)
    ok, reason = sourceSet(node, text)
    check(ok, "Link/inline graphic accepted: " .. tostring(reason))
    assertBreaksOnSpaces(text, node.text)
    _AceRuWrapRestore(node)
end
for _, text in ipairs({
    "<a href=11>Ссылка без закрытия и ещё слова",
    "Лишнее закрытие</a> и ещё слова в строке",
    "<a href=\"x<y\">Кривая ссылка</a> со словами",
    "<quad width=1<b>Кривой</b>",
}) do
    local node = newNode(230, 800)
    ok, reason = _AceRuSpaceWrap(node, text)
    check(ok == false and (reason == "unknown-markup" or reason == "unbalanced-markup"), "Bad markup refused: " .. text)
end
-- Неизвестные теги непрозрачны (нулевая ширина, без стека); нестандартные параметры известных тегов балансируются.
for _, text in ipairs({
    "<wobble=1>Неизвестный тег</wobble> и ещё слова для переноса строки",
    "<unknown>Текст</unknown> и ещё немного слов для переноса строки",
    "<color=red>Цвет по имени</color> и ещё слова для переноса строки",
    "<size=12.5>Дробный размер</size> и ещё слова для переноса строки",
}) do
    local node = newNode(230, 800)
    ok, reason = sourceSet(node, text)
    check(ok, "Unknown tag is opaque, field accepted: " .. tostring(reason))
    assertBreaksOnSpaces(text, node.text)
    _AceRuWrapRestore(node)
end
for _, text in ipairs({ "<color=red>Цвет без закрытия и ещё слова", "Лишнее закрытие</color> и ещё слова в строке" }) do
    local node = newNode(230, 800)
    ok, reason = _AceRuSpaceWrap(node, text)
    check(ok == false and reason == "unbalanced-markup", "Unbalanced known tag is refused: " .. text)
end

-- Длинный абзац с <color>, открытым до жёсткого переноса: замеры идут по абзацам, число замеров ограничено.
_AceRuWrapCache = setmetatable({}, { __mode = "k" })
local words = {}
for k = 1, 65 do words[k] = "слово" .. k end
local longText = "<color=#FFAA00>" .. table.concat(words, " ", 1, 30) .. "\n" .. table.concat(words, " ", 31, 65) .. "</color> конец"
local longNode = newNode(230, 2000)
ok, reason, calls = sourceSet(longNode, longText)
check(ok and reason == "wrapped", "Long colored paragraph wraps: " .. tostring(reason))
assertBreaksOnSpaces(longText, longNode.text)
local longAtoms = select(2, longText:gsub("%S+", ""))
local longLines = select(2, longNode.text:gsub("\n", "")) + 1
check(widthOf(longNode.text) <= 229, "Long paragraph lines fit")
check(calls <= longAtoms + 2 * longLines + 8, "Measurements stay near atoms + 2 * lines + 8: " .. calls .. " vs " .. (longAtoms + 2 * longLines + 8))
check(select(2, longNode.text:gsub("<color=#FFAA00>", "")) == 1 and select(2, longNode.text:gsub("</color>", "")) == 1, "Tags of the long paragraph are kept")

do
    -- Цепочка RectTransform: rectTransform -> GetRectTransform() -> transform.
    local viaMethod = newNode(160, 400)
    viaMethod.rectTransform = nil
    ok, reason = sourceSet(viaMethod, input)
    check(ok, "GetRectTransform() is used when rectTransform is absent: " .. tostring(reason))
    local viaTransform = newNode(160, 400)
    viaTransform.rectTransform = nil
    viaTransform.GetRectTransform = nil
    viaTransform.transform = { rect = viaTransform.rect, owner = viaTransform }
    ok, reason = sourceSet(viaTransform, input)
    check(ok, "transform is the last resort: " .. tostring(reason))
    local noRect = newNode(160, 400)
    noRect.rectTransform = nil
    noRect.GetRectTransform = nil
    ok, reason = sourceSet(noRect, input)
    check(not ok and reason == "native-layout-unavailable" and noRect.text == input and noRect.horizontalOverflow == 0, "No RectTransform at all keeps the field native")
    local raising = newNode(160, 400)
    raising.rectTransform = nil
    local raisingMeta = getmetatable(raising)
    local plainIndex = raisingMeta.__index
    raisingMeta.__index = function(t, key)
        if key == "rectTransform" then error("rectTransform getter rejected") end
        return plainIndex(t, key)
    end
    ok, reason = sourceSet(raising, input)
    check(ok, "A raising rectTransform getter falls through to GetRectTransform(): " .. tostring(reason))

    -- Ширина меньше 24: ждём реальную раскладку, не меняем её из помощника.
    local narrowRect = newNode(160, 400)
    narrowRect.rect.width = 10
    local parentCalls = 0
    narrowRect.rectTransform.parent = { onRebuild = function() parentCalls = parentCalls + 1; narrowRect.rect.width = 160 end }
    ok, reason = sourceSet(narrowRect, input)
    check(not ok and parentCalls == 0 and narrowRect.rect.width == 10 and narrowRect.text == input, "Collapsed width is deferred without a speculative rebuild: " .. tostring(reason))
    narrowRect.rect.width = 160
    ok, reason = sourceSet(narrowRect, input)
    check(ok, "The same field can wrap after its real width becomes available: " .. tostring(reason))

    -- Неизвестный тег: не длиннее 96 байт целиком.
    local tag96 = "<" .. string.rep("x", 94) .. ">"
    local tag97 = "<" .. string.rep("x", 95) .. ">"
    check(#tag96 == 96 and #tag97 == 97, "Fixture tag lengths")
    local tagNode = newNode(230, 800)
    ok, reason = sourceSet(tagNode, "Слово " .. tag96 .. "тест и ещё слова")
    check(ok, "Opaque tag of 96 bytes is accepted: " .. tostring(reason))
    tagNode = newNode(230, 800)
    ok, reason = _AceRuSpaceWrap(tagNode, "Слово " .. tag97 .. "тест и ещё слова")
    check(ok == false and reason == "unknown-markup", "Opaque tag of 97 bytes is refused")

    -- Движок рисует непрозрачный тег буквально: финальный замер ширины или замер слова откатывает поле.
    local literalTail = newNode(100, 800)
    literalTail.literalTags = true
    local tailText = "Слово ещё " .. string.rep("<wobble=1>", 4)
    ok, reason = sourceSet(literalTail, tailText)
    check(not ok and tostring(reason):find("native-final-width-overflow", 1, true) and literalTail.text == tailText and literalTail.horizontalOverflow == 0, "Literally drawn trailing tags roll the field back: " .. tostring(reason))
    local literalHead = newNode(100, 800)
    literalHead.literalTags = true
    local headText = string.rep("<wobble=1>", 6) .. " Слово ещё"
    ok, reason = sourceSet(literalHead, headText)
    check(not ok and literalHead.text == headText and literalHead.horizontalOverflow == 0, "Literally drawn leading tags roll the field back: " .. tostring(reason))
end

do
    -- Лог диагностики: общий счётчик в _G на все чанки, не более 200 строк, первая запись сессии "w", только коды причин.
    local function makeIo()
        local log = { lines = {}, modes = {}, opened = 0, closed = 0 }
        return { open = function(path, mode)
            log.opened = log.opened + 1
            log.modes[#log.modes + 1] = mode
            local handle = {}
            function handle:write(...)
                if log.failWrite then error("disk full") end
                log.lines[#log.lines + 1] = (table.concat({ ... }):gsub("\n$", ""))
            end
            function handle:close() log.closed = log.closed + 1 end
            return handle
        end }, log
    end
    local badText = "<b>Секретный незакрытый текст"
    local function failOnce(content)
        local node = newNode(160, 400)
        node.text = content
        return _AceRuWrap(node, content)
    end
    os = { getenv = function(name) return name == "TEMP" and "C:\\Tmp" or nil end }

    -- chunk-loaded: один раз на чанк и только через _AceRuMark (диагностическая сборка).
    rawset(_G, "__AceRuWrapLogN", nil)
    local stubIo, log = makeIo()
    io = stubIo
    local marked = newNode(160, 400)
    _AceRuMark(marked)
    _AceRuMark(marked)
    check(#log.lines == 1 and log.lines[1]:match(" %- chunk%-loaded$"), "chunk-loaded is written once, by the diagnostic mark only: ")
    check(log.modes[1] == "w", "First record of the session truncates the file")

    -- Два «чанка» делят счётчик: после предустановленных 199 строк пишется ровно одна.
    rawset(_G, "__AceRuWrapLogN", 199)
    stubIo, log = makeIo()
    io = stubIo
    failOnce(badText)
    failOnce(badText)
    check(#log.lines == 1 and log.modes[1] == "a", "Counter shared through _G caps all chunks at 200 lines and appends after the first write")

    -- 250 отказов -> ровно 200 строк, первая в режиме "w", остальные "a", дескриптор закрыт каждый раз.
    rawset(_G, "__AceRuWrapLogN", nil)
    stubIo, log = makeIo()
    io = stubIo
    for _ = 1, 250 do failOnce(badText) end
    check(#log.lines == 200 and log.opened == 200 and log.closed == 200, "At most 200 lines per session")
    check(log.modes[1] == "w" and log.modes[2] == "a" and log.modes[200] == "a", "Only the first write of a session truncates")
    for _, line in ipairs(log.lines) do
        check(line:match("^[%w_%.%-%?]+ [%w_%.%-%?]+ unbalanced%-markup$") and not line:find("Секретный", 1, true), "Log carries a reason code only: " .. line)
    end

    -- Пустой content не пишется; текст исключения превращается в lua-error; ошибка write не мешает закрыть дескриптор.
    rawset(_G, "__AceRuWrapLogN", nil)
    stubIo, log = makeIo()
    io = stubIo
    failOnce("")
    check(#log.lines == 0, "Empty content is not logged")
    _AceRuWrapLog("m", "f", "x.lua:12: attempt to index a nil value (secret text)")
    _AceRuWrapLog("m", "f", { "table reason" })
    check(#log.lines == 2 and log.lines[1] == "m f lua-error" and log.lines[2] == "m f lua-error", "Non-whitelisted reasons collapse to lua-error")
    _AceRuWrapLog("m", "f", "chunk:12: native-height-overflow")
    check(log.lines[3] == "m f native-height-overflow", "The position prefix of assert() is dropped, the code stays")
    log.failWrite = true
    local before = log.closed
    check(pcall(_AceRuWrapLog, "m", "f", "code") and log.closed == before + 1, "A failing write still closes the handle")

    -- Нет io, os или debug: тихо ничего, поле откатывается как обычно.
    rawset(_G, "__AceRuWrapLogN", nil)
    io = nil
    ok, reason = failOnce(badText)
    check(ok == false and reason == "unbalanced-markup" and pcall(_AceRuWrapLog, "m", "f", "code"), "Works with io = nil")
    io = stubIo
    log.failWrite = false
    os = nil
    ok, reason = failOnce(badText)
    check(ok == false and reason == "unbalanced-markup", "Works with os = nil")
    os = { getenv = function() return nil end }
    check(pcall(_AceRuWrapLog, "m", "f", "code"), "Works without TEMP")
    debug = nil
    ok, reason = failOnce(badText)
    check(ok == false and reason == "unbalanced-markup" and pcall(_AceRuMark, newNode(160, 400), false, "x"), "Works with debug = nil")
    os, debug, io = realOs, realDebug, nil
end

-- Diagnostic marks.
local mark = newNode(160, 400)
mark.text = "Заголовок"
_AceRuMark(mark)
check(mark.text == "Заголовок·", "Mark without a verdict means only that the Lua ran")
mark.text = "Заголовок"
_AceRuMark(mark, true)
check(mark.text == "Заголовок··", "Accepted field is marked with two dots")
for reasonText, code in pairs({ ["locale-guard"] = "l", ["unbreakable-word"] = "u", ["native-height-overflow"] = "h", ["no-cyrillic"] = "c", ["reentrant"] = "r", ["???"] = "x" }) do
    mark.text = "Заголовок"
    _AceRuMark(mark, false, "x.lua:1: " .. reasonText)
    check(mark.text == "Заголовок·" .. code, "Reason code of " .. reasonText)
end
check(pcall(_AceRuMark, nil, true) and pcall(_AceRuMark, hostile, false, "x"), "Marking never raises")

-- Loop pattern of the chess-player viewer (texts[i].text = ...; helper call per element; restore by field in vOnUninitialize).
local viewer = { age = newNode(160, 400), identity = newNode(160, 400), character1 = newNode(160, 400) }
local loopTexts = { viewer.age, viewer.identity, viewer.character1 }
local loopData = { "23 года", "Потомственный мастер игры на доске", "Характер: спокойный, внимательный и очень упрямый игрок" }
for i = 1, #loopTexts do
    loopTexts[i].text = loopData[i]
    _AceRuWrap(loopTexts[i])
end
check(viewer.age.text == loopData[1], "Short loop element keeps its exact text")
check(viewer.character1.text:find("\n", 1, true) and widthOf(viewer.character1.text) <= 159, "Long loop element wraps on spaces")
for i = 1, #loopTexts do loopTexts[i].text = loopData[i]; _AceRuWrap(loopTexts[i]) end
local nilLoop = { nil, viewer.age }
check(pcall(function() for i = 1, 2 do _AceRuWrap(nilLoop[i]) end end), "Loop over a list with a nil element does not raise")
for _, name in ipairs({ "age", "identity", "character1" }) do pcall(_AceRuWrapRestore, viewer[name]) end
check(viewer.character1.text == loopData[3] and viewer.character1.horizontalOverflow == 0 and viewer.character1.resizeTextForBestFit == false, "Loop element restored by field on uninitialize")

-- ===== v15: живой layout, pooled-состояние, кеш размеров и последствия rebuild =====
do
    local UI = CS.UnityEngine.UI
    local LE, CSF, HVG, GRID = { "LE" }, { "CSF", FitMode = { PreferredSize = "PS", Unconstrained = "U" } }, { "HVG" }, { "GRID" }
    UI.LayoutElement, UI.ContentSizeFitter, UI.HorizontalOrVerticalLayoutGroup, UI.GridLayoutGroup = LE, CSF, HVG, GRID
    typeof = function(x) return x end
    local function boxed(width, height, comps, parentComps, native, ours)
        local node = newNode(width, height)
        node.heightNative, node.heightOurs = native, ours
        local rt = node.rectTransform
        rt.GetComponent = function(_, kind)
            if comps == "raise" then error("destroyed object") end
            return comps[kind]
        end
        rt.parent.GetComponent = function(_, kind) return parentComps and parentComps[kind] or nil end
        return node
    end
    local fitter = { enabled = true, verticalFit = "PS" }
    local function modeOf(node) return _AceRuWrapHeightMode(node.rectTransform) end
    local function cleanCache() _AceRuWrapCache = setmetatable({}, { __mode = "k" }) end
    cleanCache()
    local auto = boxed(160, 40, { [CSF] = fitter }, nil, 60, 88)
    auto.autoHeight = true
    local before = rebuildCalls
    ok, reason = sourceSet(auto, input)
    check(ok and auto.rect.height == 88 and rebuildCalls == before + 1, "A proven fitter grows through its live layout and is verified: " .. tostring(reason))
    check(_AceRuWrapLast.grown == true and select(2, modeOf(auto)) == "fitter", "The measured growth and layout type are retained for diagnostics")
    ok, reason = sourceSet(auto, input)
    check(ok and reason == "cached" and rebuildCalls == before + 1, "Verified actual dimensions are cached after growth")
    _AceRuWrapRestore(auto)
    check(auto.text == input and auto.horizontalOverflow == 0 and auto.rect.height == 60,
        "Unload recalculates a proven layout with its original text, clearing the helper's grown height")
    local fixedFitter = boxed(160, 40, { [CSF] = fitter }, nil, 60, 88)
    before = rebuildCalls
    ok, reason = sourceSet(fixedFitter, input)
    check(not ok and fixedFitter.text == input and fixedFitter.horizontalOverflow == 0 and rebuildCalls == before + 2,
        "A fitter that does not grow rejects the wrap and rebuilds once more with native text")
    local huge = boxed(160, 40, { [CSF] = fitter }, nil, 30, 88)
    before = rebuildCalls
    ok, reason = sourceSet(huge, input)
    check(not ok and tostring(reason):find("native-height-growth", 1, true) and rebuildCalls == before, "Growth cap is checked before any rebuild")
    for _, config in ipairs({
        { { [LE] = { enabled = true, ignoreLayout = false, preferredHeight = 40, minHeight = 0 }, [CSF] = fitter }, nil, "fixed", "le" },
        { {}, { [GRID] = { enabled = true } }, "fixed", "group" },
        { {}, { [HVG] = { enabled = true, childControlHeight = false } }, "fixed", "none" },
        { "raise", nil, "unknown", "read-fail" },
        { {}, nil, "fixed", "none" },
    }) do
        local field = boxed(160, 40, config[1], config[2], 60, 88)
        field.verticalOverflow = 1
        before = rebuildCalls
        ok, reason = sourceSet(field, input)
        local mode, source = modeOf(field)
        check(not ok and mode == config[3] and source == config[4] and field.text == input, "Fixed/unreadable layout never inherits permission to spill")
        check(rebuildCalls == before, "Unproven growth performs no rebuild")
    end
    local grouped = boxed(160, 40, {}, { [HVG] = { enabled = true, childControlHeight = true } }, 60, 88)
    grouped.autoHeight = true
    check(sourceSet(grouped, input) and select(2, modeOf(grouped)) == "group", "A live parent group can grow its text box")
    -- Те же pooled-узел и текст, но fitter выключен и высота снова фиксирована: старое решение auto не применяется.
    cleanCache()
    local liveFitter = { enabled = true, verticalFit = "PS" }
    local pooled = boxed(160, 40, { [CSF] = liveFitter }, nil, 60, 88)
    pooled.autoHeight = true
    check(sourceSet(pooled, input), "Pooled node initially has an auto layout")
    liveFitter.enabled, pooled.autoHeight, pooled.rect.height = false, false, 40
    before = rebuildCalls
    ok, reason = sourceSet(pooled, input)
    check(not ok and pooled.text == input and pooled.horizontalOverflow == 0 and modeOf(pooled) == "fixed" and rebuildCalls == before,
        "Pooled fields re-read fitter enabled state and actual height instead of using stale cache")
    -- Reparent при сохранных размерах тоже сбрасывает кеш решения.
    pooled = boxed(160, 400, {}, nil, 60, 88)
    check(sourceSet(pooled, input), "Pooled node starts with a valid box")
    pooled.rectTransform.parent = { GetComponent = function() return nil end }
    ok, reason = sourceSet(pooled, input)
    check(ok and reason ~= "cached", "Reparenting invalidates a cached decision")
    -- Игра заново включила BestFit на том же узле: не выключаем его при отказе/выгрузке.
    pooled.resizeTextForBestFit = true
    ok, reason = sourceSet(pooled, input)
    check(not ok and reason == "native-best-fit" and pooled.resizeTextForBestFit == true and pooled.horizontalOverflow == 0,
        "A pooled native BestFit change is respected instead of restored from old helper state")
    _AceRuWrapRestore(pooled)
    check(pooled.resizeTextForBestFit == true, "Unload never overwrites new native typography")
    -- Выгрузка после нового текста/стиля игры не воскрешает прошлое значение.
    pooled = boxed(160, 400, {}, nil, 60, 88)
    check(sourceSet(pooled, input), "Restoration fixture wraps")
    pooled.text, pooled.horizontalOverflow, pooled.fontSize, pooled.lineSpacing = "Новый текст игры", 0, 24, 1.5
    _AceRuWrapRestore(pooled)
    check(pooled.text == "Новый текст игры" and pooled.horizontalOverflow == 0 and pooled.fontSize == 24 and pooled.lineSpacing == 1.5,
        "Restoration only removes helper-owned values and preserves new game state")
    -- При rebuild изменилась ширина: перенос рассчитан уже не для этого поля, нужен полный откат.
    local resized = boxed(160, 40, { [CSF] = fitter }, nil, 60, 88)
    resized.autoHeight = true
    resized.rectTransform.parent.onRebuild = function() resized.rect.width = 120 end
    ok, reason = sourceSet(resized, input)
    check(not ok and tostring(reason):find("native-layout-changed", 1, true) and resized.text == input and resized.horizontalOverflow == 0,
        "Width changes during rebuild reject an obsolete wrap")
    -- Смена размеров может происходить прямо в text setter, в том числе при применении кеша.
    for _, useCache in ipairs({ false, true }) do
        local changing = boxed(160, 400, {}, nil, 60, 88)
        if useCache then check(sourceSet(changing, input), "The cache side-effect fixture initially wraps") end
        local meta, plainSet = getmetatable(changing), getmetatable(changing).__newindex
        meta.__newindex = function(n, key, value)
            plainSet(n, key, value)
            if key == "text" and value:find("\n", 1, true) then changing.rect.width = 120 end
        end
        ok, reason = sourceSet(changing, input)
        check(not ok and changing.text == input and changing.horizontalOverflow == 0 and changing.rect.width == 120,
            "A text setter's changed width rejects both freshly measured and cached output")
    end
    -- Память одноимённого поля не даёт ширину свёрнутому соседу, включая специально подложенный старый memo.
    cleanCache()
    rawset(_G, "__AceRuWidthMemo", setmetatable({ count = 1 }, { __index = function() return 320 end }))
    for _, initialWidth in ipairs({ 80, 320 }) do
        local twin = boxed(initialWidth, 400, {}, nil, 60, 88)
        twin.gameObject = { name = "Txt_Name" }
        twin.transform = { parent = { gameObject = { name = "Item" } } }
        sourceSet(twin, "Длинная подпись героя")
        twin.rect.width = 0
        twin.rectTransform.anchorMin, twin.rectTransform.anchorMax, twin.rectTransform.sizeDelta = { x = 0 }, { x = 1 }, { x = 0 }
        twin.rectTransform.parent.rect = { width = 320, height = 400 }
        before = rebuildCalls
        ok, reason = sourceSet(twin, "Длинная подпись героя")
        check(not ok and reason == "native-layout-unavailable" and twin.horizontalOverflow == 0 and rebuildCalls == before,
            "No previous/native/parent/name width can authorize a collapsed field")
    end
    rawset(_G, "__AceRuWidthMemo", nil)
    -- Варианты API rebuild выбираются детерминированно и используются только при доказанном auto layout.
    check(_AceRuWrapRebuild(newNode(10, 10).rectTransform, true) == 3 and _AceRuWrapRebuild(nil) == nil, "Rebuild reports the working variant")
    ForceRebuildLayoutImmediate = function() end
    check(_AceRuWrapRebuild(newNode(10, 10).rectTransform, true) == 1, "The native global rebuild is preferred")
    ForceRebuildLayoutImmediate = nil
    UIUtility = { ForceRebuildLayoutImmediate = function() end }
    check(_AceRuWrapRebuild(newNode(10, 10).rectTransform, true) == 2, "UIUtility is the next rebuild variant")
    UIUtility, typeof = nil, nil
end

return assertions
