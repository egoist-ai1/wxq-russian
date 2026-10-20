-- ДИАГНОСТИЧЕСКИЙ СЛОЙ (только сборка build_lua_package.py --diag-log). Дописывается ПОСЛЕ ru-space-wrap.lua в тот же чанк;
-- сам помощник не меняется: слой перекрывает локальное имя _AceRuWrap обёрткой, которая вызывает прежнюю функцию и пишет числа в журнал.
-- Журнал: <TEMP>\ace-ru-diag.log (запасные папки: TMP, %LOCALAPPDATA%\Temp, Unity temporaryCachePath; нет io - CS.UnityEngine.Debug.Log, затем print).
-- Каждая строка: "<os.clock или -> <вид> ...". Виды: env/sink (по разу за сессию), chunk (загрузка чанка, до 400), call (вызов _AceRuWrap, до 4000),
-- rep (подряд идущие одинаковые call: поле+статус+путь схлопываются в одну строку "rep xN"), sweep (обход UI, см. _AceRuDiagSweep, до 400),
-- err (сбой слоя, до 100). v13: файл не перезаписывается, а дополняется; первая запись сессии - заголовок "session <os.date>",
-- файл больше 4 МБ в начале сессии обрезается. Счётчики общие для всех чанков (_G.__AceRuDiag*).
-- В журнал попадают только числа, коды причин и имена модуля/поля: ни текста поля, ни текста исключений. Всё внутри pcall.
-- Подстановки сборщика: модуль и ресурс конкретного чанка.
local _AceRuDiagModule = "@@AceRuDiagModule@@"
local _AceRuDiagResource = "@@AceRuDiagResource@@"
local _AceRuDiagCaps = { chunk = { "__AceRuDiagChunkN", 400 }, call = { "__AceRuDiagCallN", 4000 }, err = { "__AceRuDiagErrN", 100 }, probe = { "__AceRuDiagProbeN", 120 }, exp = { "__AceRuDiagExpN", 120 },
    rep = { "__AceRuDiagRepN", 1500 }, sweep = { "__AceRuDiagSweepN", 400 } }
local _AceRuDiagMaxFileBytes = 4194304

local function _AceRuDiagClean(value, tail)
    if type(value) ~= "string" then return "?" end
    local text = string.gsub(value, "[^%w_%.%-]", "?")
    if #text > 64 then text = tail and string.sub(text, -64) or string.sub(text, 1, 64) end
    if #text == 0 then return "?" end
    return text
end

-- Код причины как в основном логе: позиция "чанк:строка: " отбрасывается, остаток обязан быть одним кодом из белого списка.
local function _AceRuDiagCode(reason)
    local code = reason
    if type(code) == "string" then code = string.match(code, "^[^\n]-:%d+: (.*)$") or code end
    if type(code) ~= "string" or #code > 48 or not string.match(code, "^[%w%-]+$") then return "lua-error" end
    return code
end

local function _AceRuDiagNumber(value)
    if type(value) == "number" and value == value and value > -1e9 and value < 1e9 then return string.format("%.1f", value) end
    return "?"
end

-- Папки для файла журнала в порядке предпочтения: {метка, путь}.
local function _AceRuDiagFolders()
    local list = {}
    pcall(function()
        local value = os.getenv("TEMP")
        if type(value) == "string" and #value > 0 then list[#list + 1] = { "temp", value } end
    end)
    pcall(function()
        local value = os.getenv("TMP")
        if type(value) == "string" and #value > 0 then list[#list + 1] = { "tmp", value } end
    end)
    pcall(function()
        local value = os.getenv("LOCALAPPDATA")
        if type(value) == "string" and #value > 0 then list[#list + 1] = { "localappdata", value .. "\\Temp" } end
    end)
    pcall(function()
        local value = CS.UnityEngine.Application.temporaryCachePath
        if type(value) == "string" and #value > 0 then list[#list + 1] = { "unitycache", (string.gsub(value, "/", "\\")) } end
    end)
    return list
end

