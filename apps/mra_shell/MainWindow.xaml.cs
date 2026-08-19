using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using Windows.Graphics;
using Windows.UI;
using WinRT.Interop;

namespace mra_shell;

/// <summary>
/// MRA 正式 shell —— 唯一 GUI。职责边界：窗口 + sidecar 生命周期 + 消息转发。
/// 业务（Controller）完全在 Python sidecar，本类不承载任何业务逻辑。
/// </summary>
public sealed partial class MainWindow : Window
{
    // 最小窗口尺寸（DIP，逻辑像素）
    private const int MinWindowWidth = 1000;
    private const int MinWindowHeight = 700;

    /// <summary>
    /// 从 exe 运行目录逐级向上查找仓库根（含 pyproject.toml 的目录）。
    /// 用于替代硬编码的本机绝对路径，保证跨机器可移植；找不到返回 null。
    /// </summary>
    private static string? ResolveRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            if (File.Exists(Path.Combine(dir.FullName, "pyproject.toml")))
                return dir.FullName;
            dir = dir.Parent;
        }
        return null;
    }

    /// <summary>
    /// 从仓库根向上查找指定相对路径的资源文件（sidecar / venv 等一律以仓库根为锚）。
    /// 找不到返回 null，调用方自行决定是否降级。
    /// </summary>
    private static string? ResolveRepoAssetPath(params string[] relativeSegments)
    {
        var root = ResolveRepoRoot();
        if (root is null)
            return null;
        var candidate = Path.Combine(new[] { root }.Concat(relativeSegments).ToArray());
        return File.Exists(candidate) ? candidate : null;
    }

    private readonly PythonSidecar? _sidecar;

    // WM_GETMINMAXINFO 拦截（Win32 subclass，保证最小尺寸在系统级生效）
    private const int WM_GETMINMAXINFO = 0x0024;
    private const int GWLP_WNDPROC = -4;
    private readonly WndProcDelegate _wndProcHook;
    private readonly IntPtr _oldWndProc;

    // 前端上报的标题栏交互区（DIP）：系统 drag region 需挖掉该区，
    // 双击按钮区不会触发最大化（见 UpdateDragRects 挖孔计算）
    private RectInt32 _dragExcludeDips;

    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    public MainWindow()
    {
        InitializeComponent();
        Title = "MRA";

        // 窗口图标（任务栏/Alt-Tab 显示）：AppWindow.SetIcon 只接受 .ico 路径
        // 从 exe 运行目录向上查找仓库根 assets/icon.ico（避免硬编码本机绝对路径）
        var iconPath = ResolveRepoAssetPath("assets", "icon.ico");
        if (iconPath is not null)
            AppWindow.SetIcon(iconPath);

        // AppWindowTitleBar：HTML 内容延伸到标题栏 + 系统按钮 overlay（winui_spike 已实测通过）
        var titleBar = AppWindow.TitleBar;
        titleBar.ExtendsContentIntoTitleBar = true;
        // Tall：系统按钮 48px 高，与 HTML 48px header 垂直对齐（Standard 32px 会显得偏小）
        titleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        // 系统按钮配色：白色标题栏（与 HTML 背景一致），符号深色，hover 浅灰
        // 注意：失焦（Inactive）时若不显式设置，会回退系统主题色，必须成对覆盖
        titleBar.ButtonBackgroundColor = Color.FromArgb(0xFF, 0xFF, 0xFF, 0xFF);
        titleBar.ButtonForegroundColor = Color.FromArgb(0xFF, 0x1F, 0x23, 0x28);
        titleBar.ButtonHoverBackgroundColor = Color.FromArgb(0xFF, 0xE5, 0xE7, 0xEB);
        titleBar.ButtonHoverForegroundColor = Color.FromArgb(0xFF, 0x1F, 0x23, 0x28);
        titleBar.ButtonPressedBackgroundColor = Color.FromArgb(0xFF, 0xD1, 0xD5, 0xDB);
        titleBar.ButtonPressedForegroundColor = Color.FromArgb(0xFF, 0x1F, 0x23, 0x28);
        titleBar.ButtonInactiveBackgroundColor = Color.FromArgb(0xFF, 0xFF, 0xFF, 0xFF);
        titleBar.ButtonInactiveForegroundColor = Color.FromArgb(0xFF, 0x1F, 0x23, 0x28);
        UpdateDragRects();
        AppWindow.Changed += (_, args) =>
        {
            if (args.DidSizeChange)
                UpdateDragRects();
        };

        // 最小尺寸：subclass 窗口过程拦截 WM_GETMINMAXINFO（系统级，拖拽/Resize 均被钳制）
        var hwnd = WindowNative.GetWindowHandle(this);
        _wndProcHook = WndProc;
        _oldWndProc = SetWindowLongPtr(hwnd, GWLP_WNDPROC, Marshal.GetFunctionPointerForDelegate(_wndProcHook));

        // 启动尺寸：以最小安全尺寸启动（= MinWindowWidth/Height，物理像素按 DPI 换算，
        // 与 WndProc 的 MINMAXINFO 同一套换算；避免默认尺寸偏大导致低分辨率屏溢出）
        var startupScale = GetDpiForWindow(hwnd) / 96.0;
        AppWindow.Resize(new SizeInt32(
            (int)(MinWindowWidth * startupScale),
            (int)(MinWindowHeight * startupScale)));

        // 仓库根（含 pyproject.toml 的目录）作为所有相对路径的锚：venv、前端 HTML 一律以其为基准。
        // 从 exe 基目录向上推导，彻底摆脱硬编码的本机绝对路径，仓库可 clone 到任意位置运行。
        var projectRoot = ResolveRepoRoot();
        if (projectRoot is null)
        {
            Console.Error.WriteLine("[shell] 未找到仓库根（pyproject.toml 向上搜索失败），后端与页面不可用");
        }

        // 1. 启动 Python sidecar（唯一业务后端；失败不阻塞窗口，前端显示 backend unavailable）
        if (projectRoot is not null)
        {
            // 发布模式优先用随包的自带 runtime（runtime\python\python.exe），
            // 否则回退开发模式的 .venv\Scripts\python.exe，保证两种环境都能跑。
            var releasePython = Path.Combine(projectRoot, "runtime", "python", "python.exe");
            var pythonExe = File.Exists(releasePython)
                ? releasePython
                : Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
            try
            {
                _sidecar = new PythonSidecar(pythonExe, "-u -m maaracing_assistant.sidecar", projectRoot);
            }
            catch (Exception ex)
            {
                _sidecar = null;
                Console.Error.WriteLine($"[shell] sidecar 启动失败: {ex.Message}");
            }
        }

        web.WebMessageReceived += OnWebMessageReceived;
        Closed += OnClosed;
        // 自定义 GUI：禁用右键默认上下文菜单（放大预览 / 任意区域右键都不再弹菜单）
        web.CoreWebView2Initialized += (_, _) =>
        {
            if (web.CoreWebView2 is not null)
                web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
        };
        var indexPath = ResolveRepoAssetPath("apps", "mra_shell", "frontend", "index.html");
        if (indexPath is not null)
        {
            // 绝对 windows 路径按文件 URI 解析（auto file:/// scheme），WebView 本地加载
            web.Source = new Uri(indexPath);
        }
    }

    /// <summary>窗口过程钩子：钳制最小尺寸。MINMAXINFO 单位 = 物理像素，需按 DPI 换算。</summary>
    private IntPtr WndProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WM_GETMINMAXINFO)
        {
            var mmi = Marshal.PtrToStructure<MINMAXINFO>(lParam);
            var scale = GetDpiForWindow(hWnd) / 96.0;
            mmi.ptMinTrackSize.X = (int)(MinWindowWidth * scale);
            mmi.ptMinTrackSize.Y = (int)(MinWindowHeight * scale);
            Marshal.StructureToPtr(mmi, lParam, false);
            return IntPtr.Zero;
        }
        return CallWindowProc(_oldWndProc, hWnd, msg, wParam, lParam);
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MINMAXINFO
    {
        public POINT ptReserved;
        public POINT ptMaxSize;
        public POINT ptMaxPosition;
        public POINT ptMinTrackSize;
        public POINT ptMaxTrackSize;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr CallWindowProc(IntPtr lpPrevWndFunc, IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hwnd);

    /// <summary>HTML 标题栏拖拽区 = 顶部 52px 全宽，挖掉前端上报的交互区（brand/tabs）。
    /// 坐标单位物理像素（系统级 hit-test），与 WndProc 的 DPI 换算一致。</summary>
    private void UpdateDragRects()
    {
        var scale = GetDpiForWindow(WindowNative.GetWindowHandle(this)) / 96.0;
        int width = AppWindow.Size.Width;
        int top = (int)(52 * scale);
        var rects = new List<RectInt32>();
        bool hasExclude = _dragExcludeDips.Width > 0 && _dragExcludeDips.Height > 0;
        if (hasExclude)
        {
            int exL = (int)(_dragExcludeDips.X * scale);
            int exR = (int)((_dragExcludeDips.X + _dragExcludeDips.Width) * scale);
            int exB = (int)((_dragExcludeDips.Y + _dragExcludeDips.Height) * scale);
            // 挖孔：交互区左侧、右侧、下方三段仍可拖拽（双击空白标题栏仍能最大化）
            if (exL > 0) rects.Add(new RectInt32(0, 0, exL, top));
            if (exR < width) rects.Add(new RectInt32(exR, 0, width - exR, top));
            if (exB < top) rects.Add(new RectInt32(0, exB, width, top - exB));
        }
        else
        {
            // 未收到排除信息（如页面尚未加载完成）：整条可拖拽
            rects.Add(new RectInt32(0, 0, width, top));
        }
        if (rects.Count > 0)
            AppWindow.TitleBar.SetDragRectangles(rects.ToArray());
    }

    // ---------- HTML → C# → Python 转发 ----------

    private void OnWebMessageReceived(WebView2 sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        try
        {
            using var doc = JsonDocument.Parse(args.WebMessageAsJson);
            var root = doc.RootElement;
            var msgType = root.TryGetProperty("type", out var t) ? t.GetString() : "";
            if (msgType == "drag-exclude")
            {
                // 前端上报标题栏交互区（DIP）→ 重算系统 drag region（不回复）
                if (root.TryGetProperty("rect", out var r) && r.ValueKind == JsonValueKind.Object)
                {
                    _dragExcludeDips = new RectInt32(
                        (int)r.GetProperty("x").GetDouble(),
                        (int)r.GetProperty("y").GetDouble(),
                        (int)r.GetProperty("w").GetDouble(),
                        (int)r.GetProperty("h").GetDouble());
                    UpdateDragRects();
                }
            }
            else if (msgType == "call")
            {
                var callId = root.GetProperty("callId").GetInt64();
                var method = root.GetProperty("method").GetString() ?? "";
                var paramsEl = root.TryGetProperty("params", out var p) && p.ValueKind != JsonValueKind.Null
                    ? p
                    : (JsonElement?)null;
                _ = HandleCallAsync(sender, callId, method, paramsEl);
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[shell] webmessage 解析失败: {ex.Message}");
        }
    }

    private async Task HandleCallAsync(WebView2 sender, long callId, string method, JsonElement? paramsEl)
    {
        object? data = null;
        string? error = null;
        bool ok = true;
        try
        {
            if (_sidecar is null)
                throw new InvalidOperationException("backend unavailable");
            var resp = await _sidecar.CallAsync(method, paramsEl, TimeSpan.FromSeconds(10));
            data = resp.GetProperty("data"); // JsonElement：null 或对象直接嵌入回传
        }
        catch (Exception ex)
        {
            ok = false;
            error = ex.Message;
        }

        try
        {
            var reply = JsonSerializer.Serialize(new { type = "response", callId, ok, data, error });
            sender.CoreWebView2.PostWebMessageAsJson(reply); // 同步 API
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[shell] 回传 JS 失败: {ex.Message}");
        }
    }

    // ---------- 关闭钩子：grace period shutdown ----------

    private void OnClosed(object sender, WindowEventArgs args)
    {
        if (_sidecar is not null)
        {
            try
            {
                // UI 线程同步 await 带 UI SynchronizationContext 的 async 会死锁
                // （OnClosed 在 UI 线程，ShutdownAsync 内部 await 需要回 UI 上下文续体）。
                // Task.Run 把整个调用挪到线程池（无 SynchronizationContext），
                // 同步等待只阻塞本线程，不会死锁；最坏等待 grace 3s。
                Task.Run(() => _sidecar.ShutdownAsync(TimeSpan.FromSeconds(3)))
                    .GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"[shell] sidecar 关闭异常: {ex.Message}");
            }
            _sidecar.Dispose();
        }
    }
}
