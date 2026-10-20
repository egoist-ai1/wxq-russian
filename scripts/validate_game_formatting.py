"""Validate formatting resources in an independent shipped Lua state, never Unity."""
from pathlib import Path
import argparse,ctypes,datetime,hashlib,json,re

PROJECT=Path(__file__).resolve().parent.parent
DLL=Path(r'C:\Program Files\WXQLauncher\WXQ\WXQ_Data\Plugins\x86_64\GameCore.dll')

class LuaState:
 def __init__(self,path):
  self.library=ctypes.CDLL(str(path))
  specs={
   'luaL_newstate':([],ctypes.c_void_p),
   'luaL_openlibs':([ctypes.c_void_p],None),
   'lua_close':([ctypes.c_void_p],None),
   'luaL_loadbufferx':([ctypes.c_void_p,ctypes.c_char_p,ctypes.c_size_t,ctypes.c_char_p,ctypes.c_char_p],ctypes.c_int),
   'lua_pcallk':([ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ssize_t,ctypes.c_void_p],ctypes.c_int),
   'lua_tolstring':([ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(ctypes.c_size_t)],ctypes.c_void_p),
   'lua_settop':([ctypes.c_void_p,ctypes.c_int],None),
  }
  for name,(arguments,result) in specs.items():
   f=getattr(self.library,name);f.argtypes=arguments;f.restype=result
  self.state=self.library.luaL_newstate()
  if not self.state:raise RuntimeError('Lua state allocation failed')
  self.library.luaL_openlibs(self.state)
 def string(self,index=-1):
  length=ctypes.c_size_t();p=self.library.lua_tolstring(self.state,index,ctypes.byref(length))
  return None if not p else ctypes.string_at(p,length.value).decode('utf8')
 def load(self,raw,name,execute=False):
  self.library.lua_settop(self.state,0)
  rc=self.library.luaL_loadbufferx(self.state,raw,len(raw),name.encode(),b't')
  if rc:raise RuntimeError(f'Lua compile {name}: '+str(self.string()))
  if execute:
   rc=self.library.lua_pcallk(self.state,0,1,0,0,None)
   if rc:raise RuntimeError(f'Lua call {name}: '+str(self.string()))
   return self.string()
 def close(self):
  if self.state:self.library.lua_close(self.state);self.state=None

def function_span(source,header):
 start=source.index(header)
 rest=source[start:]
 boundary=re.search(r'^function |^local function ',rest[len(header):],re.M)
 end=len(rest) if boundary is None else len(header)+boundary.start()
 text=rest[:end]
 # Top-level return terminates a module, and auto_bind is only a decorator.
 text=re.split(r'^return |^auto_bind\(\)',text,1,flags=re.M)[0]
 return text

def selected_snippet(name,raw,helper):
 source=raw.decode('utf8')
 if name=='UIUtility.lua':
  start=source.index('local function FormatNumberWithUnit(');end=source.index('function UIUtilityClass:SplitTheNumbers(')
  code=source[start:end]
 else:
  header={'OperationUtility.lua':'function OperationUtilityClass:ConvertAwardNumToStr(',
          'UITalkOSHProgitShareContent.lua':'function UITalkOSHProgitShareContentClass:FormatDamage(',
          'UICommonPropTags_AutoBind.lua':'function UICommonPropTagsClass_AutoBind:SetDiscountText('}[name]
  code=function_span(source,header)
 return helper+b'\n'+code.encode('utf8')