-- Одна физическая запись. Возвращает метку приёмника ("temp", ..., "unitylog", "print") или nil.
local function _AceRuDiagSink(line)
    local chosen = rawget(_G, "__AceRuDiagFolder")
    local first = not rawget(_G, "__AceRuDiagOpened")
    local folders = chosen and { chosen } or _AceRuDiagFolders()
    for _, folder in ipairs(folders) do
        local path = folder[2] .. "\\ace-ru-diag.log"
        local ok, file = pcall(function() return io.open(path, "a") end)
        if ok and file then
            if first then
                -- Начало сессии: слишком большой файл обрезается (режим "w"), затем заголовок сессии.
                local size
                pcall(function() size = file:seek("end") end)
                if type(size) == "number" and size > _AceRuDiagMaxFileBytes then
                    pcall(function() file:close() end)
                    local reopenOk, again = pcall(function() return io.open(path, "w") end)
                    if not (reopenOk and again) then reopenOk, again = pcall(function() return io.open(path, "a") end) end
                    file = reopenOk and again or file
                end
                local stamp = "?"
                pcall(function() stamp = tostring(os.date("%Y-%m-%d %H:%M:%S")) end)
                pcall(function() file:write("session ", stamp, "\n") end)
            end
            pcall(function() file:write(line, "\n") end)
            pcall(function() file:close() end)
            rawset(_G, "__AceRuDiagFolder", folder)
            rawset(_G, "__AceRuDiagOpened", true)
            return folder[1]
        end
    end
    if pcall(function() CS.UnityEngine.Debug.Log("[ace-ru-diag] " .. line) end) then return "unitylog" end
    if pcall(print, "[ace-ru-diag] " .. line) then return "print" end
    return nil
end

