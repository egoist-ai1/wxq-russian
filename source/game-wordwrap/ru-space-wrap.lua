-- Finite native display-field helper. No global text interception.
-- v15 (2026-10-20): перенос только целых слов в реальной коробке и при штатном Wrap без BestFit.
-- Кегль, шрифт, lineSpacing, BestFit, verticalOverflow и размеры не меняются. Рост разрешён только живой раскладке.
-- Состояние pooled-поля читается заново; ширина другого одноимённого поля и прошлый размер не используются.
local _AceRuWrapCache = setmetatable({}, { __mode = "k" })
local _AceRuWrapRejected = setmetatable({}, { __mode = "k" })
local _AceRuWrapRestoring = setmetatable({}, { __mode = "k" })
local _AceRuWrapMaxBytes = 12288
local _AceRuWrapMaxAtoms = 320
local _AceRuWrapMaxHard = 128
local _AceRuWrapMaxMeasures = 800
local _AceRuWrapKnownTags = { b = true, i = true, c = true, color = true, size = true, a = true }

-- Диагностика без интерфейса: строка "модуль поле причина" в %TEMP%\ace-ru-wrap.log.
-- Счётчик общий для всех чанков (_G.__AceRuWrapLogN), не более 200 строк за сессию; первая запись сессии идёт в режиме "w",
-- чтобы файл не рос между сессиями. В лог попадает только код причины из белого списка ^[%w%-]+$ (иначе "lua-error"),
-- никогда текст исключения или поля. Любая ошибка (io/os закрыты, нет TEMP, диск) молча игнорируется; osgame_log не используется.
local function _AceRuWrapLog(module, field, reason)
    pcall(function()
        local count = rawget(_G, "__AceRuWrapLogN") or 0
        if type(count) ~= "number" or count >= 200 then return end
        local code = reason
        -- assert() в движке дописывает позицию "чанк:строка: " - она отбрасывается; остаток обязан быть одним кодом.
        if type(code) == "string" then code = string.match(code, "^[^\n]-:%d+: (.*)$") or code end
        if type(code) ~= "string" or #code > 48 or not string.match(code, "^[%w%-]+$") then code = "lua-error" end
        local function clean(value)
            if type(value) ~= "string" then return "?" end
            return string.sub((string.gsub(value, "[^%w_%.%-]", "?")), 1, 48)
        end
        local folder = os.getenv("TEMP")
        if type(folder) ~= "string" or #folder == 0 then return end
        rawset(_G, "__AceRuWrapLogN", count + 1)
        local ok, file = pcall(io.open, folder .. "\\ace-ru-wrap.log", count == 0 and "w" or "a")
        if not ok or not file then return end
        pcall(function() file:write(clean(module), " ", clean(field), " ", code, "\n") end)
        pcall(function() file:close() end)
    end)
end

-- Пересчёт раскладки: каждый вариант в своём pcall, ошибки молча игнорируются.
-- Возвращает номер первого сработавшего варианта (1-3) или nil.
local function _AceRuWrapRebuild(target, firstOnly)
    if target == nil then return nil end
    local done, variant = false, nil
    local function attempt(call, number)
        if done and firstOnly then return end
        if pcall(call) then
            done = true
            variant = variant or number
        end
    end
    attempt(function() ForceRebuildLayoutImmediate(target) end, 1)
    attempt(function() UIUtility:ForceRebuildLayoutImmediate(target) end, 2)
    attempt(function() CS.UnityEngine.UI.LayoutRebuilder.ForceRebuildLayoutImmediate(target) end, 3)
    return variant
end

-- Последнее решение для диагностического слоя. ModeCache хранит только последний результат, не решение для следующего вызова.
local _AceRuWrapLast = {}
local _AceRuWrapModeCache = setmetatable({}, { __mode = "k" })