def validate_native_proxy(lua,manifest,all_strings):
 keys=manifest['native_localize_proxy']['verified_key_scales']
 selected={k:all_strings[k] for k in keys}
 selected['SettleShareAnyTime']=all_strings['SettleShareAnyTime']
 stub='''
nativeStrings={STRINGS}; marker="设置"; markerFailure=false
CS={LuaCallCSharpInteraction={GetLocalizeString=function(key,...)
 if type(key)~="string" then error("native-invalid-key",0) end
 if key=="TitleSetting" then if markerFailure then error("native-marker-error",0) end return marker end
 if key=="FALSE_RETURN" then return false end
 if key=="MISSING" then return nil end
 local args={...}; if args[1]=="native-error" then error("native-value-error",0) end
 local text=nativeStrings[key] or key
 return (text:gsub("{(%d+)}",function(i) return tostring(args[tonumber(i)+1]) end))
end}}
'''.replace('{STRINGS}','{'+','.join('['+json.dumps(k,ensure_ascii=False)+']='+json.dumps(v,ensure_ascii=False) for k,v in selected.items())+'}')
 lua.load(stub.encode('utf8'),'native_proxy_CSharp_stub',True)
 resource=next(r for r in manifest['resources'] if r['context_module']=='Localize.lua')
 lua.load((PROJECT/resource['original_project_path']).read_bytes(),'native_proxy_original_full_module',True)
 lua.load(b'originalGetString=Localize.GetString','retain_native_proxy_original',True)
 lua.load((PROJECT/resource['modified_project_path']).read_bytes(),'native_proxy_modified_full_module',True)
 tests='''
local passed=0
local function eq(got,expected,name) assert(got==expected,name..": got "..tostring(got)..", expected "..tostring(expected));passed=passed+1 end
local function same(key,...)
 local expectedOK,expected=pcall(originalGetString,key,...)
 local gotOK,got=pcall(Localize.GetString,key,...)
 eq(gotOK,expectedOK,"native fallback success/error");eq(got,expected,"native fallback value/error")
end
local keys={"InMillionsOfUnit","InHundredMillionOfUnit","Operation_Rank_BigNumAbbreviation_TenThousand","GamePreviewCntFormat","OperationNewbinMarkerOfW","OperationGlobalRankItemDesc1","SeasonJourney_KingNeed"}
local values={1.5,-1.5,"123.4","",true,false,{},function() end,math.huge,-math.huge,0/0,"1,5","native-error"}
for _,language in ipairs({"设置","Settings","Выбор языка"}) do
 marker=language
 for _,key in ipairs(keys) do
  for _,value in ipairs(values) do same(key,value) end
  same(key);same(key,nil);same(key,1.5,"extra");same(key,nil,"extra")
 end
end
marker="Настройки"
local function shown(key,text)
 if key=="SeasonJourney_KingNeed" then return "Королей на сервере: "..text end
 if key=="OperationGlobalRankItemDesc1" then return text.." игроков" end
 return text
end
for _,key in ipairs(keys) do
 local hundred=key=="InHundredMillionOfUnit"
 eq(Localize.GetString(key,1.5),shown(key,hundred and "150 млн." or "15 тыс."),"RU scale")
 eq(Localize.GetString(key,-1.5),shown(key,hundred and "-150 млн." or "-15 тыс."),"RU negative")
 eq(Localize.GetString(key," 1.5 "),shown(key,hundred and "150 млн." or "15 тыс."),"RU numeric string")
 eq(Localize.GetString(key,0),shown(key,"0"),"RU zero")
 same(key);same(key,nil);same(key,1.5,"extra");same(key,nil,"extra")
 for _,value in ipairs({"",true,false,{},function() end,math.huge,-math.huge,0/0,"1,5","native-error"}) do same(key,value) end
end
eq(Localize.GetString("InMillionsOfUnit",123.4),"1,234 млн.","Preserve caller decimal at million scale")
eq(Localize.GetString("InHundredMillionOfUnit",1.2345),"123,45 млн.","Preserve source fractional hundred million")
eq(Localize.GetString("GamePreviewCntFormat",9999),"99,99 млн.","Preserve caller cap")
eq(Localize.GetString("OperationNewbinMarkerOfW",0.05),"500","Small reconstructed count")
same("InHundredMillionOfUnit",1e308)
same("SettleShareAnyTime",1);same("UNKNOWN",1);same("FALSE_RETURN");same("MISSING")
same(nil,1);same(false,1);same({},1)
eq(Localize.GetString("TitleSetting"),"Настройки","Guard key delegates directly without recursion")
markerFailure=true
for _,key in ipairs(keys) do same(key,1.5) end
markerFailure=false;marker=nil
for _,key in ipairs(keys) do same(key,1.5) end
marker=false
for _,key in ipairs(keys) do same(key,1.5) end
return tostring(passed)
'''
 return int(lua.load(tests.encode('utf8'),'native_proxy_display_and_fallback_cases',True))

