using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

// Console-free entry point for EnvPilot.
//
// A .bat cannot avoid the black window: Windows allocates a console for cmd.exe
// before the first line of the file runs, so start, mshta and hidden
// self-relaunching all execute too late to prevent it. Compiling the entry
// point as a WinExe means no console is ever created.
//
// Rebuild with tools\build-launcher.ps1 after changing this file.
internal static class EnvPilotLauncher
{
    private const string GuiScript = "vfd2-env.ps1";

    private static int Main()
    {
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string script = Path.Combine(root, GuiScript);
        string logDir = Path.Combine(root, "logs");

        try
        {
            Directory.CreateDirectory(logDir);
        }
        catch (Exception ex)
        {
            Fail(null, "Could not create the logs folder:" + Environment.NewLine + ex.Message);
            return 1;
        }

        if (!File.Exists(script))
        {
            Fail(logDir, GuiScript + " was not found in:" + Environment.NewLine + root);
            return 1;
        }

        // -STA is required for WinForms; -WindowStyle Hidden and CreateNoWindow
        // keep the PowerShell host itself invisible.
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = "powershell.exe";
        psi.Arguments =
            "-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -STA -File \""
            + script + "\"";
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;

        try
        {
            Process.Start(psi);
        }
        catch (Exception ex)
        {
            Fail(logDir, "Could not start EnvPilot:" + Environment.NewLine + ex.Message);
            return 1;
        }

        return 0;
    }

    private static void Fail(string logDir, string message)
    {
        if (logDir != null)
        {
            try
            {
                File.AppendAllText(
                    Path.Combine(logDir, "launcher_error.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " | "
                        + message.Replace(Environment.NewLine, " ") + Environment.NewLine);
            }
            catch
            {
                // The dialog below is the fallback when even logging fails.
            }
        }

        MessageBox.Show(message, "EnvPilot", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}
