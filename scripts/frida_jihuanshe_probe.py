#!/usr/bin/env python3
"""Capture sanitized JiHuanShe runtime network signals with Frida.

The output is a JSONL stream intended for endpoint discovery. Sensitive values
are redacted both in the injected JavaScript and again before Python writes
events to disk.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import frida
except ImportError as exc:  # pragma: no cover - local operator hint
    raise SystemExit(
        "frida is not importable. Set PYTHONPATH=tools\\frida_py or install frida-tools."
    ) from exc


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
OUT_DIR = PROJECT_ROOT / "data" / "collector_runs" / "frida_jhs"

SENSITIVE_KEY_RE = re.compile(
    r"(authorization|token|cookie|session|ticket|sign|secret|password|phone|mobile|"
    r"openid|unionid|jwt|bearer|key)",
    re.I,
)
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
LONG_SECRET_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{48,}(?![A-Za-z0-9_-])")


JS_CODE = r"""
'use strict';

function emit(event) {
  try {
    send(event);
  } catch (_) {}
}

var SENSITIVE_RE = /(authorization|token|cookie|session|ticket|sign|secret|password|phone|mobile|openid|unionid|jwt|bearer|key)/i;
var JWT_RE = /eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g;
var LONG_SECRET_RE = /(^|[^A-Za-z0-9_-])([A-Za-z0-9_-]{48,})(?=$|[^A-Za-z0-9_-])/g;

