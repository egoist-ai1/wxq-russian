-- Диагностический слой (--diag-log) в Lua-состоянии игры: heartbeat, строки call, лимиты, io = nil, запасные приёмники, сбои чтения.
-- Помощник вместе со слоем (шаблон с подставленными модулем и ресурсом) вставляется ниже как строка и загружается через load().
local assertions = 0
local function check(value, message)
    assertions = assertions + 1
    assert(value, message)
end
local realIo, realOs, realDebug, realPrint = io, os, debug, print
io, print = nil, function() end
Localize = { GetString = function(key) return "Настройки" end }
CS = { UnityEngine = { HorizontalWrapMode = { Wrap = 0, Overflow = 1 }, VerticalWrapMode = { Truncate = 0, Overflow = 1 }, UI = { LayoutRebuilder = { ForceRebuildLayoutImmediate = function() end } } } }
local SOURCE = [=====[
-- {{HELPER}}
]=====]

local function widthOf(text)
    text = text:gsub("<[^>]*>", "")
    local width, maximum = 0, 0
    for _, code in utf8.codes(text) do
        if code == 10 then maximum = math.max(maximum, width); width = 0
        elseif code == 32 then width = width + 4
        else width = width + 8 end
    end
    return math.max(maximum, width)
end
local function newNode(width, height, name)
    local values = { text = "", font = {}, fontSize = 20, fontStyle = 0, textStyle = 2, lineSpacing = 1, horizontalOverflow = 0, verticalOverflow = 0, resizeTextForBestFit = false }
    local node = { rect = { width = width or 160, height = height or 400 }, gameObject = { name = name or "Title" } }
    function node:GetRectTransform() return { rect = self.rect, owner = self } end
    node.rectTransform = { rect = node.rect, owner = node }
    return setmetatable(node, {
        __index = function(n, key)
            if key == "preferredWidth" then return widthOf(values.text) * values.fontSize / 20 end
            if key == "preferredHeight" then local _, lines = values.text:gsub("\n", ""); return (lines + 1) * 22 end
            return values[key]
        end,
        __newindex = function(n, key, value) values[key] = value end,
    })
end

-- Стенд журнала: io.open пишет в таблицу, os.clock/getenv управляются тестом.
local function reset()
    for _, name in ipairs({ "__AceRuDiagChunkN", "__AceRuDiagCallN", "__AceRuDiagErrN", "__AceRuDiagEnv", "__AceRuDiagOpened", "__AceRuDiagFolder", "__AceRuWrapLogN", "__AceRuDiagProbed", "__AceRuDiagProbeN", "__AceRuDiagExpN", "__AceRuDiagProbeTries", "__AceRuWrapGuardLogged",
        "__AceRuDiagProbeFail_no-gettype", "__AceRuDiagProbeFail_gettype-read-failed", "__AceRuDiagProbeFail_gettype-call-failed", "__AceRuDiagProbeFail_gettype-nil",
        "__AceRuDiagProbeFail_no-getproperties", "__AceRuDiagProbeFail_no-getfields",
        "__AceRuDiagRepN", "__AceRuDiagSweepN", "__AceRuDiagLastKey", "__AceRuDiagRepeat", "__AceRuDiagSweepAt", "__AceRuDiagSweepOffset", "__AceRuDiagSweepLast",
        "__AceRuDiagProbeFail_sweep-no-root", "__AceRuDiagProbeFail_sweep-enum-failed" }) do
        rawset(_G, name, nil)
    end
    -- Опыт и проба ассета одноразовые: по умолчанию выключены, отдельные тесты включают их сами.
    rawset(_G, "__AceRuDiagExpDone", true)
    rawset(_G, "__AceRuDiagAssetDone", true)
