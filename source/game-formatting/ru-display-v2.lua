-- Только проверенные числовые подписи. Данные матча, цены и счётчики не меняются.
do
    local name = "_AceRuNumericDisplayV2"
    local api = rawget(_G, name)
    if api == nil then
        local scalePowers = {
            InMillionsOfUnit = 4,
            InHundredMillionOfUnit = 8,
            Operation_Rank_BigNumAbbreviation_TenThousand = 4,
            GamePreviewCntFormat = 4,
            OperationNewbinMarkerOfW = 4,
            OperationGlobalRankItemDesc1 = 4,
            SeasonJourney_KingNeed = 4,
        }
        local plain = { "{0} дес. тыс.", "{0}×10 тыс." }
        local templates = {
            InMillionsOfUnit = plain,
            InHundredMillionOfUnit = { "{0} сот. млн", "{0}×100 млн" },
            Operation_Rank_BigNumAbbreviation_TenThousand = plain,
            GamePreviewCntFormat = plain,
            OperationNewbinMarkerOfW = plain,
            OperationGlobalRankItemDesc1 = { "{0} десятков тысяч игроков", "{0}×10 тыс. игроков" },
            SeasonJourney_KingNeed = { "Короли сервера: {0} дес. тыс.", "Короли на сервере: {0}×10 тыс." },
        }
        local nativeGetter
        local wrapped = setmetatable({}, { __mode = "k" })
        local sentinel = "_AceRuNumericDisplayV2_"
        local safeInteger = 9007199254740991

        local function russian()
            if type(nativeGetter) ~= "function" then return false end
            local ok, text = pcall(nativeGetter, "TitleSetting")
            return ok and text == "Настройки"
        end

        local function knownTemplate(key)
            local ok, text = pcall(nativeGetter, key, sentinel)
            if not ok or type(text) ~= "string" then return false end
            for _, template in ipairs(templates[key]) do
                if text == (template:gsub("{0}", sentinel)) then return true end
            end
            return false
        end

        local function canonicalInteger(text)
            local negative = text:sub(1, 1) == "-"
            if negative or text:sub(1, 1) == "+" then text = text:sub(2) end
            if not text:match("^%d+$") or #text > 24 then return nil end
            text = text:gsub("^0+", "")
            if text == "" then return "0" end
            return (negative and "-" or "") .. text
        end

        -- Строковый аргумент масштаба не превращается в double: десятичная
        -- точка сдвигается ровно на4/8 позиций, исходное округление сохраняется.
        local function shiftedString(value, power)
            if #value > 64 then return nil end
            local text = value:match("^%s*(.-)%s*$")
            local sign = text:sub(1, 1)
            if sign == "+" or sign == "-" then text = text:sub(2) else sign = "" end
            local whole, fraction = text:match("^(%d+)%.(%d+)$")
            if whole == nil then
                if not text:match("^%d+$") then return nil end
                whole, fraction = text, ""
            end
            if #whole + #fraction > 24 then return nil end
            local base
            if #fraction <= power then
                base = whole .. fraction .. string.rep("0", power - #fraction)
            else
                -- Эти ключи показывают целые счётчики. Дробная базовая величина
                -- означает непроверенную схему, её оставляем штатному formatter.
                local tail = fraction:sub(power + 1)
                if tail:find("[1-9]") then return nil end
                base = whole .. fraction:sub(1, power)
            end
            return canonicalInteger(sign .. base)
        end

        local function baseInteger(value, power)
            if type(value) == "string" then return shiftedString(value, power) end
            if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then return nil end
            local scaled = value * (10 ^ power)
            if scaled ~= scaled or math.abs(scaled) > safeInteger then return nil end
            local magnitude = math.abs(scaled)
            local nearest = math.floor(magnitude)
            if magnitude - nearest >= 0.5 then nearest = nearest + 1 end
            -- Деление целого счётчика на10000 может дать погрешность double.
            -- Исправляем только след машинной погрешности, а не дробные данные.
            if math.abs(magnitude - nearest) > 0.0000001 then return nil end
            return canonicalInteger((scaled < 0 and "-" or "") .. string.format("%.0f", nearest))
        end

        local function groupedInteger(digits)
            local first = #digits % 3
            if first == 0 then first = 3 end
            local groups = { digits:sub(1, first) }
            for index = first + 1, #digits, 3 do
                groups[#groups + 1] = digits:sub(index, index + 2)
            end
            return table.concat(groups, " ")
        end

        local function compact(base)
            local negative = base:sub(1, 1) == "-"
            local digits = negative and base:sub(2) or base
            local power, unit = 0, ""
            if #digits >= 10 then power, unit = 9, " млрд."
            elseif #digits >= 7 then power, unit = 6, " млн."
            elseif #digits >= 4 then power, unit = 3, " тыс." end
            local text = digits
            if power > 0 then
                local split = #digits - power
                local fraction = digits:sub(split + 1):gsub("0+$", "")
                if #fraction > 2 then
                    -- Длинную дробь не округляем: точное целое остаётся
                    -- читаемым без изменения исходного количества.
                    text, unit = groupedInteger(digits), ""
                else
                    text = digits:sub(1, split)
                    if fraction ~= "" then text = text .. "," .. fraction end
                end
            end
            return (negative and "-" or "") .. text .. unit
        end

        api = { version = 2 }
        function api.tryScaled(key, ...)
            if type(key) ~= "string" or scalePowers[key] == nil or select("#", ...) ~= 1 then return nil end
            local base = baseInteger(select(1, ...), scalePowers[key])
            if base == nil or not russian() or not knownTemplate(key) then return nil end
            local text = compact(base)
            if key == "OperationGlobalRankItemDesc1" then return text .. " игроков" end
            if key == "SeasonJourney_KingNeed" then return "Короли на сервере: " .. text end
            return text
        end

        function api.tryShare(damage)
            if type(damage) ~= "number" or damage ~= damage or damage < 10000 or damage > safeInteger then return nil end
            if not russian() then return nil end
            -- Штатный FormatDamage отбрасывает остаток до сотен, а затем
            -- показывает x.xxW. Сохраняем именно эту квантованную величину.
            local hundreds = math.floor(damage / 100)
            local base = canonicalInteger(string.format("%.0f", hundreds) .. "00")
            if base == nil then return nil end
            return compact(base)
        end

        function api.attach(localize, getter)
            if type(localize) ~= "table" or type(localize.GetString) ~= "function" or type(getter) ~= "function" then return end
            nativeGetter = getter
            if wrapped[localize] == localize.GetString then return end
            local original = localize.GetString
            local wrapper = function(key, ...)
                if type(key) == "string" and scalePowers[key] ~= nil then
                    local accepted, text = pcall(api.tryScaled, key, ...)
                    if accepted and type(text) == "string" then return text end
                end
                return original(key, ...)
            end
            wrapped[localize] = wrapper
            localize.GetString = wrapper
        end
        rawset(_G, name, api)
    end
    if type(api) == "table" and api.version == 2 and type(api.attach) == "function" then
        api.attach(Localize, CSLuaCallCSharpInteraction.GetLocalizeString)
    end
end
