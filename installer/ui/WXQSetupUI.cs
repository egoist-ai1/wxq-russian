// WXQSetupUI: нативный интерфейс установщика русификатора Honor of Kings ACE (WPF, код без XAML, .NET Framework 4.x).
// Единственное видимое окно установщика: Inno Setup остаётся движком и обменивается с окном файлами состояния (см. PROTOCOL.md).
// Режимы: --dir <папка> --token <имя> --parent <pid> (рабочий, запускается установщиком), --demo, --render <папка> [--dpi 96|144|192], --selftest.
// Шрифты Unbounded и Onest (OFL) и растры шапки лежат в ресурсах exe. Арт и знак игры (c) Tencent; фанатский русификатор.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Shell;
using System.Windows.Threading;

namespace WXQSetup
{
    // ===== Токены Egoist DS (tokens.json): роли цвета, радиусы 6/12/20, шаг 4 px =====
    static class Tk
    {
        public static bool Still;   // true в режиме --render: без анимаций
        public static bool Anim { get { return !Still && SystemParameters.ClientAreaAnimation; } }

        public static Color C(string hex) { return (Color)ColorConverter.ConvertFromString(hex); }
        public static SolidColorBrush Bx(string hex) { var b = new SolidColorBrush(C(hex)); b.Freeze(); return b; }

        public static readonly Color CBg = C("#0E0E0F"), CSurface1 = C("#141416"), CSurface2 = C("#1C1C1E"), CSurface3 = C("#26262A"),
            CLine = C("#26262A"), CLineStrong = C("#3A3A3D"), CText = C("#EDEBE6"), CTextSoft = C("#B5B3AD"), CMuted = C("#8C8A85"),
            CFaint = C("#646460"), CGold = C("#C8A55A"), CDanger = C("#FF5A4E"), CSuccess = C("#6FD08C"), CWhite = C("#FFFFFF"), CPress = C("#D6D5D0"),
            CClear = Color.FromArgb(0, 0, 0, 0);
        public static readonly Brush Bg = Bx("#0E0E0F"), Surface1 = Bx("#141416"), Surface2 = Bx("#1C1C1E"), Surface3 = Bx("#26262A"),
            Line = Bx("#26262A"), LineStrong = Bx("#3A3A3D"), Text = Bx("#EDEBE6"), TextSoft = Bx("#B5B3AD"), Muted = Bx("#8C8A85"),
            Gold = Bx("#C8A55A"), Danger = Bx("#FF5A4E"), Success = Bx("#6FD08C"), Clear = Brushes.Transparent;

        public static FontFamily Display, Body;

        static bool IsReparse(string path)
        {
            try { return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0; } catch (FileNotFoundException) { return false; } catch (DirectoryNotFoundException) { return false; } catch (Exception) { return true; }
        }

