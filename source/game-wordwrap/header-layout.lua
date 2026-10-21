-- Общая русская шапка: штатная раскладка при изменении данных, без таймера и обхода кадров.
-- Только существующие LayoutElement и общий HorizontalLayoutGroup. Шрифты/текст/позиции не меняются.
do
    local key = "_AceRuHeaderLayoutV2"
    local api = rawget(_G, key)
    if api == nil then
        local states = setmetatable({}, { __mode = "k" })
        local busy = setmetatable({}, { __mode = "k" })
        local installed = setmetatable({}, { __mode = "k" })
        local maxDepth, maxChildren, minGap = 4, 16, 24
        local function get(object, field)
            if object == nil or object == false then return nil end
            local ok, value = pcall(function() return object[field] end)
            if ok then return value end
        end
        local function number(value, low, high)
            return type(value) == "number" and value == value and value >= low and value <= high
        end
        local function component(rect, name)
            local ok, value = pcall(function()
                return rect:GetComponent(typeof(CS.UnityEngine.UI[name]))
            end)
            if ok and value ~= false then return value end
        end
        local function rect(node)
            if node == nil or node == false then return nil end
            local candidates = {
                function() return node.rectTransform end,
                function() return node:GetRectTransform() end,
                function() return node end,
            }
            for _, candidate in ipairs(candidates) do
                local ok, value = pcall(candidate)
                if ok and value then
                    local width = get(get(value, "rect"), "width")
                    if number(width, 1, 4096) then return value end
                end
            end
        end
        local function active(node)
            return get(get(node, "gameObject"), "activeInHierarchy") == true
        end
        local function branch(child, parent)
            local current = child
            for _ = 1, maxDepth do
                local nextParent = get(current, "parent")
                if nextParent == parent then return current end
                if nextParent == nil or nextParent == false then return nil end
                current = nextParent
            end
        end
        local function rebuild(root)
            return pcall(function() CS.UnityEngine.UI.LayoutRebuilder.ForceRebuildLayoutImmediate(root) end)
        end
        local function mark(root)
            pcall(function() CS.UnityEngine.UI.LayoutRebuilder.MarkLayoutForRebuild(root) end)
        end
        local function restore(view, rebuildNow)
            local entry = states[view]
            if not entry then return nil end
            states[view] = nil
            for index = #entry.changes, 1, -1 do
                local change = entry.changes[index]
                -- Новое значение от самой игры важнее старого снимка pooled-поля.
                if get(change.object, change.field) == change.value then
                    pcall(function() change.object[change.field] = change.old end)
                end
            end
            if rebuildNow then rebuild(entry.root); mark(entry.root) end
            return entry.root
        end
        local function isRussian(text)
            return type(text) == "string" and string.find(text, "[\208\209][\128-\191]") ~= nil
        end
        local function russianLocale()
            local ok, text = pcall(function() return Localize.GetString("TitleSetting") end)
            return ok and isRussian(text)
        end
        local function findLayout(view, stack)
            local controls = {
                get(view, "widgetCommonDropdownStyle1_Skin2_Top"),
                get(view, "showFilter"), get(view, "showSearchFrame"), get(view, "showTick"),
            }
            local parent = get(stack, "parent")
            for _ = 1, maxDepth do
                if parent == nil or parent == false then return nil end
                local group = component(parent, "HorizontalLayoutGroup")
                if get(group, "enabled") == true and get(group, "childControlWidth") == true then
                    local titleBranch = branch(stack, parent)
                    if titleBranch then
                        for index = 1, 4 do
                            local other = rect(controls[index])
                            if other and active(other) then
                                local controlBranch = branch(other, parent)
                                if controlBranch and controlBranch ~= titleBranch then
                                    local element = component(titleBranch, "LayoutElement")
                                    if get(element, "enabled") == true and get(element, "ignoreLayout") == false then
                                        return parent, group, titleBranch, element
                                    end
                                end
                            end
                        end
                    end
                end
                parent = get(parent, "parent")
            end
        end
        local function availableWidth(root, titleBranch, group, gap)
            local count = get(root, "childCount")
            local width = get(get(root, "rect"), "width")
            if not number(count, 1, maxChildren) or not number(width, 1, 4096) then return nil end
            local padding = get(group, "padding")
            local left, right = get(padding, "left"), get(padding, "right")
            if not number(left, 0, 4096) or not number(right, 0, 4096) then return nil end
            local used, visible = left + right, 0
            for index = 0, count - 1 do
                local ok, child = pcall(function() return root:GetChild(index) end)
                if not ok or not child then return nil end
                local element = component(child, "LayoutElement")
                if active(child) and get(element, "ignoreLayout") ~= true then
                    visible = visible + 1
                    if child ~= titleBranch then
                        local childWidth = get(get(child, "rect"), "width")
                        if not number(childWidth, 0, 4096) then return nil end
                        used = used + childWidth
                    end
                end
            end
            return width - used - math.max(0, visible - 1) * gap
        end
        local function refreshInner(view)
            local restoredRoot = restore(view, true)
            local title = get(view, "title")
            if not russianLocale() or not isRussian(get(title, "text")) then return "not-ru" end
            local titleRect, stack = rect(title), rect(get(view, "stack"))
            if not titleRect or not stack or not active(titleRect) or not branch(titleRect, stack) then
                return "no-rect"
            end
            local root, group, titleBranch, element = findLayout(view, stack)
            if not root then return "no-layout" end
            if restoredRoot ~= root and not rebuild(root) then return "rebuild-failed" end
            -- preferredWidth читается ПОСЛЕ штатного rebuild; скорость чтения/отрисовки не измеряется таймером.
            local preferred = get(title, "preferredWidth")
            local titleWidth = get(get(titleRect, "rect"), "width")
            local branchWidth = get(get(titleBranch, "rect"), "width")
            local oldPreferred, oldMin, oldGap = get(element, "preferredWidth"), get(element, "minWidth"), get(group, "spacing")
            if not number(preferred, 1, 4096) or not number(titleWidth, 1, 4096)
                or not number(branchWidth, 1, 4096) or not number(oldPreferred, -1, 4096)
                or not number(oldMin, -1, 4096) or not number(oldGap, 0, 256) then return "geometry" end
            local wanted = math.ceil(branchWidth + math.max(0, preferred - titleWidth))
            local gap = math.max(oldGap, minGap)
            local available = availableWidth(root, titleBranch, group, gap)
            if not available or wanted > available then return "no-space" end
            local entry = { root = root, changes = {} }
            states[view] = entry
            local function set(object, field, value)
                local old = get(object, field)
                if old == value then return end
                table.insert(entry.changes, { object = object, field = field, old = old, value = value })
                object[field] = value
            end
            set(element, "preferredWidth", math.max(oldPreferred, wanted))
            set(element, "minWidth", math.max(oldMin, wanted))
            set(group, "spacing", gap)
            if #entry.changes == 0 then states[view] = nil; return "native-fit" end
            if not rebuild(root) then restore(view, true); return "rebuild-failed" end
            mark(root)
            return "applied"
        end
        local function refresh(view)
            if view == nil or busy[view] then return "busy" end
            busy[view] = true
            local ok, reason = pcall(refreshInner, view)
            if not ok then restore(view, true) end
            busy[view] = nil
            return ok and reason or "read-failed"
        end
        -- Старое горизонтальное UIMenuRight использует UIWidgets.CMMenu, а не Unity HLG.
        -- Его родной контракт — массив spacing в CMMenu.Init. Меняем только этот аргумент,
        -- после создания всех items; native stack сам размещает и прокручивает элементы.
        local function menuSpacingInner(menu, spacing, style)
            local expectedStyle = ClassLib and ClassLib.UIMenuRightStyle1ItemClass
            if expectedStyle == nil or style ~= expectedStyle or not russianLocale() then return spacing, "context" end
            local owner, mono = get(menu, "_prefab"), get(menu, "_menuMono")
            if owner == nil or mono == nil or get(owner, "_menuCM") ~= menu or get(owner, "_menu") ~= mono
                or get(owner, "_menuRollRectTransform") == nil or get(owner, "_rollContentScrollRect") == nil then
                return spacing, "context"
            end
            local children = get(menu, "_childPrefabsList")
            if type(children) ~= "table" or type(spacing) ~= "table" or #children < 2 or #children > maxChildren
                or #spacing ~= #children then return spacing, "budget" end
            local maximum, nativeSpan, russian = 0, 0, false
            for index = 1, #children do
                local child = children[index]
                local root = rect(get(child, "_rootTf"))
                local gap = spacing[index]
                if not root or not number(gap, 0, 256) then return spacing, "no-geometry" end
                local rootWidth = get(get(root, "rect"), "width")
                if not number(rootWidth, 16, 2048) then return spacing, "no-geometry" end
                nativeSpan = nativeSpan + rootWidth
                -- Два состояния имеют разные native font/style, измеряются оба без переключения состояния.
                local captions = {
                    get(child, "_txt_TabName_selectShrinkText"),
                    get(child, "_txt_TabName_unSelectShrinkText"),
                }
                for variant = 1, 2 do
                    local caption = captions[variant]
                    local text = get(caption, "text")
                    if type(text) ~= "string" or #text > 256 then return spacing, "no-caption" end
                    if isRussian(text) then
                        russian = true
                        local captionRect = rect(caption)
                        local preferred = get(caption, "preferredWidth")
                        local width = get(get(captionRect, "rect"), "width")
                        if not captionRect or not number(preferred, 1, 1024) or not number(width, 1, 2048) then
                            return spacing, "no-measure"
                        end
                        local extra = math.max(0, preferred - width)
                        if extra > 512 then return spacing, "budget" end
                        maximum = math.max(maximum, extra)
                    end
                end
            end
            if not russian or maximum <= 0.5 then return spacing, "native-fit" end
            -- Запас с обеих сторон не предполагает native выравнивание текста или порядок gap в CMMenu.
            -- Это промежуток родной раскладки, а не запись в anchored/local/world position или размер hitbox.
            local wanted = math.ceil(maximum * 2 + minGap)
            if nativeSpan + wanted * #children > 4096 then return spacing, "budget" end
            local result = {}
            for index = 1, #children do result[index] = math.max(spacing[index], wanted) end
            return result, "measured-spacing"
        end
        local function menuSpacing(menu, spacing, style)
            local ok, result, reason = pcall(menuSpacingInner, menu, spacing, style)
            return ok and result or spacing, ok and reason or "read-failed"
        end
        local function install(class, hooks, unload)
            if type(class) ~= "table" then return end
            local marks = installed[class]
            if not marks then marks = {}; installed[class] = marks end
            for _, name in ipairs(hooks) do
                local original = rawget(class, name)
                -- Этот callback объявлен в UIPrefabClass и штатно вызывается также после init.
                -- Остальные методы оборачиваются только у собственного класса, без перехвата чужого наследования.
                if original == nil and name == "vOnPrefabVisibleChanged" then original = get(class, name) end
                if type(original) == "function" and not marks[name] then
                    marks[name] = true
                    class[name] = function(self, ...)
                        local outer = busy[self]
                        busy[self] = true
                        local result = table.pack(pcall(original, self, ...))
                        busy[self] = outer
                        if not result[1] then error(result[2], 0) end
                        if not outer then
                            if name == "vOnPrefabVisibleChanged" and select(1, ...) ~= true then
                                restore(self, true)
                            else
                                refresh(self)
                            end
                        end
                        return table.unpack(result, 2, result.n)
                    end
                end
            end
            local original = rawget(class, unload)
            if type(original) == "function" and not marks[unload] then
                marks[unload] = true
                class[unload] = function(self, ...)
                    restore(self, true)
                    return original(self, ...)
                end
            end
        end
        api = { version = 2, install = install, refresh = refresh, menuSpacing = menuSpacing,
            restore = function(view) return restore(view, true) end }
        rawset(_G, key, api)
    end
    if type(api) == "table" and api.version == 2 and type(api.install) == "function" then
        api.install(UIFullScreenViewTemplateBase,
            { "ResetTitle", "RebuildUnderSubTitleLayout", "vOnPrefabVisibleChanged" }, "vOnUninitialize")
    end
end