-- Компонент или nil. Исключение GetComponent (нет typeof, уничтоженный объект) = чтение не удалось (failed).
local function _AceRuWrapHeightMode(rt)
    local failed = false
    local function component(target, typeName)
        if target == nil then return nil end
        local ok, value = pcall(function() return target:GetComponent(typeof(CS.UnityEngine.UI[typeName])) end)
        if not ok then failed = true; return nil end
        return value
    end
    -- Компонент, чьё свойство бросает исключение (уничтоженный объект), считается отсутствующим.
    local function probe(comp, reader)
        if comp == nil then return nil end
        local ok, value = pcall(reader, comp)
        if ok then return value end
        failed = true
        return nil
    end
    local mode, source
    local le = component(rt, "LayoutElement")
    if probe(le, function(c) return c.enabled and (c.ignoreLayout or c.preferredHeight >= 0 or c.minHeight > 0) end) then
        mode, source = "fixed", "le"
    end
    if not mode then
        local fitter = component(rt, "ContentSizeFitter")
        if probe(fitter, function(c)
            if not c.enabled then return false end
            local fit, wanted = c.verticalFit, CS.UnityEngine.UI.ContentSizeFitter.FitMode.PreferredSize
            return fit ~= nil and wanted ~= nil and (fit == wanted or tostring(fit) == tostring(wanted))
        end) then
            mode, source = "auto", "fitter"
        end
    end
    if not mode then
        local parent
        local parentOk = pcall(function() parent = rt.parent end)
        if not parentOk then failed = true end
        local group = component(parent, "HorizontalOrVerticalLayoutGroup")
        if probe(group, function(c) return c.enabled and c.childControlHeight == true end) then
            mode, source = "auto", "group"
        else
            local grid = component(parent, "GridLayoutGroup")
            if probe(grid, function(c) return c.enabled end) then mode, source = "fixed", "group" end
        end
    end
    if not mode then
        mode, source = failed and "unknown" or "fixed", failed and "read-fail" or "none"
    end
    _AceRuWrapModeCache[rt] = { mode, source }
    return mode, source
end

-- Только геометрия самого поля. Свёрнутый клон не получает ширину родителя, старого экземпляра или одноимённого соседа.
-- Никакой принудительный rebuild не выполняется ради чтения размеров: поле ждёт штатного расчёта раскладки.
local function _AceRuWrapRect(node)
    local getters = {
        function() return node.rectTransform end,
        function() return node:GetRectTransform() end,
        function() return node.transform end,
    }
    for _, getter in ipairs(getters) do
        local ok, rt = pcall(getter)
        if ok and rt ~= nil then
            local read, width = pcall(function() return rt.rect.width end)
            if read and type(width) == "number" then
                return rt, width, width >= 24 and width <= 4096 and "rect" or nil
            end
        end
    end
    return nil
end

local function _AceRuWrapRestore(node, currentContent)
    if node == nil then return end
    _AceRuWrapRejected[node] = nil
    local entry = _AceRuWrapCache[node]
    if not entry then return end
    _AceRuWrapCache[node] = nil
    _AceRuWrapRestoring[node] = true
    -- Возвращаем только наше значение. Новое решение игры (в том числе у повторно использованного узла) сохраняется.
    pcall(function()
        if node.horizontalOverflow == entry.appliedOverflow then node.horizontalOverflow = entry.overflow end
    end)
    local restoredText = false
    pcall(function()
        if currentContent ~= nil then node.text = currentContent; restoredText = true
        elseif node.text == entry.output or node.text == entry.marked then node.text = entry.input; restoredText = true end
    end)
    if restoredText and entry.rebuilt then pcall(function()
        if entry.rt.parent == entry.parent then _AceRuWrapRebuild(entry.parent or entry.rt, true) end
    end) end
    if entry.rt then _AceRuWrapModeCache[entry.rt] = nil end
    _AceRuWrapRestoring[node] = nil
end