        // Шрифты лежат в ресурсах exe: распаковываем в папку root (WPF грузит шрифты только из файлов/pack-URI).
        // root = null: временная папка (только --demo/--render). В рабочем режиме root = <папка exe>\fonts (папка exe - защищённая папка установщика SecDir, а не {tmp}).
        // Файлы всегда перезаписываются; если root, подпапка или файл - точка повторного разбора, шрифты не грузим (остаётся Segoe UI).
        public static void LoadFonts(string root)
        {
            if (root == null) root = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "WXQSetupUI-fonts-1");
            string[][] files = { new[] { "unb", "Unbounded.ttf" }, new[] { "onc", "Onest-Cyr.ttf" }, new[] { "onl", "Onest-Lat.ttf" } };
            var asm = Assembly.GetExecutingAssembly();
            try
            {
                Directory.CreateDirectory(root);
                if (IsReparse(root)) return;
                foreach (var f in files)
                {
                    string dir = System.IO.Path.Combine(root, f[0]);
                    Directory.CreateDirectory(dir);
                    if (IsReparse(dir)) return;
                    string path = System.IO.Path.Combine(dir, f[1]);
                    if (IsReparse(path)) return;
                    using (var s = asm.GetManifestResourceStream("fonts." + f[1]))
                    {
                        if (s == null) continue;
                        try { using (var o = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read)) s.CopyTo(o); } catch (IOException) { }
                    }
                    if (IsReparse(path)) return;
                }
            }
            catch (Exception) { return; }
            Func<string, string> u = d => new Uri(System.IO.Path.Combine(root, d) + System.IO.Path.DirectorySeparatorChar).AbsoluteUri;
            Display = new FontFamily(u("unb") + "#Unbounded, Segoe UI Variable Display, Segoe UI");
            Body = new FontFamily(u("onc") + "#Onest, " + u("onl") + "#Onest, Segoe UI Variable Text, Segoe UI");
        }

        public static TimeSpan D(int ms) { return TimeSpan.FromMilliseconds(Anim ? ms : 0); }
        public static IEasingFunction Expo { get { return new ExponentialEase { EasingMode = EasingMode.EaseOut, Exponent = 6 }; } }
        public static IEasingFunction Spring { get { return new BackEase { EasingMode = EasingMode.EaseOut, Amplitude = 0.6 }; } }

        public static void ColorTo(SolidColorBrush b, Color to, int ms)
        {
            if (!Anim) { b.BeginAnimation(SolidColorBrush.ColorProperty, null); b.Color = to; return; }
            b.BeginAnimation(SolidColorBrush.ColorProperty, new ColorAnimation(to, D(ms)) { EasingFunction = Expo });
        }
        public static void DoubleTo(UIElement el, DependencyProperty p, double to, int ms, IEasingFunction ease)
        {
            if (!Anim) { el.BeginAnimation(p, null); el.SetValue(p, to); return; }
            el.BeginAnimation(p, new DoubleAnimation(to, D(ms)) { EasingFunction = ease });
        }

        public static TextBlock TB(string text, double size, Brush brush, FontFamily font = null, double lh = 0, TextWrapping wrap = TextWrapping.NoWrap)
        {
            var t = new TextBlock { Text = text, FontSize = size, Foreground = brush, FontFamily = font ?? Body, TextWrapping = wrap };
            if (lh > 0) { t.LineStackingStrategy = LineStackingStrategy.BlockLineHeight; t.LineHeight = lh; }
            TextOptions.SetTextFormattingMode(t, TextFormattingMode.Ideal);
            TextOptions.SetTextRenderingMode(t, TextRenderingMode.ClearType);
            t.UseLayoutRounding = true;
            return t;
        }
    }

    // ===== Иконки: сетка 24, линия 2 px, скруглённые концы и стыки =====
    static class Ico
    {
        static readonly Dictionary<string, string> D = new Dictionary<string, string>
        {
            { "close", "M6 6L18 18M18 6L6 18" },
            { "check", "M5 12.5L10 17.5L19 7" },
            { "alert", "M12 3A9 9 0 1 1 11.99 3Z M12 7.5V12.5 M12 16.2V16.3" },
            { "info", "M12 3A9 9 0 1 1 11.99 3Z M12 11V16.5 M12 7.7V7.8" },
            { "link", "M7 17L17 7M9 7H17V15" },
            { "back", "M19 12H5M11 6L5 12L11 18" },
            { "trash", "M4 7H20M10 11V17M14 11V17M6 7L7 19A1 1 0 0 0 8 20H16A1 1 0 0 0 17 19L18 7M9 7V4H15V7" },
            { "play", "M8 5.5L18.5 12L8 18.5Z" },
            { "text", "M4 7H20M4 12H15M4 17H18" },
            { "wrap", "M4 6H20M4 12H15A3 3 0 0 1 15 18H11M13 16L11 18L13 20" },
            { "image", "M4 6A2 2 0 0 1 6 4H18A2 2 0 0 1 20 6V18A2 2 0 0 1 18 20H6A2 2 0 0 1 4 18Z M4 16L9 11L14 16L16 14L20 18 M15 8.5V8.6" },
            { "shield", "M12 3L19 6V11C19 15.5 16 19 12 21C8 19 5 15.5 5 11V6Z M9 12L11 14L15 10" },
            { "heart", "M12 20C12 20 4 15 4 9.5A4 4 0 0 1 12 8A4 4 0 0 1 20 9.5C20 15 12 20 12 20Z" },
            { "plane", "M21 4L3 10.8L9.8 13.4L12.5 20.5Z M9.8 13.4L21 4" },
            { "chat", "M5 5.5H19A2 2 0 0 1 21 7.5V15.5A2 2 0 0 1 19 17.5H11L7 21V17.5H5A2 2 0 0 1 3 15.5V7.5A2 2 0 0 1 5 5.5Z M9 11.5V11.6 M15 11.5V11.6" },
            { "youtube", "M3 8A3 3 0 0 1 6 5H18A3 3 0 0 1 21 8V16A3 3 0 0 1 18 19H6A3 3 0 0 1 3 16Z M10 9.5L15 12L10 14.5Z" },
            { "redo", "M20 12A8 8 0 1 1 17.7 6.3 M20 4V9H15" },
            { "lock", "M6 11H18V20H6Z M8.5 11V8A3.5 3.5 0 0 1 15.5 8V11" },
        };

        // Иконка 24-сетки, масштабируется Viewbox: линия остаётся 2/24 размера. Кисть общая — цвет меняется вместе с кнопкой.
        public static FrameworkElement Make(string name, double size, Brush color, bool fill = false)
        {
            if (name == "vk")
            {
                var t = Tk.TB("VK", size * 0.62, color, Tk.Display);
                t.HorizontalAlignment = HorizontalAlignment.Center; t.VerticalAlignment = VerticalAlignment.Center;
                var g = new Grid { Width = size, Height = size }; g.Children.Add(t); return g;
            }
            var p = new System.Windows.Shapes.Path
            {
                Data = Geometry.Parse(D[name]), Stroke = color, StrokeThickness = 2, StrokeLineJoin = PenLineJoin.Round,
                StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, Fill = fill ? color : null,
                SnapsToDevicePixels = false
            };
            var c = new Canvas { Width = 24, Height = 24 };
            c.Children.Add(p);
            return new Viewbox { Width = size, Height = size, Child = c, Stretch = Stretch.Uniform, UseLayoutRounding = false };
        }
    }

    // ===== Кнопка: инверсия (главная), поверхность (вторичная), опасная, ссылка-«чип» =====
    enum BK { Primary, Secondary, SecondaryDanger, Danger, Ghost, Chip }

    class Btn : Grid
    {
        public event Action Click;
        readonly Border body, ring;
        readonly SolidColorBrush bg, fg, bd, fg2;
        readonly BK kind;
        bool hover, down, mouseFocus, enabled = true;
        readonly ScaleTransform scale = new ScaleTransform(1, 1);

        public Btn(BK kind, string text, string icon = null, string sub = null, double height = 44, double minWidth = 0, bool iconOnly = false, string tip = null)
        {
            this.kind = kind;
            Focusable = true; FocusVisualStyle = null; Cursor = Cursors.Hand; MinWidth = minWidth; Height = height;
            HorizontalAlignment = HorizontalAlignment.Left; SnapsToDevicePixels = true;
            bg = new SolidColorBrush(BgColor(false, false)); fg = new SolidColorBrush(FgColor(false)); bd = new SolidColorBrush(BdColor(false));
            fg2 = new SolidColorBrush(kind == BK.Chip ? Tk.CMuted : FgColor(false));
            ring = new Border { Margin = new Thickness(-4), CornerRadius = new CornerRadius(height >= 40 ? 16 : 10), BorderBrush = Tk.Text, BorderThickness = new Thickness(2), Opacity = 0, IsHitTestVisible = false };
            body = new Border { Background = bg, BorderBrush = bd, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(height >= 40 ? 12 : 6), RenderTransform = scale, RenderTransformOrigin = new Point(0.5, 0.5) };
            var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(iconOnly ? 0 : (kind == BK.Chip ? 16 : 20), 0, iconOnly ? 0 : (kind == BK.Chip ? 20 : 20), 0) };
            if (icon != null)
            {
                var ic = Ico.Make(icon, iconOnly ? 20 : (kind == BK.Chip ? 20 : 18), fg, icon == "play");
                ic.VerticalAlignment = VerticalAlignment.Center; ic.Margin = new Thickness(0, 0, iconOnly || text == null ? 0 : (kind == BK.Chip ? 12 : 10), 0);
                row.Children.Add(ic);
            }
            if (text != null)
            {
                var col = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
                col.Children.Add(Tk.TB(text, 14, fg, Tk.Body));
                if (sub != null) { var s = Tk.TB(sub, 12, fg2, Tk.Body); s.Margin = new Thickness(0, 1, 0, 0); col.Children.Add(s); }
                row.Children.Add(col);
            }
            body.Child = row;
            Children.Add(ring); Children.Add(body);
            if (tip != null) { ToolTip = tip; }
            AutomationProperties.SetName(this, text ?? tip ?? "");

            MouseEnter += delegate { hover = true; Refresh(); };
            MouseLeave += delegate { hover = false; if (down) { down = false; } Refresh(); };
            MouseLeftButtonDown += delegate (object s, MouseButtonEventArgs e) { if (!enabled) return; mouseFocus = true; Focus(); CaptureMouse(); down = true; Refresh(); e.Handled = true; };
            MouseLeftButtonUp += delegate (object s, MouseButtonEventArgs e)
            {
                if (!down) return; down = false; ReleaseMouseCapture(); Refresh();
                if (IsMouseOver && enabled && Click != null) Click();
                e.Handled = true;
            };
            GotKeyboardFocus += delegate { Tk.DoubleTo(ring, OpacityProperty, mouseFocus ? 0 : 1, 120, Tk.Expo); mouseFocus = false; };
            LostKeyboardFocus += delegate { Tk.DoubleTo(ring, OpacityProperty, 0, 120, Tk.Expo); };
            KeyDown += delegate (object s, KeyEventArgs e) { if (enabled && (e.Key == Key.Space || e.Key == Key.Enter) && !e.IsRepeat) { down = true; Refresh(); e.Handled = true; } };
            KeyUp += delegate (object s, KeyEventArgs e)
            {
                if (down && (e.Key == Key.Space || e.Key == Key.Enter)) { down = false; Refresh(); if (enabled && Click != null) Click(); e.Handled = true; }
            };
        }

        public bool IsEnabledBtn { get { return enabled; } set { enabled = value; IsEnabled = value; Opacity = value ? 1 : 0.4; Cursor = value ? Cursors.Hand : Cursors.Arrow; } }

        protected override AutomationPeer OnCreateAutomationPeer() { return new BtnAutomationPeer(this); }

        internal void InvokeAccessible()
        {
            if (enabled && IsEnabled && IsVisible && Click != null) Click();
        }

        // Штатный API доступности Windows: тот же Click без мыши, клавиатуры и смены фокуса.
        sealed class BtnAutomationPeer : FrameworkElementAutomationPeer, IInvokeProvider
        {
            public BtnAutomationPeer(Btn owner) : base(owner) { }
            protected override string GetClassNameCore() { return "Button"; }
            protected override AutomationControlType GetAutomationControlTypeCore() { return AutomationControlType.Button; }
            public override object GetPattern(PatternInterface pattern) { return pattern == PatternInterface.Invoke ? this : base.GetPattern(pattern); }
            public void Invoke()
            {
                var button = (Btn)Owner;
                if (!button.IsEnabledBtn || !button.IsEnabled) throw new ElementNotEnabledException();
                button.Dispatcher.BeginInvoke(DispatcherPriority.Input, new Action(button.InvokeAccessible));
            }
        }

        Color BgColor(bool h, bool d)
        {
            switch (kind)
            {
                case BK.Primary: return d ? Tk.CPress : (h ? Tk.CWhite : Tk.CText);
                case BK.Danger: return d ? Tk.C("#D94A3F") : (h ? Tk.C("#FF776C") : Tk.CDanger);
                case BK.Ghost: return d ? Tk.CSurface3 : (h ? Tk.CSurface2 : Tk.CClear);
                default: return d ? Tk.CSurface3 : (h ? Tk.C("#222225") : Tk.CSurface2);
            }
        }
        Color FgColor(bool h)
        {
            switch (kind)
            {
                case BK.Primary: case BK.Danger: return Tk.CBg;
                case BK.SecondaryDanger: return Tk.CDanger;
                case BK.Ghost: return h ? Tk.CText : Tk.CTextSoft;
                default: return Tk.CText;
            }
        }
        Color BdColor(bool h)
        {
            if (kind == BK.Secondary || kind == BK.SecondaryDanger || kind == BK.Chip) return h ? Tk.CLineStrong : Tk.CLine;
            return Tk.CClear;
        }
        void Refresh()
        {
            Tk.ColorTo(bg, BgColor(hover && enabled, down), 140);
            Tk.ColorTo(fg, FgColor(hover && enabled), 140);
            Tk.ColorTo(bd, BdColor(hover && enabled), 140);
            double s = down ? 0.97 : 1;
            if (Tk.Anim)
            {
                var a = new DoubleAnimation(s, Tk.D(down ? 90 : 260)) { EasingFunction = down ? Tk.Expo : Tk.Spring };
                scale.BeginAnimation(ScaleTransform.ScaleXProperty, a); scale.BeginAnimation(ScaleTransform.ScaleYProperty, a);
            }
        }
    }

    // ===== Переключатель (flat toggle) =====
    class Toggle : Grid
    {
        public event Action Changed;
        readonly SolidColorBrush track, knobBrush, trackBorder;
        readonly Ellipse knob;
        readonly TranslateTransform tx = new TranslateTransform();
        readonly Border ring;
        bool on, locked, mouseFocus;
        public bool IsOn { get { return on; } set { on = value; Apply(false); } }

        public Toggle(bool isOn, bool locked, string name)
        {
            this.locked = locked; on = isOn;
            Width = 44; Height = 24; Focusable = !locked; FocusVisualStyle = null; Cursor = locked ? Cursors.Arrow : Cursors.Hand; VerticalAlignment = VerticalAlignment.Top;
            AutomationProperties.SetName(this, name);
            track = new SolidColorBrush(Tk.CSurface3); trackBorder = new SolidColorBrush(Tk.CLineStrong); knobBrush = new SolidColorBrush(Tk.CMuted);
            ring = new Border { Margin = new Thickness(-4), CornerRadius = new CornerRadius(16), BorderBrush = Tk.Text, BorderThickness = new Thickness(2), Opacity = 0, IsHitTestVisible = false };
            var tr = new Border { Background = track, BorderBrush = trackBorder, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(12) };
            knob = new Ellipse { Width = 16, Height = 16, Fill = knobBrush, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(4, 0, 0, 0), RenderTransform = tx, VerticalAlignment = VerticalAlignment.Center };
            Children.Add(ring); Children.Add(tr); Children.Add(knob);
            MouseLeftButtonDown += delegate (object s, MouseButtonEventArgs e) { if (locked) return; mouseFocus = true; Focus(); Flip(); e.Handled = true; };
            KeyDown += delegate (object s, KeyEventArgs e) { if (!locked && (e.Key == Key.Space || e.Key == Key.Enter)) { Flip(); e.Handled = true; } };
            GotKeyboardFocus += delegate { Tk.DoubleTo(ring, OpacityProperty, mouseFocus ? 0 : 1, 120, Tk.Expo); mouseFocus = false; };
            LostKeyboardFocus += delegate { Tk.DoubleTo(ring, OpacityProperty, 0, 120, Tk.Expo); };
            Apply(true);
        }
        void Flip() { on = !on; Apply(false); if (Changed != null) Changed(); }
        void Apply(bool instant)
        {
            Color t = on ? (locked ? Tk.CTextSoft : Tk.CText) : Tk.CSurface3;
            Color k = on ? Tk.CBg : Tk.CMuted;
            double x = on ? 20 : 0;
            if (instant) { tx.X = x; track.Color = t; knobBrush.Color = k; trackBorder.Color = on ? t : Tk.CLineStrong; return; }
            Tk.ColorTo(track, t, 160); Tk.ColorTo(knobBrush, k, 160); Tk.ColorTo(trackBorder, on ? t : Tk.CLineStrong, 160);
            if (Tk.Anim) tx.BeginAnimation(TranslateTransform.XProperty, new DoubleAnimation(x, Tk.D(280)) { EasingFunction = Tk.Spring }); else tx.X = x;
        }
    }

    // ===== Состояние для этапа 2 (см. PROTOCOL.md) =====
    class UiState
    {
        public string Screen = "welcome";   // welcome | progress | done | fail | installed | removed
        public double Percent;
        public int Step;                    // 1..5, 0 = нет
        public string StepState = "run";    // run | fail
        public string Detail = "";
        public string Stage = "";           // "" | close: помощник закрывает игру и лаунчер (подпись и пояснение этапа меняются)
        public string File = "";
        public string Eta = "";
        public string Op = "install";       // install | remove (что делает экран прогресса)
        public string State = "idle";       // idle | running | finished | failed
        public long DoneBytes, TotalBytes;  // объём текущего этапа; TotalBytes = 0: неизвестен, строку «X из Y» не показываем
        public bool LauncherAvailable = true;   // в сборке есть русский лаунчер (иначе блока «Русский лаунчер» нет)
        public bool CanRemove;              // на приветствии есть незавершённый след прежней установки
        public bool Interrupted;            // прошлая установка прервана: экран «Установка была прервана»
        public string FailKind = "install"; // install | remove
        public string Note = "";            // пояснение к успешной установке или удалению
        public bool LauncherOn;             // галочка «Русский лаунчер» / ставился ли лаунчер
        public bool LauncherInstalled;
        public string FailureCode = "";
        public string FailureMessage = "";
        public bool FailedBeforeWrite = true;
        public string TranslationVersion = "";
        public string ManagerVersion = "";
        public string GameVersion = "";
        public bool HasLauncherShortcut = true;
        public string LaunchState = "none", LaunchMessage = "";
        public bool UpdateEnabled;
        public string UpdateState = "none";  // none | checking | current | available | downloading | ready | handoff | failed
        public string UpdateVersion = "", UpdateMessage = "";
        public long UpdateDoneBytes, UpdateTotalBytes;
        public bool UpdateBusy { get { return UpdateState == "downloading" || UpdateState == "handoff"; } }
    }

    // ===== Протокол с Inno (PROTOCOL.md): разбор состояния, команды, белые списки =====
    static class Proto
    {
        // Единственные команды, которые окно вообще умеет посылать. Пути, адреса и произвольный текст в Inno не передаются.
        public static readonly string[] Cmds = { "install", "close", "remove", "launch", "options", "back", "pickgame", "checkupdate", "update" };
        public static readonly string[] Screens = { "welcome", "progress", "done", "fail", "installed", "removed" };
        public static readonly string[] UpdateStates = { "none", "checking", "current", "available", "downloading", "ready", "handoff", "failed" };
        const int MaxState = 65536;

        // Ссылки «Поддержать» и «Сообщество»: хост без www + путь целиком, https, без порта, логина, запроса и якоря. Регистр пути важен (код приглашения Discord).
        static readonly string[] Links = { "boosty.to/eg01stgames", "t.me/eg01stgame", "discord.gg/Zf69zsm5dQ", "vk.com/ego1stgames", "youtube.com/@egoist_games_one" };

        public static bool IsAllowedUrl(string url)
        {
            if (string.IsNullOrEmpty(url) || url.Length > 200) return false;
            foreach (char ch in url) if (ch <= ' ' || ch >= '') return false;   // Uri.TryCreate молча обрезает пробелы и переводы строк
            Uri u;
            if (!Uri.TryCreate(url, UriKind.Absolute, out u)) return false;
            if (u.Scheme != Uri.UriSchemeHttps || u.UserInfo.Length != 0 || u.Query.Length != 0 || u.Fragment.Length != 0 || !u.IsDefaultPort) return false;
            string host = u.Host.ToLowerInvariant();
            if (host.StartsWith("www.", StringComparison.Ordinal)) host = host.Substring(4);
            string path = u.AbsolutePath.TrimEnd('/');
            return Array.IndexOf(Links, host + path) >= 0;
        }

        // Открывается ссылка только из белого списка и только по клику. Через explorer.exe: браузер запускает обычная оболочка пользователя, а не окно установщика с правами администратора.
        public static void OpenUrl(string url)
        {
            if (!IsAllowedUrl(url)) return;
            try
            {
                string explorer = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe");
                Process.Start(new ProcessStartInfo(explorer, url) { UseShellExecute = false });
            }
            catch (Exception) { }
        }

        public static string SizeText(long done, long total)
        {
            var ru = new CultureInfo("ru-RU");
            if (total >= 1073741824L) return string.Format(ru, "{0:0.0} из {1:0.0} ГБ", done / 1073741824.0, total / 1073741824.0);
            return string.Format(ru, "{0:0} из {1:0} МБ", done / 1048576.0, total / 1048576.0);
        }

        // Строка команды: компактный JSON, одна строка, заканчивается переводом строки. Неизвестная команда не формируется вообще.
        public static string Cmd(int seq, string cmd, bool? launcher)
        {
            if (Array.IndexOf(Cmds, cmd) < 0) throw new ArgumentException("команда вне списка: " + cmd);
            var sb = new StringBuilder();
            sb.Append("{\"seq\":").Append(seq.ToString(CultureInfo.InvariantCulture)).Append(",\"cmd\":\"").Append(cmd).Append('"');
            if (launcher.HasValue && (cmd == "install" || cmd == "options")) sb.Append(",\"options\":{\"launcher\":").Append(launcher.Value ? "true" : "false").Append('}');
            sb.Append("}\n");
            return sb.ToString();
        }

        // Дописывает строку одним вызовом Write: читатель видит либо всю строку, либо её нет (Inno берёт только строки с переводом строки на конце).
        public static void Append(string path, string line)
        {
            byte[] data = new UTF8Encoding(false).GetBytes(line);
            // Если Inno в этот момент читает файл с запретом записи, строка не теряется: короткие повторы.
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    using (var f = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { f.Write(data, 0, data.Length); f.Flush(); }
                    return;
                }
                catch (IOException) { if (attempt >= 20) throw; System.Threading.Thread.Sleep(25); }
            }
        }

        public static string ReadShared(string path)
        {
            using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                if (f.Length > MaxState) return null;
                using (var r = new StreamReader(f, new UTF8Encoding(false), true)) return r.ReadToEnd();
            }
        }

        static string Str(Dictionary<string, object> d, string k, string def = "")
        {
            object o; if (!d.TryGetValue(k, out o) || o == null) return def;
            string s = Convert.ToString(o, CultureInfo.InvariantCulture) ?? def;
            return s.Length > 600 ? s.Substring(0, 600) : s;
        }
        static double Num(Dictionary<string, object> d, string k)
        {
            object o; if (!d.TryGetValue(k, out o) || o == null) return 0;
            double v; return double.TryParse(Convert.ToString(o, CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out v) && !double.IsNaN(v) && !double.IsInfinity(v) ? v : 0;
        }
        static bool Bool(Dictionary<string, object> d, string k, bool def = false)
        {
            object o; if (!d.TryGetValue(k, out o) || !(o is bool)) return def;
            return (bool)o;
        }

        // Строки обновления — только текст, без управляющих знаков; длинный текст остаётся в подсказке, а не меняет геометрию окна.
        static string Plain(Dictionary<string, object> d, string k, int limit)
        {
            object o; if (!d.TryGetValue(k, out o) || !(o is string)) return "";
            var sb = new StringBuilder(); bool space = false;
            foreach (char c in (string)o)
            {
                if (char.IsWhiteSpace(c)) { space = sb.Length > 0; continue; }
                if (char.IsControl(c) || char.GetUnicodeCategory(c) == UnicodeCategory.Format) continue;
                if (space && sb.Length < limit) sb.Append(' ');
                space = false;
                if (sb.Length >= limit) break;
                sb.Append(c);
            }
            return sb.ToString().TrimEnd();
        }

        static long Bytes(Dictionary<string, object> d, string k)
        {
            object o; if (!d.TryGetValue(k, out o) || o == null) return 0;
            long exact;
            if (long.TryParse(Convert.ToString(o, CultureInfo.InvariantCulture), NumberStyles.Integer, CultureInfo.InvariantCulture, out exact)) return Math.Max(0, exact);
            double value = Num(d, k);
            if (value <= 0) return 0;
            return value >= (double)long.MaxValue ? long.MaxValue : (long)value;
        }

        public static bool CanSend(UiState s, string cmd)
        {
            if (Array.IndexOf(Cmds, cmd) < 0 || s.State == "running" || s.Screen == "progress") return false;
            if (cmd == "close") return true;  // загрузку можно остановить закрытием установщика
            if (s.LaunchState == "starting") return false;
            if (s.UpdateBusy) return false;
            if (cmd == "checkupdate") return s.UpdateEnabled && s.UpdateState != "checking";
            if (cmd == "update") return s.UpdateEnabled && (s.UpdateState == "available" || s.UpdateState == "ready");
            return true;
        }

        // Разбор файла состояния. Оборванный, пустой, повреждённый файл, чужая структура, неизвестный экран, rev не больше прежнего: false, состояние не меняется.
        // screen == "quit" означает «Inno закончил, закрыться».
        public static bool TryApply(string text, UiState s, ref long lastRev, out string screen, out string sig)
        {
            screen = null; sig = null;
            if (string.IsNullOrEmpty(text) || text.Length > MaxState) return false;
            text = text.TrimStart('\uFEFF').Trim();
            if (text.Length < 2 || text[0] != '{' || text[text.Length - 1] != '}') return false;
            Dictionary<string, object> d;
            try { d = new JavaScriptSerializer { MaxJsonLength = MaxState, RecursionLimit = 8 }.DeserializeObject(text) as Dictionary<string, object>; }
            catch (Exception) { return false; }
            if (d == null || !d.ContainsKey("rev")) return false;
            double rev = Num(d, "rev");
            if (rev <= lastRev) return false;
            string scr = Str(d, "screen");
            if (scr != "quit" && Array.IndexOf(Screens, scr) < 0) return false;
            bool firstState = lastRev == 0;   // первое состояние: экран по умолчанию уже «welcome», но галочку надо взять из состояния (лаунчер по умолчанию включён)
            lastRev = (long)rev;
            screen = scr;
            if (scr == "quit") { sig = "quit"; return true; }
            bool intoNew = firstState || s.Screen != scr;
            s.Percent = Math.Max(0, Math.Min(100, Num(d, "percent")));
            s.Step = (int)Math.Max(0, Math.Min(6, Num(d, "step")));
            s.StepState = Str(d, "step_state") == "fail" ? "fail" : "run";
            s.Detail = Str(d, "detail"); s.File = Str(d, "file"); s.Eta = Str(d, "eta");
            s.Stage = Str(d, "stage") == "close" ? "close" : "";
            s.Op = Str(d, "op") == "remove" ? "remove" : "install";
            string st = Str(d, "state"); s.State = (st == "running" || st == "finished" || st == "failed") ? st : "idle";
            s.DoneBytes = (long)Math.Max(0, Num(d, "done_bytes")); s.TotalBytes = (long)Math.Max(0, Num(d, "total_bytes"));
            if (s.DoneBytes > s.TotalBytes) s.DoneBytes = s.TotalBytes;
            s.LauncherAvailable = Bool(d, "launcher_available", true);
            s.LauncherInstalled = Bool(d, "launcher_installed");
            s.HasLauncherShortcut = Bool(d, "has_launcher_shortcut", true);
            string launchState = Plain(d, "launch_state", 20);
            s.LaunchState = launchState == "starting" || launchState == "failed" ? launchState : "none";
            s.LaunchMessage = Plain(d, "launch_message", 280);
            s.FailureCode = Str(d, "failure_code"); s.FailureMessage = Str(d, "failure_message");
            s.FailedBeforeWrite = Bool(d, "failed_before_write", true);
            s.FailKind = Str(d, "fail_kind") == "remove" ? "remove" : "install";
            s.Note = Str(d, "note"); s.CanRemove = Bool(d, "can_remove"); s.Interrupted = Bool(d, "interrupted");
            s.TranslationVersion = Str(d, "translation_version"); s.GameVersion = Str(d, "game_version");
            s.ManagerVersion = Plain(d, "manager_version", 80);
            s.UpdateEnabled = Bool(d, "update_enabled");
            string updateState = Plain(d, "update_state", 20);
            s.UpdateState = Array.IndexOf(UpdateStates, updateState) >= 0 ? updateState : "none";
            s.UpdateVersion = Plain(d, "update_version", 80); s.UpdateMessage = Plain(d, "update_message", 280);
            s.UpdateDoneBytes = Bytes(d, "update_done_bytes"); s.UpdateTotalBytes = Bytes(d, "update_total_bytes");
            if (s.UpdateTotalBytes > 0 && s.UpdateDoneBytes > s.UpdateTotalBytes) s.UpdateDoneBytes = s.UpdateTotalBytes;
            // Галочка «Русский лаунчер» принадлежит окну: из состояния она берётся только при входе на экран, чтобы клик не перерисовывал экран.
            if (intoNew) s.LauncherOn = Bool(d, "launcher_on");
            sig = string.Join("|", scr, s.CanRemove, s.Interrupted, s.FailureMessage, s.FailKind, s.Note, s.LauncherAvailable, s.LauncherInstalled,
                s.HasLauncherShortcut, s.TranslationVersion, s.GameVersion, s.FailedBeforeWrite, s.Op, s.LaunchState, s.LaunchMessage);
            return true;
        }
    }

    // ===== Кадр окна: шапка + содержимое экрана =====
    class MainView : Grid
    {
        public const double W = 880, H = 640, HeaderH = 128, FooterH = 104, ProgressFooterH = 168, Pad = 40;
        public event Action<string> Command;
        public UiState S = new UiState();
        readonly Grid host = new Grid();
        readonly Grid updateHost = new Grid();
        readonly Dictionary<Btn, bool> operationButtons = new Dictionary<Btn, bool>();
        bool updatePending, checkPending, launchPending;
        string commandError = "", retryCommand = "";
        string updateSig;
        string current;
        // элементы экрана прогресса
        TextBlock pctText, etaText, fileText, detailText, infoLead, infoMore;
        Border fillBar; double dispPct, targetPct; DispatcherTimer pctTimer;
        Grid[] markers; TextBlock[] stepTitles, stepDetails; Rectangle[] links;
        Toggle launcherToggle;

        public MainView()
        {
            Width = W; Height = H; Background = Tk.Bg; ClipToBounds = true; UseLayoutRounding = true; SnapsToDevicePixels = true;
            TextOptions.SetTextFormattingMode(this, TextFormattingMode.Ideal);
            RenderOptions.SetBitmapScalingMode(this, BitmapScalingMode.HighQuality);
            RowDefinitions.Add(new RowDefinition { Height = new GridLength(HeaderH) });
            RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            var header = BuildHeader(); SetRow(header, 0); Children.Add(header);
            SetRow(host, 1); Children.Add(host);
            // тонкая рамка окна: подстраховка на Win10 без DWM-скруглений
            Children.Add(new Border { BorderBrush = Tk.Line, BorderThickness = new Thickness(1), IsHitTestVisible = false, CornerRadius = new CornerRadius(0) });
            Grid.SetRowSpan(Children[Children.Count - 1], 2);
        }

        public Border Draggable;   // шапка: за неё таскаем окно

        static BitmapImage Img(string res)
        {
            var asm = Assembly.GetExecutingAssembly();
            var bi = new BitmapImage();
            using (var s = asm.GetManifestResourceStream("assets." + res))
            {
                var ms = new MemoryStream(); s.CopyTo(ms); ms.Position = 0;
                bi.BeginInit(); bi.CacheOption = BitmapCacheOption.OnLoad; bi.StreamSource = ms; bi.EndInit(); bi.Freeze();
            }
            return bi;
        }

        FrameworkElement BuildHeader()
        {
            var g = new Grid { Height = HeaderH };
            g.Children.Add(new Image { Source = Img("header.png"), Stretch = Stretch.Fill, SnapsToDevicePixels = true });
            var mark = new Image { Source = Img("mark.png"), Width = 56, Height = 56, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(Pad, 0, 0, 0), Stretch = Stretch.Uniform };
            g.Children.Add(mark);
            var col = new StackPanel { HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(Pad + 56 + 20, 0, 0, 0) };
            var eyebrow = Tk.TB("EGOIST · РУСИФИКАТОР", 10.5, Tk.Gold, Tk.Display);
            col.Children.Add(eyebrow);
            var title = Tk.TB("Honor of Kings ACE", 26, Tk.Text, Tk.Display); title.Margin = new Thickness(0, 8, 0, 0);
            col.Children.Add(title);
            g.Children.Add(col);
            var close = new Btn(BK.Ghost, null, "close", null, 40, 40, true, "Закрыть");
            close.HorizontalAlignment = HorizontalAlignment.Right; close.VerticalAlignment = VerticalAlignment.Top; close.Margin = new Thickness(0, 16, 16, 0);
            close.Click += delegate { Fire("close"); };
            g.Children.Add(close);
            updateHost.Width = 268; updateHost.Height = 100;
            updateHost.HorizontalAlignment = HorizontalAlignment.Right; updateHost.VerticalAlignment = VerticalAlignment.Top;
            updateHost.Margin = new Thickness(0, 14, 64, 0); updateHost.Visibility = Visibility.Collapsed;
            g.Children.Add(updateHost);
            Draggable = new Border { Background = Tk.Clear, Child = g };
            return Draggable;
        }

        public void RaiseClose() { Fire("close"); }
        public void Do(string cmd) { Fire(cmd); }
        void Fire(string cmd)
        {
            if (!Proto.CanSend(S, cmd) || ((updatePending || launchPending) && cmd != "close") || (checkPending && cmd == "checkupdate")) return;
            commandError = retryCommand = "";
            if (cmd == "update") { updatePending = true; UpdateUpdates(false); }
            if (cmd == "checkupdate") { checkPending = true; UpdateUpdates(false); }
            if (cmd == "launch") { launchPending = true; UpdateUpdates(false); }
            if (Command != null) Command(cmd);
        }

        public void AcceptUpdateState()
        {
            if (S.LaunchState == "starting" || S.LaunchState == "failed") launchPending = false;
            if (S.UpdateBusy || S.UpdateState == "failed" || S.UpdateState == "current" || S.UpdateState == "available") updatePending = false;
            if (S.UpdateState != "none") checkPending = false;
            UpdateUpdates(false);
        }

        public void UpdateCommandFailed()
        {
            updatePending = checkPending = false;
            S.UpdateState = "failed"; S.UpdateMessage = "Не удалось передать команду установщику. Повторите проверку.";
            UpdateUpdates(false);
        }

        public void CommandFailed(string cmd)
        {
            if (cmd == "launch") launchPending = false;
            if (cmd == "update" || cmd == "checkupdate") { UpdateCommandFailed(); return; }
            commandError = "Не удалось передать действие установщику. Нажмите ещё раз.";
            retryCommand = cmd;
            UpdateUpdates(false);
        }

        static TextBlock UpdateText(string text, double size, Brush color)
        {
            var t = Tk.TB(text, size, color, Tk.Body, 17);
            t.TextTrimming = TextTrimming.CharacterEllipsis;
            AutomationProperties.SetName(t, text);
            return t;
        }

        public void UpdateUpdates(bool animate)
        {
            bool busy = S.UpdateBusy || updatePending || launchPending || S.LaunchState == "starting";
            foreach (var item in operationButtons) item.Key.IsEnabledBtn = item.Value && !busy;
            if (launcherToggle != null) launcherToggle.IsEnabled = !busy;
            updateHost.Visibility = S.UpdateEnabled || commandError.Length > 0 ? Visibility.Visible : Visibility.Collapsed;
            string st = commandError.Length > 0 ? "command_failed" : updatePending ? "pending" : checkPending ? "checking" : S.UpdateState;
            string nextSig = string.Join("|", st, S.UpdateVersion, S.UpdateMessage, S.UpdateDoneBytes, S.UpdateTotalBytes, S.UpdateEnabled, S.State, S.Screen);
            if (nextSig == updateSig) return;
            bool entering = updateSig != null && (st == "available" || st == "ready") && !updateSig.StartsWith(st + "|", StringComparison.Ordinal);
            updateSig = nextSig;
            updateHost.Children.Clear();
            if (!S.UpdateEnabled && commandError.Length == 0) return;
            var content = new Grid();
            content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(17) });
            content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(4) });
            content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(17) });
            content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(8) });
            content.RowDefinitions.Add(new RowDefinition { Height = new GridLength(30) });
            string headline = "Обновления перевода", sub = "Проверка при каждом запуске";
            Brush accent = Tk.Text;
            switch (st)
            {
                case "checking": headline = "Проверяю обновления…"; sub = "Можно продолжать установку"; break;
                case "current": headline = "У вас актуальная версия"; sub = string.IsNullOrEmpty(S.UpdateVersion) ? "Проверено при запуске" : "Версия " + S.UpdateVersion; accent = Tk.Success; break;
                case "available": headline = "Доступно обновление"; sub = string.IsNullOrEmpty(S.UpdateVersion) ? "Новая версия перевода" : "Версия " + S.UpdateVersion; accent = Tk.Gold; break;
                case "ready": headline = "Обновление готово"; sub = string.IsNullOrEmpty(S.UpdateVersion) ? "Можно применить обновление" : "Версия " + S.UpdateVersion; accent = Tk.Gold; break;
                case "pending": headline = "Подготовка обновления…"; sub = string.IsNullOrEmpty(S.UpdateVersion) ? "Ожидаю ответ установщика" : "Версия " + S.UpdateVersion; accent = Tk.Gold; break;
                case "downloading": headline = "Скачиваю обновление…"; sub = string.IsNullOrEmpty(S.UpdateVersion) ? "Установщик можно закрыть" : "Версия " + S.UpdateVersion; accent = Tk.Gold; break;
                case "handoff": headline = "Применяю обновление…"; sub = string.IsNullOrEmpty(S.UpdateVersion) ? "Новая версия уже скачана" : "Версия " + S.UpdateVersion; accent = Tk.Gold; break;
                case "failed": headline = "Не удалось получить обновление"; sub = string.IsNullOrEmpty(S.UpdateMessage) ? "Проверьте подключение к интернету" : S.UpdateMessage; break;
                case "command_failed": headline = "Действие не началось"; sub = commandError; accent = Tk.Danger; break;
            }
            var title = UpdateText(headline, 12, accent); content.Children.Add(title);
            var message = UpdateText(sub, 11.5, Tk.Muted); SetRow(message, 2); content.Children.Add(message);
            string full = headline + (string.IsNullOrEmpty(S.UpdateVersion) ? "" : "\nВерсия " + S.UpdateVersion) + (string.IsNullOrEmpty(S.UpdateMessage) ? "" : "\n" + S.UpdateMessage);
            content.ToolTip = full; AutomationProperties.SetName(content, full);
            FrameworkElement actions;
            if (st == "downloading" || st == "pending")
            {
                var progress = new Grid();
                string size = st == "pending" ? "Подготавливаю загрузку" : S.UpdateTotalBytes > 0 ? Proto.SizeText(S.UpdateDoneBytes, S.UpdateTotalBytes) : "Размер загрузки уточняется";
                var status = UpdateText(size, 11.5, Tk.TextSoft); status.VerticalAlignment = VerticalAlignment.Top; progress.Children.Add(status);
                var track = new Border { Height = 3, Background = Tk.Surface3, CornerRadius = new CornerRadius(1.5), VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(0, 0, 0, 2) };
                double fraction = st == "downloading" && S.UpdateTotalBytes > 0 ? Math.Max(0, Math.Min(1, (double)S.UpdateDoneBytes / S.UpdateTotalBytes)) : 0.16;
                track.Child = new Border { Height = 3, Width = 246 * fraction, Background = Tk.Gold, CornerRadius = new CornerRadius(1.5), HorizontalAlignment = HorizontalAlignment.Left };
                progress.Children.Add(track); actions = progress;
            }
            else if (st == "handoff")
            {
                var wait = UpdateText("Установщик перезапустится…", 11.5, Tk.TextSoft); wait.VerticalAlignment = VerticalAlignment.Center; actions = wait;
            }
            else
            {
                bool canUpdate = st == "available" || st == "ready";
                string label = st == "command_failed" ? "Повторить" : canUpdate ? "Обновить" : st == "failed" ? "Проверить ещё раз" : st == "checking" ? "Проверяю…" : "Проверить";
                var button = new Btn(canUpdate ? BK.Primary : BK.Secondary, label, canUpdate ? null : "redo", null, 30, canUpdate ? 116 : 0);
                string cmd = st == "command_failed" ? retryCommand : canUpdate ? "update" : "checkupdate";
                button.IsEnabledBtn = !checkPending && !updatePending && Proto.CanSend(S, cmd);
                button.Click += delegate { Fire(cmd); }; actions = button;
                if (canUpdate)
                {
                    var row = new Grid();
                    row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                    row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                    row.Children.Add(button);
                    var recheck = new Btn(BK.Ghost, null, "redo", null, 30, 30, true, "Проверить обновления");
                    recheck.IsEnabledBtn = !checkPending && !updatePending && Proto.CanSend(S, "checkupdate");
                    recheck.Click += delegate { Fire("checkupdate"); }; SetColumn(recheck, 1); row.Children.Add(recheck); actions = row;
                }
            }
            SetRow(actions, 4); content.Children.Add(actions);
            var card = new Border { Background = Tk.Surface1, BorderBrush = st == "available" || st == "ready" ? Tk.Gold : Tk.Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(12), Padding = new Thickness(10), Child = content };
            updateHost.Children.Add(card);
            if ((animate || entering) && Tk.Anim)
            {
                var shift = new TranslateTransform(10, 0); card.RenderTransform = shift;
                card.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, Tk.D(200)));
                shift.BeginAnimation(TranslateTransform.XProperty, new DoubleAnimation(10, 0, Tk.D(320)) { EasingFunction = Tk.Expo });
            }
        }

        // ----- каркас экрана: тело + подвал -----
        Grid Frame(out Grid body, out Grid footer, double footerH = FooterH)
        {
            var root = new Grid();
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(footerH) });
            body = new Grid { Margin = new Thickness(Pad, 28, Pad, 8) };
            footer = new Grid { Margin = new Thickness(Pad, 0, Pad, 0) };
            SetRow(footer, 1);
            root.Children.Add(body); root.Children.Add(footer);
            return root;
        }

        static StackPanel Stack(double gap = 0) { return new StackPanel { }; }

        // ----- общие блоки -----
        FrameworkElement SupportBlock()
        {
            var sp = new StackPanel { VerticalAlignment = VerticalAlignment.Bottom, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 24) };
            sp.Children.Add(Tk.TB("Поддержать разработку", 12, Tk.Muted));
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 8, 0, 0) };
            var b = new Btn(BK.Chip, "Boosty", "heart", "Поддержать", 48); b.Click += delegate { Open("https://boosty.to/eg01stgames"); };
            var t = new Btn(BK.Chip, "Telegram", "plane", "Новости и обновления", 48); t.Margin = new Thickness(8, 0, 0, 0); t.Click += delegate { Open("https://t.me/eg01stgame"); };
            row.Children.Add(b); row.Children.Add(t); sp.Children.Add(row);
            return sp;
        }

        static void Open(string url) { Proto.OpenUrl(url); }