function redactText(value) {
  var s = String(value);
  s = s.replace(JWT_RE, '<jwt>');
  s = s.replace(/(Authorization\s*:\s*)([^\r\n]+)/ig, '$1<redacted>');
  s = s.replace(/(Cookie\s*:\s*)([^\r\n]+)/ig, '$1<redacted>');
  s = s.replace(/(["']?(?:token|access_token|refresh_token|authorization|cookie|session|ticket|sign|openid|unionid|phone|mobile|password|secret)["']?\s*[:=]\s*["']?)([^"',&\s}\]]+)/ig, '$1<redacted>');
  s = s.replace(LONG_SECRET_RE, '$1<secret>');
  if (s.length > 2400) {
    s = s.slice(0, 2400) + '...<truncated>';
  }
  return s;
}

function sanitizeUrl(value) {
  var s = String(value);
  s = s.replace(/([?&][^=&#]*(?:token|auth|session|ticket|sign|secret|key|cookie|openid|unionid|phone|mobile)[^=&#]*=)[^&#]*/ig, '$1<redacted>');
  s = redactText(s);
  if (s.length > 1600) {
    s = s.slice(0, 1600) + '...<truncated>';
  }
  return s;
}

function safeValue(name, value) {
  if (value === null || value === undefined) {
    return null;
  }
  var s = String(value);
  if (SENSITIVE_RE.test(String(name || '')) || JWT_RE.test(s) || /Bearer\s+/i.test(s)) {
    return '<redacted len=' + s.length + '>';
  }
  return redactText(s);
}

function requestSummary(request) {
  var summary = {};
  try {
    summary.url = sanitizeUrl(request.url().toString());
  } catch (e) {
    summary.url_error = String(e);
  }
  try {
    summary.method = String(request.method());
  } catch (_) {}
  try {
    var headers = request.headers();
    var names = [];
    var values = {};
    var size = headers.size();
    for (var i = 0; i < size; i++) {
      var name = String(headers.name(i));
      names.push(name);
      values[name] = safeValue(name, headers.value(i));
    }
    summary.header_names = names;
    summary.headers = values;
  } catch (e) {
    summary.header_error = String(e);
  }
  return summary;
}

function hookJava() {
  var installed = [];

  function tryHook(name, callback) {
    try {
      callback();
      installed.push(name);
    } catch (e) {
      emit({kind: 'hook_error', hook: name, error: String(e)});
    }
  }

  tryHook('okhttp3.OkHttpClient.newCall', function () {
    var Client = Java.use('okhttp3.OkHttpClient');
    Client.newCall.overload('okhttp3.Request').implementation = function (request) {
      var summary = requestSummary(request);
      summary.kind = 'okhttp.newCall';
      emit(summary);
      return this.newCall(request);
    };
  });

  tryHook('okhttp3.Request$Builder.url', function () {
    var Builder = Java.use('okhttp3.Request$Builder');
    Builder.url.overload('java.lang.String').implementation = function (url) {
      emit({kind: 'okhttp.builder.url', url: sanitizeUrl(url)});
      return this.url(url);
    };
  });

  tryHook('okhttp3.Request$Builder.headers', function () {
    var Builder = Java.use('okhttp3.Request$Builder');
    Builder.header.overload('java.lang.String', 'java.lang.String').implementation = function (name, value) {
      emit({kind: 'okhttp.builder.header', name: String(name), value: safeValue(name, value)});
      return this.header(name, value);
    };
    Builder.addHeader.overload('java.lang.String', 'java.lang.String').implementation = function (name, value) {
      emit({kind: 'okhttp.builder.addHeader', name: String(name), value: safeValue(name, value)});
      return this.addHeader(name, value);
    };
  });

  tryHook('java.net.URL.openConnection', function () {
    var URL = Java.use('java.net.URL');
    URL.openConnection.overload().implementation = function () {
      emit({kind: 'url.openConnection', url: sanitizeUrl(this.toString())});
      return this.openConnection();
    };
    URL.openConnection.overload('java.net.Proxy').implementation = function (proxy) {
      emit({kind: 'url.openConnection.proxy', url: sanitizeUrl(this.toString())});
      return this.openConnection(proxy);
    };
  });

  tryHook('java.net.URLConnection.headers', function () {
    var URLConnection = Java.use('java.net.URLConnection');
    URLConnection.setRequestProperty.overload('java.lang.String', 'java.lang.String').implementation = function (name, value) {
      emit({kind: 'urlconnection.setRequestProperty', name: String(name), value: safeValue(name, value)});
      return this.setRequestProperty(name, value);
    };
    URLConnection.addRequestProperty.overload('java.lang.String', 'java.lang.String').implementation = function (name, value) {
      emit({kind: 'urlconnection.addRequestProperty', name: String(name), value: safeValue(name, value)});
      return this.addRequestProperty(name, value);
    };
  });

  tryHook('java.net.HttpURLConnection.responses', function () {
    var HttpURLConnection = Java.use('java.net.HttpURLConnection');
    HttpURLConnection.getResponseCode.implementation = function () {
      var code = this.getResponseCode();
      var url = '';
      var method = '';
      try { url = sanitizeUrl(this.getURL().toString()); } catch (_) {}
      try { method = String(this.getRequestMethod()); } catch (_) {}
      emit({kind: 'httpurlconnection.responseCode', url: url, method: method, status_code: code});
      return code;
    };
  });

  tryHook('android.webkit.WebView.loadUrl', function () {
    var WebView = Java.use('android.webkit.WebView');
    WebView.loadUrl.overload('java.lang.String').implementation = function (url) {
      emit({kind: 'webview.loadUrl', url: sanitizeUrl(url)});
      return this.loadUrl(url);
    };
    WebView.loadUrl.overload('java.lang.String', 'java.util.Map').implementation = function (url, headers) {
      emit({kind: 'webview.loadUrl.headers', url: sanitizeUrl(url)});
      return this.loadUrl(url, headers);
    };
    WebView.postUrl.implementation = function (url, postData) {
      var length = 0;
      try { length = postData.length; } catch (_) {}
      emit({kind: 'webview.postUrl', url: sanitizeUrl(url), body_len: length});
      return this.postUrl(url, postData);
    };
  });

  emit({kind: 'hook_status', phase: 'java_hooks_installed', hooks: installed});
}

function bytesToText(ptr, length) {
  var n = Math.min(length, 4096);
  if (n <= 0) {
    return null;
  }
  var bytes;
  try {
    bytes = new Uint8Array(ptr.readByteArray(n));
  } catch (_) {
    return null;
  }
  var out = '';
  var printable = 0;
  for (var i = 0; i < bytes.length; i++) {
    var b = bytes[i];
    if (b === 9 || b === 10 || b === 13 || (b >= 32 && b <= 126)) {
      out += String.fromCharCode(b);
      printable += 1;
    } else {
      out += '.';
    }
  }
  if (bytes.length === 0 || printable / bytes.length < 0.45) {
    return null;
  }
  return out;
}

function interestingText(text, direction) {
  if (!text) {
    return false;
  }
  if (/jihuanshe|api\.|\/api\/|Host:|GET |POST |PUT |card|market|price|raw_data/i.test(text)) {
    return true;
  }
  if (direction === 'read' && /[{[][^\r\n]{8,}/.test(text) && /price|card|data|raw_data|market/i.test(text)) {
    return true;
  }
  return false;
}

function sendTlsText(direction, fnName, ptr, length) {
  var text = bytesToText(ptr, length);
  if (!interestingText(text, direction)) {
    return;
  }
  emit({
    kind: 'tls.plaintext',
    direction: direction,
    function_name: fnName,
    byte_len: length,
    snippet: redactText(text)
  });
}

function attachNativeExport(name, onEnterHandler, onLeaveHandler) {
  var address = null;
  try {
    address = Module.findExportByName(null, name);
  } catch (_) {}
  if (address === null) {
    return false;
  }
  Interceptor.attach(address, {
    onEnter: onEnterHandler,
    onLeave: onLeaveHandler
  });
  return true;
}

function cstring(ptrValue) {
  if (ptrValue === null || ptrValue.isNull()) {
    return '';
  }
  try {
    return ptrValue.readCString() || '';
  } catch (_) {
    return '';
  }
}

function suspiciousNeedle(text) {
  return /frida|gum-js-loop|gadget|xposed|zygisk|magisk|superuser|supersu|busybox|\.fsx64|\.fsarm/i.test(String(text || ''));
}

function suspiciousPath(text) {
  var s = String(text || '');
  if (s.length === 0) {
    return false;
  }
  return /(^|\/)(su|magisk|busybox)$|frida|gum-js-loop|gadget|xposed|zygisk|superuser|supersu|\.fsx64|\.fsarm|\/proc\/net\/tcp/i.test(s);
}

function hookPathResult(installed, name, index) {
  var address = null;
  try { address = Module.findExportByName(null, name); } catch (_) {}
  if (address === null) {
    return;
  }
  Interceptor.attach(address, {
    onEnter: function (args) {
      this.path = cstring(args[index]);
      this.block = suspiciousPath(this.path);
    },
    onLeave: function (retval) {
      if (this.block) {
        emit({kind: 'stealth.block_path', function_name: name, path: redactText(this.path)});
        retval.replace(ptr(-1));
      }
    }
  });
  installed.push(name);
}

function hookPropertyGet(installed) {
  var address = null;
  try { address = Module.findExportByName(null, '__system_property_get'); } catch (_) {}
  if (address === null) {
    return;
  }
  var replacements = {
    'ro.debuggable': '0',
    'ro.secure': '1',
    'service.adb.root': '',
    'ro.build.tags': 'release-keys'
  };
  Interceptor.attach(address, {
    onEnter: function (args) {
      this.key = cstring(args[0]);
      this.out = args[1];
    },
    onLeave: function (retval) {
      if (Object.prototype.hasOwnProperty.call(replacements, this.key)) {
        var value = replacements[this.key];
        try {
          this.out.writeUtf8String(value);
          retval.replace(value.length);
          emit({kind: 'stealth.property', key: this.key, value: value});
        } catch (_) {}
      }
    }
  });
  installed.push('__system_property_get');
}

function hookExec(installed) {
  var address = null;
  try { address = Module.findExportByName(null, 'execve'); } catch (_) {}
  if (address === null) {
    return;
  }
  var original = new NativeFunction(address, 'int', ['pointer', 'pointer', 'pointer']);
  Interceptor.replace(address, new NativeCallback(function (pathPtr, argv, envp) {
    var path = cstring(pathPtr);
    if (suspiciousPath(path)) {
      emit({kind: 'stealth.block_exec', path: redactText(path)});
      return -1;
    }
    return original(pathPtr, argv, envp);
  }, 'int', ['pointer', 'pointer', 'pointer']));
  installed.push('execve');
}

function hookSystem(installed) {
  var address = null;
  try { address = Module.findExportByName(null, 'system'); } catch (_) {}
  if (address === null) {
    return;
  }
  var original = new NativeFunction(address, 'int', ['pointer']);
  Interceptor.replace(address, new NativeCallback(function (commandPtr) {
    var command = cstring(commandPtr);
    if (suspiciousPath(command) || suspiciousNeedle(command)) {
      emit({kind: 'stealth.block_system', command: redactText(command)});
      return -1;
    }
    return original(commandPtr);
  }, 'int', ['pointer']));
  installed.push('system');
}

function hookStringScans(installed) {
  ['strstr', 'strcasestr'].forEach(function (name) {
    var address = null;
    try { address = Module.findExportByName(null, name); } catch (_) {}
    if (address === null) {
      return;
    }
    Interceptor.attach(address, {
      onEnter: function (args) {
        this.needle = cstring(args[1]);
        this.block = suspiciousNeedle(this.needle);
      },
      onLeave: function (retval) {
        if (this.block && !retval.isNull()) {
          emit({kind: 'stealth.hide_string_scan', function_name: name, needle: redactText(this.needle)});
          retval.replace(ptr(0));
        }
      }
    });
    installed.push(name);
  });
}

function hookSelfKill(installed) {
  var killAddress = null;
  try { killAddress = Module.findExportByName(null, 'kill'); } catch (_) {}
  if (killAddress !== null) {
    var killOriginal = new NativeFunction(killAddress, 'int', ['int', 'int']);
    Interceptor.replace(killAddress, new NativeCallback(function (pid, sig) {
      if (pid === Process.id && (sig === 6 || sig === 9 || sig === 11)) {
        emit({kind: 'stealth.block_self_kill', function_name: 'kill', signal: sig});
        return 0;
      }
      return killOriginal(pid, sig);
    }, 'int', ['int', 'int']));
    installed.push('kill');
  }

  var tgkillAddress = null;
  try { tgkillAddress = Module.findExportByName(null, 'tgkill'); } catch (_) {}
  if (tgkillAddress !== null) {
    var tgkillOriginal = new NativeFunction(tgkillAddress, 'int', ['int', 'int', 'int']);
    Interceptor.replace(tgkillAddress, new NativeCallback(function (tgid, tid, sig) {
      if (tgid === Process.id && (sig === 6 || sig === 9 || sig === 11)) {
        emit({kind: 'stealth.block_self_kill', function_name: 'tgkill', signal: sig});
        return 0;
      }
      return tgkillOriginal(tgid, tid, sig);
    }, 'int', ['int', 'int', 'int']));
    installed.push('tgkill');
  }
}

function hookStealth(installed) {
  hookPathResult(installed, 'access', 0);
  hookPathResult(installed, 'faccessat', 1);
  hookPathResult(installed, 'open', 0);
  hookPathResult(installed, 'openat', 1);
  hookPathResult(installed, 'stat', 0);
  hookPathResult(installed, 'lstat', 0);
  hookPathResult(installed, 'readlink', 0);
  hookPropertyGet(installed);
  hookExec(installed);
  hookSystem(installed);
  hookStringScans(installed);
  hookSelfKill(installed);
}

function hookNative() {
  var installed = [];

  hookStealth(installed);

  if (attachNativeExport('SSL_write', function (args) {
    this.buf = args[1];
    this.len = args[2].toInt32();
  }, function (retval) {
    var written = retval.toInt32();
    if (written > 0) {
      sendTlsText('write', 'SSL_write', this.buf, Math.min(this.len, written));
    }
  })) {
    installed.push('SSL_write');
  }

  if (attachNativeExport('SSL_read', function (args) {
    this.buf = args[1];
  }, function (retval) {
    var read = retval.toInt32();
    if (read > 0) {
      sendTlsText('read', 'SSL_read', this.buf, read);
    }
  })) {
    installed.push('SSL_read');
  }

  if (attachNativeExport('SSL_write_ex', function (args) {
    this.buf = args[1];
    this.len = args[2].toInt32();
  }, function (retval) {
    if (retval.toInt32() === 1) {
      sendTlsText('write', 'SSL_write_ex', this.buf, this.len);
    }
  })) {
    installed.push('SSL_write_ex');
  }

  if (attachNativeExport('SSL_read_ex', function (args) {
    this.buf = args[1];
    this.len = args[2].toInt32();
  }, function (retval) {
    if (retval.toInt32() === 1) {
      sendTlsText('read', 'SSL_read_ex', this.buf, this.len);
    }
  })) {
    installed.push('SSL_read_ex');
  }

  emit({kind: 'hook_status', phase: 'native_hooks_installed', hooks: installed});
}

function tryInstallJava(attempt) {
  if (typeof Java !== 'undefined' && Java.available) {
    Java.perform(function () {
      hookJava();
    });
    return;
  }
  emit({kind: 'hook_status', phase: 'java_unavailable', attempt: attempt});
  if (attempt < 8) {
    setTimeout(function () {
      tryInstallJava(attempt + 1);
    }, 1500);
  }
}

setImmediate(function () {
  emit({kind: 'hook_status', phase: 'script_start', pid: Process.id});
  hookNative();
  tryInstallJava(1);
});
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sanitize_string(value: str, key: str = "") -> str:
    if SENSITIVE_KEY_RE.search(key):
        return f"<redacted len={len(value)}>"
    value = JWT_RE.sub("<jwt>", value)
    value = re.sub(r"(Authorization\s*:\s*)([^\r\n]+)", r"\1<redacted>", value, flags=re.I)
    value = re.sub(r"(Cookie\s*:\s*)([^\r\n]+)", r"\1<redacted>", value, flags=re.I)
    value = re.sub(
        r"([\"']?(?:token|access_token|refresh_token|authorization|cookie|session|ticket|sign|"
        r"openid|unionid|phone|mobile|password|secret)[\"']?\s*[:=]\s*[\"']?)([^\"',&\s}\]]+)",
        r"\1<redacted>",
        value,
        flags=re.I,
    )
    value = LONG_SECRET_RE.sub("<secret>", value)
    return value if len(value) <= 5000 else value[:5000] + "...<truncated>"


def sanitize_payload(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        result = {}
        for item_key, item_value in value.items():
            text_key = str(item_key)
            if SENSITIVE_KEY_RE.search(text_key):
                result[text_key] = f"<redacted type={type(item_value).__name__}>"
            else:
                result[text_key] = sanitize_payload(item_value, text_key)
        return result
    if isinstance(value, list):
        return [sanitize_payload(item, key) for item in value[:80]]
    if isinstance(value, str):
        return sanitize_string(value, key)
    return value


def choose_process(device: frida.core.Device, package: str) -> int | None:
    processes = device.enumerate_processes()
    exact = [proc for proc in processes if proc.name == package]
    if exact:
        return exact[0].pid
    prefixed = [proc for proc in processes if proc.name.startswith(package + ":")]
    return prefixed[0].pid if prefixed else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Frida probe for JiHuanShe runtime traffic.")
    parser.add_argument("--host", default="127.0.0.1:27042", help="Frida remote device host.")
    parser.add_argument("--package", default="com.jihuanshe", help="Android package/process name.")
    parser.add_argument("--duration", type=int, default=120, help="Capture duration in seconds.")
    parser.add_argument("--spawn", action="store_true", help="Spawn the package instead of attaching to a running process.")
    parser.add_argument("--out", help="Output JSONL path. Defaults to data/collector_runs/frida_jhs/<stamp>.jsonl")
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else OUT_DIR / f"{now_stamp()}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    with out_path.open("a", encoding="utf-8") as handle:

        def write_event(event: dict[str, Any]) -> None:
            event = sanitize_payload(event)
            event.setdefault("captured_at", now_iso())
            kind = str(event.get("kind") or "unknown")
            counts[kind] = counts.get(kind, 0) + 1
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

        def on_message(message: dict[str, Any], data: bytes | None) -> None:
            if message.get("type") == "send":
                payload = message.get("payload")
                if isinstance(payload, dict):
                    write_event(payload)
                else:
                    write_event({"kind": "frida.message", "payload": str(payload)[:1000]})
                return
            if message.get("type") == "error":
                write_event({
                    "kind": "frida.error",
                    "description": message.get("description"),
                    "stack": message.get("stack"),
                })
                return
            write_event({"kind": "frida.other", "message": message})

        manager = frida.get_device_manager()
        device = manager.add_remote_device(args.host)
        spawned_pid: int | None = None
        if args.spawn:
            spawned_pid = device.spawn([args.package])
            pid = spawned_pid
        else:
            pid = choose_process(device, args.package)
            if pid is None:
                raise SystemExit(f"No running process found for {args.package}. Start the app or rerun with --spawn.")

        print(f"attaching pid={pid} out={out_path}")
        session = device.attach(pid)
        script = session.create_script(JS_CODE)
        script.on("message", on_message)
        script.load()
        if spawned_pid is not None:
            device.resume(spawned_pid)

        deadline = time.monotonic() + max(args.duration, 1)
        try:
            while time.monotonic() < deadline:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                session.detach()
            except Exception:
                pass

    print(f"wrote={out_path}")
    print("counts=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