def validate(path):
 manifest=json.loads((PROJECT/'source/game-formatting/manifest.json').read_text(encoding='utf8'))
 helper=(PROJECT/manifest['helper_project_path']).read_bytes()
 global_data=json.loads((PROJECT/'source/game/strings/global-localize.zh-CN.json').read_text(encoding='utf8'))
 all_strings={x['key']:x['text'] for x in global_data['pairs']}
 strings={k:v for k,v in all_strings.items() if k in ('InMillionsOfUnit','InHundredMillionOfUnit','Operation_Rank_BigNumAbbreviation_TenThousand')}
 stubs='''
UIUtilityClass={}; OperationUtilityClass={}; UITalkOSHProgitShareContentClass={}; UICommonPropTagsClass_AutoBind={}
NumberFormat={Default=1,Comma=2,Overlap99Plus=3,Overlap999Plus=4,MKB=5,Million=6,KMG=7}
Const={NumShowOverlap99=99,NumShowOverlap999=999}; Interaction={Interaction={IsGlobal=function() return false end}}
marker="设置"; strings={STRINGS}
Localize={GetString=function(key,...) if key=="TitleSetting" then return marker end
 local text=strings[key] or key; local args={...}; return (text:gsub("{(%d+)}",function(i) return tostring(args[tonumber(i)+1]) end)) end}
function auto_bind() end
'''.replace('{STRINGS}','{'+','.join('['+json.dumps(k,ensure_ascii=False)+']='+json.dumps(v,ensure_ascii=False) for k,v in strings.items())+'}')
 lua=LuaState(path);checks=[]
 try:
  version=lua.load(b'return _VERSION','version',True)
  if version!='Lua 5.4':raise ValueError('Unexpected Lua ABI version: '+str(version))
  lua.load(stubs.encode('utf8'),'isolated_display_stubs',True)
  for r in manifest['resources']:
   for kind in ('original','modified'):
    raw=(PROJECT/r[kind+'_project_path']).read_bytes()
    expected=r['original_decoded_sha256' if kind=='original' else 'modified_sha256']
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Resource hash changed: '+r['entry_id_hex'])
    lua.load(raw,r['entry_id_hex']+'_'+kind+'_full_syntax',False)
    if kind=='original' and r['context_module']!='Localize.lua':lua.load(selected_snippet(r['context_module'],raw,b''),r['entry_id_hex']+'_original_functions',True)
   checks.append(dict(entry_id_hex=r['entry_id_hex'],full_original_and_modified_syntax_valid=True))
  lua.load(b'''baseline={uniform=UIUtilityClass.UniformNumber,area=UIUtilityClass.UniformNumberByArea,award=OperationUtilityClass.ConvertAwardNumToStr,damage=UITalkOSHProgitShareContentClass.FormatDamage,discount=UICommonPropTagsClass_AutoBind.SetDiscountText}''','retain_original_functions',True)
  for r in manifest['resources']:
   if r['context_module']=='Localize.lua':continue
   raw=(PROJECT/r['modified_project_path']).read_bytes();lua.load(selected_snippet(r['context_module'],raw,helper),r['entry_id_hex']+'_modified_functions',True)
  tests='''
local passed=0
local function eq(got,expected,name) assert(got==expected,name..": got "..tostring(got)..", expected "..tostring(expected));passed=passed+1 end
local values={0,12,999,1000,1500,9999,10000,15000,15999,99949,100000,123456,999949,999950,1000000,1800000,100000000,1000000000,-15000,-1000,-999}
marker="设置"
for _,value in ipairs(values) do
 for _,floor in ipairs({false,true}) do
  for _,format in pairs(NumberFormat) do
   eq(UIUtilityClass:UniformNumber(value,format,floor),baseline.uniform(UIUtilityClass,value,format,floor),"CN guard/mode")
  end
  eq(OperationUtilityClass:ConvertAwardNumToStr(value,floor),baseline.award(OperationUtilityClass,value,floor),"CN award")
 end
 eq(UITalkOSHProgitShareContentClass:FormatDamage(value),baseline.damage(UITalkOSHProgitShareContentClass,value),"CN damage")
 eq(UIUtilityClass:UniformNumberByArea(value,nil,true),baseline.area(UIUtilityClass,value,nil,true),"CN area")
end
for _,value in ipairs({"9","9.5","5","0","10","special label","-1","11"}) do
 local old={discountText={}};local new={discountText={}};baseline.discount(old,value);UICommonPropTagsClass_AutoBind.SetDiscountText(new,value);eq(new.discountText.text,old.discountText.text,"CN discount")
end
marker="Настройки"
eq(UIUtilityClass:UniformNumber(15000,NumberFormat.Million,true),"15 тыс.","RU15k")
eq(UIUtilityClass:UniformNumber(15999,NumberFormat.MKB,true),"15,9 тыс.","RU floor tenth")
eq(UIUtilityClass:UniformNumber(15999,NumberFormat.MKB,false),"16 тыс.","RU round tenth")
eq(UIUtilityClass:UniformNumber(999950,NumberFormat.Million,false),"1 млн.","RU carry to million")
eq(UIUtilityClass:UniformNumber(1800000,NumberFormat.Million,true),"1,8 млн.","RU million")
eq(UIUtilityClass:UniformNumber(2500000000,NumberFormat.MKB,false),"2,5 млрд.","RU billion")
eq(UIUtilityClass:UniformNumber(-15000,NumberFormat.Million,true),"-15 тыс.","RU signed")
eq(UIUtilityClass:UniformNumberByArea(15000,nil,true),"15 тыс.","RU CN-native area dispatch")
eq(OperationUtilityClass:ConvertAwardNumToStr(15999,true),"15 тыс.","RU award integer floor")
eq(OperationUtilityClass:ConvertAwardNumToStr(15999,false),"16 тыс.","RU award nearest")
eq(UITalkOSHProgitShareContentClass:FormatDamage(15099),"15,09 тыс.","RU damage")
eq(UITalkOSHProgitShareContentClass:FormatDamage(999999),"999,99 тыс.","RU damage floor")
for _,value in ipairs(values) do
 for _,format in ipairs({NumberFormat.Default,NumberFormat.Comma,NumberFormat.Overlap99Plus,NumberFormat.Overlap999Plus,NumberFormat.KMG}) do
  eq(UIUtilityClass:UniformNumber(value,format,true),baseline.uniform(UIUtilityClass,value,format,true),"RU untouched mode")
 end
end
for _,item in ipairs({{"9","-10%"},{"9.5","-5%"},{"5","-50%"},{"0","-100%"},{"10","-0%"},{"special label","special label"},{"-1","-1"},{"11","11"}}) do
 local self={discountText={}};UICommonPropTagsClass_AutoBind.SetDiscountText(self,item[1]);eq(self.discountText.text,item[2],"RU discount display only")
end
marker="Выбор языка";local self={discountText={}};UICommonPropTagsClass_AutoBind.SetDiscountText(self,"9");eq(self.discountText.text,"9","Strict verified marker")
return tostring(passed)
'''
  passed=int(lua.load(tests.encode('utf8'),'display_behavior_regressions',True))
  # Only synthetic numeric codes; no account state, network callbacks, or real player codes.
  protocol=(PROJECT/'source/game-formatting/excerpts/EmbattleShareCode-protocol.lua').read_text(encoding='utf8')
  parser=function_span(protocol,'function EmbattleFormationDataClass:SubShareCodeContent(')
  synthetic='''EmbattleFormationDataClass={};'''+parser+'''
assert(EmbattleFormationDataClass:SubShareCodeContent("【王者万象棋】【Example】【Synthetic】123456789012345【Click】")=="123456789012345")
assert(EmbattleFormationDataClass:SubShareCodeContent("【Honor of Kings ACE】【Пример】【Тест】123456789012345【Click】")=="123456789012345")
assert(EmbattleFormationDataClass:SubShareCodeContent("【Header】12x3")=="")
assert(EmbattleFormationDataClass:SubShareCodeContent(string.rep("1",65))=="")
return "4"
'''
  share_checks=int(lua.load(synthetic.encode('utf8'),'unchanged_share_parser_synthetic_cases',True))
  proxy_checks=validate_native_proxy(lua,manifest,all_strings)
  result=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),Lua_VERSION=version,runtime_DLL=str(path),runtime_DLL_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),independent_python_process=True,independent_lua_state=True,Unity_native_env_created=False,game_process_attached=False,installed_game_or_cache_written=False,full_module_compile_checks=checks,display_behavior_assertions_passed=passed,native_localize_proxy_assertions_passed=proxy_checks,synthetic_share_parser_assertions_passed=share_checks,server_protocol_roundtrip_tested=False,guard=dict(key='TitleSetting',expected='Настройки'),limits=['Font rendering, text rectangles, number/unit wrapping and static Fold glyph are not verified by headless Lua tests','Native package mount acceptance/persistence must be tested separately','tips_WAN and ShopTip3 were absent from all9908 package10_0 payloads; their native C# call sites remain unknown'],C_API_primary_sources=['https://raw.githubusercontent.com/lua/lua/v5.4.8/lua.h','https://raw.githubusercontent.com/lua/lua/v5.4.8/lauxlib.h'])
  output=PROJECT/'evidence/game-formatting-validation.json';output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=False,indent=2))
 finally:lua.close()

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dll',type=Path,default=DLL);args=p.parse_args();validate(args.dll)