#if TESTBUILD
        public void PilotToggleLauncher()
        {
            if (launcherToggle == null) return;
            launcherToggle.IsOn = !launcherToggle.IsOn; S.LauncherOn = launcherToggle.IsOn; Fire("options");
        }
#endif

        FrameworkElement Buttons(params FrameworkElement[] items)
        {
            var sp = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(0, 0, 0, 26) };
            for (int i = 0; i < items.Length; i++)
            {
                if (i > 0) items[i].Margin = new Thickness(8, 0, 0, 0);
                var button = items[i] as Btn;
                if (button != null && AutomationProperties.GetName(button) != "Закрыть") operationButtons[button] = button.IsEnabledBtn;
                sp.Children.Add(items[i]);
            }
            return sp;
        }

        FrameworkElement Title(string text, string lede, double width)
        {
            var sp = new StackPanel { Width = width, HorizontalAlignment = HorizontalAlignment.Left };
            var t = Tk.TB(text, 22, Tk.Text, Tk.Display, 28, TextWrapping.Wrap);
            sp.Children.Add(t);
            if (!string.IsNullOrEmpty(lede)) { var l = Tk.TB(lede, 14, Tk.TextSoft, Tk.Body, 21, TextWrapping.Wrap); l.Margin = new Thickness(0, 12, 0, 0); sp.Children.Add(l); }
            return sp;
        }

        FrameworkElement ItemRow(string icon, string title, string sub)
        {
            var g = new Grid { Margin = new Thickness(0, 0, 0, 12) };
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(40) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var chip = new Border { Width = 32, Height = 32, CornerRadius = new CornerRadius(8), Background = Tk.Surface2, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Child = Ico.Make(icon, 18, Tk.Text) };
            ((FrameworkElement)chip.Child).HorizontalAlignment = HorizontalAlignment.Center; ((FrameworkElement)chip.Child).VerticalAlignment = VerticalAlignment.Center;
            g.Children.Add(chip);
            var sp = new StackPanel { VerticalAlignment = VerticalAlignment.Center }; SetColumn(sp, 1);
            sp.Children.Add(Tk.TB(title, 14, Tk.Text));
            if (sub != null) { var s = Tk.TB(sub, 12.5, Tk.Muted, Tk.Body, 17, TextWrapping.Wrap); s.Margin = new Thickness(0, 2, 0, 0); sp.Children.Add(s); }
            g.Children.Add(sp);
            return g;
        }

        Border Card(UIElement child, double pad = 24)
        {
            return new Border { Background = Tk.Surface1, BorderBrush = Tk.Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(20), Padding = new Thickness(pad), Child = child };
        }

        // ================= Экран 1: приветствие =================
        public const string UpdateLimit = "После обновления игры первый запуск будет на китайском. Дойдите до главного меню, закройте игру и откройте русификатор для переустановки перевода.";
        FrameworkElement Welcome()
        {
            Grid body, footer; var root = Frame(out body, out footer);
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(352) });
            var left = new StackPanel();
            left.Children.Add(Title("Установка перевода", "Игра заговорит по-русски. Интернет не нужен, перед записью делается резервная копия.", 420));
            var list = new StackPanel { Margin = new Thickness(0, 16, 0, 0) };
            var lab = Tk.TB("Что ставится", 12, Tk.Muted); lab.Margin = new Thickness(0, 0, 0, 10); list.Children.Add(lab);
            list.Children.Add(ItemRow("text", "Тексты игры", null));
            list.Children.Add(ItemRow("image", "Картинки интерфейса", "Кнопки, ранги, вывеска «Новости»"));
            left.Children.Add(list);
            // После обновления игры перевод восстанавливается через установщик, без фонового процесса.
            var upd = new Grid { Margin = new Thickness(0, 14, 0, 0), Width = 424, HorizontalAlignment = HorizontalAlignment.Left };
            upd.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(28) });
            upd.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var ui2 = Ico.Make("info", 18, Tk.Gold); ui2.HorizontalAlignment = HorizontalAlignment.Left; ui2.VerticalAlignment = VerticalAlignment.Top; ui2.Margin = new Thickness(0, 1, 0, 0);
            upd.Children.Add(ui2);
            var ut = new StackPanel(); SetColumn(ut, 1);
            ut.Children.Add(Tk.TB("Как работает обновление", 12.5, Tk.Text, Tk.Body, 18, TextWrapping.Wrap));
            var ub = Tk.TB(UpdateLimit, 12, Tk.Muted, Tk.Body, 17, TextWrapping.Wrap); ub.Margin = new Thickness(0, 3, 0, 0); ut.Children.Add(ub);
            upd.Children.Add(ut);
            left.Children.Add(upd);
            body.Children.Add(left);

            var card = new StackPanel();
            card.Children.Add(Tk.TB("Компоненты", 12, Tk.Muted));
            card.Children.Add(Component(new Toggle(true, true, "Текст интерфейса и графика"), "Текст интерфейса и графика", "Обязательный компонент", null, 16));
            if (S.LauncherAvailable)
            {
                launcherToggle = new Toggle(S.LauncherOn, false, "Русский лаунчер");
                launcherToggle.Changed += delegate { S.LauncherOn = launcherToggle.IsOn; Fire("options"); };
                card.Children.Add(Component(launcherToggle, "Русский лаунчер", "Включён по умолчанию; с защитой ACE в игре не проверен", "новое", 16));
            }
            var sep = new Border { Height = 1, Background = Tk.Line, Margin = new Thickness(0, 18, 0, 16) }; card.Children.Add(sep);
            var note = new Grid();
            note.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(28) });
            note.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var ni = Ico.Make("info", 18, Tk.Muted); ni.HorizontalAlignment = HorizontalAlignment.Left; ni.VerticalAlignment = VerticalAlignment.Top; ni.Margin = new Thickness(0, 1, 0, 0);
            note.Children.Add(ni);
            var nt = Tk.TB("Если игра или лаунчер запущены, установщик закроет их сам, в том числе значок в области уведомлений, и назовёт, что закрыл. Лучше закрыть их заранее. Нужны права администратора: Windows спросит разрешение.", 12.5, Tk.TextSoft, Tk.Body, 18, TextWrapping.Wrap); SetColumn(nt, 1);
            note.Children.Add(nt); card.Children.Add(note);
            var c = Card(card, 24); SetColumn(c, 2); c.VerticalAlignment = VerticalAlignment.Top; body.Children.Add(c);

            footer.Children.Add(SupportBlock());
            var close = new Btn(BK.Secondary, "Закрыть", null, null, 44, 112); close.Click += delegate { Fire("close"); };
            var inst = new Btn(BK.Primary, "Установить", null, null, 44, 148); inst.Click += delegate { Fire("install"); };
            if (!confirmRemove)
            {
                if (S.CanRemove)
                {
                    var rm = new Btn(BK.SecondaryDanger, "Удалить перевод", "trash", null, 44, 188); rm.Click += delegate { confirmRemove = true; Show("welcome"); };
                    footer.Children.Add(Buttons(close, rm, inst));
                }
                else footer.Children.Add(Buttons(close, inst));
            }
            else ConfirmFooter(body, footer, "welcome");
            return root;
        }

        // Подтверждение удаления целиком внутри окна: тело затемняется, подвал заменяется вопросом. Команда remove уходит только после «Удалить».
        void ConfirmFooter(Grid body, Grid footer, string back)
        {
            body.Opacity = 0.45; body.IsHitTestVisible = false;
            var t = new Grid();
            t.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            t.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var q = new StackPanel { VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(0, 0, 0, 25) };
            var qrow = new StackPanel { Orientation = Orientation.Horizontal };
            var ai = Ico.Make("alert", 20, Tk.Danger); ai.Margin = new Thickness(0, 0, 10, 0); qrow.Children.Add(ai);
            qrow.Children.Add(Tk.TB("Удалить перевод?", 15, Tk.Text));
            q.Children.Add(qrow);
            var qs = Tk.TB("Вернётся состояние файлов до установки перевода. Запущенные игру и лаунчер установщик закроет сам.", 12.5, Tk.Muted, Tk.Body, 18); qs.Margin = new Thickness(30, 4, 0, 0); q.Children.Add(qs);
            t.Children.Add(q);
            var no = new Btn(BK.Secondary, "Отмена", null, null, 44, 112); no.Click += delegate { confirmRemove = false; Show(back); };
            var yes = new Btn(BK.Danger, "Удалить", "trash", null, 44, 148); yes.Click += delegate { confirmRemove = false; Fire("remove"); };
            var bs = Buttons(no, yes); SetColumn(bs, 1); t.Children.Add(bs);
            footer.Children.Add(t);
        }

        FrameworkElement Component(Toggle t, string title, string sub, string badge, double bottom)
        {
            var g = new Grid { Margin = new Thickness(0, 16, 0, 0) };
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(56) });
            var sp = new StackPanel();
            var head = new StackPanel { Orientation = Orientation.Horizontal };
            head.Children.Add(Tk.TB(title, 14, Tk.Text));
            if (badge != null)
            {
                var pill = new Border { CornerRadius = new CornerRadius(999), Background = Tk.Surface3, Padding = new Thickness(8, 2, 8, 3), Margin = new Thickness(8, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center, Child = Tk.TB(badge, 11, Tk.TextSoft, Tk.Body, 15) };
                head.Children.Add(pill);
            }
            sp.Children.Add(head);
            var s = Tk.TB(sub, 12.5, Tk.Muted, Tk.Body, 17, TextWrapping.Wrap); s.Margin = new Thickness(0, 6, 0, 0); sp.Children.Add(s);
            g.Children.Add(sp);
            t.HorizontalAlignment = HorizontalAlignment.Right; SetColumn(t, 1); g.Children.Add(t);
            return g;
        }

        // ================= Экран 2: ход установки =================
        static readonly string[] StepNames = { "Поиск игры", "Проверка версии игры", "Резервная копия", "Запись перевода", "Проверка результата" };
        static readonly double[] StepWeights = { 0.03, 0.10, 0.20, 0.45, 0.22 };

        FrameworkElement Progress()
        {
            Grid body, footer; var root = Frame(out body, out footer, ProgressFooterH);
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(48) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(340) });
            var left = new StackPanel();
            bool removing = S.Op == "remove";
            left.Children.Add(Tk.TB(removing ? "Восстанавливаю файлы" : "Устанавливаю перевод", 22, Tk.Text, Tk.Display, 28));
            var pctRow = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 14, 0, 0) };
            pctText = Tk.TB("0", 88, Tk.Text, Tk.Body, 96);
            Typography.SetNumeralAlignment(pctText, FontNumeralAlignment.Tabular);
            var sign = Tk.TB("%", 36, Tk.Muted, Tk.Body, 96); sign.Margin = new Thickness(4, 0, 0, 0); sign.VerticalAlignment = VerticalAlignment.Bottom; sign.Padding = new Thickness(0, 0, 0, 0);
            pctRow.Children.Add(pctText); pctRow.Children.Add(sign);
            left.Children.Add(pctRow);
            var track = new Border { Height = 4, CornerRadius = new CornerRadius(2), Background = Tk.Surface3, Margin = new Thickness(0, 14, 0, 0), HorizontalAlignment = HorizontalAlignment.Stretch };
            fillBar = new Border { Height = 4, CornerRadius = new CornerRadius(2), Background = Tk.Gold, HorizontalAlignment = HorizontalAlignment.Left, Width = 0 };
            var trackG = new Grid { Height = 4, Margin = new Thickness(0, 14, 0, 0) };
            track.Margin = new Thickness(0); trackG.Children.Add(track); trackG.Children.Add(fillBar);
            left.Children.Add(trackG);
            etaText = Tk.TB("", 14, Tk.TextSoft); etaText.Margin = new Thickness(0, 16, 0, 0); Typography.SetNumeralAlignment(etaText, FontNumeralAlignment.Tabular);
            left.Children.Add(etaText);
            fileText = Tk.TB("", 12.5, Tk.Muted, Tk.Body, 17, TextWrapping.Wrap); fileText.Margin = new Thickness(0, 6, 0, 0); fileText.TextTrimming = TextTrimming.CharacterEllipsis; fileText.MaxHeight = 36;
            left.Children.Add(fileText);
            body.Children.Add(left);

            // шаги: маркеры 24 px, соединительная линия, подпись
            markers = null;
            if (removing)
            {
                var rn = Tk.TB("Установщик возвращает файлы игры по резервным копиям. Если игра или лаунчер запущены, он закроет их сам и скажет об этом.", 14, Tk.TextSoft, Tk.Body, 21, TextWrapping.Wrap);
                rn.Margin = new Thickness(0, 8, 0, 0); SetColumn(rn, 2); body.Children.Add(rn);
            }
            else
            {
            var steps = new Grid { VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 6, 0, 0) };
            steps.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(40) });
            steps.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            markers = new Grid[5]; stepTitles = new TextBlock[5]; stepDetails = new TextBlock[5]; links = new Rectangle[5];
            for (int i = 0; i < 5; i++)
            {
                steps.RowDefinitions.Add(new RowDefinition { Height = new GridLength(54) });
                if (i < 4)
                {
                    links[i] = new Rectangle { Width = 2, Fill = Tk.Surface3, HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(11, 30, 0, 6), RadiusX = 1, RadiusY = 1 };
                    SetRow(links[i], i); steps.Children.Add(links[i]);
                }
                markers[i] = new Grid { Width = 24, Height = 24, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 2, 0, 0) };
                SetRow(markers[i], i); steps.Children.Add(markers[i]);
                var sp = new StackPanel { VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 2, 0, 0) }; SetColumn(sp, 1); SetRow(sp, i);
                stepTitles[i] = Tk.TB(StepNames[i], 14.5, Tk.Muted, Tk.Body, 20);
                stepDetails[i] = Tk.TB("", 12.5, Tk.Muted, Tk.Body, 17); stepDetails[i].TextTrimming = TextTrimming.CharacterEllipsis; stepDetails[i].Margin = new Thickness(0, 1, 0, 0);
                sp.Children.Add(stepTitles[i]); sp.Children.Add(stepDetails[i]);
                steps.Children.Add(sp);
            }
            SetColumn(steps, 2); body.Children.Add(steps);
            }

            // Пояснение этапа под шагами: суть в 1-2 предложениях + абзац «Подробнее» (что делается, как, зачем, что может пойти не так). Тексты - в StageInfo.
            var info = new StackPanel { VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 4, 0, 0) };
            info.Children.Add(new Border { Height = 1, Background = Tk.Line, Margin = new Thickness(0, 0, 0, 14) });
            infoLead = Tk.TB("", 14.5, Tk.Text, Tk.Body, 21, TextWrapping.Wrap);
            infoMore = Tk.TB("", 13, Tk.TextSoft, Tk.Body, 19, TextWrapping.Wrap); infoMore.Margin = new Thickness(0, 6, 0, 0);
            info.Children.Add(infoLead); info.Children.Add(infoMore);
            footer.Children.Add(info);
            if (pctTimer != null) pctTimer.Stop();
            pctTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(16) };
            pctTimer.Tick += delegate
            {
                dispPct += (targetPct - dispPct) * 0.16;
                if (Math.Abs(targetPct - dispPct) < 0.05) dispPct = targetPct;
                pctText.Text = ((int)Math.Round(dispPct)).ToString(CultureInfo.InvariantCulture);
            };
            pctTimer.Start();
            UpdateProgress(true);
            return root;
        }

        // Пояснение этапа: суть (что происходит) и «Подробнее» (как, зачем, что может пойти не так и что делать). Русский язык без жаргона.
        public static void StageInfo(UiState s, out string lead, out string more)
        {
            if (s.Stage == "close")
            {
                lead = "Закрываю игру и лаунчер: пока они запущены, их файлы нельзя менять безопасно.";
                more = "Если идёт партия, она прервётся. Файлы игры не повреждаются: после установки запустите игру снова. Закрываются только лаунчер, игра и их зависшие процессы; сначала окна закрываются обычным способом, затем принудительно. Если процесс не закроется, установщик назовёт его: завершите его в диспетчере задач и повторите.";
                return;
            }
            if (s.Op == "remove")
            {
                lead = "Восстанавливаю файлы игры и убираю русский лаунчер.";
                more = "Сохранённые перед установкой файлы возвращаются на место. Русификатор остаётся доступным для повторной установки и обновлений; удалить приложение целиком можно через настройки Windows. Если откат прервётся, нажмите «Удалить перевод» ещё раз: он продолжится.";
                return;
            }
            switch (s.Step)
            {
                case 1:
                    lead = "Ищу, где на компьютере стоит игра.";
                    more = "Установщик смотрит папки лаунчера и ярлыки и находит нужную копию Honor of Kings ACE. В игре при этом ничего не меняется. Если папка не найдётся или их окажется несколько, окно предложит выбрать её вручную.";
                    return;
                case 2:
                    lead = "Проверяю, что перевод подходит к вашей версии игры.";
                    more = "Сверяю версию и контрольные суммы файлов игры с теми, для которых собран перевод, и при необходимости закрываю игру и лаунчер. Если игра обновилась, установщик остановится, ничего не тронув: запустите игру один раз до главного меню, закройте её и повторите.";
                    return;
                case 3:
                    lead = "Сохраняю копии файлов игры, которые будут заменены.";
                    more = "Копии лежат в папке данных русификатора (ProgramData\\Egoist\\WXQ-Russian\\backup) вместе с отметкой о каждом файле. По ним «Удалить перевод» восстановит состояние до установки перевода, даже если установка прервётся. Если места на диске мало, установщик скажет об этом до записи.";
                    return;
                case 4:
                    lead = "Записываю перевод в файлы игры: тексты, картинки интерфейса, перенос слов.";
                    more = "Пакеты перевода записываются по одному: файл готовится рядом и подменяется целиком, затем проверяется. Не закрывайте окно и не выключайте компьютер: установщик сам закончит. Если запись сорвётся, он вернёт файлы из копии и скажет, что случилось.";
                    return;
                case 5:
                    lead = "Проверяю, что записанные файлы совпали с эталоном.";
                    more = "Для каждого записанного файла считается контрольная сумма SHA-256 и сверяется с эталоном; результат фиксируется в журнале. Если что-то не совпало, запись откатывается по копии. Запускать игру можно после сообщения «Перевод установлен».";
                    return;
                case 6:
                    lead = "Сохраняю русификатор и настраиваю русский лаунчер, если он включён.";
                    more = "Русификатор остаётся доступным для переустановки, отката и обновлений. Русский лаунчер получает ярлык «Honor of Kings ACE (RU)». Проверка обновлений происходит при запуске русификатора; закрытое приложение не работает в фоне.";
                    return;
                default:
                    lead = "Готовлюсь к установке.";
                    more = "Распаковываю файлы перевода в защищённую папку, доступную только администраторам, и проверяю их контрольные суммы. Дальше пять этапов: поиск игры, проверка версии, резервная копия, запись и проверка результата.";
                    return;
            }
        }

        // Что делать при отказе: по коду из WXQTool; пустой код - общий совет.
        public static string FixHint(string code, bool remove)
        {
            switch (code)
            {
                case "running": return "Закройте игру и лаунчер вручную: диспетчер задач (Ctrl+Shift+Esc), вкладка «Подробности», выберите процесс из причины, «Снять задачу». Не забудьте значок лаунчера в области уведомлений. Потом «Назад» и повторите.";
                case "version": return "Игра обновилась. Запустите её один раз до главного меню, закройте и повторите; новая версия перевода выходит отдельно.";
                case "disk_space": return "Освободите место на указанном диске и повторите: игра не изменялась.";
                case "busy": return "Дождитесь окончания другой операции русификатора (или перезагрузите компьютер) и повторите.";
                case "payload": case "acl": case "sha_mismatch": return "Скачайте установщик заново с официальной страницы и запустите от имени администратора.";
                default: return remove ? "Закройте игру и лаунчер и нажмите «Удалить перевод» ещё раз: копии на месте. Если ошибка повторяется, пришлите файл install.log из папки данных русификатора в чат поддержки." : "Нажмите «Назад» и повторите. Если ошибка повторяется, пришлите файл install.log (ProgramData\\Egoist\\WXQ-Russian) в чат поддержки.";
            }
        }

        // Применить S.Percent/Step/Detail к экрану прогресса. instant — без анимаций (render).
        public void UpdateProgress(bool instant)
        {
            if (current != "progress" || pctText == null) return;
            double p = Math.Max(0, Math.Min(100, S.Percent));
            targetPct = p;
            if (instant || !Tk.Anim) { dispPct = p; pctText.Text = ((int)Math.Round(p)).ToString(CultureInfo.InvariantCulture); }
            double trackW = W - 2 * Pad - 48 - 340;
            double to = trackW * p / 100.0;
            if (instant || !Tk.Anim) { fillBar.BeginAnimation(WidthProperty, null); fillBar.Width = to; }
            else fillBar.BeginAnimation(WidthProperty, new DoubleAnimation(to, Tk.D(420)) { EasingFunction = Tk.Expo });
            etaText.Text = (string.IsNullOrEmpty(S.Eta) ? "" : S.Eta) + (S.TotalBytes > 0 ? (string.IsNullOrEmpty(S.Eta) ? "" : " · ") + Proto.SizeText(S.DoneBytes, S.TotalBytes) : "");
            bool closing = S.Stage == "close";
            fileText.Text = closing ? (S.Detail ?? "") : (S.File ?? "");   // при закрытии процессов здесь вся строка «Закрываю игру и лаунчер: имена»
            string lead, more;
            StageInfo(S, out lead, out more);
            infoLead.Text = lead;
            infoMore.Inlines.Clear();
            infoMore.Inlines.Add(new Run("Подробнее  ") { Foreground = Tk.Gold });
            infoMore.Inlines.Add(new Run(more));
            if (markers == null) return;
            for (int i = 0; i < 5; i++)
            {
                string st = i + 1 < S.Step ? "done" : (i + 1 == S.Step ? (S.StepState == "fail" ? "fail" : "run") : "wait");
                if (S.Step == 6) st = "done";
                SetMarker(i, st);
                stepTitles[i].Foreground = st == "wait" ? Tk.Muted : Tk.Text;
                stepDetails[i].Text = (i + 1 == S.Step) ? (closing ? "закрываю игру и лаунчер" : (S.Detail ?? "")) : (st == "done" ? "готово" : "");
                stepDetails[i].Foreground = st == "fail" ? Tk.Danger : (i + 1 == S.Step ? Tk.TextSoft : Tk.Muted);
                if (i < 4) links[i].Fill = (st == "done") ? Tk.LineStrong : Tk.Surface3;
            }
        }

        void SetMarker(int i, string st)
        {
            var g = markers[i]; g.Children.Clear();
            Brush ink = st == "wait" ? Tk.LineStrong : Tk.Text;
            if (st == "wait") g.Children.Add(new Ellipse { Width = 22, Height = 22, Stroke = Tk.LineStrong, StrokeThickness = 2 });
            else if (st == "run")
            {
                g.Children.Add(new Ellipse { Width = 22, Height = 22, Stroke = Tk.Gold, StrokeThickness = 2 });
                var dot = new Ellipse { Width = 8, Height = 8, Fill = Tk.Gold, RenderTransformOrigin = new Point(0.5, 0.5), RenderTransform = new ScaleTransform(1, 1) };
                g.Children.Add(dot);
                if (Tk.Anim)
                {
                    var a = new DoubleAnimation(0.55, 1, TimeSpan.FromMilliseconds(900)) { AutoReverse = true, RepeatBehavior = RepeatBehavior.Forever, EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut } };
                    ((ScaleTransform)dot.RenderTransform).BeginAnimation(ScaleTransform.ScaleXProperty, a); ((ScaleTransform)dot.RenderTransform).BeginAnimation(ScaleTransform.ScaleYProperty, a);
                }
            }
            else if (st == "done")
            {
                g.Children.Add(new Ellipse { Width = 24, Height = 24, Fill = Tk.Text });
                var c = Ico.Make("check", 16, Tk.Bg); c.HorizontalAlignment = HorizontalAlignment.Center; c.VerticalAlignment = VerticalAlignment.Center; g.Children.Add(c);
            }
            else
            {
                g.Children.Add(new Ellipse { Width = 24, Height = 24, Fill = Tk.Danger });
                var c = Ico.Make("close", 14, Tk.Bg); c.HorizontalAlignment = HorizontalAlignment.Center; c.VerticalAlignment = VerticalAlignment.Center; g.Children.Add(c);
            }
        }

        // ================= Экран 3: готово =================
        FrameworkElement StatusIcon(string icon, Brush ring)
        {
            var b = new Border { Width = 56, Height = 56, CornerRadius = new CornerRadius(28), BorderBrush = ring, BorderThickness = new Thickness(2), HorizontalAlignment = HorizontalAlignment.Left, Margin = new Thickness(0, 0, 0, 20) };
            var i = Ico.Make(icon, 26, ring); i.HorizontalAlignment = HorizontalAlignment.Center; i.VerticalAlignment = VerticalAlignment.Center; b.Child = i;
            return b;
        }

        public static string DoneDescription(UiState s)
        {
            string text = string.IsNullOrEmpty(s.Note) ? "" : s.Note + " ";
            if (s.LaunchState == "failed") text = s.LaunchMessage + " " + text;
            else if (s.LaunchState == "starting") text = "Запускаю лаунчер. Подтвердите запрос прав, если его покажет Windows. " + text;
            text += s.LauncherInstalled ? "Перевод лаунчера подготовлен. " : "Лаунчер остаётся на китайском. ";
            return text + "Некоторые изображения и ранние экраны игры ещё могут оставаться на китайском.";
        }

        FrameworkElement Done()
        {
            Grid body, footer; var root = Frame(out body, out footer);
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(352) });
            var left = new StackPanel();
            left.Children.Add(StatusIcon("check", Tk.Success));
            left.Children.Add(Title("Перевод установлен", DoneDescription(S), 420));
            var com = new StackPanel { Margin = new Thickness(0, 28, 0, 0) }; com.Children.Add(Tk.TB("Сообщество", 12, Tk.Muted));
            var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 10, 0, 0) };
            string[][] soc = { new[] { "chat", "Discord", "https://discord.gg/Zf69zsm5dQ" }, new[] { "vk", "VK", "https://vk.com/ego1stgames" }, new[] { "youtube", "YouTube", "https://www.youtube.com/@egoist_games_one" } };
            foreach (var s in soc)
            {
                string url = s[2];
                var b = new Btn(BK.Secondary, null, s[0], null, 40, 40, true, s[1]); b.Margin = new Thickness(0, 0, 8, 0); b.Click += delegate { Open(url); }; row.Children.Add(b);
            }
            com.Children.Add(row); left.Children.Add(com);
            body.Children.Add(left);

            var card = new StackPanel();
            card.Children.Add(Tk.TB("Что дальше", 12, Tk.Muted));
            var l1 = ItemRow("play", S.HasLauncherShortcut ? "Запустите игру" : "Откройте игру вручную",
                S.HasLauncherShortcut ? (S.LauncherInstalled ? "Откроется русский лаунчер. Windows может запросить права" : "Откроется оригинальный лаунчер") : "Ярлык лаунчера не найден");
            l1.Margin = new Thickness(0, 16, 0, 12); card.Children.Add(l1);
            card.Children.Add(ItemRow("redo", "После обновления игры", "Дойдите до главного меню, закройте игру и откройте русификатор для переустановки"));
            if (S.LauncherInstalled) card.Children.Add(ItemRow("shield", "Русский лаунчер", "Ярлык «Honor of Kings ACE (RU)»; новая функция, с ACE в игре не проверена. Выключить: переустановка с выключенным переключателем"));
            var c = Card(card, 24); SetColumn(c, 2); c.VerticalAlignment = VerticalAlignment.Top; body.Children.Add(c);

            footer.Children.Add(SupportBlock());
            var close = new Btn(BK.Secondary, "Закрыть", null, null, 44, 112); close.Click += delegate { Fire("close"); };
            var run = new Btn(BK.Primary, "Запустить игру", "play", null, 44, 180); run.Click += delegate { Fire("launch"); };
            run.IsEnabledBtn = S.HasLauncherShortcut;
            operationButtons.Add(run, S.HasLauncherShortcut);
            footer.Children.Add(Buttons(close, run));
            return root;
        }

        // ================= Экран 4: сбой =================
        FrameworkElement Fail()
        {
            Grid body, footer; var root = Frame(out body, out footer);
            var left = new StackPanel { HorizontalAlignment = HorizontalAlignment.Left };
            left.Children.Add(StatusIcon("alert", Tk.Danger));
            bool rmFail = S.FailKind == "remove";
            left.Children.Add(Title(rmFail ? "Не удалось удалить перевод" : "Не удалось установить", rmFail
                ? "Перевод удалён не до конца. Закройте игру и лаунчер, в том числе значок в области уведомлений, и повторите: резервные копии на месте."
                : S.FailedBeforeWrite
                    ? "Ничего не повреждено: до записи в игру установщик остановился."
                    : "Установка прервана, часть файлов могла записаться. Закройте игру, нажмите «Назад», затем «Удалить перевод»: установщик вернёт файлы игры.", 340));
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(340) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var right = new StackPanel { VerticalAlignment = VerticalAlignment.Top };
            SetColumn(right, 2);
            var reason = new StackPanel();
            reason.Children.Add(Tk.TB("Причина", 12, Tk.Muted));
            var m = Tk.TB(string.IsNullOrEmpty(S.FailureMessage) ? "Помощник установщика не отвечает. Проверьте антивирус и запустите установку ещё раз." : S.FailureMessage, 14, Tk.Text, Tk.Body, 21, TextWrapping.Wrap);
            m.Margin = new Thickness(0, 8, 0, 0); reason.Children.Add(m);
            var card = Card(reason, 20); card.HorizontalAlignment = HorizontalAlignment.Stretch;
            right.Children.Add(card);
            var fix = new StackPanel { Margin = new Thickness(0, 14, 0, 0) };
            fix.Children.Add(Tk.TB("Что делать", 12, Tk.Gold));
            var fixText = Tk.TB(FixHint(S.FailureCode, rmFail), 13, Tk.TextSoft, Tk.Body, 19, TextWrapping.Wrap); fixText.Margin = new Thickness(0, 4, 0, 0);
            fix.Children.Add(fixText);
            right.Children.Add(fix);
            body.Children.Add(left);
            body.Children.Add(right);
            var close = new Btn(BK.Secondary, "Закрыть", null, null, 44, 112); close.Click += delegate { Fire("close"); };
            var back = new Btn(BK.Primary, "Назад", "back", null, 44, 132); back.Click += delegate { Fire("back"); };
            // Игра не найдена или найдено несколько папок: папку можно выбрать вручную. Путь выбирает системный диалог в Setup, окно его не передаёт.
            bool pick = !rmFail && (S.FailureCode == "game_not_found" || S.FailureCode == "game_ambiguous" || S.FailureCode == "game_missing");
            if (pick)
            {
                var choose = new Btn(BK.Secondary, "Выбрать папку игры", null, null, 44, 208); choose.Click += delegate { Fire("pickgame"); };
                footer.Children.Add(Buttons(close, choose, back));
            }
            else footer.Children.Add(Buttons(close, back));
            return root;
        }

        // ================= Экран 5: уже установлено =================
        bool confirmRemove;
        FrameworkElement Installed()
        {
            Grid body, footer; var root = Frame(out body, out footer);
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(352) });
            var left = new StackPanel();
            if (S.Interrupted)
            {
                left.Children.Add(StatusIcon("alert", Tk.Danger));
                left.Children.Add(Title("Установка была прервана", "Часть файлов игры могла остаться изменённой. Закройте игру и лаунчер, в том числе значок в области уведомлений, и нажмите «Удалить перевод»: файлы вернутся к прежнему состоянию. После этого перевод можно установить заново.", 420));
            }
            else
            {
                left.Children.Add(StatusIcon("check", Tk.Text));
                left.Children.Add(Title("Перевод уже установлен", "«Переустановить» запишет файлы заново. «Удалить перевод» восстановит файлы до установки перевода. Игру и лаунчер перед этим закройте.", 420));
            }
            body.Children.Add(left);
            var card = new StackPanel();
            card.Children.Add(Tk.TB(S.Interrupted ? "Что произойдёт" : "Установлено", 12, Tk.Muted));
            if (S.Interrupted)
            {
                var n0 = Tk.TB("Установщик вернёт файлы игры по резервным копиям, в том числе если запись успела пройти только частично. Копии лежат в ProgramData и игру не затрагивают.", 12.5, Tk.TextSoft, Tk.Body, 18, TextWrapping.Wrap); n0.Margin = new Thickness(0, 16, 0, 0);
                card.Children.Add(n0);
            }
            else
            {
                card.Children.Add(Fact("Версия перевода", string.IsNullOrEmpty(S.TranslationVersion) ? "—" : S.TranslationVersion, 16));
                card.Children.Add(Fact("Версия приложения", string.IsNullOrEmpty(S.ManagerVersion) ? "—" : S.ManagerVersion, 12));
                card.Children.Add(Fact("Версия игры", string.IsNullOrEmpty(S.GameVersion) ? "—" : S.GameVersion, 12));
                card.Children.Add(new Border { Height = 1, Background = Tk.Line, Margin = new Thickness(0, 6, 0, 14) });
                var n = Tk.TB("После обновления игры дойдите до главного меню, закройте игру и откройте русификатор для переустановки.", 12.5, Tk.Muted, Tk.Body, 18, TextWrapping.Wrap); card.Children.Add(n);
            }
            var c = Card(card, 24); SetColumn(c, 2); c.VerticalAlignment = VerticalAlignment.Top; body.Children.Add(c);

            if (!confirmRemove)
            {
                var close = new Btn(BK.Secondary, "Закрыть", null, null, 44, 112); close.Click += delegate { Fire("close"); };
                if (S.Interrupted)
                {
                    var rm0 = new Btn(BK.Primary, "Удалить перевод", "trash", null, 44, 188); rm0.Click += delegate { confirmRemove = true; Show("installed"); };
                    footer.Children.Add(Buttons(close, rm0));
                }
                else
                {
                    var rm = new Btn(BK.SecondaryDanger, "Удалить перевод", "trash", null, 44, 188); rm.Click += delegate { confirmRemove = true; Show("installed"); };
                    var re = new Btn(BK.Primary, "Переустановить", "redo", null, 44, 188); re.Click += delegate { Fire("install"); };
                    footer.Children.Add(Buttons(close, rm, re));
                }
            }
            else ConfirmFooter(body, footer, "installed");
            return root;
        }

        FrameworkElement Fact(string name, string value, double top)
        {
            var g = new Grid { Margin = new Thickness(0, top, 0, 0) };
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            g.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            g.Children.Add(Tk.TB(name, 14, Tk.TextSoft));
            var v = Tk.TB(value, 14, Tk.Text); Typography.SetNumeralAlignment(v, FontNumeralAlignment.Tabular); SetColumn(v, 1); g.Children.Add(v);
            return g;
        }

        // ================= Экран 6: удалено =================
        FrameworkElement Removed()
        {
            Grid body, footer; var root = Frame(out body, out footer);
            var left = new StackPanel { HorizontalAlignment = HorizontalAlignment.Left };
            left.Children.Add(StatusIcon("check", Tk.Success));
            left.Children.Add(Title("Перевод удалён", (string.IsNullOrEmpty(S.Note) ? "Восстановлено состояние файлов до установки перевода." : S.Note) + " Русификатор остаётся доступным для повторной установки.", 480));
            body.Children.Add(left);
            footer.Children.Add(SupportBlock());
            var close = new Btn(BK.Secondary, "Закрыть", null, null, 44, 112); close.Click += delegate { Fire("close"); };
            var again = new Btn(BK.Primary, "Установить снова", "redo", null, 44, 204); again.Click += delegate { Fire("back"); };
            footer.Children.Add(Buttons(close, again));
            return root;
        }

        // ===== переключение экранов с входом: заголовок и блоки выезжают expo =====
        public string Current { get { return current; } }

        public void Show(string screen)
        {
            if (pctTimer != null && screen != "progress") pctTimer.Stop();
            S.Screen = screen; current = screen;
            operationButtons.Clear(); launcherToggle = null;
            FrameworkElement el;
            switch (screen)
            {
                case "progress": el = Progress(); break;
                case "done": el = Done(); break;
                case "fail": el = Fail(); break;
                case "installed": el = Installed(); break;
                case "removed": el = Removed(); break;
                default: el = Welcome(); break;
            }
            host.Children.Clear(); host.Children.Add(el);
            if (Tk.Anim)
            {
                var g = (Grid)el; int k = 0;
                foreach (UIElement part in g.Children)
                {
                    var inner = part as Grid; if (inner == null) continue;
                    foreach (UIElement c in inner.Children)
                    {
                        var tr = new TranslateTransform(0, 12); c.RenderTransform = tr; c.Opacity = 0;
                        var delay = TimeSpan.FromMilliseconds(40 * k);
                        c.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(260)) { BeginTime = delay, EasingFunction = Tk.Expo });
                        tr.BeginAnimation(TranslateTransform.YProperty, new DoubleAnimation(12, 0, TimeSpan.FromMilliseconds(520)) { BeginTime = delay, EasingFunction = Tk.Expo });
                        k++;
                    }
                }
            }
            UpdateUpdates(false);
            if (screen == "installed" && confirmRemove) { /* без повторного входа: оставляем как есть */ }
        }
    }

    // ===== Окно =====
    class SetupWindow : Window
    {
        public MainView View;

        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int val, int size);

        public SetupWindow(MainView view)
        {
            View = view;
            Title = "Honor of Kings ACE: русификатор";
            WindowStyle = WindowStyle.None; ResizeMode = ResizeMode.NoResize; AllowsTransparency = false;
            Width = MainView.W; Height = MainView.H; Background = Tk.Bg; UseLayoutRounding = true; SnapsToDevicePixels = true;
            WindowStartupLocation = WindowStartupLocation.CenterScreen; ShowInTaskbar = true; FontFamily = Tk.Body;
            WindowChrome.SetWindowChrome(this, new WindowChrome { CaptionHeight = 0, ResizeBorderThickness = new Thickness(0), GlassFrameThickness = new Thickness(0, 0, 0, 1), CornerRadius = new CornerRadius(0), UseAeroCaptionButtons = false });
            Content = view;
            view.Draggable.MouseLeftButtonDown += delegate (object s, MouseButtonEventArgs e) { if (e.ButtonState == MouseButtonState.Pressed) { try { DragMove(); } catch (InvalidOperationException) { } } };
            SourceInitialized += delegate
            {
                var h = new WindowInteropHelper(this).Handle;
                int round = 2, dark = 1, border = 0x002A2626;   // DWMWCP_ROUND, тёмная рамка, цвет рамки #26262A (BGR)
                DwmSetWindowAttribute(h, 33, ref round, 4);
                DwmSetWindowAttribute(h, 20, ref dark, 4);
                DwmSetWindowAttribute(h, 34, ref border, 4);
            };
            KeyDown += delegate (object s, KeyEventArgs e) { if (e.Key == Key.Escape && View.S.Screen != "progress" && View.S.State != "running") View.RaiseClose(); };
        }
    }

    static class Program
    {
        static int Render(string dir, int dpi)
        {
            Tk.Still = true;
            Directory.CreateDirectory(dir);
            string[] names = { "01-welcome", "02-welcome-launcher-on", "03-progress", "04-done", "05-fail", "06-installed", "07-installed-confirm", "08-removed", "09-progress-start", "10-fail-after-write",
                "11-done-no-launcher", "12-interrupted", "13-progress-remove", "14-fail-remove", "15-welcome-can-remove", "16-welcome-no-launcher-block", "17-removed-note", "18-interrupted-confirm", "19-fail-pick-game",
                "20-progress-closing", "21-progress-backup", "22-progress-verify", "23-progress-finish", "24-fail-running", "25-progress-remove-closing", "26-done-launcher-not-found",
                "27-update-checking", "28-installed-update-available", "29-update-downloading", "30-update-failed", "31-update-ready", "32-update-handoff", "33-update-current", "34-update-unknown-size" };
            foreach (string n in names)
            {
                var v = new MainView();
                v.S.TranslationVersion = "0.9.4"; v.S.GameVersion = "1.0.33.8"; v.S.LauncherInstalled = true;
                string screen = "welcome";
                if (n == "02-welcome-launcher-on") v.S.LauncherOn = true;
                if (n == "03-progress") { screen = "progress"; v.S.Percent = 47; v.S.Step = 4; v.S.Detail = "Файл 312 из 740"; v.S.Eta = "осталось ~3 мин"; v.S.DoneBytes = 2250L * 1048576; v.S.TotalBytes = 4800L * 1048576; v.S.File = "Resources\\Lua\\lang\\ru_RU\\hero_skill_text.bytes"; }
                if (n == "09-progress-start") { screen = "progress"; v.S.Percent = 6; v.S.Step = 2; v.S.Detail = "Сверяю версию с перечнем"; v.S.Eta = "осталось ~8 мин"; v.S.File = "HonorOfKings.exe"; }
                if (n == "20-progress-closing") { screen = "progress"; v.S.Percent = 8; v.S.Step = 2; v.S.Stage = "close"; v.S.Detail = "Закрываю игру и лаунчер: 王者万象棋.exe, WXQLauncher.exe, WXQLauncherRU.exe, WXQ.exe"; }
                if (n == "25-progress-remove-closing") { screen = "progress"; v.S.Op = "remove"; v.S.Percent = 4; v.S.Stage = "close"; v.S.Detail = "Закрываю игру и лаунчер: WXQ.exe"; }
                if (n == "21-progress-backup") { screen = "progress"; v.S.Percent = 22; v.S.Step = 3; v.S.Detail = "Файл 2 из 9"; v.S.Eta = "осталось ~5 мин"; v.S.File = "10/10.db"; }
                if (n == "22-progress-verify") { screen = "progress"; v.S.Percent = 91; v.S.Step = 5; v.S.Detail = "Файл 4 из 9"; v.S.Eta = "осталось ~1 мин"; v.S.File = "19/19_0.db"; }
                if (n == "23-progress-finish") { screen = "progress"; v.S.Percent = 98; v.S.Step = 6; v.S.Detail = ""; }
                if (n == "24-fail-running") { screen = "fail"; v.S.FailureCode = "running"; v.S.FailureMessage = "Игра или лаунчер сейчас запущены, и закрыть их не удалось (王者万象棋.exe (PID 4120)). Закройте их вручную: диспетчер задач, вкладка «Подробности», «Снять задачу», в том числе значок лаунчера в области уведомлений, и повторите."; }
                if (n == "04-done") screen = "done";
                if (n == "11-done-no-launcher") { screen = "done"; v.S.LauncherInstalled = false; }
                if (n == "26-done-launcher-not-found") { screen = "done"; v.S.LauncherInstalled = false; v.S.Note = "Лаунчер не переведён: найдены несколько подходящих установок лаунчера. Укажите нужную папку игры и повторите установку."; }
                if (n == "12-interrupted" || n == "18-interrupted-confirm") { screen = "installed"; v.S.Interrupted = true; }
                if (n == "18-interrupted-confirm") typeof(MainView).GetField("confirmRemove", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(v, true);
                if (n == "13-progress-remove") { screen = "progress"; v.S.Op = "remove"; v.S.Percent = 40; v.S.Eta = "осталось ~1 мин"; v.S.File = "WXQ_Data\\Package\\builtin\\1\\1.db"; v.S.DoneBytes = 1200L * 1048576; v.S.TotalBytes = 3000L * 1048576; }
                if (n == "14-fail-remove") { screen = "fail"; v.S.FailKind = "remove"; v.S.FailedBeforeWrite = false; v.S.FailureMessage = "Игра запущена. Закройте её и лаунчер, в том числе значок в области уведомлений, и повторите."; }
                if (n == "15-welcome-can-remove") v.S.CanRemove = true;
                if (n == "16-welcome-no-launcher-block") v.S.LauncherAvailable = false;
                if (n == "17-removed-note") { screen = "removed"; v.S.Note = "Резервные копии другой папки игры (2) не тронуты и оставлены на месте."; }
                if (n == "05-fail") { screen = "fail"; v.S.FailureMessage = "Игра запущена. Закройте её и лаунчер, в том числе значок в области уведомлений, и запустите установку снова."; }
                if (n == "19-fail-pick-game") { screen = "fail"; v.S.FailureCode = "game_not_found"; v.S.FailureMessage = "Не удалось найти установленную игру. Если она стоит в нестандартной папке, нажмите «Выбрать папку игры»."; }
                if (n == "10-fail-after-write") { screen = "fail"; v.S.FailedBeforeWrite = false; v.S.FailureMessage = "Не хватает места на диске: нужно ещё около 1,2 ГБ."; }
                if (n == "06-installed" || n == "07-installed-confirm") screen = "installed";
                if (n == "07-installed-confirm") typeof(MainView).GetField("confirmRemove", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(v, true);
                if (n == "08-removed") screen = "removed";
                if (n.StartsWith("27-", StringComparison.Ordinal) || n.StartsWith("28-", StringComparison.Ordinal) || n.StartsWith("29-", StringComparison.Ordinal) || n.StartsWith("30-", StringComparison.Ordinal)
                    || n.StartsWith("31-", StringComparison.Ordinal) || n.StartsWith("32-", StringComparison.Ordinal) || n.StartsWith("33-", StringComparison.Ordinal) || n.StartsWith("34-", StringComparison.Ordinal))
                {
                    v.S.UpdateEnabled = true; v.S.LauncherOn = true; v.S.TranslationVersion = "0.6.3";
                    v.S.UpdateVersion = "0.6.4-rc.1234567890+проверка-длинного-обозначения-версии-перевода";
                    v.S.UpdateMessage = "Сервер обновлений временно недоступен. Проверьте подключение к интернету или повторите попытку позже. Установку и откат перевода можно продолжить без сети.";
                    switch (n)
                    {
                        case "27-update-checking": v.S.UpdateState = "checking"; break;
                        case "28-installed-update-available": screen = "installed"; v.S.UpdateState = "available"; break;
                        case "29-update-downloading": screen = "installed"; v.S.UpdateState = "downloading"; v.S.UpdateDoneBytes = 62 * 1048576; v.S.UpdateTotalBytes = 163 * 1048576; break;
                        case "30-update-failed": v.S.UpdateState = "failed"; break;
                        case "31-update-ready": screen = "done"; v.S.UpdateState = "ready"; break;
                        case "32-update-handoff": screen = "installed"; v.S.UpdateState = "handoff"; break;
                        case "33-update-current": screen = "installed"; v.S.UpdateState = "current"; v.S.UpdateVersion = "0.6.3"; break;
                        case "34-update-unknown-size": v.S.UpdateState = "downloading"; break;
                    }
                }
                v.Show(screen);
                var host = new Border { Child = v, Background = Tk.Bg, Width = MainView.W, Height = MainView.H };
                host.Measure(new Size(MainView.W, MainView.H)); host.Arrange(new Rect(0, 0, MainView.W, MainView.H)); host.UpdateLayout();
                double scale = dpi / 96.0;
                var rtb = new RenderTargetBitmap((int)(MainView.W * scale), (int)(MainView.H * scale), dpi, dpi, PixelFormats.Pbgra32);
                rtb.Render(host);
                var enc = new PngBitmapEncoder(); enc.Frames.Add(BitmapFrame.Create(rtb));
                using (var f = File.Create(System.IO.Path.Combine(dir, n + "-" + dpi + ".png"))) enc.Save(f);
            }
            return 0;
        }

        [STAThread]
        static int Main(string[] args)
        {
            var app = new Application { ShutdownMode = ShutdownMode.OnMainWindowClose };
            string renderDir = null, liveDir = null, token = null; int dpi = 96, parent = 0; bool demo = false, selftest = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--selftest") selftest = true;
                else if (args[i] == "--dir" && i + 1 < args.Length) liveDir = args[++i];
                else if (args[i] == "--token" && i + 1 < args.Length) token = args[++i];
                else if (args[i] == "--parent" && i + 1 < args.Length) int.TryParse(args[++i], out parent);
                else if (args[i] == "--render" && i + 1 < args.Length) renderDir = args[++i];
                else if (args[i] == "--dpi" && i + 1 < args.Length) int.TryParse(args[++i], out dpi);
                else if (args[i] == "--demo") demo = true;
                else if (args[i] == "--autotest") { demo = true; Demo.Auto = true; }
            }
            string exeDir = System.IO.Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            Tk.LoadFonts(liveDir != null ? System.IO.Path.Combine(exeDir, "fonts") : null);
            if (selftest) return Selftest.Run();
            if (renderDir != null) return Render(renderDir, dpi);
            if (liveDir != null) return Live.Run(app, liveDir, token, parent);
            if (!demo) { return 2; }
            return Demo.Run(app);
        }
    }

    // ===== Рабочий режим: окно, которым Inno-установщик ведёт установку =====
    static class Live
    {
        static Mutex alive;   // «окно живо»: Inno проверяет имя через OpenMutex; держим ссылку до конца процесса

        public static int Run(Application app, string dir, string token, int parentPid)
        {
            if (token == null || !Regex.IsMatch(token, "^[A-Za-z0-9]{4,40}$") || string.IsNullOrEmpty(dir) || !System.IO.Path.IsPathRooted(dir) || !Directory.Exists(dir)) return 2;
            string statePath = System.IO.Path.Combine(dir, "state.json"), cmdPath = System.IO.Path.Combine(dir, "commands.jsonl");
            var v = new MainView();
            var w = new SetupWindow(v);
            int rc = 0, seq = 0, ticks = 0;
            long rev = 0; string sig = null; bool sentClose = false, force = false;
            // Родителя берём один раз при старте: проверка HasExited по этому объекту не зависит от повторного использования PID.
            Process parentProc = null;
            if (parentPid > 0) { try { parentProc = Process.GetProcessById(parentPid); } catch (Exception) { return 2; } }

            Action<string> send = delegate (string cmd)
            {
                if (Array.IndexOf(Proto.Cmds, cmd) < 0) return;
                bool? launcher = v.S.LauncherAvailable ? (bool?)v.S.LauncherOn : null;
                try { Proto.Append(cmdPath, Proto.Cmd(++seq, cmd, launcher)); }
                catch (Exception) { v.CommandFailed(cmd); }
            };
            Action exitSoon = delegate
            {
                var t = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(1200) };
                t.Tick += delegate { t.Stop(); force = true; w.Close(); };
                t.Start();
            };
            v.Command += delegate (string cmd)
            {
                if (!Proto.CanSend(v.S, cmd)) return;   // запись игры и обновление не запускаются одновременно
                if (cmd == "close") { if (sentClose) return; sentClose = true; send(cmd); exitSoon(); return; }
                send(cmd);
            };
            w.Closing += delegate (object o, System.ComponentModel.CancelEventArgs e)
            {
                if (force) return;
                if (v.S.State == "running") { e.Cancel = true; return; }
                if (!sentClose) { sentClose = true; send("close"); }
            };

            Action poll = delegate
            {
                string text;
                try { text = File.Exists(statePath) ? Proto.ReadShared(statePath) : null; } catch (Exception) { return; }
                string screen;
                if (!Proto.TryApply(text, v.S, ref rev, out screen, out sig)) return;
                if (screen == "quit") { force = true; w.Close(); return; }
                if (v.Current != screen || (screen != "progress" && sig != lastSig)) { lastSig = sig; v.Show(screen); }
                else if (screen == "progress") v.UpdateProgress(false);
                v.AcceptUpdateState();
#if TESTBUILD
                Pilot.OnState(v);
#endif
            };
            var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
            timer.Tick += delegate
            {
                poll();
                if (++ticks % 5 == 0 && parentProc != null)
                {
                    bool gone;
                    try { gone = parentProc.HasExited; } catch (Exception) { gone = false; }
                    if (gone) { force = true; w.Close(); }
                }
            };
            w.ContentRendered += delegate
            {
                if (alive == null) { try { alive = new Mutex(false, "Local\\WXQSetupUI-" + token); } catch (Exception) { } }
                w.Activate();
            };
            app.DispatcherUnhandledException += delegate (object o, DispatcherUnhandledExceptionEventArgs e)
            {
                try { File.WriteAllText(System.IO.Path.Combine(dir, "ui-error.txt"), e.Exception.ToString()); } catch (Exception) { }
                e.Handled = true; rc = 3; force = true; w.Close();
            };
#if TESTBUILD
            Pilot.Attach(w, v, send);
#endif
            poll();
            timer.Start();
            app.Run(w);
            return rc;
        }

        static string lastSig;
    }