local function _AceRuDiagEmit(kind, body)
    pcall(function()
        local cap = _AceRuDiagCaps[kind]
        if not cap then return end
        local count = rawget(_G, cap[1]) or 0
        if type(count) ~= "number" or count >= cap[2] then return end
        rawset(_G, cap[1], count + 1)
        local stamp = "-"
        pcall(function() stamp = string.format("%.2f", os.clock()) end)
        if not rawget(_G, "__AceRuDiagEnv") then
            rawset(_G, "__AceRuDiagEnv", true)
            local function has(value) return value ~= nil and "1" or "0" end
            local unity = "0"
            pcall(function() unity = has(CS.UnityEngine.Debug.Log) end)
            local folders = {}
            for _, folder in ipairs(_AceRuDiagFolders()) do folders[#folders + 1] = folder[1] end
            local opener = "0"
            pcall(function() opener = type(io.open) == "function" and "1" or "0" end)
            local label = _AceRuDiagSink(stamp .. " env io=" .. has(io) .. " open=" .. opener .. " os=" .. has(os) .. " debug=" .. has(debug)
                .. " unity=" .. unity .. " folders=" .. (#folders > 0 and table.concat(folders, ",") or "none") .. " rid=" .. _AceRuDiagClean(_AceRuDiagResource))
            _AceRuDiagSink(stamp .. " sink " .. tostring(label))
        end
        _AceRuDiagSink(stamp .. " " .. kind .. " " .. body)
    end)
end

-- Сбой слоя: только код, без текста исключения.
local function _AceRuDiagError(code)
    _AceRuDiagEmit("err", _AceRuDiagCode(code) .. " " .. _AceRuDiagClean(_AceRuDiagModule))
end

-- Heartbeat: чанк с помощником загружен (значит, override-Lua прочитан и выполнен).
pcall(function()
    local source = "?"
    pcall(function() source = debug.getinfo(1, "S").short_src or "?" end)
    _AceRuDiagEmit("chunk", _AceRuDiagClean(_AceRuDiagModule) .. " rid=" .. _AceRuDiagClean(_AceRuDiagResource) .. " src=" .. _AceRuDiagClean(source, true))
end)

-- Путь игрового объекта поля от корня: только имена объектов (не текст поля), не более 12 уровней, хвост до 120 знаков.
local function _AceRuDiagPath(node)
    local names = {}
    local function nameOf(object)
        local ok, value = pcall(function() return object.name end)
        if ok and type(value) == "string" then return (string.gsub(value, "[^A-Za-z0-9_%.%-]", "?")) end
        return "?"
    end
    local go = node.gameObject
    if go == nil then return "?" end
    names[1] = nameOf(go)
    local current
    pcall(function() current = go.transform end)
    for _ = 1, 12 do
        local parent
        if current == nil or not pcall(function() parent = current.parent end) or parent == nil then break end
        local holder
        pcall(function() holder = parent.gameObject end)
        names[#names + 1] = nameOf(holder or parent)
        current = parent
    end
    local reversed = {}
    for k = #names, 1, -1 do reversed[#reversed + 1] = names[k] end
    local path = table.concat(reversed, "/")
    if #path > 120 then path = string.sub(path, -120) end
    if #path == 0 then return "?" end
    return path
end

-- Разовая проба движка за сессию: имена свойств и полей компонента поля (и его базовых классов), подходящие под маску
-- Wrap|Break|Line|Opt|Kinsoku|Following. Только имена членов и классов, ни значений, ни текста.
-- Не молчит: если у узла нет GetType (или чтение/вызов падает), пишется "err probe-<код>" - по одной строке на код за сессию.
-- Проба повторяется на следующих вызовах не более 3 раз (узел мог быть не тем типом).
local function _AceRuDiagProbeFail(code)
    local flag = "__AceRuDiagProbeFail_" .. code
    if rawget(_G, flag) then return end
    rawset(_G, flag, true)
    _AceRuDiagError("probe-" .. code)
end

local function _AceRuDiagProbe(node)
    if node == nil or rawget(_G, "__AceRuDiagProbed") then return end
    local tries = rawget(_G, "__AceRuDiagProbeTries") or 0
    if type(tries) ~= "number" or tries >= 3 then return end
    rawset(_G, "__AceRuDiagProbeTries", tries + 1)
    local getType = nil
    local readOk = pcall(function() getType = node.GetType end)
    if not readOk then return _AceRuDiagProbeFail("gettype-read-failed") end
    if getType == nil then return _AceRuDiagProbeFail("no-gettype") end
    local typeOk, firstType = pcall(function() return node:GetType() end)
    if not typeOk then return _AceRuDiagProbeFail("gettype-call-failed") end
    if firstType == nil then return _AceRuDiagProbeFail("gettype-nil") end
    rawset(_G, "__AceRuDiagProbed", true)
    local masks = { "wrap", "break", "line", "opt", "kinsoku", "following" }
    local function matches(name)
        local lower = string.lower(name)
        for _, mask in ipairs(masks) do
            if string.find(lower, mask, 1, true) then return true end
        end
        return false
    end
    local ok = pcall(function()
        local current = firstType
        for _ = 1, 6 do
            if current == nil then break end
            local className = "?"
            pcall(function() className = _AceRuDiagClean(tostring(current.Name)) end)
            _AceRuDiagEmit("probe", "class " .. className)
            -- 54 = Instance | Public | NonPublic | DeclaredOnly: члены только этого уровня иерархии.
            for _, kind in ipairs({ "prop", "field" }) do
                local members
                local gotOk = pcall(function()
                    if kind == "prop" then members = current:GetProperties(54) else members = current:GetFields(54) end
                end)
                if gotOk and members ~= nil then
                    pcall(function()
                        for i = 0, members.Length - 1 do
                            local name = members[i].Name
                            if type(name) == "string" and matches(name) then
                                _AceRuDiagEmit("probe", kind .. " " .. className .. "." .. _AceRuDiagClean(name))
                            end
                        end
                    end)
                else
                    _AceRuDiagEmit("probe", kind .. " " .. className .. " unavailable")
                    _AceRuDiagProbeFail(kind == "prop" and "no-getproperties" or "no-getfields")
                end
            end
            local nextType
            if not pcall(function() nextType = current.BaseType end) then break end
            current = nextType
        end
    end)
    if not ok then _AceRuDiagEmit("probe", "unavailable") end
end

-- SHA-256 на чистом Lua (5.3+: целые, побитовые операторы, string.pack). Только для файлов размером до 256 КБ.
local _AceRuDiagSha256K = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
}

local function _AceRuDiagSha256(message)
    if type(message) ~= "string" or #message > 262144 then return nil end
    local K = _AceRuDiagSha256K
    local function rotr(x, n) return ((x >> n) | (x << (32 - n))) & 0xffffffff end
    local h0, h1, h2, h3, h4, h5, h6, h7 = 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    local length = #message
    local padded = message .. "\128" .. string.rep("\0", (55 - length) % 64) .. string.pack(">I8", length * 8)
    for position = 1, #padded, 64 do
        local w = {}
        for i = 0, 15 do w[i] = string.unpack(">I4", padded, position + i * 4) end
        for i = 16, 63 do
            local s0 = rotr(w[i - 15], 7) ~ rotr(w[i - 15], 18) ~ (w[i - 15] >> 3)
            local s1 = rotr(w[i - 2], 17) ~ rotr(w[i - 2], 19) ~ (w[i - 2] >> 10)
            w[i] = (w[i - 16] + s0 + w[i - 7] + s1) & 0xffffffff
        end
        local a, b, c, d, e, f, g, h = h0, h1, h2, h3, h4, h5, h6, h7
        for i = 0, 63 do
            local S1 = rotr(e, 6) ~ rotr(e, 11) ~ rotr(e, 25)
            local ch = (e & f) ~ ((~e & 0xffffffff) & g)
            local t1 = (h + S1 + ch + K[i + 1] + w[i]) & 0xffffffff
            local S0 = rotr(a, 2) ~ rotr(a, 13) ~ rotr(a, 22)
            local maj = (a & b) ~ (a & c) ~ (b & c)
            local t2 = (S0 + maj) & 0xffffffff
            h, g, f, e, d, c, b, a = g, f, e, (d + t1) & 0xffffffff, c, b, a, (t1 + t2) & 0xffffffff
        end
        h0, h1, h2, h3 = (h0 + a) & 0xffffffff, (h1 + b) & 0xffffffff, (h2 + c) & 0xffffffff, (h3 + d) & 0xffffffff
        h4, h5, h6, h7 = (h4 + e) & 0xffffffff, (h5 + f) & 0xffffffff, (h6 + g) & 0xffffffff, (h7 + h) & 0xffffffff
    end
    return string.format("%08x%08x%08x%08x%08x%08x%08x%08x", h0, h1, h2, h3, h4, h5, h6, h7)
end

-- Разовая проверка (г): грузится ли override-версия правил переноса. Только тип, длина и SHA-256 содержимого, не сам файл.
local function _AceRuDiagAssetProbe()
    if rawget(_G, "__AceRuDiagAssetDone") then return end
    rawset(_G, "__AceRuDiagAssetDone", true)
    local path = "Font/LineBreakingFollowingCharacters"
    local loaded, asset = pcall(function() return AssetService:LoadPrimitiveAsset(path) end)
    if not loaded then _AceRuDiagEmit("exp", "asset " .. path .. " load-failed"); return end
    if asset == nil then _AceRuDiagEmit("exp", "asset " .. path .. " nil"); return end
    -- Содержимое: строка как есть, иначе .bytes (строка либо byte[] с Length), иначе .text.
    local data
    if type(asset) == "string" then data = asset end
    if data == nil then
        pcall(function()
            local bytes = asset.bytes
            if type(bytes) == "string" then data = bytes; return end
            local count = bytes.Length
            if type(count) == "number" and count <= 262144 then
                local parts = {}
                for i = 0, count - 1 do parts[#parts + 1] = string.char(bytes[i]) end
                data = table.concat(parts)
            end
        end)
    end
    if data == nil then pcall(function() local text = asset.text; if type(text) == "string" then data = text end end) end
    local kind = type(asset)
    if data == nil then _AceRuDiagEmit("exp", "asset " .. path .. " type=" .. kind .. " data=unreadable"); return end
    local okHash, sha = pcall(_AceRuDiagSha256, data)
    _AceRuDiagEmit("exp", "asset " .. path .. " type=" .. kind .. " len=" .. #data .. " sha256=" .. ((okHash and sha) or "unavailable"))
end

-- Однократный эксперимент (на первом принятом поле, всё под pcall, текст и режимы поля восстанавливаются):
--  width: ширина "абвг" против "аб"+C+"вг" для C из {U+00A0, U+2060, U+FEFF, U+2011, U+00AD};
--  wrap: в режиме Wrap число строк и startCharIdx строк генератора для "абвгд абвгд ..." (пробелы и U+00A0 между словами);
--  optmesh: чтение useOptMeshBuilder/UseOptMeshBuilder. В журнал идут числа и имена, не текст поля.
local function _AceRuDiagExperiment(node)
    if node == nil or rawget(_G, "__AceRuDiagExpDone") then return end
    rawset(_G, "__AceRuDiagExpDone", true)
    local saved = {}
    local readOk = pcall(function()
        saved.text, saved.overflow, saved.bestFit = node.text, node.horizontalOverflow, node.resizeTextForBestFit
        assert(type(saved.text) == "string")
    end)
    if not readOk then _AceRuDiagEmit("exp", "state-unreadable"); return end
    local function restore()
        pcall(function() node.horizontalOverflow = saved.overflow end)
        pcall(function() node.resizeTextForBestFit = saved.bestFit end)
        pcall(function() node.text = saved.text end)
    end
    local function number(value) return _AceRuDiagNumber(value) end
    local function modeOf(name)
        local value
        pcall(function() value = CS.UnityEngine.HorizontalWrapMode[name] end)
        return value
    end
    local overflow, wrap = modeOf("Overflow"), modeOf("Wrap")
    local function measure(text)
        local value
        local ok = pcall(function() node.text = text; value = node.preferredWidth end)
        if ok and type(value) == "number" then return value end
        return nil
    end
    local ok = pcall(function()
        if overflow ~= nil then node.horizontalOverflow = overflow end
        node.resizeTextForBestFit = false
        -- 1. Ширина строки с невидимым/неразрывным символом посередине слова.
        local base = measure("абвг")
        local specials = { { "nbsp", "\194\160" }, { "wj", "\226\129\160" }, { "feff", "\239\187\191" }, { "nbhy", "\226\128\145" }, { "shy", "\194\173" } }
        _AceRuDiagEmit("exp", "width base=" .. number(base) .. " plain2=" .. number(measure("аб")))
        for _, row in ipairs(specials) do
            local with = measure("аб" .. row[2] .. "вг")
            local delta = (base and with) and (with - base) or nil
            _AceRuDiagEmit("exp", "width char=" .. row[1] .. " with=" .. number(with) .. " delta=" .. number(delta))
        end
        -- 2. Перенос движка в режиме Wrap на этом поле.
        local fieldWidth
        pcall(function() fieldWidth = node.rectTransform.rect.width end)
        local wordWidth = measure("абвгд") or 0
        local count = 12
        if type(fieldWidth) == "number" and wordWidth > 0 then count = math.max(4, math.min(40, math.ceil(fieldWidth * 3 / wordWidth) + 1)) end
        local function line(variant, separator)
            local words = {}
            for k = 1, count do words[k] = "абвгд" end
            local text = table.concat(words, separator)
            if wrap ~= nil then node.horizontalOverflow = wrap end
            node.resizeTextForBestFit = false
            node.text = text
            local height
            pcall(function() height = node.preferredHeight end)
            local best
            for _, name in ipairs({ "cachedTextGeneratorForLayout", "cachedTextGenerator" }) do
                local generator
                pcall(function() generator = node[name] end)
                if generator == nil then
                    _AceRuDiagEmit("exp", "wrap variant=" .. variant .. " gen=" .. name .. " unavailable")
                else
                    local lineCount, starts, midword = "?", {}, 0
                    pcall(function() lineCount = tostring(generator.lineCount) end)
                    pcall(function()
                        local lines = generator.lines
                        local total = tonumber(lineCount) or lines.Count
                        for i = 0, total - 1 do
                            local startIndex = lines[i].startCharIdx
                            starts[#starts + 1] = tostring(startIndex)
                            if type(startIndex) == "number" and startIndex % 6 ~= 0 then midword = midword + 1 end
                        end
                    end)
                    _AceRuDiagEmit("exp", "wrap variant=" .. variant .. " gen=" .. name .. " words=" .. count .. " fieldW=" .. number(fieldWidth)
                        .. " prefH=" .. number(height) .. " lineCount=" .. lineCount .. " midword=" .. midword .. " starts=" .. (#starts > 0 and table.concat(starts, ",") or "none"))
                end
            end
        end
        line("space", " ")
        line("nbsp", "\194\160")
        if overflow ~= nil then node.horizontalOverflow = overflow end
    end)
    -- 3. Флаг построителя меша: node.<имя>, генератор, статическое поле класса и поиск через рефлексию.
    pcall(function()
        for _, name in ipairs({ "useOptMeshBuilder", "UseOptMeshBuilder" }) do
            local targets = { { "node", function() return node end }, { "gen", function() return node.cachedTextGenerator end },
                { "genLayout", function() return node.cachedTextGeneratorForLayout end }, { "static", function() return CS.UnityEngine.UI.Text end } }
            for _, target in ipairs(targets) do
                local gotOk, value = pcall(function() return target[2]()[name] end)
                local shown = "err"
                if gotOk then
                    if value == nil then shown = "nil" elseif type(value) == "boolean" or type(value) == "number" then shown = tostring(value) else shown = type(value) end
                end
                _AceRuDiagEmit("exp", "optmesh " .. target[1] .. "." .. name .. "=" .. shown)
            end
            local found = "no"
            pcall(function()
                local classType = node:GetType()
                -- 60 = Instance | Static | Public | NonPublic (базовые классы просматриваются сами).
                if classType:GetProperty(name, 60) ~= nil then found = "property" elseif classType:GetField(name, 60) ~= nil then found = "field" end
            end)
            _AceRuDiagEmit("exp", "optmesh reflect." .. name .. "=" .. found)
        end
    end)
    restore()
    if not ok then _AceRuDiagEmit("exp", "aborted") end
end

-- Обход-счётчик (только эта сборка; в игру ничего не пишет): не чаще раза в 2 с, изнутри вызова _AceRuWrap, без своего таймера.
-- Берёт все TimiText под корнем UIContent (Interaction.UILayerService.UIContentTf), до 150 узлов за тик (окно сдвигается по кругу),
-- считает активные (activeInHierarchy) с кириллицей и среди них те, которых нет в _AceRuWrapCache: "unseen" - текст, мимо которого
-- помощник прошёл (поле не вызывает его или отказ без записи в кеш). rej - из unseen те, что помощник видел и отклонил (_AceRuWrapRejected).
local function _AceRuDiagSweep()
    local now
    pcall(function() now = os.clock() end)
    if type(now) ~= "number" then return end
    local last = rawget(_G, "__AceRuDiagSweepAt")
    if type(last) == "number" and now >= last and now - last < 2 then return end
    rawset(_G, "__AceRuDiagSweepAt", now)
    local root
    pcall(function() root = Interaction.UILayerService.UIContentTf end)
    if root == nil then return _AceRuDiagProbeFail("sweep-no-root") end
    local list
    if not pcall(function() list = root:GetComponentsInChildren(typeof(CS.Timi.TimiText), true) end) or list == nil then
        return _AceRuDiagProbeFail("sweep-enum-failed")
    end
    local total = 0
    pcall(function() total = list.Length end)
    if type(total) ~= "number" or total < 0 then return _AceRuDiagProbeFail("sweep-enum-failed") end
    local offset = rawget(_G, "__AceRuDiagSweepOffset")
    if type(offset) ~= "number" or total == 0 then offset = 0 end
    local checked = math.min(total, 150)
    rawset(_G, "__AceRuDiagSweepOffset", total > 0 and (offset + checked) % total or 0)
    local cyr, unseen, rejected, sample = 0, 0, 0, nil
    for step = 0, checked - 1 do
        pcall(function()
            local item = list[(offset + step) % total]
            if item == nil then return end
            if item.gameObject.activeInHierarchy ~= true then return end
            local text = item.text
            if type(text) ~= "string" or not string.find(text, "[\208\209][\128-\191]") then return end
            cyr = cyr + 1
            if _AceRuWrapCache[item] ~= nil then return end
            unseen = unseen + 1
            if _AceRuWrapRejected[item] ~= nil then rejected = rejected + 1 end
            if sample == nil then sample = _AceRuDiagPath(item) end
        end)
    end
    local body = "total=" .. total .. " cyr=" .. cyr .. " unseen=" .. unseen .. " sample=" .. _AceRuDiagClean(sample or "-") .. " chk=" .. checked .. " rej=" .. rejected
    -- Одинаковый результат подряд (тот же экран) не повторяется.
    if rawget(_G, "__AceRuDiagSweepLast") == body then return end
    rawset(_G, "__AceRuDiagSweepLast", body)
    _AceRuDiagEmit("sweep", body)
end

local _AceRuWrapBase = _AceRuWrap
local function _AceRuWrap(node, content)
    pcall(_AceRuDiagSweep)
    local callCount = rawget(_G, "__AceRuDiagCallN") or 0
    -- Лимит журнала исчерпан: прежняя функция без чтения полей.
    if type(callCount) ~= "number" or callCount >= _AceRuDiagCaps.call[2] then return _AceRuWrapBase(node, content) end
    local len, words, width, height, native, overflow = "?", "?", "?", "?", "?", "?"
    local bestFit, narrow = "?", "?"
    -- Конфигурация высоты до вмешательства: fit (verticalFit фиттера), grp (группа родителя _ childControlHeight),
    -- le (preferredHeight _ ignoreLayout), anchY (растянуто ли по вертикали). "none" - компонента нет, "?" - чтение не удалось.
    local fit, grp, le, anchY = "?", "?", "?", "?"
    -- pw: ширина родителя; ax: растянуто ли поле по горизонтали (anchorMin.x ~= anchorMax.x) - условие запасной ширины помощника v12.
    local pw, ax = "?", "?"
    local readOk = pcall(function()
        local text = content
        if text == nil and node ~= nil then text = node.text end
        if type(text) == "string" then
            len = tostring(#text)
            words = tostring(select(2, string.gsub(text, "%S+", "")))
        end
        if node == nil then return end
        local getters = {
            function() return node.rectTransform end,
            function() return node:GetRectTransform() end,
            function() return node.transform end,
        }
        for _, getter in ipairs(getters) do
            local ok, rt = pcall(getter)
            if ok and rt ~= nil then
                local sizeOk = pcall(function()
                    local rect = rt.rect
                    local w, h = rect.width, rect.height
                    if type(w) == "number" then width, height = _AceRuDiagNumber(w), _AceRuDiagNumber(h) end
                end)
                if sizeOk and width ~= "?" then
                    -- Компонент: значение reader или "none" (нет компонента) или "?" (GetComponent/чтение бросили исключение).
                    local function describe(target, typeName, reader)
                        if target == nil then return "?" end
                        local gotOk, comp = pcall(function() return target:GetComponent(typeof(CS.UnityEngine.UI[typeName])) end)
                        if not gotOk then return "?" end
                        if comp == nil then return "none" end
                        local readOk2, text = pcall(reader, comp)
                        if readOk2 and type(text) == "string" then return _AceRuDiagClean(text) end
                        return "?"
                    end
                    pcall(function()
                        fit = describe(rt, "ContentSizeFitter", function(c) return tostring(c.verticalFit) end)
                    end)
                    pcall(function()
                        local parent = rt.parent
                        grp = describe(parent, "HorizontalOrVerticalLayoutGroup", function(c)
                            local name = "HVGroup"
                            pcall(function() name = c:GetType().Name end)
                            return tostring(name) .. "_" .. (c.childControlHeight == true and "1" or "0")
                        end)
                        if grp == "none" then
                            grp = describe(parent, "GridLayoutGroup", function() return "GridLayoutGroup_-" end)
                        end
                    end)
                    pcall(function()
                        le = describe(rt, "LayoutElement", function(c)
                            return _AceRuDiagNumber(c.preferredHeight) .. "_" .. (c.ignoreLayout and "1" or "0")
                        end)
                    end)
                    pcall(function() anchY = rt.anchorMin.y ~= rt.anchorMax.y and "1" or "0" end)
                    pcall(function() ax = rt.anchorMin.x ~= rt.anchorMax.x and "1" or "0" end)
                    pcall(function() pw = _AceRuDiagNumber(rt.parent.rect.width) end)
                    break
                end
            end
        end
        -- Высота до вмешательства помощника: штатная, если поле ещё не в нашем режиме (см. ov).
        pcall(function() native = _AceRuDiagNumber(node.preferredHeight) end)
        pcall(function() overflow = _AceRuDiagClean(tostring(node.horizontalOverflow)) end)
        -- bf: штатный resizeTextForBestFit до вмешательства; lw: ширина поля < 24 (раскладка не готова).
        pcall(function()
            local value = node.resizeTextForBestFit
            if value == true then bestFit = "1" elseif value == false then bestFit = "0" end
        end)
        local numeric = tonumber(width)
        if numeric then narrow = numeric < 24 and "1" or "0" end
    end)
    local called, accepted, reason = pcall(_AceRuWrapBase, node, content)
    if not readOk then _AceRuDiagError("diag-read") end
    if not called then
        _AceRuDiagError("wrap-raised")
        return false, "diag-wrap-raised"
    end
    local status = accepted == true and "ok" or ("refused:" .. _AceRuDiagCode(reason))
    local field, ourHeight, lines = "?", "?", "?"
    local writeOk = pcall(function()
        if node == nil then return end
        pcall(function() field = _AceRuDiagClean(node.gameObject.name) end)
        pcall(function() ourHeight = _AceRuDiagNumber(node.preferredHeight) end)
        pcall(function()
            local text = node.text
            if type(text) == "string" then lines = tostring(select(2, string.gsub(text, "\n", "")) + 1) end
        end)
    end)
    if not writeOk then _AceRuDiagError("diag-post") end
    -- Решение по высоте из помощника (_AceRuWrapLast): mode/src/cap/rbv/grown/rbms; "?" - до этой проверки дело не дошло.
    local mode, src, cap, rbv, grown, rbms = "?", "?", "?", "?", "?", "?"
    -- wsrc: откуда взята ширина (rect/parent/memo/none); over: 1 - штатный текст уже выше коробки и рост разрешён до cap.
    local wsrc, over = "?", "?"
    pcall(function()
        local last = _AceRuWrapLast
        if type(last.mode) == "string" then mode = _AceRuDiagClean(last.mode) end
        if type(last.src) == "string" then src = _AceRuDiagClean(last.src) end
        if type(last.wsrc) == "string" then wsrc = _AceRuDiagClean(last.wsrc) end
        if last.over == true then over = "1" elseif last.over == false then over = "0" end
        cap = _AceRuDiagNumber(last.cap)
        if type(last.rbv) == "number" then rbv = tostring(last.rbv) end
        if last.grown == true then grown = "1" elseif last.grown == false then grown = "0" end
        rbms = _AceRuDiagNumber(last.rbms)
    end)
    local frame = "?"
    pcall(function() frame = tostring(math.floor(CS.UnityEngine.Time.frameCount)) end)
    local path = "?"
    pcall(function() if node ~= nil then path = _AceRuDiagPath(node) end end)
    pcall(_AceRuDiagProbe, node)
    -- fs: кегль до>после решения помощника (целые), how: shrunk | hyphen | spill (иначе "-").
    local fsBefore, fsAfter, how = "?", "?", "-"
    pcall(function()
        local last = _AceRuWrapLast
        if type(last.fs0) == "number" then fsBefore = string.format("%.0f", last.fs0) end
        if type(last.fs1) == "number" then fsAfter = string.format("%.0f", last.fs1) end
        if last.how == "shrunk" or last.how == "hyphen" or last.how == "spill" then how = last.how end
    end)
    local moduleName = _AceRuDiagClean(_AceRuDiagModule)
    -- Подряд идущие одинаковые вызовы (поле + статус + путь) пишутся один раз; повторы считаются и сбрасываются строкой "rep xN".
    local key = field .. " " .. status .. " " .. path
    local function flushRepeats()
        local repeats = rawget(_G, "__AceRuDiagRepeat")
        if type(repeats) == "number" and repeats > 0 then
            _AceRuDiagEmit("rep", "x" .. repeats .. " " .. tostring(rawget(_G, "__AceRuDiagLastKey")))
        end
        rawset(_G, "__AceRuDiagRepeat", 0)
    end
    if rawget(_G, "__AceRuDiagLastKey") == key then
        local repeats = (rawget(_G, "__AceRuDiagRepeat") or 0) + 1
        rawset(_G, "__AceRuDiagRepeat", repeats)
        if repeats >= 50 then flushRepeats() end
        return accepted, reason
    end
    pcall(flushRepeats)
    rawset(_G, "__AceRuDiagLastKey", key)
    _AceRuDiagEmit("call", moduleName .. " " .. field .. " " .. status .. " len=" .. len .. " words=" .. words
        .. " w=" .. width .. " h=" .. height .. " nativeH=" .. native .. " ourH=" .. ourHeight .. " lines=" .. lines
        .. " ov=" .. overflow .. " r=" .. _AceRuDiagCode(reason) .. " bf=" .. bestFit .. " lw=" .. narrow
        .. " mode=" .. mode .. " src=" .. src .. " fit=" .. fit .. " grp=" .. grp .. " le=" .. le .. " anchY=" .. anchY
        .. " cap=" .. cap .. " rbv=" .. rbv .. " grown=" .. grown .. " frame=" .. frame .. " rbms=" .. rbms
        .. " wsrc=" .. wsrc .. " over=" .. over .. " pw=" .. pw .. " ax=" .. ax .. " fs=" .. fsBefore .. ">" .. fsAfter .. " how=" .. how .. " path=" .. path)
    -- Разовые пробы после строки call: файл правил переноса (на первом вызове) и эксперимент с движком (на первом принятом поле).
    pcall(_AceRuDiagAssetProbe)
    if accepted == true then pcall(_AceRuDiagExperiment, node) end
    return accepted, reason
end