-- Токены: атом (слово с тегами; parts = теги и цельные UTF-8 символы), пробелы, жёсткий перенос.
-- Жёсткий перенос хранит open: сырые открытые теги на начало следующего абзаца.
local function _AceRuWrapTokens(text)
    local tokens, stack, atom = {}, {}, {}
    local atomVisible, atomCount, hardCount = false, 0, 0
    local function closings()
        local result = {}
        for n = #stack, 1, -1 do result[#result + 1] = "</" .. stack[n].name .. ">" end
        return table.concat(result)
    end
    local function openings()
        local result = {}
        for n = 1, #stack do result[#result + 1] = stack[n].raw end
        return table.concat(result)
    end
    local function flush()
        if #atom == 0 then return true end
        atomCount = atomCount + 1
        if atomCount > _AceRuWrapMaxAtoms then return false end
        tokens[#tokens + 1] = { kind = "atom", raw = table.concat(atom), parts = atom, visible = atomVisible, close = closings() }
        atom, atomVisible = {}, false
        return true
    end
    local i = 1
    while i <= #text do
        local byte = string.byte(text, i)
        if byte == 60 then
            local ending = string.find(text, ">", i + 1, true)
            if not ending or ending - i > 95 then return nil, "unknown-markup" end
            local tag = string.sub(text, i, ending)
            -- Тег без вложенных < и > допустим; всё остальное (например, "<a href=\"x<y\">") отклоняет поле.
            if not string.match(tag, "^<[^<>]+>$") then return nil, "unknown-markup" end
            local opening
            local closing = string.match(tag, "^</([a-z]+)>$")
            if closing and _AceRuWrapKnownTags[closing] then
                if #stack == 0 or stack[#stack].name ~= closing then return nil, "unbalanced-markup" end
                stack[#stack] = nil
            elseif closing then
                -- Неизвестный закрывающий тег: непрозрачный, нулевой ширины, без стека.
            elseif tag == "<b>" then opening = "b"
            elseif tag == "<i>" then opening = "i"
            elseif string.match(tag, "^<c #%x%x%x%x%x%x>$") or string.match(tag, "^<c #%x%x%x%x%x%x%x%x>$") then opening = "c"
            elseif string.match(tag, "^<color=#%x%x%x%x%x%x>$") or string.match(tag, "^<color=#%x%x%x%x%x%x%x%x>$") then opening = "color"
            elseif string.match(tag, "^<size=%d%d?%d?>$") then opening = "size"
            elseif tag == "<a>" or string.match(tag, "^<a href=[%w_%.%-:/%?&=%%#]+>$") then opening = "a"
            elseif string.match(tag, "^<quad [%w_%.%-=%s/\"']*>$") or string.match(tag, "^<sprite [%w_%.%-=%s/\"']*>$") then
                -- Self-closing inline graphic: no stack entry, occupies width, so the atom counts as visible.
                atomVisible = true
            else
                -- Известное имя с нестандартными параметрами (<color=red>, <size=12.5>) балансируется как обычный тег;
                -- остальные теги непрозрачны: нулевая ширина, без стека.
                local name = string.match(tag, "^<([a-z]+)[ =]")
                if name == "color" or name == "size" or name == "c" or name == "a" then opening = name
                elseif name == "quad" or name == "sprite" then atomVisible = true end
            end
            if opening then
                if #stack >= 8 then return nil, "markup-depth" end
                stack[#stack + 1] = { name = opening, raw = tag }
            end
            atom[#atom + 1] = tag
            i = ending + 1
        elseif byte == 32 then
            if not flush() then return nil, "atom-budget" end
            local ending = i
            while ending < #text and string.byte(text, ending + 1) == 32 do ending = ending + 1 end
            tokens[#tokens + 1] = { kind = "space", raw = string.sub(text, i, ending) }
            i = ending + 1
        elseif byte == 10 or byte == 13 then
            if not flush() then return nil, "atom-budget" end
            hardCount = hardCount + 1
            if hardCount > _AceRuWrapMaxHard then return nil, "paragraph-budget" end
            local ending = i
            if byte == 13 and string.byte(text, i + 1) == 10 then ending = i + 1 end
            tokens[#tokens + 1] = { kind = "hard", raw = string.sub(text, i, ending), open = openings() }
            i = ending + 1
        elseif byte < 32 and byte ~= 9 then
            return nil, "unsupported-control"
        else
            -- Символ целиком: атомы сохраняются без разрывов внутри UTF-8 и внутри слова.
            local need = byte >= 240 and 4 or byte >= 224 and 3 or byte >= 192 and 2 or 1
            local size = 1
            while size < need do
                local following = string.byte(text, i + size)
                if not following or following < 128 or following > 191 then break end
                size = size + 1
            end
            atom[#atom + 1] = string.sub(text, i, i + size - 1)
            if byte ~= 9 then atomVisible = true end
            i = i + size
        end
    end
    if not flush() then return nil, "atom-budget" end
    if #stack ~= 0 then return nil, "unbalanced-markup" end
    return tokens
end

local function _AceRuSpaceWrap(node, content)
    if not node then return false, "missing-node" end
    for _, key in ipairs({ "mode", "src", "cap", "rbv", "grown", "rbms", "wsrc", "over", "nativeH", "boxH", "height", "how", "fs0", "fs1" }) do _AceRuWrapLast[key] = nil end
    local previous = _AceRuWrapCache[node]
    local function reject(reason)
        _AceRuWrapRestore(node, content)
        if reason == "no-cyrillic" or reason == "content-budget" or reason == "single-word" then
            _AceRuWrapRejected[node] = { content, reason }
        end
        return false, reason
    end
    local seen = _AceRuWrapRejected[node]
    if seen and seen[1] == content then return reject(seen[2]) end
    if type(content) ~= "string" or #content == 0 or #content > _AceRuWrapMaxBytes then return reject("content-budget") end
    if not string.find(content, "[\208\209][\128-\191]") then return reject("no-cyrillic") end
    local ruOk, ruTitle = pcall(function() return Localize.GetString("TitleSetting") end)
    if not ruOk or type(ruTitle) ~= "string" or not string.find(ruTitle, "[\208\209]") then
        pcall(function()
            if rawget(_G, "__AceRuWrapGuardLogged") then return end
            rawset(_G, "__AceRuWrapGuardLogged", true)
            _AceRuWrapLog("-", "-", "locale-guard")
        end)
        return reject("locale-guard")
    end
    local tokens, tokenError = _AceRuWrapTokens(content)
    if not tokens then return reject(tokenError) end
    local visibleAtoms = 0
    for _, token in ipairs(tokens) do
        if token.kind == "atom" and token.visible then visibleAtoms = visibleAtoms + 1 end
    end
    -- У одного слова нет допустимой границы переноса. Его не дробим, не снабжаем дефисом и не уменьшаем.
    if visibleAtoms < 2 then return reject("single-word") end
    local snapshot
    local snapshotOk = pcall(function()
        local rt, width, widthSource = _AceRuWrapRect(node)
        _AceRuWrapLast.wsrc = widthSource or "none"
        assert(rt ~= nil and widthSource == "rect")
        local liveOverflow = node.horizontalOverflow
        local baseOverflow = liveOverflow
        if previous and liveOverflow == previous.appliedOverflow then baseOverflow = previous.overflow end
        snapshot = {
            width = width, height = rt.rect.height, rt = rt, parent = rt.parent,
            overflow = baseOverflow, bestFit = node.resizeTextForBestFit, verticalOverflow = node.verticalOverflow,
            font = node.font, fontSize = node.fontSize, fontStyle = node.fontStyle,
            textStyle = node.textStyle, lineSpacing = node.lineSpacing,
        }
        assert(type(snapshot.height) == "number" and snapshot.height == snapshot.height and snapshot.height >= 0 and snapshot.height <= 4096)
        assert(type(snapshot.fontSize) == "number" and snapshot.fontSize > 0 and snapshot.fontSize == snapshot.fontSize)
    end)
    if not snapshotOk then return reject("native-layout-unavailable") end
    _AceRuWrapLast.fs0, _AceRuWrapLast.fs1 = snapshot.fontSize, snapshot.fontSize
    -- Составные подписи с BestFit защищены тем же правилом, что и одиночные. Штатная типографика остаётся у игры.
    if snapshot.bestFit ~= false then return reject("native-best-fit") end
    local wrap, overflow
    local enumOk = pcall(function()
        wrap, overflow = CS.UnityEngine.HorizontalWrapMode.Wrap, CS.UnityEngine.HorizontalWrapMode.Overflow
        assert(wrap ~= nil and overflow ~= nil)
    end)
    if not enumOk then return reject("native-overflow-unavailable") end
    if snapshot.overflow ~= wrap and tostring(snapshot.overflow) ~= tostring(wrap) then return reject("native-nowrap") end
    local mode, source = _AceRuWrapHeightMode(snapshot.rt)
    _AceRuWrapLast.mode, _AceRuWrapLast.src = mode, source
    local fingerprint = {
        snapshot.width, snapshot.height, snapshot.font, snapshot.fontSize, snapshot.fontStyle, snapshot.textStyle,
        snapshot.lineSpacing, snapshot.parent, snapshot.verticalOverflow, mode, source, snapshot.rt,
    }
    local function validateCurrent(height)
        local actual = snapshot.rt.rect
        assert(type(actual.width) == "number" and math.abs(actual.width - snapshot.width) <= 0.5
            and snapshot.rt.parent == snapshot.parent, "native-layout-changed")
        assert(type(actual.height) == "number" and actual.height == actual.height and actual.height >= height - 0.5
            and actual.height <= 4096, "native-height-overflow")
        assert(node.font == snapshot.font and node.fontSize == snapshot.fontSize and node.lineSpacing == snapshot.lineSpacing
            and node.fontStyle == snapshot.fontStyle and node.textStyle == snapshot.textStyle
            and node.verticalOverflow == snapshot.verticalOverflow
            and node.resizeTextForBestFit == snapshot.bestFit, "native-style-changed")
        return actual.height
    end
    local cacheMatch = previous and previous.input == content
    if cacheMatch then
        for n = 1, 12 do
            if previous.fingerprint[n] ~= fingerprint[n] then cacheMatch = false; break end
        end
    end
    if cacheMatch then
        local cacheOk = pcall(function()
            node.horizontalOverflow = overflow
            node.text = previous.output
            validateCurrent(previous.height)
        end)
        if cacheOk then return true, "cached", 0 end
        return reject("native-cache-apply-failed")
    end
    -- На время замеров меняются только наш текст и горизонтальный режим; любой отказ возвращает их сразу.
    local measures, output, inserted = 0, "", 0
    local rebuilt = false
    local function timedRebuild(target)
        local t0, t1
        pcall(function() t0 = os.clock() end)
        local variant = _AceRuWrapRebuild(target, true)
        pcall(function() t1 = os.clock() end)
        _AceRuWrapLast.rbv = variant
        if t0 and t1 then _AceRuWrapLast.rbms = (t1 - t0) * 1000 end
        return variant
    end
    local function rollback()
        pcall(function() node.horizontalOverflow = snapshot.overflow end)
        pcall(function() node.text = content end)
        _AceRuWrapCache[node] = nil
        -- Если живой layout уже пересчитывался с нашим текстом, возвращаем ему штатный текст до повторного расчёта.
        if rebuilt then pcall(_AceRuWrapRebuild, snapshot.parent or snapshot.rt, true) end
    end
    local success, failure = pcall(function()
        node.horizontalOverflow = snapshot.overflow
        node.text = content
        local nativeHeight = node.preferredHeight
        if type(nativeHeight) ~= "number" or nativeHeight ~= nativeHeight or nativeHeight <= 0 then nativeHeight = nil end
        node.horizontalOverflow = overflow
        local limit = snapshot.width - 1
        local function measure(text)
            measures = measures + 1
            assert(measures <= _AceRuWrapMaxMeasures, "measure-budget")
            node.text = text
            local width = node.preferredWidth
            assert(type(width) == "number" and width == width and width >= 0 and width < 1000000, "native-width-unavailable")
            return width
        end
        local open, para, pending, hasWord = "", "", "", false
        for _, token in ipairs(tokens) do
            if token.kind == "space" then pending = pending .. token.raw
            elseif token.kind == "hard" then
                output = output .. para .. pending .. token.raw
                para, pending, hasWord, open = "", "", false, token.open
            else
                local candidate = para .. pending .. token.raw
                if token.visible then
                    local width = measure(open .. candidate .. token.close)
                    assert(width > 0, "native-glyph-width-unavailable")
                    if width > limit then
                        -- Единственное место вставки LF — исходный ASCII-пробел между целыми атомами.
                        assert(hasWord and #pending > 0, "unbreakable-word")
                        local broken = para .. pending .. "\n" .. token.raw
                        if measure(open .. broken .. token.close) > limit then
                            broken = para .. string.sub(pending, 1, -2) .. "\n" .. token.raw
                            assert(measure(open .. broken .. token.close) <= limit, "unbreakable-word")
                        end
                        candidate = broken
                        inserted = inserted + 1
                    end
                    hasWord = true
                end
                para, pending = candidate, ""
            end
        end
        output = output .. para .. pending
        assert(measure(output) <= limit, "native-final-width-overflow")
        local height = node.preferredHeight
        assert(type(height) == "number" and height == height and height > 0, "native-height-unavailable")
        _AceRuWrapLast.nativeH, _AceRuWrapLast.boxH, _AceRuWrapLast.height = nativeHeight, snapshot.height, height
        _AceRuWrapLast.over = nativeHeight ~= nil and nativeHeight > snapshot.height + 0.5
        local spacing = type(snapshot.lineSpacing) == "number" and snapshot.lineSpacing > 0 and snapshot.lineSpacing or 1
        local lineH = 1.2 * snapshot.fontSize * spacing
        local cap = nativeHeight and (nativeHeight + math.max(2 * lineH, 0.15 * nativeHeight)) or nil
        _AceRuWrapLast.cap = cap
        if height > snapshot.height + 0.5 then
            -- Предпочтительная высота нативного текста и verticalOverflow не доказывают наличие свободного места под соседом.
            assert(mode == "auto", "native-height-overflow")
            assert(cap ~= nil and height <= cap, "native-height-growth")
            rebuilt = true
            timedRebuild(snapshot.parent or snapshot.rt)
            local actual = snapshot.rt.rect
            assert(type(actual.height) == "number" and actual.height == actual.height and actual.height >= height - 0.5, "native-height-overflow")
            assert(type(actual.width) == "number" and math.abs(actual.width - snapshot.width) <= 0.5, "native-layout-changed")
            _AceRuWrapLast.grown = actual.height > snapshot.height + 0.5
        end
        fingerprint[2] = validateCurrent(height)
        snapshot.renderedHeight = height
    end)
    if not success then rollback(); return false, tostring(failure), measures end
    if inserted == 0 then
        rollback()
        return true, "unchanged", measures
    end
    if not previous then
        local count = 0
        for _ in pairs(_AceRuWrapCache) do count = count + 1 end
        if count >= 128 then rollback(); return false, "node-cache-budget", measures end
    end
    _AceRuWrapRejected[node] = nil
    _AceRuWrapCache[node] = {
        input = content, output = output, overflow = snapshot.overflow, appliedOverflow = overflow,
        bestFit = snapshot.bestFit, fontSize = snapshot.fontSize, appliedFontSize = snapshot.fontSize,
        fingerprint = fingerprint, rt = snapshot.rt, rebuilt = rebuilt, parent = snapshot.parent, height = snapshot.renderedHeight,
    }
    return true, "wrapped", measures
end

-- Call site entry: never raises, never re-enters, returns (accepted, reason). All Lua errors of the helper stay inside pcall.
local _AceRuWrapBusy = false
local function _AceRuWrap(node, content)
    if node == nil then return false, "missing-node" end
    if _AceRuWrapBusy or _AceRuWrapRestoring[node] then return false, "reentrant" end
    _AceRuWrapBusy = true
    -- Call sites may pass only the node: the text is read here, inside pcall, so a nil field never raises at the call.
    if content == nil then
        local readOk, current = pcall(function() return node.text end)
        content = readOk and current or nil
    end
    local called, done, reason = pcall(_AceRuSpaceWrap, node, content)
    _AceRuWrapBusy = false
    local failure
    if not called then
        pcall(_AceRuWrapRestore, node, content)
        failure = tostring(done)
    elseif done ~= true then
        failure = tostring(reason)
    end
    if failure and type(content) == "string" and #content > 0
        and not string.find(failure, "no%-cyrillic") and not string.find(failure, "locale%-guard") and not string.find(failure, "single%-fits") and not string.find(failure, "single%-word") and not string.find(failure, "native%-best%-fit") then
        -- Отказ записывается в лог: модуль (чанк вызывающего кода), поле (имя объекта), причина.
        -- Уровень 4: анонимная функция -> pcall -> _AceRuWrap -> место вызова.
        local module, field = "?", "?"
        pcall(function() module = debug.getinfo(4, "S").short_src or "?" end)
        pcall(function() field = node.gameObject.name or "?" end)
        _AceRuWrapLog(module, field, failure)
    end
    if not called then return false, failure end
    return done == true, reason
end

-- Diagnostic build only (the builder inserts calls): visible trace on a title.
-- "·" = patched Lua ran; "··" = helper accepted the field; "·<letter>" = helper declined, letter = reason.
local _AceRuReasonCodes = {
    { "locale%-guard", "l" }, { "no%-cyrillic", "c" }, { "content%-budget", "s" }, { "native%-layout", "g" },
    { "native%-overflow", "o" }, { "native%-width", "w" }, { "native%-glyph", "z" }, { "unbreakable", "u" },
    { "native%-height", "h" }, { "native%-final", "f" }, { "measure%-budget", "b" }, { "atom%-budget", "a" },
    { "reentrant", "r" }, { "markup", "m" }, { "unsupported%-control", "k" }, { "paragraph%-budget", "p" }, { "node%-cache", "n" },
    { "single%-fits", "t" }, { "single%-word", "t" }, { "native%-best%-fit", "t" },
}
local _AceRuMarkLogged = false
local function _AceRuMark(node, accepted, reason)
    -- Строка "chunk-loaded" пишется один раз на чанк и только в диагностической сборке (вызовы _AceRuMark вставляет лишь она).
    if not _AceRuMarkLogged then
        _AceRuMarkLogged = true
        pcall(function()
            local chunk = "?"
            pcall(function() chunk = debug.getinfo(1, "S").short_src or "?" end)
            _AceRuWrapLog(chunk, "-", "chunk-loaded")
        end)
    end
    pcall(function()
        local suffix = "·"
        if accepted == true then
            suffix = "··"
        elseif accepted == false then
            local code = "x"
            for _, row in ipairs(_AceRuReasonCodes) do
                if string.find(tostring(reason), row[1]) then code = row[2]; break end
            end
            suffix = "·" .. code
        end
        node.text = tostring(node.text) .. suffix
        local entry = _AceRuWrapCache[node]
        if entry then entry.marked = node.text end
    end)
end