#if TESTBUILD
    // Только тестовая сборка (build_ui.py --test-build): сценарий нажатий и снимки окна без мыши и клавиатуры. В релизном exe этого кода нет.
    //   WXQ_UI_AUTOPILOT = "welcome=toggle;welcome=install;done=close": когда показан экран, выполняется следующий шаг списка, если он про этот экран
    //   WXQ_UI_SHOTS     = папка для PNG: снимок на каждом экране и на 25/50/75 % прогресса
    static class Pilot
    {
        static readonly List<string[]> steps = new List<string[]>();
        static string shots; static int shotNo; static double nextMark = 25; static string lastScreen;
        static MainView view; static SetupWindow win; static Action<string> sendCmd;

        public static void Attach(SetupWindow w, MainView v, Action<string> send)
        {
            view = v; win = w; sendCmd = send;
            string plan = Environment.GetEnvironmentVariable("WXQ_UI_AUTOPILOT");
            if (!string.IsNullOrEmpty(plan)) foreach (var part in plan.Split(';')) { var kv = part.Split('='); if (kv.Length == 2) steps.Add(kv); }
            shots = Environment.GetEnvironmentVariable("WXQ_UI_SHOTS");
            if (!string.IsNullOrEmpty(shots)) Directory.CreateDirectory(shots);
        }

        public static void OnState(MainView v)
        {
            string scr = v.Current;
            if (scr != lastScreen)
            {
                lastScreen = scr;
                Later(900, delegate { Shot(scr); });
                if (steps.Count > 0 && steps[0][0] == scr)
                {
                    string act = steps[0][1]; steps.RemoveAt(0);
                    Later(1800, delegate { if (act == "toggle") { v.PilotToggleLauncher(); lastScreen = null; Later(1500, delegate { OnState(v); }); } else v.Do(act); });   // после переключателя экран и состояние те же: сами запускаем следующий шаг плана
                }
            }
            if (scr == "progress" && v.S.Percent >= nextMark) { nextMark += 25; Later(150, delegate { Shot("progress-" + (int)v.S.Percent); }); }
        }

        static void Later(int ms, Action a)
        {
            var t = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(ms) };
            t.Tick += delegate { t.Stop(); a(); };
            t.Start();
        }

        static void Shot(string name)
        {
            if (string.IsNullOrEmpty(shots) || view == null) return;
            try
            {
                var dpi = VisualTreeHelper.GetDpi(view);
                var rtb = new RenderTargetBitmap((int)Math.Round(MainView.W * dpi.DpiScaleX), (int)Math.Round(MainView.H * dpi.DpiScaleY), 96 * dpi.DpiScaleX, 96 * dpi.DpiScaleY, PixelFormats.Pbgra32);
                rtb.Render(view);
                var enc = new PngBitmapEncoder(); enc.Frames.Add(BitmapFrame.Create(rtb));
                using (var f = File.Create(System.IO.Path.Combine(shots, (++shotNo).ToString("00") + "-" + name + ".png"))) enc.Save(f);
            }
            catch (Exception) { }
        }
    }
