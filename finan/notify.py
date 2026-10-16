"""Windows toast notification (winotify, with a WinRT PowerShell fallback)."""

import logging
import os
import subprocess
import tempfile

log = logging.getLogger("finan")

# AppUserModelID of Windows PowerShell; using it lets an unregistered script show toasts.
_POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

# Same XML shape as winotify's TEMPLATE (ToastImageAndText02, text in CDATA).
_FALLBACK_PS1 = """\
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$Template = @"
<toast activationType="protocol" launch="{launch}" duration="long">
    <visual>
        <binding template="ToastImageAndText02">
            <text id="1"><![CDATA[{title}]]></text>
            <text id="2"><![CDATA[{body}]]></text>
        </binding>
    </visual>
    <audio src="ms-winsoundevent:Notification.Default" loop="false" />
</toast>
"@

$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$SerializedXml.LoadXml($Template)

$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml)
$Toast.Tag = "{tag}"
$Toast.Group = "{group}"

$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}")
$Notifier.Show($Toast);
"""


def sanitize_toast_text(s: str) -> str:
    """ASCII only, no double quotes (the text is interpolated into a PowerShell command)."""
    return s.encode("ascii", "replace").decode("ascii").replace('"', "")


def _send_toast_powershell(title: str, body: str, launch_url: str, app_name: str) -> None:
    script = _FALLBACK_PS1.format(
        launch=launch_url, title=title, body=body,
        tag=title[:60], group=app_name, app_id=_POWERSHELL_APP_ID,
    )
    path = os.path.join(tempfile.gettempdir(), "finan_toast.ps1")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
        timeout=30, check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def send_toast(title: str, body: str, launch_url: str, app_name: str = "Finan") -> str:
    """Show one Windows toast. Returns the backend used: 'winotify' or 'powershell'.

    Raises RuntimeError when both backends fail.
    """
    try:
        from winotify import Notification, audio

        n = Notification(app_id=app_name, title=title, msg=body, duration="long", launch=launch_url)
        n.set_audio(audio.Default, loop=False)
        n.show()
        return "winotify"
    except Exception as exc:  # noqa: BLE001 - any failure falls back to PowerShell
        log.warning("winotify toast failed (%s: %s); falling back to PowerShell", type(exc).__name__, exc)

    try:
        _send_toast_powershell(title, body, launch_url, app_name)
        return "powershell"
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"toast failed on both backends: {type(exc).__name__}: {exc}") from exc


def send_email(subject: str, body: str) -> None:
    # TODO: email delivery is intentionally not implemented (non-goal).
    # Hook for a future SMTP implementation. Never called by tracker.py.
    raise NotImplementedError("email delivery is not implemented")
