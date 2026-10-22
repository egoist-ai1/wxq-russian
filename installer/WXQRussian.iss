; Egoist · Русификатор Honor of Kings ACE — установщик (маршрут B, override).
; Собирается installer\build_installer.py: он готовит staging-папку с данными перевода и вызывает ISCC.
; Проверки и откат делает WXQTool.exe (см. README), здесь только фирменное окно и порядок шагов.
#ifndef Staging
  #error Staging (папка, подготовленная build_installer.py) обязательна
#endif
#ifndef OutputDir
  #error OutputDir обязателен
#endif
#ifndef OutputName
  #define OutputName "WXQ-Russian-Setup"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef BuildDate
  #define BuildDate "0000-00-00"
#endif
#ifndef GameVersion
  #define GameVersion "?"
#endif
#ifndef PackagesText
  #define PackagesText "?"
#endif
; TestBuild (только /DTESTBUILD из build_installer.py --test-build) разрешает /CURRENTUSER и /ALLUSERS в командной строке для тестов.
; Релизная сборка без него всегда требует прав администратора.
; SHA-256 собранных exe и install.json подставляет build_installer.py (/DToolSha256= /DInstallSha256= /DUiSha256= /DProfilesSha256=):
; повышенный мастер сверяет с ними КОПИИ в защищённой папке (см. ui\PROTOCOL.md, «Защищённая папка установщика»).
#ifndef ToolSha256
  #error ToolSha256 обязателен (SHA-256 WXQTool.exe; подставляет build_installer.py)
#endif
#ifndef InstallSha256
  #error InstallSha256 обязателен (SHA-256 install.json; подставляет build_installer.py)
#endif
#ifdef WithUI
  #ifndef UiSha256
    #error UiSha256 обязателен при WithUI (SHA-256 WXQSetupUI.exe; подставляет build_installer.py)
  #endif
#endif
#ifdef WithLauncher
  #ifndef ProfilesSha256
    #error ProfilesSha256 обязателен при WithLauncher (SHA-256 launcher-profiles.json; подставляет build_installer.py)
  #endif
#endif
; Числовая версия для VersionInfo* (Inno требует x.x.x.x): build_installer.py передаёт её отдельно, когда в AppVersion есть буква (0.4.3d).
#ifndef AppVersionNumeric
  #define AppVersionNumeric AppVersion
#endif
; DiagBuild (/DDIAGBUILD, build_installer.py --diag): диагностическая сборка с Lua-журналом; видна в заголовке окна, подвале и свойствах exe.
#ifdef DiagBuild
  #define DiagSuffix " (диагностическая сборка)"
  #define DiagFooter "  ·  ДИАГНОСТИКА"
#else
  #define DiagSuffix ""
  #define DiagFooter ""
#endif
#define AppTitle "Egoist · Русификатор Honor of Kings ACE"
#define ToolExe "WXQTool.exe"

