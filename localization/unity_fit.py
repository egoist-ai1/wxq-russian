"""План автоподбора размера для статических надписей Unity UI (пакет 19).

Русский текст длиннее китайского: в рамке, рассчитанной на иероглифы, он переносится и налезает на соседей.
Для таких Text мы включаем m_BestFit (игра сама уменьшит кегль до m_MinSize), ставим m_MaxSize = текущий кегль
(при влезающем тексте вид не меняется) и, если надпись шла в одну строку без переноса, включаем перенос.
Берутся только надписи, где китайский оригинал помещался в рамку, а ширина рамки известна (якоря по X не растянуты).
Это оценка по геометрии (средняя ширина кириллицы 0,56 кегля), реальный шрифт её уточнит BestFit на месте.
"""
from __future__ import annotations

import re

from localization import unity_text_codec as codec  # noqa: E402  (добавляет scripts/ в sys.path)

guf = codec.guf

CYR_WIDTH = 0.56
MIN_SIZE_SHARE = 0.5
MIN_SIZE_FLOOR = 12
RECT_CLASS = 224
TAGS = re.compile(r'<[^>]+>')


def _lines(text: str, size: float, width: float, char_width: float) -> int:
    total = 0
    for paragraph in TAGS.sub('', text).splitlines() or ['']:
        used, lines = 0.0, 1
        for word in paragraph.split(' '):
            word_width = len(word) * size * char_width
            gap = size * char_width * 0.5 if used else 0.0
            if used == 0.0 or used + gap + word_width <= width:
                used += gap + word_width
                while used > width > 0:
                    lines += 1
                    used -= width
            else:
                lines += 1
                used = word_width
        total += lines
    return total


def _size(parse, path_id: int | None, depth: int = 0) -> tuple[float, float] | None:
    """Размер RectTransform в пикселях: у рамки с точечными якорями это её sizeDelta, у растянутой - доля размера родителя.
    Если до известного размера не дойти (корень без размера, нулевая точка), None: ширину не угадываем."""
    rect = parse(path_id) if path_id else None
    if not rect or depth > 12:
        return None
    anchor_min, anchor_max, delta = rect['m_AnchorMin'], rect['m_AnchorMax'], rect['m_SizeDelta']
    father = rect.get('m_Father')
    parent_id = father.get('m_PathID') if isinstance(father, dict) else None
    parent = None
    result = []
    for axis in ('x', 'y'):
        span = anchor_max[axis] - anchor_min[axis]
        if span < 1e-4:
            result.append(delta[axis])
            continue
        if parent is None:
            parent = _size(parse, parent_id, depth + 1)
        if parent is None:
            return None
        result.append(parent[0 if axis == 'x' else 1] * span + delta[axis])
    return (result[0], result[1]) if result[0] > 2 and result[1] > 2 else None


def _rect_id(objects: dict, parse, text_obj: dict) -> int | None:
    game_object = parse(text_obj['m_GameObject']['m_PathID']) if text_obj.get('m_GameObject') else None
    for item in (game_object or {}).get('m_Component', []):
        path_id = item.get('component', {}).get('m_PathID') if isinstance(item, dict) else None
        if path_id in objects and objects[path_id].get('class_id') == RECT_CLASS:
            return path_id
    return None


def _rect(original: bytes, objects: dict, parse, text_obj: dict):
    game_object = parse(text_obj['m_GameObject']['m_PathID']) if text_obj.get('m_GameObject') else None
    if not game_object:
        return None
    for item in game_object.get('m_Component', []):
        path_id = item.get('component', {}).get('m_PathID') if isinstance(item, dict) else None
        if path_id in objects and objects[path_id].get('class_id') == RECT_CLASS:
            return parse(path_id)
    return None


def plan_styles(original: bytes, chosen: dict[tuple[int, str], str], sources: dict[tuple[int, str], str]) -> dict[tuple[int, str], int]:
    """{(path_id, поле стиля): значение} для надписей из chosen (ключ -> русский текст); sources - их китайские оригиналы."""
    objects = {o['path_id']: o for o in guf.read_index(original)['objects']}
    cache: dict[int, dict | None] = {}

    def parse(path_id: int):
        if path_id not in cache:
            obj = objects.get(path_id)
            try:
                cache[path_id] = guf.read_fields(codec._body(original, obj), codec.schema_for(obj))['fields'] if obj else None
            except (ValueError, UnicodeError, KeyError, codec.UnityTextError):
                cache[path_id] = None
        return cache[path_id]

    plan: dict[tuple[int, str], int] = {}
    for (path_id, path), ru in chosen.items():
        if path != 'm_Text':
            continue
        text = parse(path_id)
        font = (text or {}).get('m_FontData')
        frame = _size(parse, _rect_id(objects, parse, text)) if text else None
        size = (font or {}).get('m_FontSize')
        if not font or not frame or not size:
            continue
        line_gap = font.get('m_LineSpacing') or 1
        width, height = frame
        cn = TAGS.sub('', sources[(path_id, path)])
        ru_plain = TAGS.sub('', ru)
        if font.get('m_BestFit'):
            # автоподбор уже включён, но минимум почти равен максимуму: русскому тексту не дают ужаться
            lowest = min(size, max(MIN_SIZE_FLOOR, round(size * MIN_SIZE_SHARE)))
            current = font.get('m_MinSize') or size
            if lowest < current and font.get('m_HorizontalOverflow') != 1 \
                    and _lines(cn, size, width, 1.0) * size * line_gap * 1.15 <= height * 1.05 \
                    and _lines(ru_plain, current, width, CYR_WIDTH) * current * line_gap * 1.15 > height * 1.05:
                plan[(path_id, 'm_FontData.m_MinSize')] = lowest
            continue
        if font.get('m_HorizontalOverflow') == 1:
            if not (len(cn) * size <= width * 1.05 and size * 1.1 <= height * 1.1):
                continue
            if max(len(line) for line in ru_plain.splitlines() or ['']) * size * CYR_WIDTH <= width * 1.02:
                continue
            plan[(path_id, 'm_FontData.m_HorizontalOverflow')] = 0
        else:
            cn_lines, ru_lines = _lines(cn, size, width, 1.0), _lines(ru_plain, size, width, CYR_WIDTH)
            if cn_lines * size * line_gap * 1.15 > height * 1.05 or ru_lines <= cn_lines or ru_lines * size * line_gap * 1.15 <= height * 1.05:
                continue
        plan[(path_id, 'm_FontData.m_BestFit')] = 1
        plan[(path_id, 'm_FontData.m_MaxSize')] = size
        plan[(path_id, 'm_FontData.m_MinSize')] = min(size, max(MIN_SIZE_FLOOR, round(size * MIN_SIZE_SHARE)))
    return plan