end
local function makeIo(failFor)
    local log = { lines = {}, modes = {}, paths = {}, other = {} }
    return { open = function(path, mode)
        if failFor and path:find(failFor, 1, true) then return nil, "denied" end
        -- Основной лог помощника (ace-ru-wrap.log) копится отдельно: здесь проверяется только журнал диагностики.
        local mine = path:find("ace-ru-diag.log", 1, true) ~= nil
        local target = mine and log.lines or log.other
        if mine then
            log.modes[#log.modes + 1] = mode
            log.paths[#log.paths + 1] = path
        end
        local handle = {}
        function handle:write(...) target[#target + 1] = (table.concat({ ... }):gsub("\n$", "")) end
        function handle:close() end
        return handle
    end }, log
end
local function setEnv(temp, tmp, localAppData)
    os = { clock = function() return 12.5 end, date = function() return "2026-10-18 12:00:00" end, getenv = function(name)
        if name == "TEMP" then return temp elseif name == "TMP" then return tmp elseif name == "LOCALAPPDATA" then return localAppData end
    end }
end
-- Загружает «чанк» с помощником; возвращает его _AceRuWrap.
local function loadChunk(module)
    local text = SOURCE:gsub("@@AceRuDiagModule@@", module or "UIDemoClass_AutoBind"):gsub("@@AceRuDiagResource@@", "aaaaaaaaaaaaaaaa")
    local chunk = assert(load(text .. "\nreturn _AceRuWrap", "@Lua/Demo/" .. (module or "UIDemoClass_AutoBind") .. ".lua"))
    return chunk()
end
local function filtered(lines, kind)
    local result = {}
    for _, line in ipairs(lines) do if line:find(" " .. kind .. " ", 1, true) then result[#result + 1] = line end end
    return result
end
local input = "Увеличивает силу атаки героя на 10% и скорость атаки на {0}, пока союзники находятся рядом."
-- Хвост строки call: поля высоты (mode/src/fit/grp/le/anchY/cap/rbv/grown/frame/rbms) идут после lw=; v13: fs=<до>><после> how=<причина>.
local TAIL = " mode=%S+ src=%S+ fit=%S+ grp=%S+ le=%S+ anchY=%S+ cap=%S+ rbv=%S+ grown=%S+ frame=%S+ rbms=%S+ wsrc=%S+ over=%S+ pw=%S+ ax=%S+ fs=%S+ how=%S+ path=%S+$"

-- 1. Загрузка чанка: session, env, sink, chunk; файл только дополняется (режим "a"); папка TEMP; имя файла ace-ru-diag.log.
reset()
setEnv("C:\\Tmp", "C:\\Tmp2", "C:\\Users\\X\\AppData\\Local")
local stub, log = makeIo()
io = stub
local wrap = loadChunk()
check(type(wrap) == "function", "Chunk exposes the wrapped _AceRuWrap")
check(#log.lines == 4, "session + env + sink + chunk after one load: " .. #log.lines)
check(log.modes[1] == "a" and log.modes[2] == "a" and log.modes[3] == "a", "The log is appended to, never truncated at a normal size")
check(log.paths[1] == "C:\\Tmp\\ace-ru-diag.log", "Log file is ace-ru-diag.log in TEMP: " .. tostring(log.paths[1]))
check(log.lines[1] == "session 2026-10-18 12:00:00", "The first record of a session is its header: " .. log.lines[1])
check(log.lines[2]:match("^12%.50 env io=1 open=1 os=1 debug=1 unity=0 folders=temp,tmp,localappdata rid=aaaaaaaaaaaaaaaa$"), "env line: " .. log.lines[2])
check(log.lines[3] == "12.50 sink temp", "sink line: " .. log.lines[3])
check(log.lines[4]:match("^12%.50 chunk UIDemoClass_AutoBind rid=aaaaaaaaaaaaaaaa src=%S+$"), "chunk heartbeat: " .. log.lines[4])
check(log.lines[4]:find("UIDemoClass_AutoBind.lua", 1, true), "chunk line names the chunk from debug.getinfo")
loadChunk("UIOtherClass_AutoBind")
check(#filtered(log.lines, "chunk") == 2 and #log.lines == 5, "Second chunk adds one line; header, env and sink are written once per session")

-- 2. Вызов принят: возвращаемые значения прежние, строка call с числами, без текста поля.
local node = newNode(160, 400, "Description")
node.text = input
local accepted, reason = wrap(node, input)
check(accepted == true and reason == "wrapped", "Return values of the base helper are kept")
local calls = filtered(log.lines, "call")
check(#calls == 1, "One call line")
local line = calls[1]
check(line:match("^12%.50 call UIDemoClass_AutoBind Description ok len=%d+ words=%d+ w=160%.0 h=400%.0 nativeH=%d+%.0 ourH=%d+%.0 lines=%d+ ov=%S+ r=wrapped bf=0 lw=0" .. TAIL), "call ok line: " .. line)
check(line:find("len=" .. #input, 1, true) and line:find("words=15 ", 1, true), "len and words are measured: " .. line)
local lines = tonumber(line:match("lines=(%d+)"))
check(lines and lines >= 2 and select(2, node.text:gsub("\n", "")) + 1 == lines, "lines equals the wrapped line count")
check(not line:find("Увеличивает", 1, true), "Field text is never logged")
check(tonumber(line:match("ourH=([%d%.]+)")) >= tonumber(line:match("nativeH=([%d%.]+)")), "wrapped height is not below the native one")

-- 3. Отказы: no-cyrillic, locale-guard, unbalanced-markup, пустой content; текст не попадает в журнал.
local latin = newNode(160, 400, "Latin")
latin.text = "Plain english text"
local ok, why = wrap(latin, "Plain english text")
check(ok == false and why == "no-cyrillic", "Base verdict is kept for Latin text")
check(filtered(log.lines, "call")[2]:match(" Latin refused:no%-cyrillic len=18 words=3 .* r=no%-cyrillic bf=0 lw=0" .. TAIL), "refused no-cyrillic line: " .. tostring(filtered(log.lines, "call")[2]))
local broken = newNode(160, 400, "Broken")
broken.text = "<b>Секретный незакрытый текст"
ok, why = wrap(broken, broken.text)
check(ok == false and why == "unbalanced-markup", "Unbalanced markup refused")
check(filtered(log.lines, "call")[3]:match(" Broken refused:unbalanced%-markup "), "refused unbalanced-markup line")
Localize = { GetString = function() return "设置" end }
local zh = newNode(160, 400, "Chinese")
ok, why = wrap(zh, input)
check(ok == false and why == "locale-guard" and filtered(log.lines, "call")[4]:match(" Chinese refused:locale%-guard "), "refused locale-guard line")
Localize = { GetString = function(key) return "Настройки" end }
local silent = newNode(160, 400, "Silent")
silent.text = input
ok, why = wrap(silent)
check(ok == true and filtered(log.lines, "call")[5]:match(" Silent ok len=" .. #input .. " "), "Call with the node only reads the field text")
for _, entry in ipairs(log.lines) do
    check(not entry:find("Секретный", 1, true) and not entry:find("Увеличивает", 1, true) and not entry:find("<b>", 1, true), "No field text in any line: " .. entry)
end
check(wrap(nil) == false, "Missing node: still no error")
check(filtered(log.lines, "call")[6]:match(" %? refused:missing%-node len=%? "), "Missing node is logged with question marks")

-- 3б. Поля bf и lw: штатный best-fit (1/0/?), узкое поле < 24 (layout не готов); одиночное слово остаётся штатным.
local single = newNode(100, 400, "Tab")
single.resizeTextForBestFit = false
single.text = "Новички"
ok, why = wrap(single, single.text)
local lastCall = filtered(log.lines, "call")
lastCall = lastCall[#lastCall]
check(ok == false and why == "single-word" and single.text == "Новички" and lastCall:match(" Tab refused:single%-word len=14 words=1 .* r=single%-word bf=0 lw=0" .. TAIL), "single-fits line: " .. tostring(lastCall))
-- v14: BestFit включён игрой - одиночное слово остаётся штатным (single-word), поле не меняется.
local bestFitSingle = newNode(100, 22, "BfSingle")
bestFitSingle.resizeTextForBestFit = true
bestFitSingle.text = "Сверхдлинноеслово"
ok, why = wrap(bestFitSingle, bestFitSingle.text)
lastCall = filtered(log.lines, "call")
lastCall = lastCall[#lastCall]
check(ok == false and why == "single-word" and bestFitSingle.fontSize == 20 and bestFitSingle.resizeTextForBestFit == true and lastCall:match(" BfSingle refused:single%-word .* r=single%-word bf=1 lw=0" .. TAIL), "single-word line: " .. tostring(lastCall))
-- v15: ни маленькое, ни высокое поле не получают уменьшенный кегль или дефис.
for _, height in ipairs({ 22, 60 }) do
    local whole = newNode(100, height, "Whole")
    whole.text = "Сверхдлинноеслово"
    ok, why = wrap(whole, whole.text)
    lastCall = filtered(log.lines, "call")
    check(not ok and why == "single-word" and whole.fontSize == 20 and whole.horizontalOverflow == 0
        and whole.text == "Сверхдлинноеслово" and lastCall[#lastCall]:match(" Whole refused:single%-word "),
        "A single atom is retained whole and the refusal is logged")
end
local bestFitCaption = newNode(100, 120, "BfCaption")
bestFitCaption.resizeTextForBestFit, bestFitCaption.text = true, "Оконный режим"
ok, why = wrap(bestFitCaption, bestFitCaption.text)
lastCall = filtered(log.lines, "call")
check(not ok and why == "native-best-fit" and bestFitCaption.fontSize == 20 and bestFitCaption.resizeTextForBestFit == true
    and bestFitCaption.text == "Оконный режим" and bestFitCaption.horizontalOverflow == 0
    and lastCall[#lastCall]:match(" BfCaption refused:native%-best%-fit .* bf=1 lw=0" .. TAIL),
    "A multiword native BestFit caption remains unchanged and is diagnosed")
local unbreakable = newNode(100, 400, "WholeWords")
unbreakable.text = "Это сверхдлинноеслово"
ok, why = wrap(unbreakable, unbreakable.text)
lastCall = filtered(log.lines, "call")
check(not ok and tostring(why):find("unbreakable-word", 1, true) and unbreakable.fontSize == 20
    and unbreakable.text == "Это сверхдлинноеслово" and lastCall[#lastCall]:match(" WholeWords refused:unbreakable%-word "),
    "An oversized atom inside prose is refused whole and diagnosed")
local noBestFit = newNode(160, 400, "NoBf")
noBestFit.resizeTextForBestFit = false
noBestFit.text = input
wrap(noBestFit, input)
lastCall = filtered(log.lines, "call")
check(lastCall[#lastCall]:match(" NoBf ok .* bf=0 lw=0" .. TAIL), "bf=0 line: " .. lastCall[#lastCall])
local thin = newNode(10, 400, "Thin")
thin.text = input
wrap(thin, input)
lastCall = filtered(log.lines, "call")
check(lastCall[#lastCall]:match(" Thin refused:native%-layout%-unavailable .* bf=0 lw=1" .. TAIL), "lw=1 line: " .. lastCall[#lastCall])
local noRect = newNode(160, 400, "NoRect")
noRect.rectTransform, noRect.GetRectTransform = nil, nil
noRect.text = input
wrap(noRect, input)
lastCall = filtered(log.lines, "call")
check(lastCall[#lastCall]:match(" NoRect refused:native%-layout%-unavailable .* bf=0 lw=%?" .. TAIL), "lw=? line: " .. lastCall[#lastCall])

-- 3в. Поля решения по высоте: поле с ContentSizeFitter и группой родителя, rebuild не нужен (mode=auto src=fitter).
do
    local UI = CS.UnityEngine.UI
    UI.LayoutElement, UI.ContentSizeFitter, UI.HorizontalOrVerticalLayoutGroup, UI.GridLayoutGroup = { "LE" }, { "CSF", FitMode = { PreferredSize = "PS" } }, { "HVG" }, { "GRID" }
    local savedTypeof, savedTime = typeof, CS.UnityEngine.Time
    typeof = function(x) return x end
    CS.UnityEngine.Time = { frameCount = 4242 }
    local parent = { GetComponent = function(_, t) if t == UI.HorizontalOrVerticalLayoutGroup then
        return { enabled = true, childControlHeight = true, GetType = function() return { Name = "VerticalLayoutGroup" } end } end end }
    local fitted = newNode(160, 40, "Fitted")
    fitted.rectTransform.parent = parent
    fitted.rectTransform.anchorMin, fitted.rectTransform.anchorMax = { x = 0, y = 0 }, { x = 1, y = 1 }
    fitted.rectTransform.GetComponent = function(_, t)
        if t == UI.ContentSizeFitter then return { enabled = true, verticalFit = "PS" } end
        if t == UI.LayoutElement then return { enabled = true, ignoreLayout = false, preferredHeight = -1, minHeight = 0 } end
    end
    fitted.text = input
    ok, why = wrap(fitted, input)
    lastCall = filtered(log.lines, "call")
    local entry = lastCall[#lastCall]
    -- Мок штатно не переносит строки (натив = 1 строка), поэтому потолок cap мал и поле отклоняется по росту: важны поля журнала.
    check(ok == false and tostring(why):find("native-height-growth", 1, true) ~= nil,"Fitted field refused by the growth cap: " .. tostring(why))
    check(entry:match(" Fitted refused:native%-height%-growth .* mode=auto src=fitter fit=PS grp=VerticalLayoutGroup_1 le=%-1%.0_0 anchY=1 cap=70%.0 rbv=%? grown=%? frame=4242 rbms=%? wsrc=rect over=%S+ pw=%? ax=%S+ fs=20>%S+ how=%- path=Fitted$"), "Config fields are logged: " .. entry)
    check(not entry:find("Увеличивает", 1, true), "No text in the line")
    typeof, CS.UnityEngine.Time = savedTypeof, savedTime
end

-- 3г. Путь игрового объекта в строке call и разовая проба членов компонента (только имена по маске).
do
    reset()
    setEnv("C:\\Tmp", nil, nil)
    stub, log = makeIo()
    io = stub
    wrap = loadChunk()
    local root = { name = "Canvas" }
    local panel = { name = "Panel", parent = root, gameObject = { name = "Panel" } }
    root.gameObject = { name = "Canvas" }
    local pathNode = newNode(160, 400, "Descr\208\182")
    pathNode.gameObject.transform = { parent = panel, gameObject = pathNode.gameObject }
    local function members(names)
        local list = { Length = #names }
        for k, name in ipairs(names) do list[k - 1] = { Name = name } end
        return list
    end
    local probed = 0
    local derived = { Name = "TimiText", GetProperties = function() probed = probed + 1; return members({ "text", "lineSpacing", "WrapRule", "Following" }) end,
        GetFields = function() return members({ "m_Kinsoku", "m_Color" }) end }
    local base = { Name = "Text", GetProperties = function() return members({ "alignment", "LineBreakRule" }) end,
        GetFields = function() error("denied") end }
    derived.BaseType, base.BaseType = base, nil
    pathNode.GetType = function() return derived end
    pathNode.text = input
    wrap(pathNode, input)
    wrap(pathNode, input)
    wrap(pathNode, input)
    local callLines = filtered(log.lines, "call")
    check(#callLines == 1 and callLines[1]:match(" path=Canvas/Panel/Descr%?%?$"), "call line carries the object path, identical repeats are collapsed: " .. #callLines)
    check(#filtered(log.lines, "rep") == 0, "Repeats are counted silently until the run ends")
    local otherNode = newNode(160, 400, "Other")
    otherNode.text = input
    wrap(otherNode, input)
    local repLines = filtered(log.lines, "rep")
    check(#repLines == 1 and repLines[1]:match("^12%.50 rep x2 Descr%S%S ok Canvas/Panel/Descr%S%S$") ~= nil,"A changed key flushes the run as rep xN: " .. tostring(repLines[1]))
    check(#filtered(log.lines, "call") == 2, "Collapsed repeats are not new call lines")
    local probes = filtered(log.lines, "probe")
    local joined = table.concat(probes, "\n")
    check(probed == 1, "The probe runs once per session")
    check(joined:find("probe class TimiText", 1, true) and joined:find("probe prop TimiText.lineSpacing", 1, true) and joined:find("probe prop TimiText.WrapRule", 1, true)
        and joined:find("probe prop TimiText.Following", 1, true) and joined:find("probe field TimiText.m_Kinsoku", 1, true), "Probe lists members matching the mask: " .. joined)
    check(not joined:find("m_Color", 1, true) and not joined:find("TimiText.text", 1, true) and not joined:find(".alignment", 1, true), "Probe skips members outside the mask")
    check(joined:find("probe class Text", 1, true) and joined:find("probe field Text unavailable", 1, true) and joined:find("Text.LineBreakRule", 1, true), "Probe walks base classes and reports a failed lookup")
    check(not joined:find("Увеличивает", 1, true), "Probe never carries field text")
end

-- 4. Лимиты: 4000 вызовов за сессию (общий счётчик в _G), 400 строк chunk.
reset()
stub, log = makeIo()
io = stub
wrap = loadChunk()
rawset(_G, "__AceRuDiagCallN", 3999)
local capNode = newNode(160, 400, "Cap")
capNode.text = input
for _ = 1, 5 do wrap(capNode, input) end
check(#filtered(log.lines, "call") == 1, "Exactly one call line after the 4000 cap")
capNode.text = "Сброс текста до обёртки при исчерпанном лимите без чтения полей"
check(select(2, wrap(capNode, capNode.text)) ~= nil, "After the cap the base helper still answers")
reset()
stub, log = makeIo()
io = stub
for _ = 1, 450 do loadChunk() end
check(#filtered(log.lines, "chunk") == 400, "Heartbeat is capped at 400 chunk lines: " .. #filtered(log.lines, "chunk"))
reset()
stub, log = makeIo()
io = stub
wrap = loadChunk()
-- Разные поля (иначе подряд идущие одинаковые схлопываются): 4100 вызовов -> ровно 4000 строк call.
for k = 1, 4100 do
    local many = newNode(160, 400, "Many" .. k)
    many.text = input
    wrap(many, input)
end
check(#filtered(log.lines, "call") == 4000, "Call lines are capped at 4000: " .. #filtered(log.lines, "call"))

-- 5. Запасные приёмники: TEMP не открывается -> TMP; нет io -> Unity Debug.Log -> print; нет os и debug -> без ошибок.
reset()
setEnv("C:\\Denied", "C:\\Tmp2", nil)
stub, log = makeIo("C:\\Denied")
io = stub
loadChunk()
check(log.paths[1] == "C:\\Tmp2\\ace-ru-diag.log" and log.lines[3] == "12.50 sink tmp", "Falls back to the next folder when open fails")
reset()
setEnv("C:\\Tmp", nil, nil)
io = nil
local unity = {}
CS.UnityEngine.Debug = { Log = function(text) unity[#unity + 1] = text end }
wrap = loadChunk()
local unityNode = newNode(160, 400, "NoIo")
unityNode.text = input
check(wrap(unityNode, input) == true, "Wrapping works with io = nil")
check(#unity >= 4 and unity[1]:find("[ace-ru-diag] 12.50 env io=0 ", 1, true) == 1 and unity[2]:find("sink unitylog", 1, true), "io = nil is reported by env and goes to Debug.Log: " .. tostring(unity[1]))
local unityCall = false
for _, entry in ipairs(unity) do if entry:find(" call UIDemoClass_AutoBind NoIo ok ", 1, true) then unityCall = true end end
check(unityCall, "Call line reaches Debug.Log")
CS.UnityEngine.Debug = nil
reset()
local printed = {}
print = function(text) printed[#printed + 1] = text end
wrap = loadChunk()
check(#printed == 3 and printed[2]:find("sink print", 1, true), "Without io and Debug.Log the print fallback is used")
print = function() end
reset()
os, debug = nil, nil
io = nil
check(pcall(function() local f = loadChunk(); local n = newNode(160, 400, "Bare"); n.text = input; assert(f(n, input) == true) end), "No io, os and debug: no error")
os, debug = realOs, realDebug

-- 6. Сбои слоя: чтение поля падает (err diag-read), обёртка остаётся прозрачной.
reset()
setEnv("C:\\Tmp", nil, nil)
stub, log = makeIo()
io = stub
wrap = loadChunk()
local hostile = setmetatable({}, { __index = function() error("boom secret") end, __newindex = function() error("boom") end })
local got = { pcall(wrap, hostile) }
check(got[1] == true and got[2] == false, "Hostile node: no error and a refusal")
local errors = filtered(log.lines, "err")
local diagRead = 0
for _, entry in ipairs(errors) do
    if entry:match("^12%.50 err diag%-read UIDemoClass_AutoBind$") then diagRead = diagRead + 1 end
end
check(diagRead == 1, "err line carries a code only: " .. table.concat(errors, " | "))
check(filtered(log.lines, "err")[1] ~= nil, "Probe failures are reported as err lines, not silent")
for _, entry in ipairs(log.lines) do check(not entry:find("secret", 1, true), "Exception text is never logged") end

-- 7. v12: проба типа не молчит, опыт и проба ассета пишут exp-строки и не портят поле.
reset()
setEnv("C:\\Tmp", nil, nil)
stub, log = makeIo()
io = stub
wrap = loadChunk()
local noType = newNode(160, 400, "NoType")
noType.text = input
wrap(noType, input)
local probeErrors = table.concat(filtered(log.lines, "err"), "\n")
check(probeErrors:find("err probe-no-gettype", 1, true), "Missing GetType is reported as err probe-no-gettype: " .. probeErrors)
local failType = newNode(160, 400, "FailType")
failType.text = input
failType.GetType = function() error("boom") end
wrap(failType, input)
check(table.concat(filtered(log.lines, "err"), "\n"):find("err probe-gettype-call-failed", 1, true), "A throwing GetType is reported")
local nilType = newNode(160, 400, "NilType")
nilType.text = input
nilType.GetType = function() return nil end
wrap(nilType, input)
check(table.concat(filtered(log.lines, "err"), "\n"):find("err probe-gettype-nil", 1, true), "A nil GetType result is reported")
check(rawget(_G, "__AceRuDiagProbeTries") == 3, "The probe stops after three tries")
for _, entry in ipairs(log.lines) do check(not entry:find("boom", 1, true), "Probe exception text is never logged") end

reset()
stub, log = makeIo()
io = stub
wrap = loadChunk()
rawset(_G, "__AceRuDiagExpDone", nil)
rawset(_G, "__AceRuDiagAssetDone", nil)
AssetService = { LoadPrimitiveAsset = function(_, path) return path == "Font/LineBreakingFollowingCharacters" and "abc" or nil end }
CS.UnityEngine.HorizontalWrapMode = { Wrap = 0, Overflow = 1 }
local expNode = newNode(160, 400, "Exp")
expNode.text = input
expNode.horizontalOverflow = 0
expNode.resizeTextForBestFit = false
expNode.cachedTextGenerator = { lineCount = 3, lines = { [0] = { startCharIdx = 0 }, { startCharIdx = 6 }, { startCharIdx = 13 } } }
local before = expNode.text
local accepted = wrap(expNode, input)
local expLines = filtered(log.lines, "exp")
local expJoined = table.concat(expLines, "\n")
check(accepted == true, "The experiment does not break wrapping")
check(expJoined:find("asset Font/LineBreakingFollowingCharacters type=string len=3 sha256=ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", 1, true), "Asset probe logs type, length and the SHA-256 of 'abc': " .. expJoined)
check(expJoined:find("exp width base=", 1, true) and expJoined:find("width char=nbsp", 1, true) and expJoined:find("width char=wj", 1, true)
    and expJoined:find("width char=feff", 1, true) and expJoined:find("width char=nbhy", 1, true) and expJoined:find("width char=shy", 1, true), "Width experiment lists all five characters: " .. expJoined)
check(expJoined:find("wrap variant=space gen=cachedTextGenerator", 1, true) and expJoined:find("wrap variant=nbsp gen=cachedTextGenerator", 1, true) and expJoined:find("starts=0,6,13", 1, true), "Wrap experiment logs lineCount and startCharIdx: " .. expJoined)
check(expJoined:find("optmesh node.useOptMeshBuilder=nil", 1, true) and expJoined:find("optmesh reflect.UseOptMeshBuilder=no", 1, true), "OptMesh flag reads are logged")
check(not expJoined:find("Увеличивает", 1, true) and not expJoined:find("абвгд", 1, true), "The experiment never logs text")
local expCount = #expLines
wrap(expNode, input)
check(#filtered(log.lines, "exp") == expCount, "Experiment and asset probe run once per session")
-- После опыта wrap() в этот раз положил свой текст; сам опыт текст/режимы вернул (проверка на отдельном поле без wrap).
reset()
stub, log = makeIo()
io = stub
wrap = loadChunk()
rawset(_G, "__AceRuDiagExpDone", nil)
local refused = newNode(160, 400, "Latin")
refused.text = "Settings"
refused.horizontalOverflow = 0
wrap(refused, "Settings")
check(#filtered(log.lines, "exp") == 0, "No experiment on a refused field")
AssetService = nil
CS.UnityEngine.HorizontalWrapMode = nil

-- 9. Обход-счётчик: раз в 2 с, ничего не пишет в игру, считает активные поля с кириллицей, не попавшие в кеш помощника.
reset()
stub, log = makeIo()
io = stub
wrap = loadChunk()
typeof = function(value) return value end
CS.Timi = { TimiText = {} }
CS.UnityEngine.HorizontalWrapMode = { Wrap = 0, Overflow = 1 }
local sweepA = newNode(160, 400, "SweepA")   -- активный, кириллица, помощник его ещё не видел
local sweepB = newNode(160, 400, "SweepB")   -- неактивный: не считается
local sweepC = newNode(160, 400, "SweepC")   -- активный, но латиница: не считается
sweepA.gameObject.activeInHierarchy, sweepB.gameObject.activeInHierarchy, sweepC.gameObject.activeInHierarchy = true, false, true
sweepA.text, sweepB.text, sweepC.text = input, input, "Settings"
Interaction = { UILayerService = { UIContentTf = { GetComponentsInChildren = function(self, kind)
    return { Length = 3, [0] = sweepA, sweepB, sweepC }
end } } }
local clock = 12.5
os.clock = function() return clock end
local beforeA, beforeB, beforeC = sweepA.text, sweepB.text, sweepC.text
wrap(sweepC, "Settings")
local sweeps = filtered(log.lines, "sweep")
check(#sweeps == 1 and sweeps[1]:match("^12%.50 sweep total=3 cyr=1 unseen=1 sample=%S+ chk=3 rej=0$"), "Sweep counts active Cyrillic fields missing from the cache: " .. table.concat(sweeps, "|"))
check(sweepA.text == beforeA and sweepB.text == beforeB and sweepC.text == beforeC, "The sweep itself writes nothing to the game")
clock = 13.0
wrap(sweepC, "Settings")
check(#filtered(log.lines, "sweep") == 1, "At most one sweep per 2 seconds")
clock = 20.0
wrap(sweepA, input)   -- обход идёт до вызова помощника: поле ещё не в кеше
clock = 30.0
wrap(sweepC, "Settings")
sweeps = filtered(log.lines, "sweep")
check(#sweeps == 2 and sweeps[2]:match(" unseen=0 "), "A field the helper has handled is no longer unseen: " .. table.concat(log.lines, "|", math.max(1, #log.lines - 6)))
-- Без корня обхода: одна запись о причине, остальной слой работает.
reset()
stub, log = makeIo()
io = stub
wrap = loadChunk()
Interaction = nil
clock = 40.0
check(wrap(sweepC, "Settings") == false and #filtered(log.lines, "sweep") == 0, "A missing sweep root does not break the layer")
check(table.concat(filtered(log.lines, "err"), "\n"):find("err probe-sweep-no-root", 1, true), "A missing sweep root is reported once")
Interaction, typeof, CS.Timi = nil, nil, nil

-- 10. Файл журнала больше 4 МБ в начале сессии обрезается (повторное открытие в режиме "w"), меньший - только дополняется.
reset()
local opens = { modes = {}, lines = {} }
local function bigIo(size)
    return { open = function(path, mode)
        if not path:find("ace-ru-diag.log", 1, true) then return nil, "other" end
        opens.modes[#opens.modes + 1] = mode
        local handle = {}
        function handle:seek() return size end
        function handle:write(...) opens.lines[#opens.lines + 1] = (table.concat({ ... }):gsub("\n$", "")) end
        function handle:close() end
        return handle
    end }
end
io = bigIo(5000000)
loadChunk()
check(opens.modes[1] == "a" and opens.modes[2] == "w", "An oversized log is reopened for truncation: " .. table.concat(opens.modes, ","))
check(opens.lines[1] == "session 2026-10-18 12:00:00", "The session header is the first record after truncation")
reset()
opens = { modes = {}, lines = {} }
io = bigIo(1000)
loadChunk()
check(opens.modes[1] == "a" and opens.modes[2] == "a" and not table.concat(opens.modes, ","):find("w", 1, true), "A normal-size log is only appended to")

os, io, print = realOs, nil, realPrint
return assertions