[Setup]
#ifdef TestBuild
; Тестовая сборка пишет в Uninstall другой ключ: запись настоящего AppId тесты не создают и не трогают.
AppId={{B3E1D8A4-6F21-4C7B-9A0E-5D2C8F4E7A19}-TEST
#else
AppId={{B3E1D8A4-6F21-4C7B-9A0E-5D2C8F4E7A19}
#endif
AppName={#AppTitle}
AppVersion={#AppVersion}
AppVerName={#AppTitle} {#AppVersion}{#DiagSuffix}
AppPublisher=EGOIST
; Помощник лежит в Program Files (писать туда могут только администраторы); данные и резервные копии — в ProgramData под защитой ACL.
DefaultDirName={commonpf64}\Egoist\WXQ-Russian
UsePreviousAppDir=no
; Один установщик на компьютер: Global-мьютекс общий для всех сеансов и учётных записей (второй запуск не стартует).
SetupMutex=Egoist.WXQRussian.Setup,Global\Egoist.WXQRussian.Setup
PrivilegesRequired=admin
#ifdef TestBuild
PrivilegesRequiredOverridesAllowed=commandline
#endif
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#Staging}\wxq-ru.ico
UninstallDisplayIcon={app}\wxq-ru.ico
UninstallDisplayName={#AppTitle}
WizardStyle=modern
DisableProgramGroupPage=yes
DefaultGroupName=Egoist
DisableWelcomePage=no
DisableDirPage=yes
DisableReadyPage=yes
DisableFinishedPage=no
DisableStartupPrompt=yes
WizardResizable=no
WizardSizePercent=100
ShowLanguageDialog=no
Compression=lzma2/normal
SolidCompression=yes
CloseApplications=no
RestartApplications=no
UninstallLogMode=append
VersionInfoVersion={#AppVersionNumeric}.0
VersionInfoCompany=EGOIST
#ifdef TestBuild
VersionInfoDescription={#AppTitle} [тестовая сборка]
#else
VersionInfoDescription={#AppTitle}{#DiagSuffix}
#endif
VersionInfoProductName={#AppTitle}
VersionInfoProductVersion={#AppVersionNumeric}.0
VersionInfoProductTextVersion={#AppVersion} ({#BuildDate})

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
; Установленные на диск: помощник (нужен для удаления перевода) и значок.
Source: "{#Staging}\{#ToolExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Staging}\wxq-ru.ico"; DestDir: "{app}"; Flags: ignoreversion
; Единый установщик остаётся доступен для повторного открытия, обновления и отката.
Source: "{srcexe}"; DestDir: "{app}"; DestName: "WXQ-Russian.exe"; Flags: external ignoreversion; Check: SelfCopyNeeded
#ifdef WithLauncher
; Русский лаунчер (переключатель на странице приветствия, Check: LauncherWanted): активатор и шесть файлов В ОДНОЙ папке {app}\launcher.
; Активатор берёт target.txt и файлы из папки собственного exe, поэтому exe обязан лежать рядом с ними; runtime читает словарь, draft, css и png из своей папки.
; Активатор сверяет SHA модуля и словаря со значениями, зашитыми в его exe, ACL папки launcher и {app}, положение цели в Program Files.
; target.txt (путь к оригинальному 王者万象棋.exe) пишет код установки после копирования.
Source: "{#Staging}\launcher\WXQLauncherRU.exe"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
Source: "{#Staging}\launcher\launcher-ru-runtime.cjs"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
Source: "{#Staging}\launcher\launcher-ru.json"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
Source: "{#Staging}\launcher\profiles.json"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
Source: "{#Staging}\launcher\launcher-entry-public-ru.draft.json"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
Source: "{#Staging}\launcher\launcher-layout.css"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
Source: "{#Staging}\launcher\main-logo-title.a73b14e6.png"; DestDir: "{app}\launcher"; Flags: ignoreversion; Check: LauncherWanted
#endif
; Только для работы мастера (распаковываются в {tmp}).
#include Staging + "\payload-files.iss"

[Icons]
Name: "{code:DesktopDir}\Egoist · Русификатор ACE"; Filename: "{app}\WXQ-Russian.exe"; WorkingDir: "{app}"; IconFilename: "{app}\wxq-ru.ico"
Name: "{code:StartDir}\Egoist · Русификатор ACE"; Filename: "{app}\WXQ-Russian.exe"; WorkingDir: "{app}"; IconFilename: "{app}\wxq-ru.ico"

#ifdef WithLauncher
[Icons]
; Значок берём у оригинального лаунчера. Активатор запускается от обычного пользователя (asInvoker).
Name: "{code:DesktopDir}\Honor of Kings ACE (RU)"; Filename: "{app}\launcher\WXQLauncherRU.exe"; WorkingDir: "{app}\launcher"; IconFilename: "{code:OriginalLauncherExe}"; Comment: "Оригинальный лаунчер с русским переводом"; Check: LauncherWanted
Name: "{code:StartDir}\Honor of Kings ACE (RU)"; Filename: "{app}\launcher\WXQLauncherRU.exe"; WorkingDir: "{app}\launcher"; IconFilename: "{code:OriginalLauncherExe}"; Comment: "Оригинальный лаунчер с русским переводом"; Check: LauncherWanted

[Registry]
; Флаг включения читает активатор (HKLM, 64-битный вид). HKA в обычной установке (администратор) равен HKLM; HKCU только в тестовой сборке с /CURRENTUSER.
Root: HKA; Subkey: "SOFTWARE\Egoist\WXQ-Russian"; ValueType: dword; ValueName: "LauncherRU"; ValueData: 1; Flags: uninsdeletekey; Check: LauncherWanted
Root: HKA; Subkey: "SOFTWARE\Egoist"; Flags: uninsdeletekeyifempty; Check: LauncherWanted
#endif

[UninstallDelete]
; Резервные копии, work и state.json лежат в каталоге данных (не в {app}); их убирает CurUninstallStepChanged.
Type: files; Name: "{app}\datadir.txt"
#ifdef WithLauncher
Type: filesandordirs; Name: "{app}\launcher"
#endif

[Code]
// Сдержанное окно: графит, слоновая кость и один тёплый акцент (золото) у главной кнопки, заполнения прогресса и маленьких меток.
// Цвета в Inno задаются как BGR. Растры (знак, флажок, маркеры шагов) нарисованы build_installer.py заранее для каждого масштаба Windows.
const
  BackgroundColor = $00120F0E;    // #0E0F12 фон окна
  SurfaceColor = $001C1715;       // #15171C поверхность
  TrackColor = $002D2623;         // #23262D фон полосы прогресса
  LineColor = $00352D2A;          // #2A2D35 волосяные линии и рамка окна
  LineStrongColor = $00493F3A;    // #3A3F49 контур вторичной кнопки
  LineHotColor = $00706A62;       // #626A70 контур при наведении
  HoverColor = $00231D1B;         // #1B1D23 заливка вторичной кнопки при наведении
  PressColor = $002D2623;         // #23262D заливка вторичной кнопки при нажатии
  TextColor = $00DFE8EC;          // #ECE8DF слоновая кость
  MutedColor = $00A39A9A;         // #9A9AA3 вторичный текст
  HeaderMutedColor = $00B2A9A9;   // #A9A9B2 подпись шапки: контраст с графитом выше 7:1
  FaintColor = $00857C7A;         // #7A7C85 подписи в подвале
  GoldColor = $005AA5C8;          // #C8A55A акцент
  GoldHotColor = $0071B4D6;       // #D6B471 главная кнопка при наведении
  GoldPressColor = $0047899F;     // #9F8947 главная кнопка при нажатии
  InkColor = $000C1214;           // #14120C тёмный текст на золоте
  DangerColor = $005E60D0;        // #D0605E

  // Сетка 8 px; левая граница всего содержимого — Margin, правая — Margin + ContentWidth.
  WindowWidth = 600;
  WindowHeight = 608;
  Margin = 40;
  ContentWidth = 518;
  HeaderHeight = 96;
  StateTop = 120;
  DetailTop = 162;
  ContentTop = 216;
  InfoHeight = 264;
  InfoToggleHeight = 152;   // справка, когда ниже неё стоит переключатель «Русский лаунчер»
  ToggleLineTop = 376;
  ToggleTop = 392;
  ToggleNoteTop = 420;
  StepCount = 5;
  StepTop = 216;
  StepHeight = 32;
  ButtonTop = 504;
  ButtonHeight = 44;
  FooterTop = 576;

  StPending = 0;
  StActive = 1;
  StDone = 2;
  StFailed = 3;

var
  Surface: TPanel;
  MarkImage, HeaderImage: TBitmapImage;
  StateLabel, DetailLabel, InfoLabel, FailureLabel, FooterLabel, IndexLabel, CloseLabel, PercentLabel, EtaLabel: TNewStaticText;
  PrimaryButton, SecondaryButton, PrimaryInner, SecondaryInner: TPanel;   // Button — рамка в 1 пиксель, Inner — заливка с подписью
  ProgressTrack, ProgressFill, ToggleLine: TPanel;
  StepDot: array[1..StepCount] of TBitmapImage;
  StepName, StepDetail: array[1..StepCount] of TNewStaticText;
  DotWait, DotAct, DotDone, DotFail, CheckOn, CheckOff: TBitmap;
  UiPct: Integer;   // вариант растров: 100, 125, 150, 175, 200 или 250 (ближайший к масштабу Windows)
  ToolLines: TArrayOfString;
  AppliedResources, SkippedResources, PreservedTextRows: Int64;   // итог verify; следующая команда перезапишет ToolLines
  GameParam, FoundGame, LauncherLnk, LastFailure, LastCode, SizeText: String;
  IsInstalled, IsPending, IsInterrupted, NeedsReapply, ToolReady, Removed: Boolean;
  UninstDataDir: String;
  KeepBackups: Boolean;
  RemoveMode: Boolean;     // последняя операция - «Удалить перевод» (для честного текста при сбое отката)
  PayloadFailText, PayloadFailCode: String;
  InstalledVersion, InstalledGameVersion: String;
  ToggleImage: TBitmapImage;
  ToggleTitle, ToggleNote: TNewStaticText;
  LauncherOn: Boolean;     // переключатель «Русский лаунчер» (по умолчанию ВКЛЮЧЁН, когда лаунчер есть в сборке; функция новая; /LAUNCHERRU=0 выключает, в том числе в тихом режиме)
  LauncherExe, LauncherBuildText, LauncherFindReason: String;   // оригинальный 王者万象棋.exe и результат поиска
  UpdateMode, UpdateRestored: Boolean;   // /UPDATE=1 (запуск из автообновления): reinstall и pending снимаются откатом; UpdateRestored - откат был, а установка после него не прошла
  Busy: Boolean;  // идёт работа помощника: нажатия кнопок и закрытие игнорируются
  Mode: Integer;  // 0 приветствие, 1 сбой, 2 готово, 3 удалено
  ShownPermille: Integer;
  StartTick: Cardinal;

function DwmSetWindowAttribute(Wnd: Integer; Attribute: Integer;
  var Value: Integer; Size: Integer): Integer;
  external 'DwmSetWindowAttribute@dwmapi.dll stdcall';

function GetTickCount: Cardinal;
  external 'GetTickCount@kernel32.dll stdcall';

function PostMessageW(Wnd: Integer; Msg, WParam, LParam: LongInt): Integer;
  external 'PostMessageW@user32.dll stdcall';

type
  TWinMsg = record
    Wnd: LongWord;
    Msg: LongWord;
    WParam: LongWord;
    LParam: LongWord;
    Time: LongWord;
    PtX: Integer;
    PtY: Integer;
  end;

type
  TToolStartupInfo = record
    Size, Reserved, Desktop, Title: LongWord;
    X, Y, XSize, YSize, XChars, YChars, Fill, Flags: LongWord;
    ShowWindow, ReservedSize: Word;
    ReservedData, StdInput, StdOutput, StdError: LongWord;
  end;
  TToolProcessInfo = record
    ProcessHandle, ThreadHandle, ProcessId, ThreadId: LongWord;
  end;
  TToolIoCounters = record
    ReadCount, WriteCount, OtherCount, ReadBytes, WriteBytes, OtherBytes: Int64;
  end;

function CreateToolProcess(ApplicationName, CommandLine: String; ProcessSecurity, ThreadSecurity: LongWord;
  InheritHandles: Boolean; Flags, Environment: LongWord; CurrentDirectory: String;
  var Startup: TToolStartupInfo; var ProcessInfo: TToolProcessInfo): Boolean;
  external 'CreateProcessW@kernel32.dll stdcall';
function WaitToolProcess(Handle, Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function GetToolExitCode(Handle: LongWord; var Code: LongWord): Boolean;
  external 'GetExitCodeProcess@kernel32.dll stdcall';
function GetToolIoCounters(Handle: LongWord; var Counters: TToolIoCounters): Boolean;
  external 'GetProcessIoCounters@kernel32.dll stdcall';

type
  TGuidRec = record
    D1: LongWord;
    D2: Word;
    D3: Word;
    D4: array[0..7] of Byte;
  end;

function CoCreateGuid(var G: TGuidRec): LongInt;
  external 'CoCreateGuid@ole32.dll stdcall';

function PeekMessage(var M: TWinMsg; Wnd, MinMsg, MaxMsg, Remove: LongWord): Boolean;
  external 'PeekMessageW@user32.dll stdcall';
function TranslateMessage(var M: TWinMsg): Boolean;
  external 'TranslateMessage@user32.dll stdcall';
function DispatchMessage(var M: TWinMsg): LongInt;
  external 'DispatchMessageW@user32.dll stdcall';

// Обработка очереди окна на время ожидания помощника: полоса и надписи перерисовываются, окно не «зависает».
procedure PumpMessages;
var
  M: TWinMsg;
begin
  while PeekMessage(M, 0, 0, 0, 1) do
  begin
    TranslateMessage(M);
    DispatchMessage(M);
  end;
end;


// Ставим ли русский лаунчер: сборка с лаунчером, переключатель включён и помощник нашёл оригинальный лаунчер.
function LauncherWanted: Boolean;
begin
#ifdef WithLauncher
  Result := LauncherOn and (LauncherExe <> '');
#else
  Result := False;
#endif
end;

function LauncherAvailable: Boolean;
begin
#ifdef WithLauncher
  Result := True;
#else
  Result := False;
#endif
end;

// ---- обмен с окном WXQSetupUI.exe (протокол — installer\ui\PROTOCOL.md) ----
// Видимое окно ведёт WXQSetupUI.exe, окно мастера Inno скрыто (режим UI). Inno пишет state.json (целиком во временный файл,
// затем подмена одной операцией) и читает commands.jsonl. UI не передаёт в Inno ни путей, ни произвольных команд:
// принимаются только install, options, remove, launch, back, close, и только строки строго заданного вида.
var
  UiActive: Boolean;     // окно WXQSetupUI.exe ведёт установку; родные страницы Inno скрыты (в тихом режиме и при откате на них UiActive = False)
  UiGone, UiClosing, InTick, InStatus: Boolean;   // InStatus: идёт проверка состояния (помощник), команды окна подождут
  UiDir, UiToken, UiStatePath, UiCmdPath: String;
  SecExtra: Integer;     // лишних записей ACL (релиз 0; тестовая сборка 1 - текущий пользователь)
  SecDir: String;        // защищённая папка установщика (только Администраторы и SYSTEM): отсюда запускаются WXQTool.exe и окно, здесь же файлы обмена
  UiPubFailed: Boolean;   // последняя запись state.json не удалась: следующий такт таймера повторит
  UiTimer, UiAppWnd, UiLastPub, UiCloseAt: LongWord;
  UiCmdSize, UiLastSeq, SvRev, SvStep: Integer;
  SvScreen, SvOp, SvState, SvDetail, SvFile, SvEta, SvStepState, SvFailKind, SvFailMsg, SvFailCode, SvNote: String;
  SvStage, SvDetailBeforeClose, PreClosingCaption: String;   // SvStage = 'close' пока помощник закрывает игру и лаунчер (протокол: поле stage)
  SvLaunchState, SvLaunchMessage: String;
  ClosingShown: Boolean;
  SvDone, SvTotal: Int64;
  SvCanRemove, SvInterrupted, SvFailedBefore: Boolean;
  SuState, SuVersion, SuMessage, SuAction, SuLast, SuOut: String;
  SuDone, SuTotal: Int64;
  SuWaiting, AutoContinue, SelfUpdateContinue, SuDownloadQueued, ManagerReady: Boolean;
  SuStarted: LongWord;
  WizardReady: Boolean;
  WelcomeReadyAt: LongWord;
  SelfSourceHandle: LongWord;
  SelfSourceSha: String;

function GetCurrentProcessId: LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function CreateFileW(Name: String; Access, Share, Security, Creation, Flags, Template: LongWord): LongWord;
  external 'CreateFileW@kernel32.dll stdcall';
function OpenMutexW(Access: LongWord; Inherit: Boolean; Name: String): LongWord;
  external 'OpenMutexW@kernel32.dll stdcall';
function CloseHandle(H: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function MoveFileExW(Source, Target: String; Flags: LongWord): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';
function IsWindowVisible(H: LongWord): Boolean;
  external 'IsWindowVisible@user32.dll stdcall';
function ShowWindow(H: LongWord; Command: Integer): Boolean;
  external 'ShowWindow@user32.dll stdcall';
function SetWindowLongW(H: LongWord; Index: Integer; Value: LongInt): LongInt;
  external 'SetWindowLongW@user32.dll stdcall';
function GetWindowLongW(H: LongWord; Index: Integer): LongInt;
  external 'GetWindowLongW@user32.dll stdcall';
function SetLayeredWindowAttributes(H: LongWord; Key: LongWord; Alpha: Byte; Flags: LongWord): Boolean;
  external 'SetLayeredWindowAttributes@user32.dll stdcall';
function FindWindowExW(Parent: LongWord; After: LongWord; ClassName: String; Title: LongWord): LongWord;
  external 'FindWindowExW@user32.dll stdcall';
function GetWindowThreadProcessId(H: LongWord; var Pid: LongWord): LongWord;
  external 'GetWindowThreadProcessId@user32.dll stdcall';
function SetTimer(H: LongWord; Id: LongWord; Elapse: LongWord; Proc: LongWord): LongWord;
  external 'SetTimer@user32.dll stdcall';
function KillTimer(H: LongWord; Id: LongWord): Boolean;
  external 'KillTimer@user32.dll stdcall';
function SetForegroundWindow(H: LongWord): Boolean;
  external 'SetForegroundWindow@user32.dll stdcall';

function JsonStr(S: String): String;
var
  I: Integer;
  C: String;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := Copy(S, I, 1);
    if C = '\' then Result := Result + '\\'
    else if C = '"' then Result := Result + '\"'
    else if Ord(S[I]) < 32 then Result := Result + ' '
    else Result := Result + C;
  end;
end;

function JsonBool(B: Boolean): String;
begin
  if B then Result := 'true' else Result := 'false';
end;

// Файл пишется целиком в state.json.tmp и подменяется одной операцией: читатель не видит половину записи.
procedure WriteStateFile(Json: String);
var
  Lines: TArrayOfString;
  Tmp: String;
  I: Integer;
  Done: Boolean;
begin
  Done := False;
  SetArrayLength(Lines, 1);
  Lines[0] := Json;
  Tmp := UiStatePath + '.tmp';
  if SaveStringsToUTF8File(Tmp, Lines, False) then
  begin
    // Окно в этот момент может читать файл: подмена повторяется (до ~100 мс), смена экрана не должна теряться.
    for I := 1 to 10 do
    begin
      if MoveFileExW(Tmp, UiStatePath, 1) then
      begin
        Done := True;
        Break;
      end;
      Sleep(10);
    end;
    if not Done then Log('state.json: не удалось подменить файл');
  end
  else
    Log('state.json: не удалось записать временный файл');
  UiPubFailed := not Done;
  UiLastPub := GetTickCount;
end;

procedure PublishState;
begin
  if (not UiActive) or UiGone then Exit;
  Inc(SvRev);
  WriteStateFile('{"rev":' + IntToStr(SvRev) + ',"screen":"' + SvScreen + '","op":"' + SvOp + '","percent":' +
    IntToStr(ShownPermille div 10) + '.' + IntToStr(ShownPermille mod 10) + ',"step":' + IntToStr(SvStep) +
    ',"step_state":"' + SvStepState + '","detail":"' + JsonStr(SvDetail) + '","file":"' + JsonStr(SvFile) +
    '","eta":"' + JsonStr(SvEta) + '","done_bytes":' + IntToStr(SvDone) + ',"total_bytes":' + IntToStr(SvTotal) +
    ',"state":"' + SvState + '","stage":"' + SvStage + '","launcher_on":' + JsonBool(LauncherOn) + ',"launcher_available":' + JsonBool(LauncherAvailable) +
    ',"launcher_installed":' + JsonBool(LauncherWanted) + ',"has_launcher_shortcut":' + JsonBool((LauncherLnk <> '') or LauncherWanted) +
    ',"launch_state":"' + JsonStr(SvLaunchState) + '","launch_message":"' + JsonStr(SvLaunchMessage) + '"' +
    ',"failure_code":"' + JsonStr(SvFailCode) + '","failure_message":"' + JsonStr(SvFailMsg) + '","failed_before_write":' + JsonBool(SvFailedBefore) +
    ',"fail_kind":"' + SvFailKind + '","note":"' + JsonStr(SvNote) + '","interrupted":' + JsonBool(SvInterrupted) +
    ',"can_remove":' + JsonBool(SvCanRemove) + ',"manager_version":"{#AppVersion}","translation_version":"' + JsonStr(InstalledVersion) +
    '","game_version":"' + JsonStr(InstalledGameVersion) + '","update_state":"' + JsonStr(SuState) +
    '","update_version":"' + JsonStr(SuVersion) + '","update_message":"' + JsonStr(SuMessage) +
    '","update_done_bytes":' + IntToStr(SuDone) + ',"update_total_bytes":' + IntToStr(SuTotal) +
#ifdef WithUpdater
    ',"update_enabled":true}');
#else
    ',"update_enabled":false}');
#endif
end;

// «Inno закончил»: окно, если оно ещё открыто, закрывается само.
procedure UiQuit;
begin
  if UiStatePath = '' then Exit;
  Inc(SvRev);
  WriteStateFile('{"rev":' + IntToStr(SvRev) + ',"screen":"quit"}');
end;

// 32 шестнадцатеричных знака из CoCreateGuid: токен мьютекса окна не угадывается по PID и времени.
function HexPart(V: LongWord; Digits: Integer): String;
var
  I: Integer;
  N: LongWord;
begin
  Result := '';
  for I := Digits - 1 downto 0 do
  begin
    N := (V shr (4 * I)) and $F;
    if N < 10 then Result := Result + Chr(48 + N)
    else Result := Result + Chr(87 + N);
  end;
end;

function NewUiToken: String;
var
  G: TGuidRec;
  I: Integer;
begin
  Result := '';
  if CoCreateGuid(G) = 0 then
  begin
    Result := HexPart(G.D1, 8) + HexPart(G.D2, 4) + HexPart(G.D3, 4);
    for I := 0 to 7 do Result := Result + HexPart(G.D4[I], 2);
  end;
  if Length(Result) <> 32 then   // CoCreateGuid не сработал: запасной токен хуже, но допустимого вида
    Result := IntToStr(GetCurrentProcessId) + 'x' + IntToStr(GetTickCount) + 'x' + IntToStr(GetTickCount xor $5A5A5A5A);
end;

// ---- защищённая папка установщика ----
// {tmp} (%TEMP%\is-*.tmp) доступна обычным процессам того же пользователя: между извлечением и запуском они подменили бы exe или файлы обмена
// и получили бы выполнение с правами администратора. Поэтому повышенный мастер не запускает ничего из {tmp}:
// он создаёт %ProgramData%\WXQ-setup-<guid> (прямо в ProgramData: обычный пользователь может создать там подпапку, но не может менять чужую),
// СРАЗУ закрывает её для всех, кроме Администраторов и SYSTEM, проверяет ACL чтением,
// только потом копирует туда WXQTool.exe, окно и данные и сверяет SHA-256 КОПИЙ с константами build_installer.py. Подробнее - ui\PROTOCOL.md.
type
  TSecFileData = record
    Attr, CreateLo, CreateHi, AccessLo, AccessHi, WriteLo, WriteHi, SizeHi, SizeLo: LongWord;
  end;
  TSecFileTime = record
    Lo, Hi: LongWord;
  end;

function GetFileAttributesW(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function GetFileAttributesExW(Name: String; Level: Integer; var Data: TSecFileData): Boolean;
  external 'GetFileAttributesExW@kernel32.dll stdcall';
procedure GetSystemTimeAsFileTime(var T: TSecFileTime);
  external 'GetSystemTimeAsFileTime@kernel32.dll stdcall';
function GetNamedSecurityInfoW(ObjectName: String; ObjectType, SecurityInfo: LongWord; var Owner: LongWord; Group, Dacl, Sacl: LongWord; var SD: LongWord): LongWord;
  external 'GetNamedSecurityInfoW@advapi32.dll stdcall';
function IsWellKnownSid(Sid: LongWord; SidType: Integer): Boolean;
  external 'IsWellKnownSid@advapi32.dll stdcall';
function EqualSid(A, B: LongWord): Boolean;
  external 'EqualSid@advapi32.dll stdcall';
function LocalFree(H: LongWord): LongWord;
  external 'LocalFree@kernel32.dll stdcall';

const
  SecNamePrefix = 'WXQ-setup-';
  // Права, которые выдаёт мастер: только эти два SID (по SID, а не по имени: имена зависят от языка Windows).
  SecGrantAdmins = '*S-1-5-32-544:(OI)(CI)F';
  SecGrantSystem = '*S-1-5-18:(OI)(CI)F';

// Точка повторного разбора (junction, символьная ссылка). Несуществующий путь сюда не передаётся.
function SecIsReparse(Path: String): Boolean;
begin
  Result := (GetFileAttributesW(Path) and $400) <> 0;
end;

function SecIcacls(Args: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\icacls.exe'), Args, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  if not Result then Log('icacls ' + Args + ' -> код ' + IntToStr(Code));
end;

// Сколько знаков Ch в S и N-е поле (с нуля) строки, разделённой «;».
function SecCount(S, Ch: String): Integer;
var
  I: Integer;
begin
  Result := 0;
  for I := 1 to Length(S) do
    if Copy(S, I, 1) = Ch then Inc(Result);
end;

function SecField(S: String; N: Integer): String;
var
  I, P: Integer;
begin
  for I := 1 to N do
  begin
    P := Pos(';', S);
    if P = 0 then
    begin
      Result := '';
      Exit;
    end;
    S := Copy(S, P + 1, Length(S));
  end;
  P := Pos(';', S);
  if P = 0 then Result := S else Result := Copy(S, 1, P - 1);
end;

// Права папки в SDDL (icacls /save; SDDL не зависит от языка Windows). Пустая строка - прочитать не удалось.
function SecReadSddl(Dir: String): String;
var
  Tmp, Last, Line, C: String;
  Raw: AnsiString;
  I: Integer;
begin
  Result := '';
  Tmp := Dir + '\acl-check.txt';
  DeleteFile(Tmp);
  if not SecIcacls('"' + Dir + '" /save "' + Tmp + '"') then Exit;
  if not LoadStringFromFile(Tmp, Raw) then Exit;
  DeleteFile(Tmp);
  // Файл в UTF-16 без BOM: убираем нулевые байты. Первая строка - имя папки (в нём может быть «D:»), нужна последняя непустая.
  Last := '';
  Line := '';
  for I := 1 to Length(Raw) do
  begin
    C := Copy(Raw, I, 1);
    if C = #0 then Continue;
    if (C = #10) or (C = #13) then
    begin
      if Line <> '' then Last := Line;
      Line := '';
    end
    else
      Line := Line + C;
  end;
  if Line <> '' then Last := Line;
  Result := Last;
end;

// Права закрыты ровно так, как выдал мастер: наследование отключено (флаг P), записей только "разрешить, OI CI, полный доступ" для
// Администраторов (BA) и SYSTEM (SY) по одной и ровно Extra лишних (в релизе 0; в тестовой сборке 1 - текущий пользователь).
function SecAclLocked(Sddl: String; Extra: Integer): Boolean;
var
  P, Q, Admins, System_, Others: Integer;
  Rest, Flags, Ace, Sid: String;
begin
  Result := False;
  if Copy(Sddl, 1, 2) <> 'D:' then Exit;
  Rest := Copy(Sddl, 3, Length(Sddl));
  P := Pos('S:', Rest);
  if P > 0 then Rest := Copy(Rest, 1, P - 1);
  Q := Pos('(', Rest);
  if Q = 0 then Exit;
  Flags := Copy(Rest, 1, Q - 1);
  if Pos('P', Flags) = 0 then Exit;
  Rest := Copy(Rest, Q, Length(Rest));
  Admins := 0;
  System_ := 0;
  Others := 0;
  while Rest <> '' do
  begin
    if Copy(Rest, 1, 1) <> '(' then Exit;
    Q := Pos(')', Rest);
    if Q = 0 then Exit;
    Ace := Copy(Rest, 2, Q - 2);
    Rest := Copy(Rest, Q + 1, Length(Rest));
    if SecCount(Ace, ';') <> 5 then Exit;
    if (SecField(Ace, 0) <> 'A') or (SecField(Ace, 1) <> 'OICI') or (SecField(Ace, 2) <> 'FA') or
       (SecField(Ace, 3) <> '') or (SecField(Ace, 4) <> '') then Exit;
    Sid := SecField(Ace, 5);
    if (Sid = 'BA') or (Sid = 'S-1-5-32-544') then Inc(Admins)
    else if (Sid = 'SY') or (Sid = 'S-1-5-18') then Inc(System_)
    else Inc(Others);
  end;
  Result := (Admins = 1) and (System_ = 1) and (Others = Extra);
end;

// Владелец папки (читается через GetNamedSecurityInfoW, без PowerShell): только Администраторы (WinBuiltinAdministratorsSid = 26) или
// SYSTEM (WinLocalSystemSid = 22). Повышенный процесс создаёт папку с владельцем «Администраторы»; обычный пользователь владельцем стать не может.
// Тестовая сборка (без повышения): допустим и тот же владелец, что у {tmp} мастера (текущий пользователь); /SECOWNER=SY - только SYSTEM (проверка отказа).
function SecOwnerOk(Path: String): Boolean;
var
  Owner, SD, RefOwner, RefSD, Rc: LongWord;
begin
  Result := False;
  Owner := 0;
  SD := 0;
  Rc := GetNamedSecurityInfoW(Path, 1, 1, Owner, 0, 0, 0, SD);
  if (Rc <> 0) or (Owner = 0) then
  begin
    Log('Владелец ' + Path + ' не прочитан (код ' + IntToStr(Rc) + ')');
    Exit;
  end;
  try
    Result := IsWellKnownSid(Owner, 26) or IsWellKnownSid(Owner, 22);
#ifdef TestBuild
    if ExpandConstant('{param:SECOWNER|}') = 'SY' then
      Result := IsWellKnownSid(Owner, 22)
    else if not Result then
    begin
      RefOwner := 0;
      RefSD := 0;
      if GetNamedSecurityInfoW(ExpandConstant('{tmp}'), 1, 1, RefOwner, 0, 0, 0, RefSD) = 0 then
      try
        Result := EqualSid(Owner, RefOwner);
      finally
        LocalFree(RefSD);
      end;
    end;
#endif
  finally
    LocalFree(SD);
  end;
  if not Result then Log('Владелец ' + Path + ' не Администраторы и не SYSTEM');
end;

function SecIsEmptyDir(Dir: String): Boolean;
var
  FR: TFindRec;
begin
  Result := True;
  if FindFirst(Dir + '\*', FR) then
  try
    repeat
      if (FR.Name <> '.') and (FR.Name <> '..') then
      begin
        Result := False;
        Break;
      end;
    until not FindNext(FR);
  finally
    FindClose(FR);
  end;
end;

// WXQ-setup- и ровно 32 шестнадцатеричных знака: чужие папки и ссылки этим именам не соответствуют.
function SecIsOurName(Name: String): Boolean;
var
  I: Integer;
  C: String;
begin
  Result := (Length(Name) = Length(SecNamePrefix) + 32) and (Copy(Name, 1, Length(SecNamePrefix)) = SecNamePrefix);
  if not Result then Exit;
  for I := Length(SecNamePrefix) + 1 to Length(Name) do
  begin
    C := Copy(Name, I, 1);
    if not (((C >= '0') and (C <= '9')) or ((C >= 'a') and (C <= 'f'))) then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

// Старше суток (по времени последней записи): единица старшего слова FILETIME - 2^32 * 100 нс, около 429 с, 202 единицы - около 24,1 часа.
function SecIsOld(Path: String): Boolean;
var
  D: TSecFileData;
  T: TSecFileTime;
begin
  Result := False;
  if not GetFileAttributesExW(Path, 0, D) then Exit;
  GetSystemTimeAsFileTime(T);
  Result := (T.Hi > D.WriteHi) and (T.Hi - D.WriteHi >= 202);
end;

// Удаление остатков прежних запусков: только внутри Parent, только папки с точным именем WXQ-setup-<32 hex>, не ссылки, владелец Администраторы/SYSTEM, старше суток.
// Рекурсивно удаляется только содержимое такой папки; чужие имена, ссылки и свежие папки (параллельный запуск) не трогаются.
procedure SecCleanOld(Parent: String);
var
  FR: TFindRec;
  Path: String;
begin
  if FindFirst(Parent + '\' + SecNamePrefix + '*', FR) then
  try
    repeat
      Path := Parent + '\' + FR.Name;
      if SecIsOurName(FR.Name) and (CompareText(Path, SecDir) <> 0) and not SecIsReparse(Path) and SecOwnerOk(Path) and SecIsOld(Path) then
      begin
        Log('Удаляю остатки прежней установки (старше суток): ' + Path);
        DelTree(Path, True, True, True);
      end;
    until not FindNext(FR);
  finally
    FindClose(FR);
  end;
end;

// Повторная проверка защищённой папки (после создания, после копирования, перед каждым запуском помощника и окна): не ссылка,
// права закрыты ровно как выдал мастер, владелец - Администраторы или SYSTEM. Любое расхождение - False (работать нельзя).
function SecVerify(Where: String): Boolean;
var
  Sddl: String;
begin
  Result := False;
  if (SecDir = '') or not DirExists(SecDir) then
  begin
    Log('Защищённая папка (' + Where + '): папки нет, отказ');
    Exit;
  end;
  if SecIsReparse(SecDir) then
  begin
    Log('Защищённая папка (' + Where + '): стала ссылкой, отказ');
    Exit;
  end;
  Sddl := SecReadSddl(SecDir);
  if not SecAclLocked(Sddl, SecExtra) then
  begin
    Log('Защищённая папка (' + Where + '): права не соответствуют ожидаемым (' + Sddl + '), отказ');
    Exit;
  end;
  if not SecOwnerOk(SecDir) then
  begin
    Log('Защищённая папка (' + Where + '): неверный владелец, отказ');
    Exit;
  end;
  Result := True;
end;

// Копия файла из {tmp} в защищённую папку и сверка SHA-256 КОПИИ с эталоном сборки. Несовпадение: копия удаляется, результат False.
// Эталон '' - сверки нет (только тестовая сборка с чужим exe окна).
function SecCopyFile(Source, Name, Expected: String): Boolean;
var
  Target, Actual: String;
begin
  Result := False;
  Target := SecDir + '\' + Name;
  if not FileExists(Source) then
  begin
    Log(Name + ' не найден: ' + Source);
    Exit;
  end;
  if not FileCopy(Source, Target, True) then
  begin
    Log(Name + ': не удалось скопировать в защищённую папку');
    Exit;
  end;
  if Expected <> '' then
  begin
    Actual := GetSHA256OfFile(Target);
    if CompareText(Actual, Expected) <> 0 then
    begin
      Log(Name + ': SHA-256 копии в защищённой папке (' + Actual + ') не совпал с эталоном сборки (' + Expected + ')');
      DeleteFile(Target);
      Exit;
    end;
    Log(Name + ': SHA-256 копии в защищённой папке совпал с эталоном сборки (' + Actual + ')');
  end;
  Result := True;
end;

// Удаление защищённой папки при выходе. Окно может ещё закрываться и держать свой exe: несколько попыток; не удалилась - не страшно,
// её уберёт следующий запуск (SecCleanOld).
procedure SecRemove;
var
  I: Integer;
begin
  if SecDir = '' then Exit;
  for I := 1 to 40 do
  begin
    if not DirExists(SecDir) then Break;
    if SecIsReparse(SecDir) then Break;   // подменили ссылкой: по ней не удаляем
    DelTree(SecDir, True, True, True);
    if not DirExists(SecDir) then Break;
    Sleep(100);
  end;
  if DirExists(SecDir) then Log('Защищённая папка не удалилась: ' + SecDir + ' (её уберёт следующий запуск)')
  else Log('Защищённая папка удалена');
  SecDir := '';
end;

// Создаёт защищённую папку и копирует в неё помощник и install.json. False - работать нельзя (помощника без неё не запустить): установка отказывается.
function SecPrepare: Boolean;
var
  Parent, Dir, Guid, Grant, ToolSha: String;
begin
  Result := False;
  SecDir := '';
  SecExtra := 0;
  Guid := NewUiToken;
  if Length(Guid) <> 32 then
  begin
    Log('Защищённая папка: не удалось получить GUID');
    Exit;
  end;
  Parent := RemoveBackslashUnlessRoot(ExpandConstant('{commonappdata}'));
  Dir := '';
#ifdef TestBuild
  // Тестовая сборка: /SECPARENT=<папка> - другой родитель (тесты не трогают настоящий ProgramData), /UISTATEDIR=<папка> - точный путь
  // защищённой папки (она обязана не существовать, как и в релизе). Права: к двум SID добавляется текущий пользователь, иначе тест запер бы себя.
  if ExpandConstant('{param:SECPARENT|}') <> '' then Parent := RemoveBackslashUnlessRoot(ExpandConstant('{param:SECPARENT|}'));
  if ExpandConstant('{param:UISTATEDIR|}') <> '' then
  begin
    Dir := RemoveBackslashUnlessRoot(ExpandConstant('{param:UISTATEDIR|}'));
    Parent := RemoveBackslashUnlessRoot(ExtractFilePath(Dir));
  end;
  if (Parent <> '') and not DirExists(Parent) then ForceDirectories(Parent);
#endif
  if Dir = '' then Dir := Parent + '\' + SecNamePrefix + Guid;

  // Родитель - сам ProgramData: должен существовать и не быть ссылкой. Мастер его не создаёт и не меняет (заранее подготовить родителя нельзя).
  if not DirExists(Parent) then
  begin
    Log('Защищённая папка: ' + Parent + ' не существует, отказ');
    Exit;
  end;
  if SecIsReparse(Parent) then
  begin
    Log('Защищённая папка: ' + Parent + ' является ссылкой (точкой повторного разбора), отказ');
    Exit;
  end;

  SecCleanOld(Parent);

  // Имя уникально: существующая папка или файл (в том числе ссылка) с таким именем - отказ; CreateDir не создаёт поверх существующего.
  if DirExists(Dir) or FileExists(Dir) then
  begin
    Log('Защищённая папка: ' + Dir + ' уже существует, отказ');
    Exit;
  end;
  if not CreateDir(Dir) then
  begin
    Log('Защищённая папка: не удалось создать ' + Dir);
    Exit;
  end;
  SecDir := Dir;

  // Сначала права, потом файлы.
  Grant := SecGrantAdmins + ' ' + SecGrantSystem;
#ifdef TestBuild
  Grant := Grant + ' "' + GetEnv('USERDOMAIN') + '\' + GetUserNameString + ':(OI)(CI)F"';
  SecExtra := 1;
#endif
  if not SecIcacls('"' + SecDir + '" /inheritance:r /grant:r ' + Grant) then
  begin
    Log('Защищённая папка: права не выставлены');
    SecRemove;
    Exit;
  end;
  // Результат проверяем чтением: ссылка ли это, закрыты ли права, чей владелец, пуста ли папка (до прав в неё мог что-то положить посторонний).
  if not SecVerify('после создания') then
  begin
    SecRemove;
    Exit;
  end;
  if not SecIsEmptyDir(SecDir) then
  begin
    Log('Защищённая папка: перед копированием в ней уже что-то есть, отказ');
    SecRemove;
    Exit;
  end;
  Log('Защищённая папка создана, права и владелец проверены: ' + SecDir);

  ExtractTemporaryFile('{#ToolExe}');
  ExtractTemporaryFile('install.json');
  ToolSha := '{#ToolSha256}';
#ifdef TestBuild
  if ExpandConstant('{param:TOOLSHA|}') <> '' then ToolSha := ExpandConstant('{param:TOOLSHA|}');   // проверка отказа при чужом помощнике
#endif
  if not SecCopyFile(ExpandConstant('{tmp}\{#ToolExe}'), '{#ToolExe}', ToolSha) then
  begin
    SecRemove;
    Exit;
  end;
  if not SecCopyFile(ExpandConstant('{tmp}\install.json'), 'install.json', '{#InstallSha256}') then
  begin
    SecRemove;
    Exit;
  end;
#ifdef WithLauncher
  ExtractTemporaryFile('launcher-profiles.json');
  if not SecCopyFile(ExpandConstant('{tmp}\launcher-profiles.json'), 'launcher-profiles.json', '{#ProfilesSha256}') then
  begin
    SecRemove;
    Exit;
  end;
#endif
  if not SecVerify('после копирования') then
  begin
    SecRemove;
    Exit;
  end;
  Log('WXQTool.exe и данные скопированы в защищённую папку, SHA-256 копий совпал с эталонами сборки');
  Result := True;
end;

// Окно живо, пока существует его мьютекс (он исчезает и при аварийном завершении процесса).
function UiAliveNow: Boolean;
var
  Handle: LongWord;
begin
  Handle := OpenMutexW($00100000, False, 'Local\WXQSetupUI-' + UiToken);
  Result := Handle <> 0;
  if Result then CloseHandle(Handle);
end;

// Окно мастера Inno и служебное окно приложения остаются невидимыми: ни окна, ни кнопки на панели задач.
// Служебное окно приложения (кнопка на панели задач) прячется и прозрачным стилем, и ShowWindow: ни одного кадра.
procedure HideAppWindows;
var
  H, Pid: LongWord;
begin
  H := 0;
  repeat
    H := FindWindowExW(0, H, 'TApplication', 0);
    if H <> 0 then
    begin
      GetWindowThreadProcessId(H, Pid);
      if (Pid = GetCurrentProcessId) and IsWindowVisible(H) then
      begin
        UiAppWnd := H;
        SetWindowLongW(H, -20, GetWindowLongW(H, -20) or $080800A0);
        SetLayeredWindowAttributes(H, 0, 0, 2);
        ShowWindow(H, 0);
      end;
    end;
  until H = 0;
end;

procedure KeepHidden;
begin
  if UiClosing then Exit;   // закрытие: мастеру нужно живое (хотя и прозрачное) окно, иначе отмена не срабатывает
  if IsWindowVisible(WizardForm.Handle) then ShowWindow(WizardForm.Handle, 0);
  HideAppWindows;
end;

// Скрытие без мигания: окно прозрачно (alpha 0), не принимает мышь, не активируется и не попадает на панель задач ещё до первого показа.
procedure UiHideWizard;
var
  Ex: LongInt;
begin
  Ex := GetWindowLongW(WizardForm.Handle, -20);
  SetWindowLongW(WizardForm.Handle, -20, Ex or $080800A0);   // LAYERED, TOOLWINDOW, TRANSPARENT, NOACTIVATE
  SetLayeredWindowAttributes(WizardForm.Handle, 0, 0, 2);
end;

// Откат на стандартные страницы Inno: окно UI не стартовало или исчезло; возвращаем мастеру обычный вид.
procedure UiRestoreWizard;
var
  Ex, Mask: LongInt;
begin
  Mask := $080800A0;
  Ex := GetWindowLongW(WizardForm.Handle, -20);
  SetWindowLongW(WizardForm.Handle, -20, Ex and (not Mask));
  ShowWindow(WizardForm.Handle, 5);
  if UiAppWnd <> 0 then
  begin
    SetWindowLongW(UiAppWnd, -20, GetWindowLongW(UiAppWnd, -20) and (not Mask));
    ShowWindow(UiAppWnd, 8);
  end;
  SetForegroundWindow(WizardForm.Handle);
end;

procedure UiLost;
begin
  Log('Окно WXQSetupUI закрылось не по команде: возвращаю стандартные страницы установщика');
  UiGone := True;
  UiActive := False;
  KillTimer(0, UiTimer);
  UiRestoreWizard;
end;

// ---- события установки -> состояние окна ----

procedure UiBeginProgress(Op: String);
begin
  if not UiActive then Exit;
  SvScreen := 'progress';
  SvOp := Op;
  SvState := 'running';
  SvStep := 0;
  SvStepState := 'run';
  SvDetail := '';
  SvStage := '';
  SvFile := '';
  SvEta := '';
  SvDone := 0;
  SvTotal := 0;
  SvNote := '';
  PublishState;
end;

procedure UiStepHook(I, State: Integer; Detail: String);
begin
  if not UiActive then Exit;
  if State = StPending then Exit;
  SvStage := '';
  if State = StDone then
  begin
    if I >= StepCount then SvStep := 6 else SvStep := I + 1;
    SvStepState := 'run';
    SvDetail := '';
  end
  else
  begin
    SvStep := I;
    SvDetail := Detail;
    if State = StFailed then SvStepState := 'fail' else SvStepState := 'run';
  end;
  SvDone := 0;
  SvTotal := 0;
  SvFile := '';
  PublishState;
end;

// Каталоги ярлыков. DESKTOPDIR и STARTDIR работают только в тестовой сборке (ярлыки тестов не попадают на настоящий рабочий стол).
function DesktopDir(Param: String): String;
begin
#ifdef TestBuild
  Result := ExpandConstant('{param:DESKTOPDIR|}');
  if Result <> '' then Exit;
#endif
  Result := ExpandConstant('{autodesktop}');
end;

function StartDir(Param: String): String;
begin
#ifdef TestBuild
  Result := ExpandConstant('{param:STARTDIR|}');
  if Result <> '' then Exit;
#endif
  Result := ExpandConstant('{autoprograms}\Egoist');
end;

// Активатор работает от обычного пользователя. Разрешён локальный exe; ссылки дополнительно отвергает помощник и активатор.
function LauncherTargetTrusted(Exe: String): Boolean;
begin
  Result := (Length(Exe) > 3) and (Copy(Exe, 2, 2) = ':\') and
    (CompareText(ExtractFileExt(Exe), '.exe') = 0) and FileExists(Exe);
end;

function OriginalLauncherExe(Param: String): String;
begin
  Result := LauncherExe;
end;

function GetDataDir(Param: String): String;
begin
  Result := '';
#ifdef TestBuild
  Result := ExpandConstant('{param:DATADIR|}');   // /DATADIR= читается только в тестовой сборке; в релизе каталог данных один
#endif
  if Result = '' then
    Result := ExpandConstant('{commonappdata}\Egoist\WXQ-Russian');
  Result := RemoveBackslashUnlessRoot(Result);
end;

// Каноническая папка программы. /DIR= в релизе не поддерживается: деинсталлятор и помощник лежат в {app}, а это Program Files только для администраторов.
function CanonicalAppDir: String;
begin
  Result := RemoveBackslashUnlessRoot(ExpandConstant('{commonpf64}\Egoist\WXQ-Russian'));
end;

function AppDirOk: Boolean;
begin
#ifdef TestBuild
  Result := True;   // тесты ставят в песочницу через /DIR=
#else
  Result := CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')), CanonicalAppDir) = 0;
#endif
end;

function AppDirRefusal: String;
begin
  Result := 'Установка в другую папку не поддерживается: программа ставится только в ' + CanonicalAppDir +
    '. Запустите установщик без параметра /DIR. В игру ничего не записано.';
end;

// Помощник запускается только из защищённой папки (SecDir), не из {tmp}.
function ToolPath: String;
begin
  Result := SecDir + '\{#ToolExe}';
end;

function LineValue(Lines: TArrayOfString; Name: String): String;
var
  I, P: Integer;
begin
  Result := '';
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    P := Pos('=', Lines[I]);
    if (P > 0) and (Copy(Lines[I], 1, P - 1) = Name) then
    begin
      Result := Copy(Lines[I], P + 1, Length(Lines[I]));
      Exit;
    end;
  end;
end;

function ToolValue(Name: String): String;
begin
  Result := LineValue(ToolLines, Name);
end;

// Запуск помощника. Результат команды — в ToolLines; True, если статус ok.
function RunTool(Command, Extra: String): Boolean;
var
  Code: Integer;
  OutFile, Params: String;
begin
  OutFile := SecDir + '\tool-out.txt';
  SetArrayLength(ToolLines, 0);
  if not SecVerify('перед запуском помощника') then
  begin
    Result := False;
    Exit;
  end;
  DeleteFile(OutFile);
  Params := Command + ' --payload "' + SecDir +
    '" --data "' + GetDataDir('') + '" --out "' + OutFile + '"' + GameParam + Extra;
  Log('Tool: ' + Command);
  Result := Exec(ToolPath, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
  if Result and FileExists(OutFile) then
    LoadStringsFromFile(OutFile, ToolLines)
  else
    Result := False;
  Result := Result and (ToolValue('status') = 'ok');
  Log('Tool ' + Command + ' -> ' + ToolValue('status') + ' ' + ToolValue('code'));
end;

function SelfCopyNeeded: Boolean;
begin
  if (SelfSourceSha = '') or (GetSHA256OfFile(ExpandConstant('{srcexe}')) <> SelfSourceSha) then
    RaiseException('Файл установщика изменился после запуска. Откройте его заново.');
  Result := CompareText(ExpandConstant('{srcexe}'), AddBackslash(WizardDirValue) + 'WXQ-Russian.exe') <> 0;
end;

function FailureText: String;
begin
  Result := ToolValue('message');
  if Result = '' then
    Result := 'Помощник установщика не отвечает. Проверьте антивирус и запустите установку ещё раз.';
end;

// Отказ случился до первой записи в игру (или запись уже откатана): только тогда можно честно сказать «Ничего не повреждено».
function FailedBeforeWrite(Code: String): Boolean;
begin
  Result := (Code = 'version') or (Code = 'running') or (Code = 'foreign') or (Copy(Code, 1, 5) = 'game_') or
    (Code = 'payload') or (Code = 'cache_missing') or (Code = 'reparse') or (Code = 'acl') or (Code = 'busy') or
    (Code = 'apply_reverted') or (Code = 'disk_space') or (Code = 'changed') or (Code = 'backup') or (Code = 'dir') or
    (Code = 'sdk') or (Code = 'transplant') or (Code = 'no_match') or (Code = 'reinstall');
end;

function FailureLead: String;
begin
  // Сбой при откате: код «running» (игру запустили посреди отката) и любой код вне списка «до записи» означают, что часть файлов уже возвращена.
  if RemoveMode and ((LastCode = 'running') or not FailedBeforeWrite(LastCode)) then
    Result := 'Откат прошёл только частично: часть файлов игры уже возвращена, часть ещё нет. Закройте игру и лаунчер, в том числе значок в области уведомлений, ' +
      'и нажмите «Удалить перевод» ещё раз: откат продолжится, резервные копии на месте. Причина:'
  else if FailedBeforeWrite(LastCode) then
    Result := 'Ничего не повреждено. Причина:'
  else
    Result := 'Установка прервана, часть файлов могла быть записана. Закройте игру и нажмите «Удалить перевод» для отката. Причина:';
end;

// Состояние для окна: установлен ли перевод и есть ли прерванная установка (тогда «Удалить перевод» откатывает её).
procedure RefreshStatus;
begin
  IsInstalled := False;
  IsPending := False;
  IsInterrupted := False;
  NeedsReapply := False;
  InStatus := True;
  try
    if RunTool('status', '') then
    begin
      IsInstalled := ToolValue('installed') = 'yes';
      IsPending := ToolValue('pending') = 'yes';
      IsInterrupted := ToolValue('interrupted') = 'yes';
      NeedsReapply := ToolValue('needs_reapply') = 'yes';
      InstalledVersion := ToolValue('version');
      InstalledGameVersion := ToolValue('game_version');
    end;
  finally
    InStatus := False;
  end;
end;

procedure SetBounds(Control: TControl; X, Y, W, H: Integer);
begin
  Control.SetBounds(ScaleX(X), ScaleY(Y), ScaleX(W), ScaleY(H));
end;

function NewLabel(X, Y, W, H, FontSize: Integer; Text: String;
  FontColor: TColor; Bold: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := Surface;
  Result.AutoSize := False;
  Result.WordWrap := True;
  Result.ShowAccelChar := False;
  Result.Font.Name := 'Segoe UI';
  Result.Font.Size := FontSize;
  Result.Font.Color := FontColor;
  if Bold then Result.Font.Name := 'Segoe UI Semibold';
  SetBounds(Result, X, Y, W, H);
  Result.Caption := Text;
end;

// Линия или заливка без сглаживания. Высота и ширина 1 — один физический пиксель (ScaleX/ScaleY(1) не даёт больше пикселя на 100–125 %).
function NewLine(X, Y, W, H: Integer; Tint: TColor): TPanel;
begin
  Result := TPanel.Create(WizardForm);
  Result.Parent := Surface;
  Result.BevelOuter := bvNone;
  Result.ParentBackground := False;
  Result.Color := Tint;
  SetBounds(Result, X, Y, W, H);
end;

// Вариант растров по масштабу Windows: ближайший из 100, 125, 150, 175, 200, 250. В тестовой сборке его можно задать переменной WXQ_UI_SCALE.
function PickUiScale: Integer;
var
  P: Integer;
begin
  P := ScaleX(100);
#ifdef TestBuild
  if GetEnv('WXQ_UI_SCALE') <> '' then P := StrToIntDef(GetEnv('WXQ_UI_SCALE'), P);
#endif
  if P < 113 then Result := 100
  else if P < 138 then Result := 125
  else if P < 163 then Result := 150
  else if P < 188 then Result := 175
  else if P < 225 then Result := 200
  else Result := 250;
end;

function LoadAsset(Name: String): TBitmap;
var
  FileName: String;
begin
  FileName := Name + '-' + IntToStr(UiPct) + '.bmp';
  ExtractTemporaryFile(FileName);
  Result := TBitmap.Create;
  Result.LoadFromFile(ExpandConstant('{tmp}') + '\' + FileName);
end;

// Картинка в натуральных пикселях (без растяжения): размер берётся из самого растра.
function NewImage(X, Y: Integer; Bmp: TBitmap): TBitmapImage;
begin
  Result := TBitmapImage.Create(WizardForm);
  Result.Parent := Surface;
  Result.AutoSize := False;
  Result.Stretch := False;
  Result.Center := False;
  Result.BackColor := BackgroundColor;
  Result.Bitmap.Assign(Bmp);
  Result.SetBounds(ScaleX(X), ScaleY(Y), Bmp.Width, Bmp.Height);
end;

// Подпись с разрядкой: между буквами тонкий пробел (трекинга у обычной надписи нет).
function Tracked(Text: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(Text) do
  begin
    Result := Result + Copy(Text, I, 1);
    if I < Length(Text) then Result := Result + ' ';   // U+200A, волосяной пробел
  end;
end;

// Кнопка: внешняя панель — рамка в 1 пиксель, внутренняя — заливка с подписью. У панели нет событий мыши, кроме клика,
// поэтому нажатие показывается короткой вспышкой более тёмной заливки перед действием; при наведении меняется курсор.
procedure PaintButton(Inner: TPanel; Down: Boolean);
begin
  if Inner = PrimaryInner then
  begin
    if Down then PrimaryInner.Color := GoldPressColor else PrimaryInner.Color := GoldColor;
    PrimaryButton.Color := PrimaryInner.Color;
  end
  else
  begin
    if Down then
    begin
      SecondaryInner.Color := PressColor;
      SecondaryButton.Color := LineHotColor;
    end
    else
    begin
      SecondaryInner.Color := BackgroundColor;
      SecondaryButton.Color := LineStrongColor;
    end;
  end;
end;

procedure PressPrimary(Sender: TObject); forward;
procedure SecondaryRouter(Sender: TObject); forward;

procedure ClickPrimary(Sender: TObject);
begin
  if not Busy then
  begin
    PaintButton(PrimaryInner, True);
    PrimaryInner.Update;
    Sleep(80);
    PaintButton(PrimaryInner, False);
  end;
  PressPrimary(Sender);
end;

procedure ClickSecondary(Sender: TObject);
begin
  if not Busy then
  begin
    PaintButton(SecondaryInner, True);
    SecondaryInner.Update;
    Sleep(80);
    PaintButton(SecondaryInner, False);
  end;
  SecondaryRouter(Sender);
end;

function NewButton(X, Y, W, H: Integer; Primary: Boolean; Handler: TNotifyEvent; var Inner: TPanel): TPanel;
begin
  Result := TPanel.Create(WizardForm);
  Result.Parent := Surface;
  Result.BevelOuter := bvNone;
  Result.ParentBackground := False;
  Result.Cursor := crHand;
  Result.OnClick := Handler;
  if Primary then Result.Color := GoldColor else Result.Color := LineStrongColor;
  SetBounds(Result, X, Y, W, H);
  Inner := TPanel.Create(WizardForm);
  Inner.Parent := Result;
  Inner.BevelOuter := bvNone;
  Inner.ParentBackground := False;
  Inner.Font.Name := 'Segoe UI Semibold';
  Inner.Font.Size := 10;
  Inner.Cursor := crHand;
  Inner.OnClick := Handler;
  if Primary then
  begin
    Inner.Color := GoldColor;
    Inner.Font.Color := InkColor;
  end
  else
  begin
    Inner.Color := BackgroundColor;
    Inner.Font.Color := TextColor;
  end;
  Inner.SetBounds(1, 1, Result.Width - 2, Result.Height - 2);
end;

function StepTitle(I: Integer): String;
begin
  case I of
    1: Result := 'Поиск игры';
    2: Result := 'Проверка версии игры';
    3: Result := 'Резервная копия';
    4: Result := 'Запись перевода';
    5: Result := 'Проверка результата';
  end;
end;

// Вес шагов в общей полосе (в тысячных): поиск 3 %, версия 10 %, копия 20 %, запись 45 %, проверка 22 %. Шаг 0 — вся полоса (удаление).
function StepBase(I: Integer): Integer;
begin
  case I of
    0: Result := 0;
    1: Result := 0;
    2: Result := 30;
    3: Result := 130;
    4: Result := 330;
    5: Result := 780;
  else Result := 0;
  end;
end;

function StepWeight(I: Integer): Integer;
begin
  case I of
    0: Result := 1000;
    1: Result := 30;
    2: Result := 100;
    3: Result := 200;
    4: Result := 450;
    5: Result := 220;
  else Result := 0;
  end;
end;

function EtaText(Permille: Integer): String;
var
  Elapsed, Remain: Integer;
begin
  Result := '';
  if Permille >= 1000 then begin Result := 'готово'; Exit; end;
  if Permille < 40 then begin Result := 'считаю время…'; Exit; end;
  Elapsed := Integer((GetTickCount - StartTick) div 1000);
  Remain := (Elapsed * (1000 - Permille)) div Permille;
  if Remain < 5 then Result := 'почти готово'
  else if Remain < 60 then Result := 'осталось ~' + IntToStr(Remain) + ' с'
  else Result := 'осталось ~' + IntToStr((Remain + 59) div 60) + ' мин';
end;

// Полоса идёт чаще, чем окно успевает её рисовать: запись в файл не чаще раза в 90 мс (кроме последней).
procedure UiOverallHook(Permille: Integer);
begin
  if not UiActive then Exit;
  SvEta := EtaText(Permille);
  if (Permille >= 1000) or (Integer(GetTickCount - UiLastPub) >= 90) then PublishState;
end;

procedure UiLiveHook(Step: Integer; Lines: TArrayOfString);
var
  Idx, Cnt: String;
begin
  if not UiActive then Exit;
  // Фаза closing: помощник закрывает мешающие процессы (item - их имена). Подпись шага возвращается, когда фаза закончилась.
  if LineValue(Lines, 'phase') = 'closing' then
  begin
    if SvStage <> 'close' then SvDetailBeforeClose := SvDetail;
    SvStage := 'close';
    SvDetail := 'Закрываю игру и лаунчер: ' + LineValue(Lines, 'item');
    SvFile := '';
    SvDone := 0;
    SvTotal := 0;
    Exit;
  end;
  if SvStage = 'close' then
  begin
    SvStage := '';
    SvDetail := SvDetailBeforeClose;
  end;
  SvDone := StrToInt64Def(LineValue(Lines, 'done'), 0);
  SvTotal := StrToInt64Def(LineValue(Lines, 'total'), 0);
  SvFile := LineValue(Lines, 'item');
  Idx := LineValue(Lines, 'index');
  Cnt := LineValue(Lines, 'count');
  if (Step >= 1) and (Idx <> '') and (Cnt <> '') and (Cnt <> '0') then SvDetail := 'Файл ' + Idx + ' из ' + Cnt;
end;

// Результат удаления для окна: «удалено» (с заметкой) или сбой удаления.
procedure UiRemoved(Note: String);
begin
  if not UiActive then Exit;
  SvScreen := 'removed';
  SvOp := 'remove';
  SvState := 'finished';
  SvNote := Note;
  ShownPermille := 1000;
  PublishState;
end;

// Сбой установки (текст — результат PrepareToInstall): страница wpPreparing показывается ещё ДО проверок, поэтому экран сбоя выставляется здесь, а не по странице.
procedure UiInstallFailed(Msg: String);
begin
  if not UiActive then Exit;
  if Msg = '' then Msg := 'Подготовка к установке не удалась.';
  SvScreen := 'fail';
  SvOp := 'install';
  SvFailKind := 'install';
  SvFailMsg := Msg;
  SvFailCode := LastCode;
  SvFailedBefore := FailedBeforeWrite(LastCode);
  SvState := 'failed';
  PublishState;
end;

procedure UiRemoveFailed;
begin
  if not UiActive then Exit;
  SvScreen := 'fail';
  SvFailKind := 'remove';
  SvFailMsg := LastFailure;
  SvFailCode := LastCode;
  SvFailedBefore := FailedBeforeWrite(LastCode) and (LastCode <> 'running');   // при откате «running» - часть файлов уже возвращена
  SvState := 'failed';
  PublishState;
end;

// Общая полоса в тысячных долях; только вперёд (при пересчёте объёма назад не прыгает).
procedure SetOverall(Permille: Integer);
begin
  if Permille > 1000 then Permille := 1000;
  if Permille < ShownPermille then Permille := ShownPermille;
  ShownPermille := Permille;
  ProgressFill.Width := (ProgressTrack.ClientWidth * Permille) div 1000;
  PercentLabel.Caption := IntToStr(Permille div 10) + '%';
  EtaLabel.Caption := EtaText(Permille) + SizeText;
  UiOverallHook(Permille);
end;

procedure SetStep(I, State: Integer; Detail: String);
begin
  case State of
    StPending: begin StepDot[I].Bitmap.Assign(DotWait); StepName[I].Font.Color := MutedColor; StepDetail[I].Font.Color := MutedColor; end;
    StActive: begin StepDot[I].Bitmap.Assign(DotAct); StepName[I].Font.Color := TextColor; StepDetail[I].Font.Color := GoldColor; end;
    StDone: begin StepDot[I].Bitmap.Assign(DotDone); StepName[I].Font.Color := TextColor; StepDetail[I].Font.Color := MutedColor; end;
    StFailed: begin StepDot[I].Bitmap.Assign(DotFail); StepName[I].Font.Color := TextColor; StepDetail[I].Font.Color := DangerColor; end;
  end;
  StepDetail[I].Caption := Detail;
  UiStepHook(I, State, Detail);
  if State = StDone then
  begin
    SizeText := '';
    SetOverall(StepBase(I) + StepWeight(I));
  end;
  WizardForm.Update;
end;

procedure ResetSteps;
var
  I: Integer;
begin
  for I := 1 to StepCount do SetStep(I, StPending, '');
  ShownPermille := 0;
  SizeText := '';
  ProgressFill.Width := 0;
  PercentLabel.Caption := '0%';
  EtaLabel.Caption := '';
end;

procedure ShowSteps(Visible: Boolean);
var
  I: Integer;
begin
  for I := 1 to StepCount do
  begin
    StepDot[I].Visible := Visible;
    StepName[I].Visible := Visible;
    StepDetail[I].Visible := Visible;
  end;
  ProgressTrack.Visible := Visible;
  PercentLabel.Visible := Visible;
  EtaLabel.Visible := Visible;
end;

// Прогресс без списка шагов (удаление перевода): только полоса, проценты и время.
procedure ShowBarOnly;
begin
  ProgressTrack.Visible := True;
  PercentLabel.Visible := True;
  EtaLabel.Visible := True;
end;

// Обновить окно по файлу прогресса помощника (ключи phase, done, total, item, index, count, state).
procedure ShowLive(Step: Integer; Lines: TArrayOfString);
var
  Done, Total: Int64;
  Frac: Integer;
  Item: String;
begin
  Done := StrToInt64Def(LineValue(Lines, 'done'), 0);
  Total := StrToInt64Def(LineValue(Lines, 'total'), 0);
  Frac := 0;
  if Total > 0 then Frac := Integer((Done * 1000) div Total);
  if Frac > 1000 then Frac := 1000;
  SizeText := '';
  if Total >= 1073741824 then
    SizeText := '  ·  ' + IntToStr(Done div 1073741824) + ',' + IntToStr(((Done mod 1073741824) * 10) div 1073741824) + ' из ' +
      IntToStr(Total div 1073741824) + ',' + IntToStr(((Total mod 1073741824) * 10) div 1073741824) + ' ГБ'
  else if Total >= 1048576 then
    SizeText := '  ·  ' + IntToStr(Done div 1048576) + ' из ' + IntToStr(Total div 1048576) + ' МБ';
  Item := LineValue(Lines, 'item');
  if LineValue(Lines, 'phase') = 'closing' then
  begin
    // Помощник закрывает игру и лаунчер: строка видна и в окне Inno (запасные страницы), и в WXQSetupUI.
    if (Step >= 1) and not ClosingShown then PreClosingCaption := StepDetail[Step].Caption;
    ClosingShown := True;
    if Step >= 1 then StepDetail[Step].Caption := 'Закрываю игру и лаунчер: ' + Item
    else DetailLabel.Caption := 'Закрываю игру и лаунчер: ' + Item;
    UiLiveHook(Step, Lines);
    Exit;
  end;
  if ClosingShown then
  begin
    ClosingShown := False;
    if Step >= 1 then StepDetail[Step].Caption := PreClosingCaption
    else DetailLabel.Caption := 'Восстанавливаю файлы по резервным копиям.';
  end;
  if (Step >= 1) and (Item <> '') then StepDetail[Step].Caption := Item;
  UiLiveHook(Step, Lines);
  SetOverall(StepBase(Step) + (StepWeight(Step) * Frac) div 1000);
end;

// Помощник просят остановиться файлом-флагом «<файл прогресса>.cancel». Он читает его только между файлами (перед чтением или копированием очередного файла),
// никогда посреди записи; при остановке во время замены он сам откатывает уже записанное (так же, как при любом сбое замены). Процесс НЕ убиваем:
// Kill посреди замены файла оставил бы игру в промежуточном состоянии. Ждём завершения по его дескриптору до 10 минут и всё это время
// показываем «идёт запись, подождите». True - помощник завершился сам.
function ToolStopped(ProgFile: String; Step: Integer; ProcessHandle: LongWord): Boolean;
var
  I: Integer;
  Handle: LongWord;
  Flag: TArrayOfString;
  Text: String;
begin
  Result := False;
  SetArrayLength(Flag, 1);
  Flag[0] := 'stop';
  SaveStringsToFile(ProgFile + '.cancel', Flag, False);
  Text := 'Идёт запись, подождите: помощник заканчивает текущий файл. Не закрывайте окно и не выключайте компьютер.';
  DetailLabel.Caption := Text;
  if (Step >= 1) and (Step <= StepCount) then StepDetail[Step].Caption := 'идёт запись, подождите';
  if UiActive then
  begin
    SvDetail := 'идёт запись, подождите';
    PublishState;
  end;
  for I := 1 to 15000 do
  begin
    Handle := WaitToolProcess(ProcessHandle, 0);
    if Handle = 0 then
    begin
      Result := True;
      Break;
    end;
    if Handle = $FFFFFFFF then Break;
    Sleep(40);
    PumpMessages;
    if UiActive and (not UiClosing) and (not UiAliveNow) then UiLost;
  end;
  if Result then Log('Остановка помощника по флагу: помощник завершился сам')
  else Log('Остановка помощника по флагу: помощник не завершился за 10 минут');
end;

// Закрытие игры и лаунчера, которые мешают, берёт на себя помощник (WXQTool.cs, Closer). Фоновых закрытий нет: с /UPDATE=1 (старый путь помощника, вручную)
// передаётся stale, который ничего не закрывает; интерактивный мастер (в том числе по кнопке «Обновить сейчас»), явный тихий запуск и удаление закрывают всё подходящее.
function CloseParam: String;
begin
  if UpdateMode and (not SelfUpdateContinue) then Result := ' --close stale' else Result := ' --close all';
end;

// Запуск помощника с живым прогрессом: окно не зависает, полоса идёт по файлу --progress. Результат — как у RunTool.
function RunToolLive(Command, Extra: String; Step: Integer): Boolean;
var
  Idle: Integer;
  ExitCode, WaitCode: LongWord;
  Startup: TToolStartupInfo;
  ProcessInfo: TToolProcessInfo;
  Io: TToolIoCounters;
  OutFile, ProgFile, Params, Last, Cur: String;
  LastReadBytes, LastWriteBytes: Int64;
  PL: TArrayOfString;
  Finished: Boolean;
begin
  OutFile := SecDir + '\tool-out.txt';
  ProgFile := SecDir + '\tool-progress.txt';
  SetArrayLength(ToolLines, 0);
  if not SecVerify('перед запуском помощника') then
  begin
    Result := False;
    Exit;
  end;
  DeleteFile(OutFile);
  DeleteFile(ProgFile);
  DeleteFile(ProgFile + '.cancel');
  Params := Command + ' --payload "' + SecDir +
    '" --data "' + GetDataDir('') + '" --out "' + OutFile + '" --progress "' + ProgFile + '"' + GameParam + CloseParam + Extra;
  Log('Tool (live): ' + Command);
  // В ewNoWait Exec не возвращает дескриптор процесса. Файл --out тоже может
  // появиться до завершения финальной записи. Ждём именно созданный нами процесс.
  Startup.Size := 68;
  Startup.Flags := 1; // STARTF_USESHOWWINDOW
  Startup.ShowWindow := SW_HIDE;
  Result := CreateToolProcess(ToolPath, '"' + ToolPath + '" ' + Params, 0, 0, False,
    $08000000, 0, SecDir, Startup, ProcessInfo); // CREATE_NO_WINDOW
  if not Result then Exit;
  CloseHandle(ProcessInfo.ThreadHandle);
  try
  Last := '';
  LastReadBytes := 0;
  LastWriteBytes := 0;
  Idle := 0;
  Finished := False;
  while not Finished do
  begin
    Sleep(40);
    PumpMessages;
    // Окно пропало во время записи: установка идёт дальше, а мастер Inno снова становится видимым (прогресс и итог).
    if UiActive and (not UiClosing) and (not UiAliveNow) then UiLost;
    SetArrayLength(PL, 0);
    if FileExists(ProgFile) and LoadStringsFromFile(ProgFile, PL) then
    begin
      Cur := LineValue(PL, 'done') + '|' + LineValue(PL, 'index') + '|' + LineValue(PL, 'state') + '|' + LineValue(PL, 'phase') + '|' + LineValue(PL, 'item');
      if Cur <> Last then
      begin
        Last := Cur;
        Idle := 0;
        ShowLive(Step, PL);
      end
      else
      begin
        Inc(Idle);
        if (Idle mod 3) = 0 then SetOverall(ShownPermille);   // время живёт и между записями
      end;
    end
    else
      Inc(Idle);
    // Активное чтение/запись на медленном диске не считаем зависанием даже при
    // задержке файла прогресса. Счётчики относятся только к нашему дескриптору.
    if GetToolIoCounters(ProcessInfo.ProcessHandle, Io) then
    begin
      // Мелкая служебная запись самого progress-файла не продлевает ожидание.
      if (Io.ReadBytes > LastReadBytes) or (Io.WriteBytes - LastWriteBytes > 65536) then Idle := 0;
      LastReadBytes := Io.ReadBytes;
      LastWriteBytes := Io.WriteBytes;
    end;
    WaitCode := WaitToolProcess(ProcessInfo.ProcessHandle, 0);
    Finished := WaitCode = 0;
    if WaitCode = $FFFFFFFF then
    begin
      Result := False;
      Exit;
    end;
    // Длительная обработка одного файла допустима. Только 15 минут без прогресса
    // и без дискового обмена приводят к просьбе остановиться между файлами.
    if (not Finished) and (Idle > 22500) then
    begin
      Log('Tool ' + Command + ': нет прогресса и дискового обмена 15 минут, прошу помощника остановиться');
      if not ToolStopped(ProgFile, Step, ProcessInfo.ProcessHandle) then
      begin
        Log('Tool ' + Command + ': помощник не завершился за 10 минут после просьбы остановиться');
        SetArrayLength(ToolLines, 2);
        ToolLines[0] := 'code=tool_hung';
        ToolLines[1] := 'message=Установка ещё не завершилась. Файлы игры могут быть записаны не полностью. Не запускайте игру: ' +
          'перезагрузите компьютер, запустите установщик ещё раз и нажмите «Удалить перевод».';
        Result := False;
        Exit;
      end;
      Finished := True;   // помощник остановился сам: его результат читается ниже
    end;
  end;
  if FileExists(OutFile) then LoadStringsFromFile(OutFile, ToolLines)
  else Result := False;
  if not GetToolExitCode(ProcessInfo.ProcessHandle, ExitCode) then
  begin
    Result := False;
    SetArrayLength(ToolLines, 3);
    ToolLines[0] := 'status=error';
    ToolLines[1] := 'code=tool_process';
    ToolLines[2] := 'message=Не удалось подтвердить завершение операции. Повторите установку; резервные копии сохранены.';
  end
  else if (ExitCode <> 0) and (ToolValue('status') = 'ok') then
  begin
    Result := False;
    SetArrayLength(ToolLines, 3);
    ToolLines[0] := 'status=error';
    ToolLines[1] := 'code=tool_exit';
    ToolLines[2] := 'message=Операция завершилась с ошибкой после записи результата. Повторите установку; резервные копии сохранены.';
  end;
  Result := Result and (ExitCode = 0) and (ToolValue('status') = 'ok');
  Log('Tool ' + Command + ' -> ' + ToolValue('status') + ' ' + ToolValue('code'));
  finally
    CloseHandle(ProcessInfo.ProcessHandle);
  end;
end;

function ShortPath(Path: String): String;
begin
  Result := Path;
  if Length(Result) > 36 then Result := Copy(Result, 1, 10) + '…' + Copy(Result, Length(Result) - 23, 24);
end;

function LaunchGame: Boolean;
var
  Code: Integer;
  Ru: String;
begin
  Result := False;
  Code := 2;
  // Активатор сам запрашивает UAC только при реальной необходимости. Ждём его
  // результат, а не закрываем окно после одного успешного создания процесса.
  if LauncherWanted then
  begin
    Ru := ExpandConstant('{app}\launcher\WXQLauncherRU.exe');
    if FileExists(Ru) then
      Result := ShellExecAsOriginalUser('open', Ru, '', ExtractFileDir(Ru), SW_SHOWNORMAL, ewWaitUntilTerminated, Code) and (Code = 0);
  end
  else if LauncherExe <> '' then
    Result := ShellExecAsOriginalUser('open', LauncherExe, '', ExtractFileDir(LauncherExe), SW_SHOWNORMAL, ewNoWait, Code)
  else if LauncherLnk <> '' then
    Result := ShellExecAsOriginalUser('open', LauncherLnk, '', '', SW_SHOWNORMAL, ewNoWait, Code);
  Log('Launch game: accepted=' + JsonBool(Result) + ' code=' + IntToStr(Code));
  if not Result then
  begin
    if Code = 1223 then SvLaunchMessage := 'Запуск отменён в окне подтверждения Windows. Можно повторить.'
    else SvLaunchMessage := 'Не удалось открыть лаунчер (код ' + IntToStr(Code) + '). Перевод сохранён. Повторите запуск.';
  end;
end;

// Тексты окна. Без жаргона: что ставится, что не входит, как удалить, что делать после обновления игры.
function NL: String;
begin
  Result := Chr(13) + Chr(10);
end;

function InfoWhatIs: String;
begin
#ifdef WithLauncher
  Result := 'Ставится: тексты игры и картинки интерфейса (кнопки, ранги, вывеска «Новости»). ' +
    'Некоторые изображения и ранние экраны игры ещё могут оставаться на китайском.';
#else
  Result := 'Ставится: тексты игры и картинки интерфейса (кнопки, ранги, вывеска «Новости»).' + NL +
    'Лаунчер остаётся на китайском. Некоторые изображения и ранние экраны игры ещё могут оставаться на китайском.';
#endif
end;

function InfoAfterUpdate: String;
begin
  Result := 'Обновление игры может заменить перевод. Здесь можно повторно установить известные строки: новые и изменившиеся останутся в оригинале. ' +
    'Если игра ещё не создала новый кэш, установщик подскажет один раз запустить её до меню.';
end;

function InfoWelcome: String;
begin
#ifdef WithLauncher
  Result := InfoWhatIs + NL + NL +
    'Перед установкой закройте игру и лаунчер, в том числе значок в области уведомлений: если они ещё запущены, установщик сам закроет их и скажет об этом. Интернет не нужен. ' +
    'Перед записью делается резервная копия; перевод можно удалить кнопкой в этом окне или через «Установку и удаление программ».';
#else
  Result := InfoWhatIs + NL + NL +
    'Перед установкой закройте игру и лаунчер, в том числе значок в области уведомлений: если они ещё запущены, установщик сам закроет их и скажет об этом. Интернет не нужен.' + NL + NL +
    'Перед записью установщик сохраняет резервную копию. Удалить перевод можно кнопкой в этом окне ' +
    'или через «Установку и удаление программ».' + NL + NL + InfoAfterUpdate;
#endif
end;

function InfoInstalled: String;
begin
#ifdef WithLauncher
  Result := '«Переустановить» запишет файлы перевода заново, «Удалить перевод» восстановит файлы до установки перевода. ' +
    'Игру и лаунчер перед этим закройте.' + NL + NL + InfoWhatIs;
#else
  Result := '«Переустановить» запишет файлы перевода заново, «Удалить перевод» восстановит файлы до установки перевода. ' +
    'Игру и лаунчер перед этим закройте.' + NL + NL + InfoWhatIs + NL + NL + InfoAfterUpdate;
#endif
end;

function InfoInterrupted: String;
begin
  Result := 'Прошлая установка или удаление не завершились. «Удалить перевод» восстановит файлы до установки перевода по резервным копиям ' +
    '(в том числе если запись успела пройти только частично). После этого перевод можно установить заново.' + NL + NL +
    'Игру и лаунчер перед этим закройте, в том числе значок в области уведомлений.';
end;

function RemoveCaption: String;
begin
  Result := '';
  if IsInstalled or IsPending then Result := 'Удалить перевод';
end;

function LauncherOffReason: String;
begin
  if not LauncherOn then Result := 'вы выключили его в начале установки'
  else if LauncherFindReason = 'ambiguous' then Result := 'найдено несколько установок лаунчера'
  else if LauncherFindReason = 'unsafe-path' then Result := 'путь лаунчера содержит ссылку или сетевой каталог'
  else Result := 'подходящая версия оригинального лаунчера не найдена; нужен профиль этой сборки';
end;

function InstallResultNote: String;
begin
  Result := '';
  if (SkippedResources > 0) or (PreservedTextRows > 0) then
  begin
    if AppliedResources > 0 then
      Result := 'Применён перевод совместимых ресурсов. Новые и изменённые данные оставлены в оригинале.'
    else
      Result := 'Совместимых данных для перевода не найдено. Файлы игры оставлены в оригинале.';
  end;
end;

function InfoFinished(HasLauncher: Boolean): String;
begin
  if LauncherWanted then
  begin
    Result := 'Переведена игра, перевод лаунчера подготовлен. Русский лаунчер запускается ярлыком «Honor of Kings ACE (RU)»: он открывает оригинальный ' +
      'лаунчер без изменения его файлов и подключает перевод от обычного пользователя. ' +
      'Русский лаунчер включён по умолчанию; функция новая и не проверена в игре с защитой ACE. Если подключить перевод ' +
      'не получится, откроется обычный лаунчер на китайском. Оригинальный ярлык лаунчера остаётся прежним. ' +
      'Чтобы убрать русский лаунчер, нажмите «Удалить перевод» и поставьте перевод заново с выключенным переключателем (в тихом режиме: /LAUNCHERRU=0).' +
      NL + NL + 'Некоторые изображения и ранние экраны игры ещё могут оставаться на китайском. Удалить перевод: «Установка и удаление программ». После обновления игры запустите её один раз, затем установите перевод заново; новое останется китайским.';
    Exit;
  end;
#ifdef WithLauncher
  Result := 'Переведена игра. Русский лаунчер не установлен (' + LauncherOffReason + '): лаунчер остаётся на китайском.' + NL + NL +
    'Некоторые изображения и ранние экраны игры ещё могут оставаться на китайском.' + NL + NL;
#else
  Result := 'Переведена игра. Перевод лаунчера в эту сборку не включён.' +
    NL + NL + 'Некоторые изображения и ранние экраны игры ещё могут оставаться на китайском.' + NL + NL;
#endif
  if HasLauncher then
    Result := Result + 'Кнопка «Запустить игру» открывает оригинальный лаунчер. '
  else
    Result := Result + 'Ярлык лаунчера не найден, откройте игру вручную. ';
  Result := Result + 'Удалить перевод: «Установка и удаление программ». После обновления игры запустите её один раз, затем установите перевод заново; новое останется китайским.';
end;

procedure PressRemove(Sender: TObject); forward;

// Снятие записи из «Установки и удаления программ» после отката. Деинсталлятор Inno запускает вторую фазу и сразу возвращает управление, поэтому
// после запуска ждём (до 60 с), пока unins000.exe исчезнет: так видно, что запись действительно снята. False - нет: запись осталась.
function RemoveUninstallEntry(Uninstaller: String): Boolean;
var
  Code, I: Integer;
begin
  Result := False;
  if not FileExists(Uninstaller) then
  begin
    Result := True;   // записи уже нет
    Exit;
  end;
  if not AppDirOk then
  begin
    Log('Деинсталлятор не запускается: папка программы не каноническая (' + Uninstaller + ')');
    Exit;
  end;
  if not Exec(Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code) then
  begin
    Log('Деинсталлятор не запустился: ' + Uninstaller);
    Exit;
  end;
  if Code <> 0 then
  begin
    Log('Деинсталлятор вернул код ' + IntToStr(Code));
    Exit;
  end;
  for I := 1 to 240 do
  begin
    if not FileExists(Uninstaller) then
    begin
      Result := True;
      Break;
    end;
    Sleep(250);
    PumpMessages;
  end;
  if Result then Log('Запись об установке снята')
  else Log('Деинсталлятор не завершил работу за 60 секунд: запись об установке осталась');
end;

// Переключатель «Русский лаунчер»: растровый флажок по масштабу Windows, клик по любой его части переключает (родных чекбоксов нет).
procedure ApplyToggle;
begin
  if LauncherOn then
  begin
    ToggleImage.Bitmap.Assign(CheckOn);
    ToggleTitle.Font.Color := TextColor;
  end
  else
  begin
    ToggleImage.Bitmap.Assign(CheckOff);
    ToggleTitle.Font.Color := MutedColor;
  end;
end;

procedure PressToggle(Sender: TObject);
begin
  if Busy then Exit;
  LauncherOn := not LauncherOn;
  ApplyToggle;
end;

procedure ShowToggle(Visible: Boolean);
begin
  if ToggleImage = nil then Exit;
  ToggleLine.Visible := Visible;
  ToggleImage.Visible := Visible;
  ToggleTitle.Visible := Visible;
  ToggleNote.Visible := Visible;
  if Visible then InfoLabel.Height := ScaleY(InfoToggleHeight)
  else InfoLabel.Height := ScaleY(InfoHeight);
end;

procedure PressPrimary(Sender: TObject);
begin
  if Busy then Exit;
  if WizardForm.CurPageID = wpFinished then
  begin
    if Mode = 2 then
      if not LaunchGame then
      begin
        InfoLabel.Caption := SvLaunchMessage;
        InfoLabel.Visible := True;
        Exit;
      end;
    WizardForm.NextButton.OnClick(WizardForm.NextButton);
  end
  else if Mode = 1 then
  begin
    // Сбой на странице установки: вернуться к приветствию. Сбой удаления (приветствие): только закрыть.
    if WizardForm.CurPageID = wpWelcome then WizardForm.CancelButton.OnClick(WizardForm.CancelButton)
    else WizardForm.BackButton.OnClick(WizardForm.BackButton);
  end
  else if Mode = 3 then
    WizardForm.CancelButton.OnClick(WizardForm.CancelButton)
  else if (Mode = 0) and (WizardForm.CurPageID = wpWelcome) and IsInterrupted then
    PressRemove(Sender)   // прерванная установка: главное действие — откат
  else if WizardForm.NextButton.Enabled then
    WizardForm.NextButton.OnClick(WizardForm.NextButton);
end;

procedure ShowMode(NewMode: Integer; State, Detail, Info, Primary, Secondary: String); forward;
#ifdef WithLauncher
procedure RemoveLauncherTraces; forward;
#endif

// «Удалить перевод» из окна установщика: откат по резервной копии, затем снятие записи из «Установки и удаления программ».
procedure PressRemove(Sender: TObject);
var
  Foreign, Note: String;
begin
  if Mode = 0 then
  begin
    PrimaryButton.Visible := False;
    SecondaryButton.Visible := False;
    InfoLabel.Visible := False;
    ResetSteps;
    StartTick := GetTickCount;
    ShowBarOnly;
    StateLabel.Caption := 'Восстанавливаю файлы';
    DetailLabel.Caption := 'Игра и лаунчер должны быть закрыты.';
    Busy := True;
    RemoveMode := True;
    UiBeginProgress('remove');
    if RunToolLive('restore', '', 0) then
    begin
      Foreign := ToolValue('foreign');
#ifdef WithLauncher
      RemoveLauncherTraces;
      LauncherExe := '';
#endif
      Busy := False;
      Removed := True;
      IsInstalled := False;
      IsPending := False;
      IsInterrupted := False;
      Note := '';
      if (Foreign <> '') and (Foreign <> '0') then
        Note := 'Резервные копии другой папки игры (' + Foreign + ') не тронуты и оставлены на месте.'
      else if ToolValue('skipped') <> '' then
        Note := 'Часть файлов (' + ToolValue('skipped') + ') уже заменена игрой или обновлением; их установщик не тронул.';
      if Note <> '' then
        ShowMode(3, 'Перевод удалён', Note, '', 'Закрыть', '')
      else
        ShowMode(3, 'Перевод удалён', 'Файлы игры возвращены в состояние до установки перевода.', '', 'Закрыть', '');
      UiRemoved(Note);
    end
    else
    begin
      Busy := False;
      LastFailure := FailureText;
      LastCode := ToolValue('code');
      ShowMode(1, 'Не удалось удалить перевод', LastFailure, '', 'Закрыть', '');
      UiRemoveFailed;
    end;
  end
  else
    WizardForm.CancelButton.OnClick(WizardForm.CancelButton);
end;

procedure PressClose(Sender: TObject);
begin
  if Busy then Exit;
  if WizardForm.CurPageID = wpFinished then
    WizardForm.NextButton.OnClick(WizardForm.NextButton)
  else if WizardForm.CancelButton.Enabled then
    WizardForm.CancelButton.OnClick(WizardForm.CancelButton);
end;

// Вторичная кнопка: на приветствии «Удалить перевод», на остальных страницах «Закрыть».
procedure SecondaryRouter(Sender: TObject);
begin
  if Busy then Exit;
  if (WizardForm.CurPageID = wpFinished) or (Mode = 1) or (Mode = 2) or (Mode = 3) then PressClose(Sender)
  else PressRemove(Sender);
end;

procedure ParkNativeControls;
var
  Off: Integer;
begin
  // Родные кнопки остаются живыми (Enter, Esc, проверки Inno), но вне видимой области.
  Off := -ScaleX(400);
  WizardForm.BackButton.Parent := WizardForm;
  WizardForm.NextButton.Parent := WizardForm;
  WizardForm.CancelButton.Parent := WizardForm;
  WizardForm.BackButton.SetBounds(Off, ScaleY(8), ScaleX(80), ScaleY(26));
  WizardForm.NextButton.SetBounds(Off, ScaleY(40), ScaleX(80), ScaleY(26));
  WizardForm.CancelButton.SetBounds(Off, ScaleY(72), ScaleX(80), ScaleY(26));
  WizardForm.NextButton.Default := True;
  WizardForm.CancelButton.Cancel := True;
end;

procedure HidePageContent;
begin
  WizardForm.OuterNotebook.Visible := False;
  WizardForm.InnerNotebook.Visible := False;
  WizardForm.PreparingLabel.Visible := False;
  WizardForm.PreparingMemo.Visible := False;
  WizardForm.PreparingYesRadio.Visible := False;
  WizardForm.PreparingNoRadio.Visible := False;
  WizardForm.RunList.Visible := False;
  WizardForm.YesRadio.Visible := False;
  WizardForm.NoRadio.Visible := False;
  FailureLabel.Visible := False;
end;

procedure ShowMode(NewMode: Integer; State, Detail, Info, Primary, Secondary: String);
begin
  Mode := NewMode;
  StateLabel.Caption := State;
  DetailLabel.Caption := Detail;
  FailureLabel.Visible := False;
  if NewMode = 1 then StateLabel.Font.Color := DangerColor
  else StateLabel.Font.Color := TextColor;
  // Справка (Info) и список шагов делят одно место: справка на приветствии и в конце, шаги во время установки.
  ShowSteps(False);
  InfoLabel.Visible := False;
  if NewMode = 1 then
  begin
    FailureLabel.Caption := Detail;
    FailureLabel.Visible := True;
    DetailLabel.Caption := FailureLead;
  end
  else if Info <> '' then
  begin
    InfoLabel.Caption := Info;
    InfoLabel.Visible := True;
  end
  else if NewMode <> 3 then
    ShowSteps(True);
  ShowToggle(LauncherAvailable and (NewMode = 0) and (Primary <> '') and (WizardForm.CurPageID = wpWelcome) and not IsInterrupted);
  PrimaryInner.Caption := Primary;
  PrimaryButton.Visible := Primary <> '';
  SecondaryInner.Caption := Secondary;
  SecondaryButton.Visible := Secondary <> '';
  PaintButton(PrimaryInner, False);
  PaintButton(SecondaryInner, False);
  if NewMode = 1 then IndexLabel.Caption := '02/03'
  else if NewMode = 2 then IndexLabel.Caption := '03/03'
  else if NewMode = 3 then IndexLabel.Caption := '03/03'
  else IndexLabel.Caption := '01/03';
end;

// ---- режим UI: команды окна, таймер, экраны страниц мастера ----

procedure CurPageChanged(CurPageID: Integer); forward;

// Экран окна по странице мастера. Страницы Inno остаются двигателем (проверки, копирование, реестр); окно только показывает их итог.
procedure UiOnPage(PageID: Integer);
begin
  if not UiActive then Exit;
  SvOp := 'install';
  if PageID = wpWelcome then
  begin
    SvInterrupted := IsInterrupted;
    SvCanRemove := IsPending or IsInstalled or IsInterrupted;
    if IsInterrupted or IsInstalled then SvScreen := 'installed' else SvScreen := 'welcome';
    SvState := 'idle';
    SvStep := 0;
    SvStepState := 'run';
    SvDetail := '';
    SvFile := '';
    SvEta := '';
    SvDone := 0;
    SvTotal := 0;
    SvNote := '';
  end
  else if PageID = wpInstalling then
  begin
    SvScreen := 'progress';
    SvState := 'running';
    SvStep := 6;
    SvStepState := 'run';
    SvDetail := 'Добавляю запись в «Установку и удаление программ»';
    SvFile := '';
    SvEta := '';
    ShownPermille := 1000;
  end
  else if PageID = wpFinished then
  begin
    SvScreen := 'done';
    SvState := 'finished';
    SvStep := 6;
    SvNote := InstallResultNote;
    if LauncherAvailable and LauncherOn and not LauncherWanted then
    begin
      if SvNote <> '' then SvNote := SvNote + ' ';
      SvNote := SvNote + 'Лаунчер не переведён: ' + LauncherOffReason + '.';
    end;
    ShownPermille := 1000;
  end
  else
    Exit;
  PublishState;
end;

// ---- команды окна ----

// Строка должна совпасть ровно с одним из двух видов: {"seq":N,"cmd":"X"} или {"seq":N,"cmd":"X","options":{"launcher":B}}.
function UiParseCmd(Line: String; var Seq: Integer; var Cmd: String; var HasOpt, Launcher: Boolean): Boolean;
var
  P, Q: Integer;
  Num, Rest: String;
begin
  Result := False;
  HasOpt := False;
  Launcher := False;
  Seq := 0;
  Cmd := '';
  if (Length(Line) < 20) or (Length(Line) > 120) then Exit;
  if Copy(Line, 1, 7) <> '{"seq":' then Exit;
  Rest := Copy(Line, 8, Length(Line));
  P := Pos(',"cmd":"', Rest);
  if P < 2 then Exit;
  Num := Copy(Rest, 1, P - 1);
  if Length(Num) > 9 then Exit;
  Seq := StrToIntDef(Num, -1);
  if Seq < 1 then Exit;
  if IntToStr(Seq) <> Num then Exit;   // без плюса, нулей впереди, пробелов
  Rest := Copy(Rest, P + 8, Length(Rest));
  Q := Pos('"', Rest);
  if Q < 2 then Exit;
  Cmd := Copy(Rest, 1, Q - 1);
  Rest := Copy(Rest, Q + 1, Length(Rest));
  if Rest = '}' then
    Result := True
  else if Rest = ',"options":{"launcher":true}}' then
  begin
    HasOpt := True;
    Launcher := True;
    Result := True;
  end
  else if Rest = ',"options":{"launcher":false}}' then
  begin
    HasOpt := True;
    Result := True;
  end;
end;

function UiKnownCmd(Cmd: String): Boolean;
begin
  Result := (Cmd = 'install') or (Cmd = 'options') or (Cmd = 'remove') or (Cmd = 'launch') or (Cmd = 'back') or (Cmd = 'pickgame') or (Cmd = 'close') or (Cmd = 'checkupdate') or (Cmd = 'update');
end;

// «Выбрать папку игры» (экран сбоя при game_not_found / game_ambiguous / game_missing). Папку выбирает системный диалог в самом Setup:
// окно путей не передаёт. Выбранная папка проверяется помощником (find --game: WXQ.exe, WXQ_Data, Package); принятая остаётся в GameParam
// на повторную попытку, отвергнутая не запоминается, а причина показывается на экране сбоя.
procedure UiPickGame;
var
  Dir, OldParam: String;
begin
  Dir := '';
  if not BrowseForFolder('Выберите папку игры: внутри лаунчера это папка WXQ(1), где лежит WXQ.exe', Dir, False) then Exit;
  Dir := RemoveBackslashUnlessRoot(Dir);
  if (Dir = '') or (Pos('"', Dir) > 0) then Exit;
  OldParam := GameParam;
  GameParam := ' --game "' + Dir + '"';
  Log('UI: папка игры выбрана вручную, проверяю');
  if RunTool('find', '') then
  begin
    Log('UI: папка игры принята: ' + Dir);
    if WizardForm.CurPageID = wpPreparing then WizardForm.BackButton.OnClick(WizardForm.BackButton);
  end
  else
  begin
    GameParam := OldParam;
    LastCode := ToolValue('code');
    LastFailure := FailureText;
    Log('UI: выбранная папка отвергнута: ' + LastCode);
    UiInstallFailed(LastFailure);
  end;
end;

function UiOnWelcome: Boolean;
begin
  Result := (WizardForm.CurPageID = wpWelcome) and ((SvScreen = 'welcome') or (SvScreen = 'installed'));
end;

procedure UiExec(Cmd: String; HasOpt, Launcher: Boolean); forward;

procedure SuStart(Action: String);
var
  Params, Extra: String;
  Code: Integer;
begin
#ifdef WithUpdater
  if SuWaiting then
  begin
    // Плашка available может появиться раньше финального --out проверки.
    // Сохраняем клик до завершения check, а не теряем его молча.
    if (Action = 'download') and (SuAction = 'check') then SuDownloadQueued := True;
    Exit;
  end;
  if Busy or UiClosing or WizardSilent then Exit;
  if not SecVerify('перед проверкой обновления') then Exit;
  SuAction := Action;
  SuOut := SecDir + '\setup-update-out.txt';
  DeleteFile(SuOut);
  DeleteFile(SecDir + '\setup-update.cancel');
  DeleteFile(GetDataDir('') + '\setup-update-status.txt');
  SuDone := 0;
  SuTotal := 0;
  SuLast := '';
  if Action = 'download' then SuState := 'downloading'
  else if Action = 'handoff' then SuState := 'handoff'
  else SuState := 'checking';
  SuMessage := 'Проверяю новую версию…';
  if Action = 'download' then SuMessage := 'Подготавливаю скачивание…';
  if Action = 'handoff' then SuMessage := 'Открываю новую версию установщика…';
  Extra := '';
#ifdef TestBuild
  if ExpandConstant('{param:UPDATEBASE|}') <> '' then Extra := ' --test-base-url "' + ExpandConstant('{param:UPDATEBASE|}') + '"';
#endif
  Params := 'setup-update --mode ' + Action + ' --current-version {#AppVersion} --data "' + GetDataDir('') +
    '" --out "' + SuOut + '" --parent ' + IntToStr(GetCurrentProcessId) + ' --cancel "' + SecDir + '\setup-update.cancel"' + GameParam + Extra;
  if LauncherOn then Params := Params + ' --launcher 1' else Params := Params + ' --launcher 0';
  SuWaiting := Exec(ToolPath, Params, '', SW_HIDE, ewNoWait, Code);
  SuStarted := GetTickCount;
  if not SuWaiting then
  begin
    SuState := 'failed';
    SuMessage := 'Не удалось запустить проверку. Попробуйте ещё раз.';
  end;
  PublishState;
#endif
end;

procedure SuApplyStatus(Lines: TArrayOfString);
var
  State_: String;
begin
  State_ := LineValue(Lines, 'state');
  if (State_ = 'checking') and (SuAction = 'download') then State_ := 'downloading';
  if (State_ <> 'checking') and (State_ <> 'current') and (State_ <> 'available') and (State_ <> 'downloading') and
     (State_ <> 'ready') and (State_ <> 'handoff') and (State_ <> 'failed') then Exit;
  SuState := State_;
  SuVersion := LineValue(Lines, 'available');
  SuMessage := LineValue(Lines, 'message');
  SuDone := StrToInt64Def(LineValue(Lines, 'bytes_done'), 0);
  SuTotal := StrToInt64Def(LineValue(Lines, 'bytes_total'), 0);
  PublishState;
end;

procedure SuPoll;
var
  Lines, ResultLines: TArrayOfString;
  Current, Finished: String;
begin
  if not SuWaiting then Exit;
  SetArrayLength(Lines, 0);
  if FileExists(GetDataDir('') + '\setup-update-status.txt') then LoadStringsFromFile(GetDataDir('') + '\setup-update-status.txt', Lines);
  Current := LineValue(Lines, 'state') + '|' + LineValue(Lines, 'message') + '|' + LineValue(Lines, 'bytes_done');
  if (GetArrayLength(Lines) > 0) and (Current <> SuLast) then
  begin
    SuLast := Current;
    SuApplyStatus(Lines);
  end;
  SetArrayLength(ResultLines, 0);
  if FileExists(SuOut) then LoadStringsFromFile(SuOut, ResultLines);
  Finished := LineValue(ResultLines, 'status');
  if Finished <> '' then
  begin
    SuWaiting := False;
    // status.txt может быть занят читателем в момент атомарной замены. Полный
    // финальный результат содержит тот же снимок и не оставляет плашку «Проверяю».
    if Finished = 'ok' then SuApplyStatus(ResultLines);
    if Finished <> 'ok' then
    begin
      SuState := 'failed';
      SuMessage := LineValue(ResultLines, 'message');
      if SuMessage = '' then SuMessage := 'Проверка не завершилась. Попробуйте ещё раз.';
      PublishState;
    end
    else if (SuAction = 'download') and (SuState = 'ready') then SuStart('handoff')
    else if (SuAction = 'handoff') and (SuState = 'handoff') then UiExec('close', False, False);
    if SuDownloadQueued and not SuWaiting then
    begin
      SuDownloadQueued := False;
      if (SuState = 'available') or (SuState = 'ready') then SuStart('download');
    end;
  end
  else if (SuAction = 'check') and (GetTickCount - SuStarted > 12000) then
  begin
    SaveStringToFile(SecDir + '\setup-update.cancel', 'cancel', False);
    SuWaiting := False;
    SuState := 'failed';
    SuMessage := 'Проверка пока не завершилась. Установка доступна; проверку можно повторить.';
    PublishState;
  end;
end;

// Каждая команда проверяется по текущему экрану: «удалить» из окна приветствия, «запустить» только на «Готово». Прочее молча игнорируется.
procedure UiExec(Cmd: String; HasOpt, Launcher: Boolean);
var
  Page: Integer;
begin
  if SvState = 'running' then Exit;   // во время записи команды окна не исполняются
  if Busy or UiClosing then Exit;
  if SuWaiting and (SuAction <> 'check') and (Cmd <> 'close') then Exit;
  Page := WizardForm.CurPageID;
  Log('UI: команда ' + Cmd);
  if (Cmd = 'checkupdate') or (Cmd = 'update') then
  begin
    if HasOpt then Exit;
    if Cmd = 'checkupdate' then SuStart('check')
    else if (SuState = 'available') or (SuState = 'ready') then SuStart('download');
  end
  else if Cmd = 'options' then
  begin
    if HasOpt and LauncherAvailable and UiOnWelcome then LauncherOn := Launcher;
  end
  else if Cmd = 'install' then
  begin
    if UiOnWelcome and (not IsInterrupted) then
    begin
      if HasOpt and LauncherAvailable then LauncherOn := Launcher;
      if WizardForm.NextButton.Enabled then WizardForm.NextButton.OnClick(WizardForm.NextButton);
    end;
  end
  else if Cmd = 'remove' then
  begin
    if UiOnWelcome and (Mode = 0) and (IsInstalled or IsPending or IsInterrupted) then PressRemove(nil);
  end
  else if Cmd = 'back' then
  begin
    if (SvScreen = 'removed') and (Page = wpWelcome) then CurPageChanged(wpWelcome)
    else if SvScreen = 'fail' then
    begin
      if Page = wpPreparing then WizardForm.BackButton.OnClick(WizardForm.BackButton)
      else if Page = wpWelcome then CurPageChanged(wpWelcome);   // сбой удаления: снова приветствие по свежему состоянию
    end;
  end
  else if Cmd = 'pickgame' then
  begin
    if (SvScreen = 'fail') and (SvFailKind = 'install') and
       ((SvFailCode = 'game_not_found') or (SvFailCode = 'game_ambiguous') or (SvFailCode = 'game_missing')) then UiPickGame;
  end
  else if Cmd = 'launch' then
  begin
    if (SvScreen = 'done') and (Page = wpFinished) and (SvLaunchState <> 'starting') then
    begin
      SvLaunchState := 'starting';
      SvLaunchMessage := '';
      PublishState;
      if LaunchGame then
      begin
        if not UiClosing then
        begin
          UiClosing := True;
          UiCloseAt := GetTickCount;
          WizardForm.NextButton.OnClick(WizardForm.NextButton);
        end;
      end
      else
      begin
        SvLaunchState := 'failed';
        PublishState;
      end;
    end
    else if SvLaunchState <> 'starting' then
    begin
      SvLaunchState := 'failed';
      SvLaunchMessage := 'Окно ещё не готово к запуску. Повторите действие.';
      PublishState;
    end;
  end
  else if Cmd = 'close' then
  begin
    UiClosing := True;
    UiCloseAt := GetTickCount;
    if Page = wpFinished then WizardForm.NextButton.OnClick(WizardForm.NextButton)
    else
    begin
      ShowWindow(WizardForm.Handle, 4);   // SW_SHOWNOACTIVATE: окно прозрачно (alpha 0), глазу ничего не видно
      PostMessageW(WizardForm.CancelButton.Handle, $00F5, 0, 0);   // BM_CLICK через очередь сообщений, не вложенный вызов из таймера
    end;
    // UiClosing остаётся True: мастер должен выйти сам; если не вышел, UiTick повторит закрытие.
  end;
end;

// Полные строки файла команд, которых ещё не было. Недописанная последняя строка (без перевода строки) ждёт следующего опроса.
procedure UiReadCommands;
var
  Raw: AnsiString;
  Line: String;
  Last, Start, E, Seq: Integer;
  Cmd: String;
  HasOpt, L: Boolean;
begin
  if not FileExists(UiCmdPath) then Exit;
  if not LoadStringFromFile(UiCmdPath, Raw) then Exit;
  if Length(Raw) > 65536 then Exit;   // настоящий файл — десятки строк по 60 байт
  if Length(Raw) < UiCmdSize then UiCmdSize := 0;
  Last := Length(Raw);
  while (Last > 0) and (Ord(Raw[Last]) <> 10) do Dec(Last);
  Start := UiCmdSize + 1;
  while Start <= Last do
  begin
    E := Start;
    while Ord(Raw[E]) <> 10 do Inc(E);
    Line := Copy(Raw, Start, E - Start);
    if (Length(Line) > 0) and (Ord(Line[Length(Line)]) = 13) then Line := Copy(Line, 1, Length(Line) - 1);
    Start := E + 1;
    UiCmdSize := E;
    if UiParseCmd(Line, Seq, Cmd, HasOpt, L) and (Seq > UiLastSeq) and UiKnownCmd(Cmd) then
    begin
      UiLastSeq := Seq;
      UiExec(Cmd, HasOpt, L);
    end
    else
      Log('UI: строка команды отвергнута (вид, номер или команда вне списка)');
    if UiGone or (not UiActive) then Exit;
  end;
end;

procedure UiTick(H, Msg, Id, Tm: LongWord);
begin
  if InTick then Exit;
  InTick := True;
  try
    SuPoll;
    if AutoContinue and WizardReady and (GetTickCount - WelcomeReadyAt > 300) and (not Busy) and (not UiClosing) and UiOnWelcome then
    begin
      AutoContinue := False;
      UiExec('install', False, False);
    end;
    if UiActive then
    begin
      KeepHidden;
      if UiPubFailed then PublishState;
      if UiClosing and (GetTickCount - UiCloseAt > 1500) then
      begin
        UiCloseAt := GetTickCount;
        Log('UI: закрытие не сработало, повтор');
        PostMessageW(WizardForm.CancelButton.Handle, $00F5, 0, 0);
      end;
      if not UiAliveNow then
      begin
        if not UiClosing then UiLost;
      end
      // Окно уже показывает welcome/installed во время InitializeWizard,
      // но Inno ещё не открыл wpWelcome. Команды остаются в файле до готовности:
      // ранний Next иначе только открывает приветствие и теряет установку.
      else if WizardReady and (not InStatus) then
        UiReadCommands;
    end;
  finally
    InTick := False;
  end;
end;

procedure CreateBrandShell;
var
  I, Round, Dark, BorderColor: Integer;
  HeaderText: TNewStaticText;
begin
  WizardForm.BorderStyle := bsNone;
  WizardForm.Caption := '{#AppTitle}{#DiagSuffix}';
  WizardForm.ClientWidth := ScaleX(WindowWidth);
  WizardForm.ClientHeight := ScaleY(WindowHeight);
  WizardForm.Color := LineColor;
  WizardForm.Font.Name := 'Segoe UI';
  WizardForm.Font.Size := 9;
  // Системная рамка окна: тёмная тема, скруглённые углы Windows 11 и контур в цвет волосяных линий (на старых Windows вызовы ничего не делают).
  Round := 2;
  DwmSetWindowAttribute(WizardForm.Handle, 33, Round, 4);
  Dark := 1;
  DwmSetWindowAttribute(WizardForm.Handle, 20, Dark, 4);
  BorderColor := LineColor;
  DwmSetWindowAttribute(WizardForm.Handle, 34, BorderColor, 4);
  WizardForm.Bevel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.BeveledLabel.Visible := False;

  // Поверхность на один физический пиксель меньше окна: оставшаяся кайма — волосяная рамка.
  Surface := TPanel.Create(WizardForm);
  Surface.Parent := WizardForm;
  Surface.SetBounds(1, 1, WizardForm.ClientWidth - 2, WizardForm.ClientHeight - 2);
  Surface.Color := BackgroundColor;
  Surface.ParentBackground := False;
  Surface.BevelOuter := bvNone;

  UiPct := PickUiScale;
  Log('Растры окна: вариант ' + IntToStr(UiPct) + '% (масштаб Windows ' + IntToStr(ScaleX(100)) + '%)');
  DotWait := LoadAsset('dot-wait');
  DotAct := LoadAsset('dot-act');
  DotDone := LoadAsset('dot-done');
  DotFail := LoadAsset('dot-fail');
  CheckOn := LoadAsset('check-on');
  CheckOff := LoadAsset('check-off');

  // Шапка: арт игры справа (один растр в натуральных пикселях, растворяется слева в графит), знак Egoist слева, маленькая подпись и название
  // обычным текстом поверх (в картинках текста нет), волосяная линия снизу. Арт кончается у правого края поверхности.
  HeaderImage := NewImage(0, 0, LoadAsset('header'));
  HeaderImage.Left := Surface.Width - HeaderImage.Width;
  MarkImage := NewImage(Margin, (HeaderHeight - 56) div 2, LoadAsset('mark'));
  // Надпись — окно с непрозрачным фоном: ширина подписей шапки не заходит на арт (он начинается правее, см. ART_FROM_DP в build_installer.py), без переноса.
  HeaderText := NewLabel(Margin + 72, 26, 220, 16, 9, Tracked('EGOIST · РУСИФИКАТОР'), HeaderMutedColor, True);
  HeaderText.WordWrap := False;
  HeaderText := NewLabel(Margin + 72, 42, 264, 36, 20, 'Honor of Kings ACE', TextColor, True);
  HeaderText.WordWrap := False;
  CloseLabel := NewLabel(WindowWidth - 2 - Margin - 16, 30, 24, 28, 14, '×', TextColor, False);
  CloseLabel.Cursor := crHand;
  CloseLabel.OnClick := @PressClose;
  NewLine(0, HeaderHeight, WindowWidth - 2, 1, LineColor);

  StateLabel := NewLabel(Margin, StateTop, ContentWidth, 32, 18, '', TextColor, True);
  DetailLabel := NewLabel(Margin, DetailTop, ContentWidth, 48, 10, '', MutedColor, False);
  InfoLabel := NewLabel(Margin, ContentTop, ContentWidth, InfoHeight, 10, '', MutedColor, False);
  InfoLabel.Visible := False;
  if LauncherAvailable then
  begin
    ToggleLine := NewLine(Margin, ToggleLineTop, ContentWidth, 1, LineColor);
    // Флажок — растр по масштабу Windows; клик по значку, подписи и пояснению переключает (родных чекбоксов нет).
    ToggleImage := NewImage(Margin, ToggleTop, CheckOn);
    ToggleImage.Cursor := crHand;
    ToggleImage.OnClick := @PressToggle;
    ToggleTitle := NewLabel(Margin + 32, ToggleTop, ContentWidth - 32, 24, 10, 'Русский лаунчер', TextColor, True);
    ToggleTitle.Cursor := crHand;
    ToggleTitle.OnClick := @PressToggle;
    ToggleNote := NewLabel(Margin + 32, ToggleNoteTop, ContentWidth - 32, 64, 9,
      'Ярлык «Honor of Kings ACE (RU)» запускает оригинальный лаунчер и подключает перевод через временный отладочный порт; ' +
      'нужны права администратора. Включено по умолчанию; функция новая, с защитой ACE в игре не проверена. ' +
      'При сбое откроется лаунчер на китайском. Не нужен - снимите флажок.', MutedColor, False);
    ToggleNote.Cursor := crHand;
    ToggleNote.OnClick := @PressToggle;
    ShowToggle(False);
    ApplyToggle;
  end;
  FailureLabel := NewLabel(Margin, ContentTop, ContentWidth, InfoHeight, 10, '', TextColor, False);
  FailureLabel.Visible := False;

  for I := 1 to StepCount do
  begin
    StepDot[I] := NewImage(Margin - 2, StepTop + (I - 1) * StepHeight + 8, DotWait);
    StepName[I] := NewLabel(Margin + 24, StepTop + (I - 1) * StepHeight + 6, 200, 22, 10, StepTitle(I), MutedColor, False);
    StepDetail[I] := NewLabel(Margin + 240, StepTop + (I - 1) * StepHeight + 8, ContentWidth - 240, 20, 9, '', MutedColor, False);
  end;

  PercentLabel := NewLabel(Margin, StepTop + StepCount * StepHeight + 16, 132, 52, 28, '0%', TextColor, True);
  EtaLabel := NewLabel(Margin + 132, StepTop + StepCount * StepHeight + 44, ContentWidth - 132, 20, 9, '', MutedColor, False);
  ProgressTrack := TPanel.Create(WizardForm);
  ProgressTrack.Parent := Surface;
  ProgressTrack.Color := TrackColor;
  ProgressTrack.ParentBackground := False;
  ProgressTrack.BevelOuter := bvNone;
  SetBounds(ProgressTrack, Margin, StepTop + StepCount * StepHeight + 76, ContentWidth, 6);
  ProgressFill := TPanel.Create(WizardForm);
  ProgressFill.Parent := ProgressTrack;
  ProgressFill.Color := GoldColor;
  ProgressFill.ParentBackground := False;
  ProgressFill.BevelOuter := bvNone;
  ProgressFill.SetBounds(0, 0, 0, ProgressTrack.Height);
  ShowSteps(False);

  PrimaryButton := NewButton(Margin, ButtonTop, 200, ButtonHeight, True, @ClickPrimary, PrimaryInner);
  SecondaryButton := NewButton(Margin + 216, ButtonTop, 176, ButtonHeight, False, @ClickSecondary, SecondaryInner);

  // Подвал: реальные продукт, версия и дата сборки; индекс — номер страницы мастера.
  NewLine(Margin, FooterTop - 12, ContentWidth, 1, LineColor);
  FooterLabel := NewLabel(Margin, FooterTop, 400, 18, 9, 'EGOIST  ·  WXQ-RU  ·  {#AppVersion}  ·  {#BuildDate}{#DiagFooter}', FaintColor, False);
  IndexLabel := NewLabel(Margin + ContentWidth - 40, FooterTop, 40, 18, 9, '01/03', FaintColor, False);

  ParkNativeControls;
end;

// Запуск окна WXQSetupUI.exe. Любая неудача (нет файла, не стартовало за 10 секунд, нет /NOUI-разрешения) оставляет обычные страницы Inno:
// окно мастера в этом случае даже не скрывалось. Тихий режим окна не получает.
procedure UiStart;
var
  Code, I: Integer;
  Exe, Dir, Expected, Source: String;
  Started: Boolean;
begin
  WizardReady := False;
  UiActive := False;
  UiGone := False;
  UiClosing := False;
  InTick := False;
  UiTimer := 0;
  UiPubFailed := False;
  UiCmdSize := 0;
  UiLastSeq := 0;
  SvRev := 0;
  SvFailedBefore := True;
  SvFailKind := 'install';
  if WizardSilent then Exit;
  if ExpandConstant('{param:NOUI|0}') = '1' then
  begin
    Log('Окно WXQSetupUI отключено параметром /NOUI=1: стандартные страницы установщика');
    Exit;
  end;
#ifdef WithUI
  // Окно запускается только из защищённой папки: копия из {tmp} и сверка SHA-256 копии. Любая неудача - стандартные страницы Inno
  // (помощник при этом работает, он уже в защищённой папке).
  Exe := SecDir + '\WXQSetupUI.exe';
  Dir := SecDir;
  Expected := '{#UiSha256}';
  Source := ExpandConstant('{tmp}\WXQSetupUI.exe');
#ifdef TestBuild
  if ExpandConstant('{param:UIEXE|}') <> '' then
  begin
    Source := ExpandConstant('{param:UIEXE|}');
    Expected := ExpandConstant('{param:UIEXESHA|}');   // у подставного окна своей эталонной суммы нет: без /UIEXESHA= сверки нет
  end;
#endif
  try
#ifdef TestBuild
    if ExpandConstant('{param:UIEXE|}') = '' then
#endif
      ExtractTemporaryFile('WXQSetupUI.exe');
    if not FileExists(Source) then
    begin
      Log('WXQSetupUI.exe не найден: стандартные страницы установщика');
      UiActive := False;
      UiRestoreWizard;
      Exit;
    end;
    if not SecCopyFile(Source, 'WXQSetupUI.exe', Expected) then
    begin
      Log('WXQSetupUI.exe не прошёл проверку в защищённой папке: окно не запускается, стандартные страницы установщика');
      UiActive := False;
      UiRestoreWizard;
      Exit;
    end;
    if not SecVerify('перед запуском окна') then
    begin
      Log('Защищённая папка не прошла проверку: окно не запускается');
      UiActive := False;
      UiRestoreWizard;
      Exit;
    end;
    UiDir := Dir;
    UiStatePath := Dir + '\state.json';
    UiCmdPath := Dir + '\commands.jsonl';
    DeleteFile(UiStatePath);
    DeleteFile(UiCmdPath);
    UiToken := NewUiToken;
    // Первое состояние готово до появления окна: оно сразу открывается на нужном экране (установка, «уже установлено», прерванная).
    UiActive := True;
    RefreshStatus;
    UiOnPage(wpWelcome);
    HideAppWindows;
    Started := Exec(Exe, '--dir "' + Dir + '" --token ' + UiToken + ' --parent ' + IntToStr(GetCurrentProcessId), '', SW_SHOWNORMAL, ewNoWait, Code);
    if Started then
    begin
      Started := False;
      for I := 1 to 200 do
      begin
        if UiAliveNow then
        begin
          Started := True;
          Break;
        end;
        HideAppWindows;
        Sleep(50);
      end;
    end;
    if not Started then
    begin
      Log('WXQSetupUI не запустился за 10 секунд: стандартные страницы установщика');
      UiQuit;    // если окно всё же появится позже, оно закроется
      UiActive := False;
      UiRestoreWizard;
      Exit;
    end;
    UiHideWizard;
    UiTimer := SetTimer(0, 0, 50, CreateCallback(@UiTick));
    Log('Окно WXQSetupUI.exe запущено, страницы Inno скрыты');
  except
    Log('Окно WXQSetupUI.exe: ' + GetExceptionMessage + '; стандартные страницы установщика');
    UiActive := False;
    UiRestoreWizard;
  end;
#endif
end;

procedure DeinitializeSetup;
var
  I: Integer;
begin
  if SecDir <> '' then SaveStringToFile(SecDir + '\setup-update.cancel', 'cancel', False);
  if UiTimer <> 0 then KillTimer(0, UiTimer);
  UiTimer := 0;
  if UiActive then
  begin
    UiQuit;
    // Окно видит quit за ~100 мс и закрывается; до этого папку не удаляем: окно читает из неё state.json и держит свой exe (до ~3 с).
    for I := 1 to 60 do
    begin
      if not UiAliveNow then Break;
      Sleep(50);
    end;
  end;
  SecRemove;   // не удалилась - уберёт следующий запуск
  if (SelfSourceHandle <> 0) and (SelfSourceHandle <> $FFFFFFFF) then CloseHandle(SelfSourceHandle);
  SelfSourceHandle := 0;
end;

// Самое раннее место для скрытия служебного окна приложения (кнопки на панели задач): окно UI должно быть единственным видимым.
// Если окно UI потом не стартует, UiRestoreWizard вернёт служебное окно на место.
function InitializeSetup: Boolean;
begin
  Result := True;
  // Копия единого EXE в Program Files не должна браться по пути, который могли заменить после запуска.
  SelfSourceHandle := CreateFileW(ExpandConstant('{srcexe}'), $80000000, 1, 0, 3, $80, 0);
  if SelfSourceHandle = $FFFFFFFF then
  begin
    Result := False;
    Exit;
  end;
  SelfSourceSha := GetSHA256OfFile(ExpandConstant('{srcexe}'));
#ifdef WithUI
  if (not WizardSilent) and (ExpandConstant('{param:NOUI|0}') <> '1') then HideAppWindows;
#endif
  // Без защищённой папки помощника запустить безопасно нельзя ни с окном, ни со стандартными страницами: установка отказывается.
  if not SecPrepare then
  begin
    Log('Установщик отказывается работать: защищённая папка не подготовлена');
    if not WizardSilent then
      MsgBox('Не удалось подготовить защищённую папку установщика в ' + ExpandConstant('{commonappdata}') + '.' + #13#10 +
        'Запустите установщик от имени администратора ещё раз. Если не поможет, проверьте антивирус и папку ProgramData. В игру ничего не записано.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure InitializeWizard;
begin
  Mode := 0;
  GameParam := '';
  UpdateMode := ExpandConstant('{param:UPDATE|0}') = '1';
  SelfUpdateContinue := UpdateMode and (ExpandConstant('{param:CONTINUE|0}') = '1');
  AutoContinue := SelfUpdateContinue;
  SuDownloadQueued := False;
  ManagerReady := False;
  UpdateRestored := False;
  LauncherOn := LauncherAvailable and (ExpandConstant('{param:LAUNCHERRU|1}') <> '0');   // по умолчанию включён; /LAUNCHERRU=0 выключает
  LauncherExe := '';
  LauncherBuildText := '';
  if ExpandConstant('{param:GAMEDIR|}') <> '' then
    GameParam := ' --game "' + RemoveBackslashUnlessRoot(ExpandConstant('{param:GAMEDIR|}')) + '"';
  CreateBrandShell;
  IsInstalled := False;
  IsPending := False;
  IsInterrupted := False;
  LastCode := '';
  UiStart;
  if UiTimer = 0 then UiTimer := SetTimer(0, 0, 50, CreateCallback(@UiTick));
  SuStart('check');
end;

// Закрытие без вопроса «Прервать установку?»: никаких системных окон.
procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  Confirm := False;
  if Busy then Cancel := False;
end;

// Данные перевода: извлечение в {tmp}, затем копия в защищённую папку с проверкой SHA-256 каждой копии (помощник читает только её).
// Место: извлечение в {tmp} и копия в защищённую папку (оба обычно на диске C:), запас 10 %. Не хватает - отказ с кодом disk_space до извлечения.
function PayloadSpaceOk: Boolean;
var
  Need, FreeTmp, FreeSec, Total: Int64;
  SameDrive: Boolean;
begin
  Result := True;
  Need := StrToInt64Def('{#PayloadBytes}', 0);
  if Need <= 0 then Exit;
  Need := Need + Need div 10;
  FreeTmp := -1;
  FreeSec := -1;
  if not GetSpaceOnDisk64(ExpandConstant('{tmp}'), FreeTmp, Total) then FreeTmp := -1;
  if not GetSpaceOnDisk64(SecDir, FreeSec, Total) then FreeSec := -1;
#ifdef TestBuild
  if StrToInt64Def(ExpandConstant('{param:FREEBYTES|}'), -1) >= 0 then
  begin
    FreeTmp := StrToInt64Def(ExpandConstant('{param:FREEBYTES|}'), -1);
    FreeSec := FreeTmp;
  end;
#endif
  SameDrive := CompareText(ExtractFileDrive(ExpandConstant('{tmp}')), ExtractFileDrive(SecDir)) = 0;
  if SameDrive and (FreeTmp >= 0) and (FreeTmp < Need * 2) then
    Result := False
  else if (not SameDrive) and (((FreeTmp >= 0) and (FreeTmp < Need)) or ((FreeSec >= 0) and (FreeSec < Need))) then
    Result := False;
  if not Result then
  begin
    PayloadFailCode := 'disk_space';
    PayloadFailText := 'Не хватает места на диске для данных перевода: нужно около ' + IntToStr((Need + 1048575) div 1048576) +
      ' МБ во временной папке и столько же в ' + ExtractFileDrive(SecDir) + '\ProgramData. Освободите место и запустите установку ещё раз. В игру ничего не записано.';
    Log('Места для данных перевода не хватает: нужно ' + IntToStr(Need) + ' байт, свободно в {tmp} ' + IntToStr(FreeTmp) + ', в защищённой папке ' + IntToStr(FreeSec));
  end;
end;

function ExtractPayload: Boolean;
begin
  Result := False;
  PayloadFailCode := 'payload';
  PayloadFailText := 'Не удалось подготовить данные перевода в защищённой папке установщика. Чаще всего не хватает места на диске (временная папка и ProgramData) ' +
    'или мешает антивирус. Освободите место, проверьте антивирус и запустите установку ещё раз. В игру ничего не записано.';
  if not PayloadSpaceOk then Exit;
  try
#include Staging + "\payload-extract.iss"
#include Staging + "\payload-copy.iss"
    Result := True;
  except
    // ExtractTemporaryFile сообщает о полном диске и других сбоях исключением: оно не должно оборвать мастер без объяснения.
    Log('Данные перевода не подготовлены: ' + GetExceptionMessage);
    PayloadFailText := 'Не удалось подготовить данные перевода (' + GetExceptionMessage + '). Чаще всего не хватает места на диске (временная папка и ProgramData) ' +
      'или мешает антивирус. Освободите место, проверьте антивирус и запустите установку ещё раз. В игру ничего не записано.';
    Result := False;
  end;
end;

// Шаг не прошёл: помечаем его, запоминаем текст и код отказа помощника (по коду окно решает, что сказать о целости игры).
function FailStep(Step: Integer): String;
begin
  SetStep(Step, StFailed, '');
  LastFailure := FailureText;
  LastCode := ToolValue('code');
  Result := LastFailure;
end;

// Всё, что пишет в игру, идёт здесь: в обычном и в тихом режиме. Пустая строка — успех.
function DoInstall: String;
var
  State, Label_, ActualGameVersion: String;
begin
  Result := '';
  LastFailure := '';
  LastCode := '';
  AppliedResources := 0;
  SkippedResources := 0;
  PreservedTextRows := 0;
  RemoveMode := False;
  // /DIR= в релизе отвергается до любых действий.
  if not AppDirOk then
  begin
    Log('Установка в нестандартную папку отвергнута: ' + ExpandConstant('{app}'));
    LastFailure := AppDirRefusal;
    LastCode := 'dir';
    Result := LastFailure;
    Exit;
  end;
#ifndef TestBuild
  if not RunTool('legacy-cleanup', ' --app "' + ExpandConstant('{app}') + '"') then
  begin
    LastFailure := FailureText;
    LastCode := ToolValue('code');
    Result := LastFailure;
    Exit;
  end;
#endif
  ResetSteps;
  StartTick := GetTickCount;
  UiBeginProgress('install');
  StateLabel.Font.Color := TextColor;
  StateLabel.Caption := 'Устанавливаю перевод';
  DetailLabel.Caption := 'Не закрывайте окно. Игра и лаунчер должны оставаться закрытыми.';
  PrimaryButton.Visible := False;
  SecondaryButton.Visible := False;
  InfoLabel.Visible := False;
  FailureLabel.Visible := False;
  ShowSteps(True);
  if not ExtractPayload then
  begin
    SetStep(1, StFailed, '');
    LastFailure := PayloadFailText;
    LastCode := PayloadFailCode;
    Result := LastFailure;
    Exit;
  end;

  SetStep(1, StActive, '');
  if not RunTool('find', '') then
  begin
    Result := FailStep(1);
    Exit;
  end;
  FoundGame := ToolValue('game');
  LauncherLnk := ToolValue('launcher_lnk');
  LauncherExe := ToolValue('launcher_exe');
  LauncherBuildText := ToolValue('launcher_build');
  LauncherFindReason := ToolValue('launcher_reason');
  // Вторая проверка локального пути поверх помощника.
  if (LauncherExe <> '') and not LauncherTargetTrusted(LauncherExe) then
  begin
    Log('Недопустимый локальный путь лаунчера: ' + LauncherExe);
    LauncherExe := '';
    LauncherBuildText := '';
  end;
  if LauncherAvailable and LauncherOn and (LauncherExe = '') then
    Log('Русский лаунчер не ставится: ' + LauncherOffReason);
  GameParam := ' --game "' + FoundGame + '"';
  SetStep(1, StDone, ShortPath(FoundGame));

  SetStep(2, StActive, 'сверяю файлы игры');
  if not RunToolLive('check', '', 2) then
  begin
    // /UPDATE=1 (автообновление): остатки прежней установки (reinstall) и оборванная запись (pending) снимаются откатом, как кнопкой «Удалить перевод»,
    // затем проверка идёт заново. Любой другой отказ, как и сбой после отката, оставляет игру в исходном виде и сообщает об этом.
    if UpdateMode and ((ToolValue('code') = 'reinstall') or (ToolValue('code') = 'pending')) then
    begin
      Log('Режим обновления: ' + ToolValue('code') + ', возвращаю прежние файлы и проверяю заново');
      SetStep(2, StActive, 'возвращаю прежние файлы');
      if not RunToolLive('restore', '', 2) then
      begin
        Result := FailStep(2);
        Exit;
      end;
      SetStep(2, StActive, 'сверяю файлы игры');
      if not RunToolLive('check', '', 2) then
      begin
        SetStep(2, StFailed, '');
        LastCode := ToolValue('code');
        LastFailure := 'Прежний перевод снят, восстановлено состояние до его установки. ' + FailureText;
        UpdateRestored := True;
        Result := LastFailure;
        Exit;
      end;
    end
    else
    begin
      Result := FailStep(2);
      Exit;
    end;
  end;
  State := ToolValue('state');
  ActualGameVersion := ToolValue('game_version');
  if ActualGameVersion = '' then ActualGameVersion := '{#GameVersion}';
  SetStep(2, StDone, 'игра ' + ActualGameVersion);

  Label_ := '';
  if State = 'same' then
  begin
    SetStep(3, StDone, 'не требуется');
    SetStep(4, StDone, 'уже установлено');
  end
  else
  begin
    SetStep(3, StActive, '');
    if not RunToolLive('backup', '', 3) then
    begin
      Result := FailStep(3);
      Exit;
    end;
    Label_ := ToolValue('label');
    if ToolValue('backed_up') = '0' then
      SetStep(3, StDone, 'прежних файлов не было')
    else
      SetStep(3, StDone, 'сохранено файлов: ' + ToolValue('backed_up'));

    SetStep(4, StActive, '');
    if not RunToolLive('unpack', ' --label "' + Label_ + '"', 4) then
    begin
      Result := FailStep(4);
      Exit;
    end;
    SetStep(4, StDone, 'готово');
  end;

  SetStep(5, StActive, '');
  if not RunToolLive('verify', ' --label "' + Label_ + '"', 5) then
  begin
    Result := FailStep(5);
    Exit;
  end;
  AppliedResources := StrToInt64Def(ToolValue('applied_resources'), 0);
  SkippedResources := StrToInt64Def(ToolValue('skipped_resources'), 0);
  PreservedTextRows := StrToInt64Def(ToolValue('preserved_text_rows'), 0);
  if AppliedResources < 0 then AppliedResources := 0;
  if SkippedResources < 0 then SkippedResources := 0;
  if PreservedTextRows < 0 then PreservedTextRows := 0;
  if InstallResultNote <> '' then SetStep(5, StDone, 'перевод проверен')
  else SetStep(5, StDone, 'всё совпало');
  SetOverall(1000);
end;

#ifdef WithUpdater
// ---- автообновление ------------------------------------------------------------------------------------------
// Задача планировщика «Egoist\WXQ-Russian Update»: от SYSTEM, БЕЗ триггеров (проверка только по запросу значка, когда человек зашёл в русификатор), ниже обычного приоритета,
// действие - {app}\WXQTool.exe update --check-only (без пользовательских аргументов; только проверка и докачка установщика в кэш). Дескриптор безопасности даёт группе «Пользователи» только запуск и чтение
// (GRGX): значок в трее просит schtasks /run, а менять или удалять задачу могут лишь администраторы и SYSTEM.
// Любой сбой здесь не прерывает установку перевода: без задачи просто нет автообновления (в журнале строка).

// Саму регистрацию делает помощник (WXQTool.exe task-register / task-remove): Pascal-скрипт через CreateOleObject падал с пустым сообщением.
// Установка запускает помощника из защищённой копии (как остальные команды); удаление - установленного {app}\WXQTool.exe.

procedure RemoveUpdateTask;
var
  Tool, OutFile, Params: String;
  Code: Integer;
  Lines: TArrayOfString;
begin
  Tool := ExpandConstant('{app}\{#ToolExe}');
  if not FileExists(Tool) then
  begin
    Log('Задача автообновления не удалена: помощника нет');
    Exit;
  end;
  OutFile := ExpandConstant('{app}') + '\task-out.txt';
  DeleteFile(OutFile);
  Params := 'legacy-cleanup --app "' + ExpandConstant('{app}') + '" --out "' + OutFile + '"';
  if UninstDataDir <> '' then Params := Params + ' --data "' + UninstDataDir + '"';
  if Exec(Tool, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
  begin
    Log('Удаление задачи автообновления: код ' + IntToStr(Code));
    if LoadStringsFromFile(OutFile, Lines) and (GetArrayLength(Lines) > 0) then Log('  ' + Lines[0] + ' ... ' + Lines[GetArrayLength(Lines) - 1]);
  end
  else
    Log('Удаление задачи автообновления: помощник не запустился');
  DeleteFile(OutFile);
end;

// Работающие WXQTool.exe (процесс обновления) и WXQTray.exe нельзя перезаписать, но можно переименовать: [Files] положит новые файлы рядом,
// старые удалят при следующем запуске помощника и значка (а если установка оборвётся, вернут на место).
procedure RetireRunningExe(Name: String);
var
  Cur, Old: String;
begin
  Cur := ExpandConstant('{app}\') + Name;
  Old := Cur + '.old';
  if not FileExists(Cur) then Exit;
  if FileExists(Old) then DeleteFile(Old);
  if not RenameFile(Cur, Old) then Log('Не удалось переименовать ' + Name + ' в ' + Name + '.old');
end;
#endif

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Busy := True;
  try
    Result := DoInstall;
  finally
    Busy := False;
  end;
#ifdef WithUpdater
  // Установка в игру прошла: теперь можно освободить имена запущенных exe. Позже не получится: [Files] копирует сразу после этой функции.
  if Result = '' then
  begin
    RetireRunningExe('{#ToolExe}');
    if SelfCopyNeeded then RetireRunningExe('WXQ-Russian.exe');
  end;
#endif
  if Result <> '' then UiInstallFailed(Result);
end;

// При скрытом мастере Inno может принудительно показать wpReady, считая,
// что предыдущие страницы не были показаны. Установку уже подтвердили в нашем окне.
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := PageID = wpReady;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Detail, ManagerDir: String;
begin
  Log('Page ' + IntToStr(CurPageID));
  HidePageContent;
  ParkNativeControls;
  CloseLabel.Visible := CurPageID <> wpInstalling;
  if CurPageID = wpWelcome then
  begin
    WizardReady := False;
    ResetSteps;
    // На welcome {app} ещё не готов: WizardDirValue возвращает выбранный
    // или стандартный путь без преждевременного раскрытия константы.
    if not ManagerReady then
    begin
      ManagerDir := RemoveBackslashUnlessRoot(WizardDirValue);
      if UpdateMode and SelfCopyNeeded and DirExists(ManagerDir) then
      begin
        LastFailure := '';
        LastCode := '';
        Busy := True;
        try
          UiBeginProgress('install');
          SvDetail := 'Сохраняю новую версию приложения';
          PublishState;
#ifndef TestBuild
          if CompareText(ManagerDir, CanonicalAppDir) <> 0 then
          begin
            LastFailure := AppDirRefusal;
            LastCode := 'dir';
          end
          else
#endif
          if not RunTool('manager-update', ' --app "' + ManagerDir + '" --source "' +
             ExpandConstant('{srcexe}') + '" --sha ' + SelfSourceSha) then
          begin
            LastFailure := 'Не удалось сохранить новую версию приложения: ' + FailureText;
            LastCode := ToolValue('code');
          end;
        finally
          Busy := False;
        end;
        if LastFailure <> '' then
        begin
          AutoContinue := False;
          UiInstallFailed(LastFailure);
          // Состояние ошибки уже опубликовано: доступны закрытие и возврат.
          WelcomeReadyAt := GetTickCount;
          WizardReady := True;
          Exit;
        end;
      end;
      ManagerReady := True;
    end;
    RefreshStatus;
    if IsInterrupted then
      ShowMode(0, 'Установка была прервана', 'Часть файлов игры могла остаться изменённой. Закройте игру и лаунчер и нажмите «Удалить перевод»: файлы вернутся к прежнему состоянию.', InfoInterrupted, 'Удалить перевод', 'Закрыть')
    else if LastFailure <> '' then
      ShowMode(0, 'Можно повторить', 'Закройте игру и лаунчер, если они запущены, и повторите.', InfoWelcome, 'Повторить', RemoveCaption)
    else if NeedsReapply then
      ShowMode(0, 'Файлы игры изменились', 'Перевод можно установить заново. Известные строки будут переведены, новые и изменившиеся останутся в оригинале.', InfoAfterUpdate, 'Переустановить', RemoveCaption)
    else if IsInstalled then
      ShowMode(0, 'Перевод уже установлен', 'Приложение {#AppVersion}; перевод ' + InstalledVersion + ', игра ' + InstalledGameVersion + '.', InfoInstalled, 'Переустановить', 'Удалить перевод')
    else if IsPending then
      ShowMode(0, 'Перевод Honor of Kings ACE', 'Игру найду сам, перед записью сохраню резервную копию.', InfoWelcome, 'Установить', 'Удалить перевод')
    else
      ShowMode(0, 'Перевод Honor of Kings ACE', 'Игру найду сам, перед записью сохраню резервную копию.', InfoWelcome, 'Установить', '');
    LastFailure := '';
  end
  else if CurPageID = wpPreparing then
  begin
    Detail := LastFailure;
    if Detail = '' then Detail := WizardForm.PreparingLabel.Caption;
    if Detail = '' then Detail := 'Подготовка к установке не удалась.';
    ShowMode(1, 'Установка остановлена', Detail, '', 'Назад', 'Закрыть');
  end
  else if CurPageID = wpInstalling then
  begin
    ShowMode(0, 'Завершаю установку', 'Добавляю запись в «Установку и удаление программ».', '', '', '');
  end
  else if CurPageID = wpFinished then
  begin
    Detail := InstallResultNote;
    if Detail = '' then Detail := 'Файлы проверены, резервная копия сохранена.';
    if (LauncherLnk <> '') or LauncherWanted then
      ShowMode(2, 'Перевод установлен', Detail, InfoFinished(True), 'Запустить игру', 'Закрыть')
    else
      ShowMode(2, 'Перевод установлен', Detail, InfoFinished(False), 'Закрыть', '');
    if (LauncherLnk = '') and not LauncherWanted then Mode := 3;
  end;
  UiOnPage(CurPageID);
  if CurPageID = wpWelcome then
  begin
    WelcomeReadyAt := GetTickCount;
    WizardReady := True;
  end;
end;

// ---- удаление из «Установки и удаления программ» ------------------------------------------

function InitializeUninstall: Boolean;
var
  Code: Integer;
  MutexHandle: LongWord;
  OutFile, Tool, Message: String;
  Lines: TArrayOfString;
  I, P: Integer;
  Status: String;
begin
  Result := True;
  KeepBackups := False;
  MutexHandle := OpenMutexW($00100000, False, 'Global\Egoist.WXQRussian.Setup');
  if MutexHandle <> 0 then
  begin
    CloseHandle(MutexHandle);
    if not UninstallSilent then MsgBox('Сначала закройте окно русификатора и повторите удаление приложения.', mbInformation, MB_OK);
    Result := False;
    Exit;
  end;
  // /DIR= в релизе не поддерживается: деинсталлятор из другой папки не запускает помощника и не откатывает.
  if not AppDirOk then
  begin
    MsgBox('Удаление остановлено: программа установлена не в ' + CanonicalAppDir + '. Установите перевод заново без параметра /DIR и удалите его оттуда.', mbError, MB_OK);
    Result := False;
    Exit;
  end;
  Tool := ExpandConstant('{app}\{#ToolExe}');
  if not FileExists(Tool) then Exit;
  // Каталог данных запомнен при установке (datadir.txt); у сборок до 0.3.1 данные лежали прямо в {app}.
  UninstDataDir := RemoveBackslashUnlessRoot(ExpandConstant('{app}'));
  if FileExists(ExpandConstant('{app}\datadir.txt')) and LoadStringsFromFile(ExpandConstant('{app}\datadir.txt'), Lines) and
     (GetArrayLength(Lines) > 0) and (Trim(Lines[0]) <> '') then
    UninstDataDir := RemoveBackslashUnlessRoot(Trim(Lines[0]));
  // Результат пишется в {app} (Program Files, пишут только Администраторы и SYSTEM), а не в {tmp}, куда достаёт обычный пользователь,
  // и не в каталог данных: он может оказаться не проверенным (отказ acl), и туда помощник ничего не пишет.
  OutFile := ExpandConstant('{app}') + '\restore-out.txt';
  DeleteFile(OutFile);
  SetArrayLength(Lines, 0);
  Exec(Tool, 'restore --data "' + UninstDataDir + '" --out "' + OutFile + '" --close all',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
  Status := 'error';
  Message := 'Помощник не вернул результат.';
  if FileExists(OutFile) and LoadStringsFromFile(OutFile, Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      P := Pos('=', Lines[I]);
      if P > 0 then
      begin
        if Copy(Lines[I], 1, P - 1) = 'status' then Status := Copy(Lines[I], P + 1, Length(Lines[I]));
        if Copy(Lines[I], 1, P - 1) = 'message' then Message := Copy(Lines[I], P + 1, Length(Lines[I]));
        // Резервные копии другой папки игры оставляем на месте: они ещё могут понадобиться.
        if (Copy(Lines[I], 1, P - 1) = 'foreign') and (StrToIntDef(Copy(Lines[I], P + 1, Length(Lines[I])), 0) > 0) then KeepBackups := True;
      end;
    end;
  DeleteFile(OutFile);
  if Status <> 'ok' then
  begin
    // Удаление прерывается: резервные копии остаются, игра не оказывается наполовину откаченной.
    MsgBox('Перевод не удалён: ' + Message, mbError, MB_OK);
    Result := False;
  end;
end;

// Запомнить каталог данных: деинсталлятору он нужен для отката и очистки (он не видит параметр /DATADIR установки).
#ifdef WithLauncher
// Русский лаунчер выключен (или не найден оригинал), а прежняя установка его уже ставила: убираем флаг, ярлыки и файлы.
procedure RemoveLauncherTraces;
begin
  RegDeleteKeyIncludingSubkeys(HKA64, 'SOFTWARE\Egoist\WXQ-Russian');
  DeleteFile(DesktopDir('') + '\Honor of Kings ACE (RU).lnk');
  DeleteFile(StartDir('') + '\Honor of Kings ACE (RU).lnk');
  DeleteFile(ExpandConstant('{app}\WXQLauncherRU.exe'));   // прежнее место активатора (сборки до исправления раскладки)
  DelTree(ExpandConstant('{app}\launcher'), True, True, True);
end;
#endif

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
#ifdef WithLauncher
  if (CurStep = ssInstall) and not LauncherWanted then RemoveLauncherTraces;
  if (CurStep = ssPostInstall) and LauncherWanted then
  begin
    // Куда указывает активатор: путь к нетронутому оригинальному exe лаунчера (его хеши активатор проверяет сам при каждом запуске).
    SetArrayLength(Lines, 1);
    Lines[0] := LauncherExe;
    if not SaveStringsToUTF8File(ExpandConstant('{app}\launcher\target.txt'), Lines, False) then
    begin
      Log('Не удалось записать launcher\target.txt');
      Abort;
    end;
    Log('Русский лаунчер установлен: ' + LauncherExe + ' (сборка ' + LauncherBuildText + ')');
  end;
#endif
  if CurStep = ssPostInstall then
  begin
    if (not FileExists(ExpandConstant('{app}\WXQ-Russian.exe'))) or
       (GetSHA256OfFile(ExpandConstant('{app}\WXQ-Russian.exe')) <> SelfSourceSha) then
      RaiseException('Файл приложения не обновлён. Откройте скачанный установщик снова; перевод игры уже проверен.');
    SetArrayLength(Lines, 1);
    Lines[0] := GetDataDir('');
    if not SaveStringsToUTF8File(ExpandConstant('{app}\datadir.txt'), Lines, False) then
    begin
      Log('Не удалось записать datadir.txt');
      Abort;
    end;
  end;
end;

// После отката убираем резервные копии и рабочие файлы из каталога данных (журнал install.log остаётся).
// Если рядом лежат копии другой папки игры, их оставляем.
// Чистка идёт через помощника (команда purge): он берёт тот же Global-мьютекс, что и установка с откатом, поэтому не удалит копии под чужой операцией.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Tool, OutFile, Extra: String;
  KillCode: Integer;
begin
#ifdef WithUpdater
#ifndef TestBuild
  // Тестовая сборка (песочница) не трогает ни настоящую задачу планировщика, ни работающий значок.
  if CurUninstallStep = usUninstall then
  begin
    RemoveUpdateTask;
    // Значок держит WXQTray.exe открытым: без этого файл не удалится.
  end;
#endif
#endif
  if (CurUninstallStep = usUninstall) and (UninstDataDir <> '') then
  begin
    Tool := ExpandConstant('{app}\{#ToolExe}');
    OutFile := ExpandConstant('{app}') + '\purge-out.txt';
    Extra := '';
    if KeepBackups then Extra := ' --keep 1';
    if FileExists(Tool) then
    begin
      if Exec(Tool, 'purge --data "' + UninstDataDir + '" --out "' + OutFile + '"' + Extra, '', SW_HIDE, ewWaitUntilTerminated, Code) then
        Log('Чистка каталога данных: код ' + IntToStr(Code))
      else
        Log('Чистка каталога данных: помощник не запустился');
      DeleteFile(OutFile);
      DeleteFile(ExpandConstant('{app}') + '\restore-out.txt');
    end
    else
      Log('Чистка каталога данных пропущена: помощника нет');
  end;
end;

// Только для проверки в тестах: build_installer.py --dump-script сохраняет скрипт после препроцессора (без DumpScript строки не действуют).
#ifdef DumpScript
#expr SaveToFile(DumpScript)
#endif