#endif

    // ===== --selftest: разбор состояния, белый список ссылок, формат команд (без окна) =====
    static class Selftest
    {
        static int failed, total;
        static void Check(string name, bool ok) { total++; if (!ok) { failed++; Console.WriteLine("НЕ ПРОЙДЕНО: " + name); } }

        public static int Run()
        {
            string good = "{\"rev\":5,\"screen\":\"progress\",\"percent\":47.5,\"step\":4,\"step_state\":\"run\",\"detail\":\"Запись\",\"state\":\"running\",\"done_bytes\":1000,\"total_bytes\":2000}";
            string screen, sig; long rev = 0; var s = new UiState();
            Check("валидное состояние принято", Proto.TryApply(good, s, ref rev, out screen, out sig) && screen == "progress" && rev == 5 && s.Percent == 47.5 && s.Step == 4 && s.State == "running");
            { var f = new UiState(); long fr = 0; string fs, fg; Check("первое состояние «welcome» берёт launcher_on (по умолчанию включён)", Proto.TryApply("{\"rev\":1,\"screen\":\"welcome\",\"launcher_on\":true}", f, ref fr, out fs, out fg) && f.LauncherOn); }
            { var f = new UiState(); long fr = 0; string fs, fg; Check("поле stage: close принимается, любое другое значение сбрасывается", Proto.TryApply("{\"rev\":1,\"screen\":\"progress\",\"stage\":\"close\"}", f, ref fr, out fs, out fg) && f.Stage == "close" && Proto.TryApply("{\"rev\":2,\"screen\":\"progress\",\"stage\":\"x<y\"}", f, ref fr, out fs, out fg) && f.Stage == ""); }
            Check("тот же rev повторно не применяется",!Proto.TryApply(good, s, ref rev, out screen, out sig));
            Check("меньший rev не применяется", !Proto.TryApply(good.Replace("\"rev\":5", "\"rev\":4"), s, ref rev, out screen, out sig));
            Check("больший rev применяется", Proto.TryApply(good.Replace("\"rev\":5", "\"rev\":6").Replace("47.5", "60"), s, ref rev, out screen, out sig) && s.Percent == 60);
            // поломанные входы: ни один не меняет состояние и не бросает исключение
            double before = s.Percent; long r0 = rev;
            string[] bad = { "", " ", "{", "{\"rev\":7,\"scr", good.Substring(0, good.Length / 2), good + "}", "[]", "null", "{\"screen\":\"done\"}", "{\"rev\":\"x\",\"screen\":\"done\"}",
                "{\"rev\":8,\"screen\":\"format-c\"}", "{\"rev\":9,\"screen\":42}", "{\"rev\":10,\"screen\":\"done\"} хвост", "\0\0\0\0", new string('{', 5000), "{\"rev\":11,\"screen\":\"done\",\"x\":" + new string('[', 200) + "}" , new string(' ', 70000) + "{}" };
            bool all = true;
            foreach (string b in bad) { try { if (Proto.TryApply(b, s, ref rev, out screen, out sig)) all = false; } catch (Exception) { all = false; } }
            Check("повреждённые и неполные файлы отвергаются", all && s.Percent == before && rev == r0);
            Check("BOM в начале допустим", Proto.TryApply("\uFEFF{\"rev\":20,\"screen\":\"welcome\"}\r\n", s, ref rev, out screen, out sig) && screen == "welcome");
            Check("quit принимается", Proto.TryApply("{\"rev\":21,\"screen\":\"quit\"}", s, ref rev, out screen, out sig) && screen == "quit");
            Check("проценты ограничены 0..100", Proto.TryApply("{\"rev\":22,\"screen\":\"progress\",\"percent\":950}", s, ref rev, out screen, out sig) && s.Percent == 100);
            Check("чужой объём: done не больше total", Proto.TryApply("{\"rev\":23,\"screen\":\"progress\",\"done_bytes\":9,\"total_bytes\":3}", s, ref rev, out screen, out sig) && s.DoneBytes == 3);

            {
                const string partial = "Применён перевод совместимых ресурсов. Новые и изменённые данные оставлены в оригинале.";
                const string launcher = "Лаунчер не переведён: найдено несколько установок лаунчера.";
                var done = new UiState(); long dr = 0; string ds, dg;
                Check("частичная установка остаётся успешным итогом", Proto.TryApply("{\"rev\":1,\"screen\":\"done\",\"state\":\"finished\",\"note\":\"" + partial + "\",\"launcher_installed\":true}", done, ref dr, out ds, out dg)
                    && ds == "done" && done.State == "finished" && MainView.DoneDescription(done).StartsWith(partial + " ")
                    && MainView.DoneDescription(done).Contains("Перевод лаунчера подготовлен."));
                Check("заметка лаунчера не вытесняет частичный итог", Proto.TryApply("{\"rev\":2,\"screen\":\"done\",\"state\":\"finished\",\"note\":\"" + partial + " " + launcher + "\"}", done, ref dr, out ds, out dg)
                    && MainView.DoneDescription(done).StartsWith(partial + " " + launcher + " ")
                    && MainView.DoneDescription(done).Contains("Лаунчер остаётся на китайском."));
                Check("новая полная установка не сохраняет прежнюю заметку", Proto.TryApply("{\"rev\":3,\"screen\":\"done\",\"state\":\"finished\",\"launcher_installed\":true}", done, ref dr, out ds, out dg)
                    && done.Note == "" && !MainView.DoneDescription(done).Contains(partial) && MainView.DoneDescription(done).StartsWith("Перевод лаунчера подготовлен."));
            }

            {
                var u = new UiState(); long ur = 0; string us, ug;
                foreach (string state in Proto.UpdateStates)
                    Check("состояние обновления: " + state, Proto.TryApply("{\"rev\":" + (ur + 1) + ",\"screen\":\"installed\",\"update_enabled\":true,\"update_state\":\"" + state + "\",\"translation_version\":\"0.6.3\",\"update_version\":\"0.6.4\"}", u, ref ur, out us, out ug)
                        && u.UpdateState == state && u.UpdateEnabled && u.TranslationVersion == "0.6.3" && u.UpdateVersion == "0.6.4");
                Check("неизвестное состояние обновления безопасно сбрасывается", Proto.TryApply("{\"rev\":20,\"screen\":\"welcome\",\"update_enabled\":true,\"update_state\":\"execute\"}", u, ref ur, out us, out ug) && u.UpdateState == "none");
                Check("новые поля необязательны и не сохраняют старое значение", Proto.TryApply("{\"rev\":21,\"screen\":\"welcome\"}", u, ref ur, out us, out ug) && !u.UpdateEnabled && u.UpdateState == "none" && u.UpdateVersion == "" && u.UpdateMessage == "" && u.UpdateDoneBytes == 0 && u.UpdateTotalBytes == 0);
                Check("флаг обновления принимает только bool", Proto.TryApply("{\"rev\":22,\"screen\":\"welcome\",\"update_enabled\":\"true\",\"update_version\":{},\"update_message\":[]}", u, ref ur, out us, out ug) && !u.UpdateEnabled && u.UpdateVersion == "" && u.UpdateMessage == "");
                Check("объём обновления ограничен известным итогом", Proto.TryApply("{\"rev\":23,\"screen\":\"welcome\",\"update_done_bytes\":999,\"update_total_bytes\":500}", u, ref ur, out us, out ug) && u.UpdateDoneBytes == 500 && u.UpdateTotalBytes == 500);
                Check("неизвестный итог не теряет скачанный объём", Proto.TryApply("{\"rev\":24,\"screen\":\"welcome\",\"update_done_bytes\":999,\"update_total_bytes\":0}", u, ref ur, out us, out ug) && u.UpdateDoneBytes == 999 && u.UpdateTotalBytes == 0);
                Check("отрицательный объём обновления равен нулю", Proto.TryApply("{\"rev\":25,\"screen\":\"welcome\",\"update_done_bytes\":-10,\"update_total_bytes\":-1}", u, ref ur, out us, out ug) && u.UpdateDoneBytes == 0 && u.UpdateTotalBytes == 0);
                Check("Int64.MaxValue читается точно", Proto.TryApply("{\"rev\":26,\"screen\":\"welcome\",\"update_done_bytes\":9223372036854775807,\"update_total_bytes\":9223372036854775807}", u, ref ur, out us, out ug) && u.UpdateDoneBytes == long.MaxValue && u.UpdateTotalBytes == long.MaxValue);
                Check("переполнение объёма ограничено Int64.MaxValue", Proto.TryApply("{\"rev\":27,\"screen\":\"welcome\",\"update_done_bytes\":1e30,\"update_total_bytes\":1e30}", u, ref ur, out us, out ug) && u.UpdateDoneBytes == long.MaxValue && u.UpdateTotalBytes == long.MaxValue);
                Check("нечисловой объём обновления равен нулю", Proto.TryApply("{\"rev\":28,\"screen\":\"welcome\",\"update_done_bytes\":\"NaN\",\"update_total_bytes\":\"x\"}", u, ref ur, out us, out ug) && u.UpdateDoneBytes == 0 && u.UpdateTotalBytes == 0);
                string plain = new JavaScriptSerializer().Serialize(new Dictionary<string, object> { { "rev", 29 }, { "screen", "welcome" }, { "update_version", "  0.6.4\r\n rc\u202e  " }, { "update_message", "  Нет\tсети\n\u0001\u202eпозже  " } });
                Check("описание обновления остаётся обычным текстом", Proto.TryApply(plain, u, ref ur, out us, out ug) && u.UpdateVersion == "0.6.4 rc" && u.UpdateMessage == "Нет сети позже");
                string bounded = new JavaScriptSerializer().Serialize(new Dictionary<string, object> { { "rev", 30 }, { "screen", "welcome" }, { "update_version", new string('v', 500) }, { "update_message", new string('я', 1000) } });
                Check("строки обновления ограничены независимо", Proto.TryApply(bounded, u, ref ur, out us, out ug) && u.UpdateVersion.Length == 80 && u.UpdateMessage.Length == 280);
                Check("сломанное состояние не стирает обновление", !Proto.TryApply("{\"rev\":31,\"screen\":\"welcome\",\"update_state\":", u, ref ur, out us, out ug) && u.UpdateVersion.Length == 80 && ur == 30);
            }

            {
                var u = new UiState { UpdateEnabled = true, UpdateState = "checking" };
                Check("проверка обновления не блокирует установку и откат", Proto.CanSend(u, "install") && Proto.CanSend(u, "remove") && Proto.CanSend(u, "options") && !Proto.CanSend(u, "checkupdate"));
                foreach (string idle in new[] { "welcome", "installed", "done", "fail", "removed" })
                {
                    u.Screen = idle; u.UpdateState = "current";
                    Check("проверка вручную на idle-экране " + idle, Proto.CanSend(u, "checkupdate") && !Proto.CanSend(u, "update"));
                }
                foreach (string busy in new[] { "downloading", "handoff" })
                {
                    u.UpdateState = busy;
                    Check("закрытие допустимо при " + busy, Proto.CanSend(u, "close"));
                    bool safe = true;
                    foreach (string cmd in new[] { "install", "remove", "launch", "options", "back", "pickgame", "checkupdate", "update" }) safe &= !Proto.CanSend(u, cmd);
                    Check("операции заблокированы при " + busy, safe);
                }
                u.UpdateState = "available"; Check("обновление запускается из available", Proto.CanSend(u, "update"));
                u.UpdateState = "ready"; Check("обновление запускается из ready", Proto.CanSend(u, "update"));
                u.UpdateEnabled = false; Check("без поддержки обновления команды недоступны", !Proto.CanSend(u, "update") && !Proto.CanSend(u, "checkupdate") && Proto.CanSend(u, "install"));
                u.UpdateEnabled = true; u.State = "running"; Check("обновление не вмешивается в запись игры", !Proto.CanSend(u, "update") && !Proto.CanSend(u, "checkupdate") && !Proto.CanSend(u, "close"));
                u.State = "idle"; u.Screen = "progress"; Check("экран прогресса не запускает обновление", !Proto.CanSend(u, "update") && !Proto.CanSend(u, "checkupdate"));
            }

            {
                var v = new MainView(); v.S.UpdateEnabled = true; v.S.UpdateState = "available"; v.Show("installed");
                int updates = 0, installs = 0, closes = 0;
                v.Command += delegate (string cmd) { if (cmd == "update") updates++; if (cmd == "install") installs++; if (cmd == "close") closes++; };
                v.Do("update"); v.Do("update"); v.Do("install"); v.Do("close");
                Check("повторный клик обновления не дублирует команду", updates == 1 && installs == 0 && closes == 1);
                v.S.UpdateState = "failed"; v.AcceptUpdateState(); v.Do("install");
                Check("после отказа обновления установка снова доступна", installs == 1);
                var c = new MainView(); c.S.UpdateEnabled = true; c.Show("welcome"); int checks = 0;
                c.Command += delegate (string cmd) { if (cmd == "checkupdate") checks++; };
                c.Do("checkupdate"); c.Do("checkupdate");
                Check("повторный клик проверки не дублирует команду", checks == 1);
                c.S.UpdateState = "current"; c.AcceptUpdateState(); c.Do("checkupdate");
                Check("проверку можно повторить после ответа", checks == 2);
            }

            string[] ok = { "https://boosty.to/eg01stgames", "https://t.me/eg01stgame", "https://discord.gg/Zf69zsm5dQ", "https://vk.com/ego1stgames", "https://www.youtube.com/@egoist_games_one",
                "https://youtube.com/@egoist_games_one", "https://boosty.to/eg01stgames/" };
            foreach (string u in ok) Check("ссылка разрешена: " + u, Proto.IsAllowedUrl(u));
            string[] no = { "", "http://boosty.to/eg01stgames", "https://boosty.to/eg01stgames?x=1", "https://boosty.to/eg01stgames#x", "https://boosty.to.evil.com/eg01stgames", "https://evil.com/boosty.to/eg01stgames",
                "https://user@boosty.to/eg01stgames", "https://boosty.to:444/eg01stgames", "https://boosty.to/eg01stgames2", "https://boosty.to/", "https://discord.gg/zf69zsm5dq", "javascript:alert(1)",
                "file:///C:/Windows/System32/cmd.exe", "C:\\Windows\\notepad.exe", "\\\\server\\share\\x.exe", "https://t.me/eg01stgame/../evil", "ftp://boosty.to/eg01stgames", "https://boosty.to/eg01stgames\n" };
            foreach (string u in no) Check("ссылка отвергнута: " + u.Replace("\n", "\\n"), !Proto.IsAllowedUrl(u));

            Check("команда install с флагом", Proto.Cmd(3, "install", true) == "{\"seq\":3,\"cmd\":\"install\",\"options\":{\"launcher\":true}}\n");
            Check("команда close без options", Proto.Cmd(4, "close", true) == "{\"seq\":4,\"cmd\":\"close\"}\n");
            Check("команда checkupdate не содержит options", Proto.Cmd(5, "checkupdate", true) == "{\"seq\":5,\"cmd\":\"checkupdate\"}\n");
            Check("команда update не содержит options", Proto.Cmd(6, "update", false) == "{\"seq\":6,\"cmd\":\"update\"}\n");
            foreach (string c in Proto.Cmds) Check("команда в списке: " + c, Proto.Cmd(1, c, false).EndsWith("}\n") && Proto.Cmd(1, c, false).IndexOf('\n') == Proto.Cmd(1, c, false).Length - 1);
            bool threw = false; try { Proto.Cmd(1, "format", null); } catch (ArgumentException) { threw = true; }
            Check("неизвестная команда не формируется", threw);
            Check("ровно девять команд, включая две команды обновления", Proto.Cmds.Length == 9);

            string tmp = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "wxq-ui-selftest-" + Guid.NewGuid().ToString("N") + ".jsonl");
            try
            {
                Proto.Append(tmp, Proto.Cmd(1, "options", true)); Proto.Append(tmp, Proto.Cmd(2, "install", false));
                string[] lines = File.ReadAllText(tmp).Split('\n');
                Check("дописывание: две полные строки", lines.Length == 3 && lines[2] == "" && lines[0].Contains("\"seq\":1") && lines[1].Contains("\"seq\":2"));
            }
            finally { try { File.Delete(tmp); } catch (Exception) { } }

            Console.WriteLine("selftest: " + (total - failed) + " из " + total);
            return failed == 0 ? 0 : 1;
        }
    }

    // ===== Демо: окно + панель переключения экранов и фиктивный прогресс =====
    static class Demo
    {
        public static bool Auto;
        public static int Run(Application app)
        {
            var v = new MainView();
            v.S.TranslationVersion = "0.9.4"; v.S.GameVersion = "1.0.33.8"; v.S.LauncherInstalled = true;
            var w = new SetupWindow(v);
            DispatcherTimer tick = null;
            Action startFake = delegate
            {
                v.S.Percent = 0; v.S.Step = 1; v.S.Detail = "Ищу игру"; v.Show("progress");
                if (tick != null) tick.Stop();
                tick = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(120) };
                tick.Tick += delegate
                {
                    v.S.Percent += 0.7;
                    double p = v.S.Percent, acc = 0; int step = 1;
                    double[] wts = { 3, 10, 20, 45, 22 };
                    string[] det = { "Ищу игру", "Сверяю версию с перечнем", "Копирую оригинальные файлы", "Записываю перевод", "Проверяю записанное" };
                    for (int i = 0; i < 5; i++) { acc += wts[i]; if (p < acc) { step = i + 1; break; } step = 6; }
                    v.S.Step = step; v.S.Detail = step <= 5 ? det[step - 1] : "";
                    v.S.Eta = "осталось ~" + Math.Max(1, (int)Math.Ceiling((100 - p) / 12)) + " мин";
                    v.S.File = "Resources\\Lua\\lang\\ru_RU\\file_" + ((int)(p * 7)).ToString(CultureInfo.InvariantCulture) + ".bytes";
                    if (p >= 100) { tick.Stop(); v.S.Percent = 100; v.UpdateProgress(false); v.Show("done"); return; }
                    v.UpdateProgress(false);
                };
                tick.Start();
            };
            v.Command += delegate (string cmd)
            {
                switch (cmd)
                {
                    case "close": w.Close(); break;
                    case "install": startFake(); break;
                    case "remove": v.Show("removed"); break;
                    case "back": v.Show("welcome"); break;
                    default: break;   // launch/options: в демо ничего
                }
            };
            // панель выбора экранов
            var panel = new Window { Title = "Demo", Width = 150, Height = 330, Topmost = true, ResizeMode = ResizeMode.NoResize, WindowStartupLocation = WindowStartupLocation.Manual, Background = Tk.Bg };
            var sp = new StackPanel { Margin = new Thickness(8) };
            string[][] items = { new[] { "Приветствие", "welcome" }, new[] { "Установка", "p" }, new[] { "Готово", "done" }, new[] { "Сбой", "fail" }, new[] { "Установлено", "installed" }, new[] { "Удалено", "removed" } };
            foreach (var it in items)
            {
                string key = it[1];
                var b = new Button { Content = it[0], Margin = new Thickness(0, 0, 0, 6), Padding = new Thickness(6) };
                b.Click += delegate { if (key == "p") startFake(); else { if (tick != null) tick.Stop(); v.Show(key); } };
                sp.Children.Add(b);
            }
            panel.Content = sp;
            w.Loaded += delegate { panel.Left = w.Left + w.Width + 12; panel.Top = w.Top; panel.Show(); };
            w.Closed += delegate { panel.Close(); };
            v.Show("welcome");
            int rc = 0;
            app.DispatcherUnhandledException += delegate (object o, DispatcherUnhandledExceptionEventArgs e) { Console.Error.WriteLine(e.Exception.ToString()); e.Handled = true; rc = 3; w.Close(); };
            if (Auto)
            {
                // самопроверка анимированного пути: установка -> готово -> сбой -> установлено -> подтверждение -> закрыть
                var seq = new Action[] { () => v.Do("install"), () => v.Show("done"), () => v.Show("fail"), () => v.Show("installed"), () => v.Show("removed"), () => w.Close() };
                int idx = 0;
                var at = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(1200) };
                at.Tick += delegate { seq[idx++](); if (idx >= seq.Length) at.Stop(); };
                w.Loaded += delegate { at.Start(); };
            }
            app.Run(w);
            return rc;
        }
    }
}
