# Шрифты окна установщика

Unbounded (заголовки) и Onest (текст): SIL Open Font License 1.1, есть кириллица. Взяты из Egoist DS и приложений Egoist.

- `Unbounded.ttf` — как есть (вариативный, WPF берёт начертание 400).
- `Onest-Cyr.ttf`, `Onest-Lat.ttf` — подмножества Onest, превращённые из woff2 в TTF скриптом `../tools/woff2_to_ttf.py` (WPF не читает woff2). WPF не знает осей вариативных шрифтов, поэтому используется одно начертание 400; иерархия строится размером, цветом и Unbounded.
- exe распаковывает их во временную папку `%TEMP%\WXQSetupUI-fonts-1` и подключает как FontFamily. Если шрифт не найден, WPF берёт Segoe UI.
