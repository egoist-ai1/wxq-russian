"""Кандидаты покрытия Lua-помощника переноса слов: модули пакета 10, которых ещё нет в build_lua_package.TARGETS.

Ищет в Lua-ресурсах пакета 10 (только чтение установленной игры) AutoBind-модули (DeclarePrivateClass("<имя>_AutoBind"),
setter-вид, как у большинства TARGETS), у которых поле объявлено как `---@type Timi.TimiText` и пишется строкой
`self.<поле>.text = arg` внутри `<модуль>:Set...(arg)` (ровно одна такая строка на поле - так же требует patch_lua).
Чат и ники исключаются: модули Talk*/*Chat*/*InputField*, поля nick/playername/rolename/username.

Ничего не меняет: выдаёт список кандидатов (ресурс, модуль, поля) в JSON и сводку. Подключение в TARGETS и
pilot_contract.LUA_REPLACEMENT_ALLOWED - отдельное решение (см. отчёт).

    python scripts/gen_wrap_targets.py [--game-root ПУТЬ] [--out candidates.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
for _path in (str(PROJECT), str(PROJECT / 'scripts')):
    if _path not in sys.path:
        sys.path.insert(0, _path)

PACKAGE = '10'
# Общий чанк: базовый класс всех UI-префабов (UIPrefabClass); в него один раз кладётся помощник переноса (см. build_lua_package.py).
SHARED_ID = '3482bf89614b5abb'
SHARED_MODULE = 'UIPrefabClass'
MODULE_RE = re.compile(r'DeclarePrivateClass\("(?P<module>\w+_AutoBind)"')
# Аннотация типа бывает строкой выше поля или в конце строки поля (`self.x = false ---@type Timi.TimiText`).
FIELD_RE = re.compile(r'---@type Timi\.TimiText[ \t]*\r?\n[ \t]*self\.(?P<field>\w+)[ \t]*=|^[ \t]*self\.(?P<field2>\w+)[ \t]*=[ \t]*false[ \t]*---@type Timi\.TimiText', re.M)
CHAT_MODULE_RE = re.compile(r'^Talk|Chat|InputField|Barrage|Danmu', re.I)
NICK_FIELD_RE = re.compile(r'nick|playername|rolename|username|friendname|guildname|clanname|teamname|sendername|authorname|ownername|chat|talk|barrage|signature'
                           r'|password|phone|token|uid|^id$|_id$|playerid|guildid|teamid|vipid|sharecode|input|message|msg|reply|gmtxt|debug|^fps$|^ping$'
                           r'|friendsname|myname|playernme|autor|declaration|greetings|speechcontent|emailcontent|reportcontent|voicecontent|applicationcontent'
                           r'|customizedname|investorname', re.I)


_NAME_FIELD = re.compile(r'name', re.I)
_IDENTITY_MODULE = re.compile(r'Player|Friend|User|Team|Guild', re.I)
_CLASS_DECL = re.compile(r'DeclareClass\("(?P<module>\w+)"')
_METHOD = re.compile(r'^function \w+[:.]\w+\([^\r\n]*\)', re.M)
_RECEIVER = r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*'
_LONG_BRACKET = re.compile(r'\[(=*)\[')


def _code_only(text: str) -> str:
    """Позиции сохранены; строковые literals и комментарии не служат proof."""
    result = list(text)
    index = 0
    while index < len(text):
        start = index
        comment = text.startswith('--', index)
        probe = index + 2 if comment else index
        long = _LONG_BRACKET.match(text, probe)
        if long:
            end_marker = ']' + long.group(1) + ']'
            stop = text.find(end_marker, probe + len(long.group(0)))
            stop = len(text) if stop < 0 else stop + len(end_marker)
        elif comment:
            stop = text.find('\n', index)
            stop = len(text) if stop < 0 else stop
        elif text[index] in ('"', "'"):
            quote = text[index]
            stop = index + 1
            while stop < len(text):
                if text[stop] == '\\':
                    stop += 2
                elif text[stop] == quote:
                    stop += 1
                    break
                else:
                    stop += 1
        else:
            index += 1
            continue
        for offset in range(start, min(stop, len(text))):
            if text[offset] not in '\r\n':
                result[offset] = ' '
        index = stop
    return ''.join(result)


def _closing_paren(code: str, start: int) -> int | None:
    depth = 1
    for index in range(start, len(code)):
        if code[index] == '(':
            depth += 1
        elif code[index] == ')':
            depth -= 1
            if depth == 0:
                return index
    return None


def _identity_source_reason(module: str, field: str, value: str, context: str, original: str, depth: int = 0) -> str | None:
    if depth > 2:
        return None
    if re.search(r'\bFriendUtility\.(?:GetShowName|GetShowNameByLenControl|GetUserName|GetPlayerName)\s*\(', value):
        return 'friend-player-display-name-utility'
    if re.search(r'\.(?:PlayerName|PlatUserName|GuildName|UserName|AccountName|AuthorName)\b', value):
        return 'explicit-player-user-guild-name-property'
    if re.search(r':GetAuthor\s*\(', value):
        return 'user-authored-display-author'
    if re.search(r'\.BaseInfo\.Name\b', value):
        return 'account-base-info-name'
    if re.search(r'\b(?:VipService:SetvipPlayerNameColor|FriendUtility\.SetTimiFriendNameState)\([^\n]*\bself\.' + re.escape(field) + r'\b', context):
        return 'native-player-name-color-or-friend-name-state'
    # Только наблюдаемый scalar name/getter и тот же receiver identity.
    for match in re.finditer(r'(?P<receiver>' + _RECEIVER + r')(?:\.(?:name|nickname|rolename)\b|:GetName\s*\()', value, re.I):
        receiver = match.group('receiver')
        if _IDENTITY_MODULE.search(module) and re.search(r'\b' + re.escape(receiver) + r'(?:\.(?:uid|guildid)\b|:(?:GetUid|GetGuildID)\s*\()', context, re.I):
            return 'same-name-receiver-uid-or-guild-id'
        if re.search(r'\bself:(?:ShowNameSuffix|RefreshPlayerNameSuffix)\(\s*' + re.escape(receiver) + r'\s*[,)]', context):
            return 'native-player-name-suffix-receiver'
        if re.fullmatch(r'\w+', receiver) and re.search(r'\blocal\s+' + re.escape(receiver) + r'\s*=\s*[^\n]*(?:GetSinglePlayerData|SGGuildData|GetApllyGuildSnap)\s*\(', context):
            return 'typed-player-or-guild-data-receiver'
        if re.search(r'\b' + re.escape(receiver) + r'\s*=\s*(?:false|nil)\s*---@type\s+(?:SinglePlayerDataClass|SingleFriendDataClass|GuildInfoDataClass)\b', original):
            return 'typed-instance-player-or-guild-receiver'
        if receiver == 'data' and re.search(r'^---@field\s+name\s+string[^\n]*(?:玩家名字|玩家名|昵称)', original, re.M):
            return 'typed-player-name-dto-field'
    variable = value.strip()
    if re.fullmatch(r'[A-Za-z_]\w*', variable):
        definitions = list(re.finditer(r'^\s*local\s+' + re.escape(variable) + r'\s*=\s*(?P<value>[^\r\n]+)', context, re.M))
        if len(definitions) == 1:
            return _identity_source_reason(module, field, definitions[0].group('value'), context, original, depth + 1)
    return None


def protected_display_name_fields(module: str, fields: list[str], controllers: list[str]) -> dict[str, list[dict]]:
    """Ники/имена авторов и гильдий доказываются caller, а не словом Player.

    Названия карт, предметов, рангов и Localize-подписи без identity proof остаются.
    Исходники только анализируются: userdata, игра и её состояния не читаются.
    """
    if len(controllers) != 1 or not any(_NAME_FIELD.search(field) for field in fields):
        return {}
    protected = {}
    for original in controllers:
        code = _code_only(original)
        spans = list(_METHOD.finditer(code))
        for field in fields:
            if not _NAME_FIELD.search(field):
                continue
            setter = 'Set' + field[0].upper() + field[1:]
            sites = []
            for match in re.finditer(r'\bself:' + re.escape(setter) + r'\(', code):
                stop = _closing_paren(code, match.end())
                if stop is not None:
                    sites.append((match.start(), code[match.end():stop]))
            for match in re.finditer(r'\bself\.' + re.escape(field) + r'\.text\s*=\s*(?P<value>[^\r\n]+)', code):
                sites.append((match.start(), match.group('value')))
            for position, value in sites:
                preceding = [m for m in spans if m.start() <= position]
                following = [m for m in spans if m.start() > position]
                start = preceding[-1].start() if preceding else 0
                stop = following[0].start() if following else len(code)
                reason = _identity_source_reason(module, field, value, code[start:stop], original)
                if reason:
                    protected.setdefault(field, []).append({'reason': reason, 'line': code.count('\n', 0, position) + 1})
    return protected


def setter_assign_count(text: str, module: str, field: str) -> tuple[int, int, int]:
    """(строк `self.<поле>.text = arg` всего, из них внутри setter `<модуль>:Set...(arg)`, из них с `---@param arg string`)."""
    pattern = re.compile(r'^(?P<indent>[ \t]*)self\.' + re.escape(field) + r'\.text = arg(?P<eol>\r?\n)', re.M)
    header = re.compile(r'function ' + re.escape(module) + r':Set\w+\(arg\)\r?\n')
    total = inside = typed = 0
    for match in pattern.finditer(text):
        total += 1
        start = text.rfind('\nfunction ', 0, match.start()) + 1
        if header.match(text[start:]) and '\nend' not in text[start:match.start()]:
            inside += 1
            before = text[:start].rstrip().splitlines()
            if before and before[-1].strip() == '---@param arg string':
                typed += 1
    return total, inside, typed


def scan(game: Path, known: dict[str, tuple] | None = None) -> dict:
    """Просмотр пакета 10: {'candidates': [...], 'stats': {...}}. known - TARGETS (ресурсы, которые уже подключены)."""
    import build_pilot_carrier as bp
    from game_inspect import QtsContainer

    known = known or {}
    shared = None
    controllers = {}
    candidates, stats = [], {'lua_modules': 0, 'timitext_modules': 0, 'already_in_targets': 0, 'excluded_chat_modules': 0,
                             'excluded_nick_fields': 0, 'no_setter_assign': 0, 'ambiguous_assign': 0, 'non_string_setter': 0, 'no_unload_hook': 0, 'candidate_fields': 0}
    for path in bp.data_files(game, PACKAGE):
        with QtsContainer(path) as container:
            for index in sorted(container.entries):
                try:
                    data = container.decode_entry(index)
                except Exception:
                    continue
                if f'{index:016x}' == SHARED_ID:
                    shared = {'id_hex': SHARED_ID, 'module': SHARED_MODULE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                    if f'DeclareClass("{SHARED_MODULE}")'.encode() not in data:
                        raise SystemExit('Общий чанк: в ресурсе нет DeclareClass("UIPrefabClass")')
                # Для generic name-полей нужен source-bound caller proof.
                if b'DeclareClass("' in data[:16384]:
                    try:
                        controller = data.decode('utf-8', 'strict')
                    except UnicodeDecodeError:
                        controller = None
                    declared_controller = _CLASS_DECL.search(controller[:16384]) if controller else None
                    if declared_controller:
                        controllers.setdefault(declared_controller.group('module') + '_AutoBind', []).append(controller)
                if b'DeclarePrivateClass("' not in data[:4000]:
                    continue
                try:
                    text = data.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                declared = MODULE_RE.search(text)
                if not declared:
                    continue
                module = declared.group('module')
                stats['lua_modules'] += 1
                rid = f'{index:016x}'
                fields = list(dict.fromkeys(m.group('field') or m.group('field2') for m in FIELD_RE.finditer(text)))
                if not fields:
                    continue
                stats['timitext_modules'] += 1
                if rid in known:
                    stats['already_in_targets'] += 1
                    continue
                if CHAT_MODULE_RE.search(module):
                    stats['excluded_chat_modules'] += 1
                    continue
                if len(re.findall(r'^function ' + re.escape(module) + r':vOnResourceUnLoaded\(\)\r?\n', text, re.M)) != 1:
                    stats['no_unload_hook'] += 1
                    continue
                chosen = []
                for field in fields:
                    if NICK_FIELD_RE.search(field):
                        stats['excluded_nick_fields'] += 1
                        continue
                    total, inside, typed = setter_assign_count(text, module, field)
                    if total == 0:
                        stats['no_setter_assign'] += 1
                    elif total != 1 or inside != 1:
                        stats['ambiguous_assign'] += 1
                    elif typed != 1:
                        stats['non_string_setter'] += 1
                    else:
                        chosen.append(field)
                if chosen:
                    stats['candidate_fields'] += len(chosen)
                    candidates.append({'id_hex': rid, 'module': module, 'fields': chosen, 'bytes': len(data),
                                       'sha256': hashlib.sha256(data).hexdigest(), 'data_file': path.name})
    source_name_exclusions = []
    filtered = []
    for candidate in candidates:
        protected = protected_display_name_fields(candidate['module'], candidate['fields'], controllers.get(candidate['module'], []))
        for field, proof in protected.items():
            source_name_exclusions.append({'id_hex': candidate['id_hex'], 'module': candidate['module'], 'field': field, 'proof': proof})
        candidate['fields'] = [field for field in candidate['fields'] if field not in protected]
        stats['excluded_nick_fields'] += len(protected)
        stats['candidate_fields'] -= len(protected)
        if candidate['fields']:
            filtered.append(candidate)
    candidates = filtered
    stats['candidates'] = len(candidates)
    return {'package': PACKAGE, 'candidates': candidates, 'stats': stats, 'shared': shared, 'source_name_exclusions': source_name_exclusions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--game-root', type=Path, default=None, help='По умолчанию установка, которую использует лаунчер')
    parser.add_argument('--out', type=Path, default=None, help='Записать список кандидатов в JSON')
    parser.add_argument('--write-coverage', type=Path, default=None, help='Записать source/game-wordwrap/coverage-targets.json (модули общего покрытия и пины SHA оригиналов)')
    args = parser.parse_args(argv)
    import build_lua_package as blp
    import wxq_install

    game = wxq_install.require_install_structure(args.game_root if args.game_root else wxq_install.detect_active_install(), full=True)
    result = scan(game, blp.TARGETS)
    if args.out:
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', 'utf-8')
    if args.write_coverage:
        if not result['shared']:
            raise SystemExit('Общий чанк UIPrefabClass не найден в пакете 10')
        modules = {c['id_hex']: {'module': c['module'], 'fields': c['fields'], 'sha256': c['sha256']} for c in result['candidates']}
        coverage = {'package': PACKAGE, 'shared': {k: result['shared'][k] for k in ('id_hex', 'module', 'sha256')}, 'modules': dict(sorted(modules.items()))}
        args.write_coverage.write_text(json.dumps(coverage, indent=1, ensure_ascii=False) + '\n', 'utf-8')
    for key, value in result['stats'].items():
        print(f'{key}: {value}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
