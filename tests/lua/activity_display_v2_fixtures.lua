-- Текущие модули исполняются целиком; заменяются только границы PB и UI.
local passed = 0
local function expect(actual, expected, label)
    assert(actual == expected, label .. ": got " .. tostring(actual) .. ", expected " .. tostring(expected))
    passed = passed + 1
end
local function clone(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for key, item in pairs(value) do result[key] = clone(item) end
    return result
end
local function sameData(actual, expected, label)
    expect(type(actual), type(expected), label .. " type")
    if type(expected) ~= "table" then expect(actual, expected, label); return end
    for key, value in pairs(expected) do sameData(actual[key], value, label .. "." .. tostring(key)) end
    for key in pairs(actual) do assert(expected[key] ~= nil, label .. ": unexpected key " .. tostring(key)) end
end

function import() return {} end
function DeclareClass() return {} end
function auto_bind() end
super = { vOnInitialize = function(self, argument) self.superArgument = argument end }
function string.isNullOrEmpty(value) return value == nil or value == false or value == "" end
function table.append(array, value) array[#array + 1] = value end
function table.clear(array) for key in pairs(array) do array[key] = nil end end
function table.find(array, value) for index, item in ipairs(array) do if item == value then return index end end end
function table.find_value(array, predicate) for index, value in ipairs(array) do if predicate(index, value) then return value end end end

local marker = "Настройки"
local markerError = false
local nativeText = { Confirm = "Подтвердить", Cancel = "Отмена", LordChooseOperateBtnName1 = "Готово", IntimacyOperateBtnName0 = "Далее" }
Localize = { GetString = function(key)
    if key == "TitleSetting" then
        if markerError then error("native-marker-error", 0) end
        return marker
    end
    return nativeText[key]
end }
local syncToken = {}
CS = { Timi = { TimiListView = { AsyncSetting = { GetSyncModeData = function() return syncToken end } } } }
UICommonAction = { CloseUI = 1, HelpContentItem2Clicked = 2, PlayCommonSlideAnime = 3 }
OSProto = { MsgBoxClickIndex = { Close = 1, Confirm = 2, Cancel = 3 } }
UICommonHelpSystemWithImageClassNumberOfButtons = { One = 1, Two = 2 }
UICommonHelpSystemWithImageClassDisplayMode = { Image = 1, Video = 2, CloudsVideo = 3 }
UICommonHelpSystemWithImageClassSliding = { None = 0, Yes = 1 }
osgame_log = { cat = { UI = 1, Logic = 2 }, error = function() end, debug = function() end }
local bubbleMessage
Bubble = { Show = function(_, message) bubbleMessage = message end }

local database, groupOverride
local lookupCalls = {}
local function setDatabase(rows, override)
    database, groupOverride, lookupCalls = {}, override, {}
    for _, cfg in ipairs(rows) do database[cfg.ID] = cfg end
end
PBDataManager = { ResHelpSystemCfgDataBin = {
    Get = function(id) lookupCalls[#lookupCalls + 1] = { "Get", id }; return database[id] end,
    GetMulti_ByGroupID = function(id)
        lookupCalls[#lookupCalls + 1] = { "Group", id }
        if groupOverride then return clone(groupOverride) end
        local result = {}
        for _, cfg in pairs(database) do if cfg.GroupID == id then result[#result + 1] = cfg end end
        return result
    end,
} }

-- {{ORIGINAL_MODULES}}
-- {{PATCHED_MODULES}}
-- {{ACTIVITY_TITLE}}
-- {{HELPER_API}}

local function makeCfg(id, title, group, order)
    return {
        ID = id, Title = title, GroupTitle = "", GroupID = group or 0, GroupInnerOrder = order or 1,
        ScrollToPassage = 0, ConfirmContent = "确认", CancelContet = "native-cancel",
        ClickConfirmContent = "native-confirm-message", ClickCancelContet = "native-cancel-message",
        VideoUrl = {}, IsCloudsVideo = false,
        TabData = { { Passage = { { Content = "#SOURCE_BODY#", ImagePath = { "image-native-path" } } } } },
    }
end
local function newFramework(view)
    return { InitPopViewFramework_L = function(_, data) view.viewData = data end }
end
local function newStyle(class)
    local view = setmetatable({ sysIDList = {}, refreshCalls = {} }, { __index = class })
    view.uiPopViewFramework_LChild = newFramework(view)
    view.smartListTimiList = { SetAsyncSetting = function(_, value) view.asyncSetting = value end }
    function view:RefreshBySystemID(...) self.refreshCalls[#self.refreshCalls + 1] = table.pack(...) end
    function view:CloseSelf() self.closed = true end
    return view
end
local function newGroup(class)
    local view = setmetatable({}, { __index = class })
    view.uiPopViewFramework_LChild = newFramework(view)
    function view:CreateUIPopViewFramework_L() self.frameworkCreated = true end
    function view:SetTxtContent(value) self.txt = value end
    function view:SetShowTxt(value) self.showTxt = value end
    function view:CloseSelf() self.closed = true end
    return view
end
local function newButton()
    return { SetText = function(self, text) self.text = text end }
end
local function newImage(class)
    local view = setmetatable({ refreshCalls = {}, slideCalls = {}, descriptionCalls = {}, imageCalls = {} }, { __index = class })
    view.confirm, view.cancel = newButton(), newButton()
    view.widgetCommonBtn_Style1_Big, view.widgetCommonBtn_Style3_Big = view.confirm, view.cancel
    view.tabListTimiList = {
        SetSingleSelectMode = function() view.singleSelect = true end,
        SetData = function(_, data) view.tabData = data end,
        GetItemByIndex = function(_, index) return view.tabItems[index] end,
        SetSelectIndex = function(_, index) view.selectedIndex = index end,
    }
    view.tabItems = { {}, {} }
    function view:SetTitle(value) self.title = value end
    function view:SetNumberOfButtons(value) self.buttons = value end
    function view:SetDisplayMode(value) self.mode = value end
    function view:SetSliding(value) self.slideCalls[#self.slideCalls + 1] = value end
    function view:InitDrag() self.drag = true end
    function view:SendUpdateUI(action, data) self.action, self.actionData = action, data end
    function view:SetDescribingFig(value) self.imageCalls[#self.imageCalls + 1] = value end
    function view:SetDescription(value) self.descriptionCalls[#self.descriptionCalls + 1] = value end
    function view:PlayVideo(value) self.playedVideo = value end
    function view:PlayCloudsVideo(value) self.playedCloudsVideo = value end
    function view:CloseSelf() self.closed = true end
    return view
end

local function compareStyle(original, patched, rows, id, override)
    local before = clone(rows)
    setDatabase(rows, override)
    local a = newStyle(original)
    a:InitBySystemID(id)
    local originalLookups = clone(lookupCalls)
    setDatabase(rows, override)
    local b = newStyle(patched)
    b:InitBySystemID(id)
    sameData(lookupCalls, originalLookups, "Native PB lookup/ID branch")
    sameData(rows, before, "Native configs are read only")
    sameData(b.sysIDList, a.sysIDList, "Selected IDs and ordering")
    sameData(b.refreshCalls, a.refreshCalls, "Native RefreshBySystemID arguments")
    expect(b.scrollToIndex, a.scrollToIndex, "Native scroll selection")
    local am, bm = a.viewData.MenuData, b.viewData.MenuData
    expect(type(bm), type(am), "Native menu branch")
    if am then
        expect(am.MenuSelect, a.OnSelectTab, "Original callback identity")
        expect(bm.MenuSelect, b.OnSelectTab, "Patched callback identity")
        sameData(bm.DefaultSelect, am.DefaultSelect, "Native default selected index")
        expect(#bm.MenuTree, #am.MenuTree, "Native menu row count")
    end
    return a, b
end
local function compareGroup(rows, id)
    local before = clone(rows)
    local callback = function() error("Initialization must not call CloseBack", 0) end
    local argument = { SystemID = id, CloseBack = callback, UserData = {} }
    setDatabase(rows)
    local a = newGroup(Original.Group)
    a:vOnInitialize(argument)
    local originalLookups = clone(lookupCalls)
    setDatabase(rows)
    local b = newGroup(Patched.Group)
    b:vOnInitialize(argument)
    sameData(lookupCalls, originalLookups, "Native group PB lookup/IDs")
    sameData(rows, before, "Grouped configs are read only")
    sameData(b.sysIDList, a.sysIDList, "Grouped ID ordering")
    expect(b._closeBack, callback, "Close callback identity")
    expect(b.superArgument, argument, "Group argument identity")
    expect(b.viewData.MenuData.MenuSelect, b.OnSelectTab, "Group menu callback identity")
    sameData(b.viewData.MenuData.DefaultSelect, a.viewData.MenuData.DefaultSelect, "Group default selected index")
    expect(b.viewData.MenuData.asyncSetting, syncToken, "Native menu async object identity")
    return a, b
end
local function compareImageRefresh(cfg, branch)
    local before = clone(cfg)
    local function run(class)
        local view = newImage(class)
        view._cfg, view._showIndex = cfg, (branch == "last" or branch == "empty-confirm") and 2 or 1
        view._imageList = (branch == "empty" or branch == "empty-confirm-no-pages") and {} or { "image-1", "image-2" }
        view._textList = { "body-1", "body-2" }
        if branch == "missing-button" then nativeText.IntimacyOperateBtnName0 = nil end
        view:RefreshUI(1)
        nativeText.IntimacyOperateBtnName0 = "Далее"
        return view
    end
    local a, b = run(Original.Image), run(Patched.Image)
    sameData(cfg, before, "Image cfg is read only")
    expect(b.cancel.text, a.cancel.text, "Cancel text stays native")
    sameData(b.imageCalls, a.imageCalls, "Native image selection")
    sameData(b.descriptionCalls, a.descriptionCalls, "Native body selection")
    expect(b.action, a.action, "Native animation action")
    if a.actionData then
        expect(b.actionData.currIdx, a.actionData.currIdx, "Native animation current index")
        expect(b.actionData.lastItemPrefab, b.tabItems[1], "Native selected UI object identity")
        expect(b.actionData.playAnim, a.actionData.playAnim, "Native animation branch")
    end
    return a, b
end

-- {{EXTENDED_CASES}}
return tostring(passed)
