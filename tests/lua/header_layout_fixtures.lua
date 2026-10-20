-- Реальные методы шаблона выполняются ниже; Unity-геометрия заменена ограниченной моделью.
local count = 0
local function check(value, message)
    assert(value, message)
    count = count + 1
end
ClassLib = { UIPrefabClass = { vOnUninitialize = function() end } }
super = ClassLib.UIPrefabClass
function DeclareClass(name, base)
    local class = setmetatable({}, { __index = base })
    ClassLib[name] = class
    return class
end
function import(_, base) return base end
function auto_bind() end
function EnumEnd(value) return value end
function typeof(value) return value end
local nativeCalls, marks = 0, 0
local function isActive(node)
    return node ~= nil and node ~= false and node.gameObject.activeInHierarchy
end
UIUtility = {
    SetActive = function(_, node, value)
        if node and node.gameObject then
            node.gameObject.activeSelf = not not value
            node.gameObject.activeInHierarchy = not not value
        end
    end,
}
CommonUtility = {
    NotNull = function(_, node) return node ~= nil and node ~= false end,
    IsNull = function(_, node) return node == nil or node == false end,
}
Localize = { GetString = function() return "Настройки" end }
local function nativeRebuild(rect)
    nativeCalls = nativeCalls + 1
    if rect == nil or rect.dead then error("destroyed fixture RectTransform") end
    local group = rect.components.HorizontalLayoutGroup
    if not group or not group.enabled then return end
    local x = group.padding.left
    for _, child in ipairs(rect.children) do
        local element = child.components.LayoutElement
        if child.gameObject.activeInHierarchy and not (element and element.ignoreLayout) then
            local width = child.rect.width
            if group.childControlWidth and element and element.enabled then
                width = math.max(element.minWidth, element.preferredWidth, 0)
            end
            child.rect.width = width
            child.anchoredPosition.x = x
            child.nativeX = x
            if child.title then child.title.rectTransform.rect.width = width end
            x = x + width + group.spacing
        end
    end
    -- preferredWidth становится доступен после штатного rebuild, а не через таймер.
    if rect.afterRebuild then rect.afterRebuild(); rect.afterRebuild = nil end
end
CS = { UnityEngine = { UI = {
    LayoutElement = "LayoutElement", HorizontalLayoutGroup = "HorizontalLayoutGroup",
    LayoutRebuilder = {
        ForceRebuildLayoutImmediate = nativeRebuild,
        MarkLayoutForRebuild = function(rect)
            assert(rect and not rect.dead, "Mark only valid RectTransform")
            marks = marks + 1
        end,
    },
} } }

-- {{BASE_MODULE}}
-- {{BACK_MODULE}}

local Base = ClassLib.UIFullScreenViewTemplateBase
local Back = ClassLib.UIFullScreenViewTemplate_BackClass
local function rect(parent, width, element)
    local value = {
        parent = parent, rect = { width = width, height = 64 },
        anchoredPosition = { x = 0, y = 0 },
        gameObject = { activeSelf = true, activeInHierarchy = true },
        components = {}, children = {},
    }
    if element then
        value.components.LayoutElement = {
            enabled = true, ignoreLayout = false, minWidth = width, preferredWidth = width,
        }
    end
    function value:GetComponent(kind)
        if self.dead then error("destroyed fixture component") end
        return self.components[kind]
    end
    function value:GetChild(index) return self.children[index + 1] end
    if parent then table.insert(parent.children, value); parent.childCount = #parent.children end
    return value
end
local function component(rt)
    local node = { rectTransform = rt, gameObject = rt.gameObject }
    function node:GetRectTransform() return self.rectTransform end
    return node
end
local function newView()
    local root = rect(nil, 1400, false)
    root.components.HorizontalLayoutGroup = {
        enabled = true, childControlWidth = true, spacing = 8,
        padding = { left = 0, right = 0 },
    }
    local stack = rect(root, 180, true)
    local titleRt = rect(stack, 180, false)
    local title = component(titleRt)
    title.text = ""
    title.preferredWidth = 0
    title.fontSize, title.resizeTextForBestFit, title.font = 36, true, "native-title-font"
    stack.title = title
    local dropdown = rect(root, 220, true)
    local filter = rect(root, 170, true)
    local search = rect(root, 240, true)
    local tick = rect(root, 120, true)
    local view = setmetatable({
        title = title, stack = component(stack),
        subtitleTxt1 = { preferredWidth = 0 }, subtitleTxt2 = {}, subtitleTxt3 = {},
        lowerSubtitleStyle = rect(nil, 180, true), lowerSubtitleStyleSmall = rect(nil, 180, true),
        widgetCommonDropdownStyle1_Skin2_Top = component(dropdown),
        showDownPull = dropdown, showFilter = filter, showSearchFrame = search, showTick = tick,
        _contentVisibleMap = {},
        operationSet = rect(nil, 240, true),
    }, { __index = Back })
    function view:SetTitle(text)
        self.title.text = text
        self.title.preferredWidth = utf8.len(text) * 22
    end
    function view:SetSubtitle(text) self.subtitleTxt1.preferredWidth = utf8.len(text) * 16 end
    function view:SetShowTick(value) UIUtility:SetActive(self.showTick, value) end
    function view:SetShowSearchFrame(value) UIUtility:SetActive(self.showSearchFrame, value) end
    function view:SetShowFilter(value) UIUtility:SetActive(self.showFilter, value) end
    function view:SetShowFilterSelect() end
    function view:SetLeftSideEndingSeparator() end
    function view:SetTopRightCornerLocation(value) self.rightLocation = value end
    view._funContentTypeSetter = {
        [EmFullScreenContentType.dropdown] = function(show, param) view:RefreshDropdownOptions(show, param) end,
        [EmFullScreenContentType.filter] = function(show, param) view:RefreshFilter(show, param) end,
        [EmFullScreenContentType.searchFrame] = function(show, param) view:RefreshSearchFrame(show, param) end,
        [EmFullScreenContentType.tick] = function(show, param) view:RefreshTick(show, param) end,
    }
    -- Те же выбранные данные и callback, что до раскладки: правка их не заменяет.
    view.widgetCommonDropdownStyle1_Skin2_Top.selection = "default"
    nativeRebuild(root)
    return view, root, stack, dropdown
end
local function refresh(view, text, mask)
    view:ResetTitle(text or "Матчи мастеров", false, false)
    view:RefreshContentVisibility(mask or (EmFullScreenContentType.dropdown | EmFullScreenContentType.filter), nil, 0)
end
local function gap(view, dropdown)
    return dropdown.nativeX - (view.stack.rectTransform.nativeX + view.title.preferredWidth)
end
local view, root, stack, dropdown = newView()
refresh(view)
check(gap(view, dropdown) >= 24, "TG41: Russian header overlaps the dropdown")
check(view.title.text == "Матчи мастеров" and view.title.fontSize == 36
    and view.title.resizeTextForBestFit and view.title.font == "native-title-font", "Native text and typography survive")
check(view.widgetCommonDropdownStyle1_Skin2_Top.selection == "default" and view.rightLocation == 0,
    "Native dropdown selection and right corner survive")

-- {{EXTENDED_CASES}}

return tostring(count)
