using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

// Keep a real Python interpreter: room pages and isolated plugins start Python children.
internal static class Launcher
{
    [DllImport("kernel32.dll")] private static extern IntPtr GetStdHandle(int handle);
    [DllImport("kernel32.dll")] private static extern bool GetConsoleMode(IntPtr handle, out uint mode);
    [DllImport("kernel32.dll")] private static extern bool SetConsoleMode(IntPtr handle, uint mode);
    [DllImport("kernel32.dll")] private static extern bool SetConsoleCP(uint page);
    [DllImport("kernel32.dll")] private static extern bool SetConsoleOutputCP(uint page);

    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    public static int Main(string[] args)
    {
        try
        {
            SetConsoleCP(65001);
            SetConsoleOutputCP(65001);
            Console.OutputEncoding = new UTF8Encoding(false);
            Console.Title = "AutoKeeper";
            uint mode;
            IntPtr output = GetStdHandle(-11);
            if (GetConsoleMode(output, out mode)) SetConsoleMode(output, mode | 4);
            // The child receives Ctrl+C too; let it restore the terminal before we exit.
            Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) { e.Cancel = true; };
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string runtime = Path.Combine(root, "runtime");
            string python = Path.Combine(runtime, "python", "python.exe");
            if (!File.Exists(python)) throw new FileNotFoundException("请先完整解压下载的压缩包，再运行 AutoKeeper.exe。", python);
            var command = new StringBuilder("-I ");
            command.Append(Quote(Path.Combine(runtime, "bootstrap.py")));
            foreach (string arg in args) command.Append(" ").Append(Quote(arg));
            var start = new ProcessStartInfo(python, command.ToString());
            start.UseShellExecute = false;
            start.WorkingDirectory = root;
            start.EnvironmentVariables["PATH"] = Path.Combine(runtime, "node") + ";" +
                Path.Combine(runtime, "python") + ";" + start.EnvironmentVariables["PATH"];
            start.EnvironmentVariables["COLORTERM"] = "truecolor";
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            using (Process child = Process.Start(start))
            {
                child.WaitForExit();
                if (child.ExitCode != 0 && args.Length == 0 && !Console.IsInputRedirected)
                {
                    Console.Error.WriteLine("启动未完成。上方为错误信息，按回车关闭。");
                    Console.ReadLine();
                }
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error.Message);
            if (args.Length == 0 && !Console.IsInputRedirected) Console.ReadLine();
            return 1;
        }
    }
}
