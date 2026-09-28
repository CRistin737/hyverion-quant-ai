//! Hyverion Quant AI desktop shell.
//!
//! Responsibilities (and nothing more):
//! - start the Python core (control API) as a child process bound to loopback,
//!   with a fresh per-launch bearer token;
//! - proxy the webview's API calls (`api_request`), so the bearer token never
//!   leaves this process and the webview never talks to the network itself;
//! - store user secrets in the OS keychain under the same service/name scheme as
//!   `trading_bot.security.secrets.KeyringSecretStore`, so secrets never travel
//!   over HTTP and are never readable back by the webview;
//! - stop the core when the app exits.
//!
//! The shell never talks to an exchange and never submits orders.

use std::net::TcpListener;
use std::path::PathBuf;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

const KEYCHAIN_SERVICE: &str = "hyverion-quant-ai";
const PROVIDER_IDS: [&str; 4] = ["anthropic", "openai", "xai", "gemini"];
const FIXED_SECRET_NAMES: [&str; 9] = [
    // Alpaca PAPER only; no live broker credential name exists.
    "broker:alpaca_paper:key_id",
    "broker:alpaca_paper:secret_key",
    "source:news_api_key",
    "source:x_bearer_token",
    "source:reddit_client_id",
    "source:reddit_client_secret",
    // Free data sources (FRED, Finnhub) and the contact email SEC/BLS require.
    "data:fred:api_key",
    "data:finnhub:api_key",
    "data:contact_email",
];

/// Internal connection details. Never serialised to the webview.
#[derive(Clone)]
struct Session {
    api_base: String,
    token: String,
    managed_core: bool,
}

/// What the webview may know about the session.
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct PublicSession {
    managed_core: bool,
}

#[derive(Serialize)]
struct ApiResponse {
    status: u16,
    body: serde_json::Value,
}

struct CoreState {
    session: Session,
    http: reqwest::Client,
    child: Mutex<Option<Child>>,
    start_error: Mutex<Option<String>>,
}

/// Only the control API surface is reachable through the proxy.
fn api_path_allowed(path: &str) -> bool {
    (path.starts_with("/api/v1/") || path.starts_with("/health/"))
        && !path.contains("..")
        && !path.contains("://")
        && !path.contains('#')
        && !path.contains(char::is_whitespace)
}

/// Only names the Python side actually reads may be written.
fn secret_name_allowed(name: &str) -> bool {
    if FIXED_SECRET_NAMES.contains(&name) {
        return true;
    }
    PROVIDER_IDS
        .iter()
        .any(|id| name == format!("provider:{id}:api_key"))
}

fn random_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).map_err(|err| format!("token generation failed: {err}"))?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn free_loopback_port() -> Result<u16, String> {
    let listener =
        TcpListener::bind("127.0.0.1:0").map_err(|err| format!("no free port: {err}"))?;
    listener
        .local_addr()
        .map(|addr| addr.port())
        .map_err(|err| format!("no free port: {err}"))
}

/// Development: run the core from the repository with `uv`.
/// Release: run the bundled PyInstaller sidecar that sits next to the executable.
fn core_command(port: u16) -> Result<Command, String> {
    let port_arg = port.to_string();
    if cfg!(debug_assertions) {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .map_err(|err| format!("repository not found: {err}"))?;
        let mut command = Command::new("uv");
        command
            .args(["run", "python", "-m", "trading_bot", "api", "--port", &port_arg])
            .current_dir(repo);
        Ok(command)
    } else {
        let exe = std::env::current_exe().map_err(|err| format!("executable path: {err}"))?;
        let sidecar = exe
            .parent()
            .ok_or("executable has no parent directory")?
            .join("hyverion-core");
        let mut command = Command::new(sidecar);
        command.args(["api", "--port", &port_arg]);
        Ok(command)
    }
}

fn start_core(session: &Session, port: u16) -> Result<Child, String> {
    let mut command = core_command(port)?;
    command
        .env("CONTROL_API_TOKEN", &session.token)
        // The core stops itself if this shell disappears (crash / force quit).
        .env("HYVERION_SHELL_PID", std::process::id().to_string())
        // We keep the write end of the core's stdin; if this process dies the
        // pipe closes and the core stops on EOF (no PID-reuse window).
        .env("HYVERION_SHELL_STDIN_WATCH", "1")
        // Own process group, so `uv` and its Python child are stopped together.
        .process_group(0)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit());
    command
        .spawn()
        .map_err(|err| format!("could not start the Hyverion core: {err}"))
}

#[tauri::command]
fn get_session(state: State<'_, CoreState>) -> Result<PublicSession, String> {
    if let Some(error) = state.start_error.lock().map_err(|e| e.to_string())?.clone() {
        return Err(error);
    }
    Ok(PublicSession { managed_core: state.session.managed_core })
}

/// Loopback proxy to the control API. Adds the bearer token here, in Rust.
#[tauri::command]
async fn api_request(
    state: State<'_, CoreState>,
    method: String,
    path: String,
    body: Option<serde_json::Value>,
) -> Result<ApiResponse, String> {
    if !api_path_allowed(&path) {
        return Err("ruta no permitida".into());
    }
    let url = format!("{}{}", state.session.api_base, path);
    let request = match method.as_str() {
        "GET" => state.http.get(url),
        "POST" => state.http.post(url),
        _ => return Err("método no permitido".into()),
    };
    let request = request.bearer_auth(&state.session.token);
    let request = match body {
        Some(value) => request.json(&value),
        None => request,
    };
    let response = request.send().await.map_err(|err| {
        if err.is_timeout() {
            "timeout".to_string()
        } else {
            "offline".to_string()
        }
    })?;
    let status = response.status().as_u16();
    let text = response.text().await.map_err(|_| "offline".to_string())?;
    let body = if text.is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::from_str(&text).unwrap_or(serde_json::Value::String(text))
    };
    Ok(ApiResponse { status, body })
}

#[tauri::command]
fn secret_set(name: String, value: String) -> Result<(), String> {
    if !secret_name_allowed(&name) {
        return Err("secret name not allowed".into());
    }
    let value = value.trim();
    if value.is_empty() {
        return Err("secret value is empty".into());
    }
    keyring::Entry::new(KEYCHAIN_SERVICE, &name)
        .and_then(|entry| entry.set_password(value))
        .map_err(|err| format!("keychain unavailable: {err}"))
}

/// Reports presence only; secret values are never returned to the webview.
#[tauri::command]
fn secret_status(names: Vec<String>) -> Result<Vec<(String, bool)>, String> {
    names
        .into_iter()
        .map(|name| {
            if !secret_name_allowed(&name) {
                return Err(format!("secret name not allowed: {name}"));
            }
            let present = keyring::Entry::new(KEYCHAIN_SERVICE, &name)
                .and_then(|entry| entry.get_password())
                .is_ok();
            Ok((name, present))
        })
        .collect()
}

#[tauri::command]
fn secret_delete(name: String) -> Result<(), String> {
    if !secret_name_allowed(&name) {
        return Err("secret name not allowed".into());
    }
    match keyring::Entry::new(KEYCHAIN_SERVICE, &name).and_then(|entry| entry.delete_credential())
    {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(err) => Err(format!("keychain unavailable: {err}")),
    }
}

/// Diagnostics bridge: webview errors land in the shell's stderr (never secrets).
#[tauri::command]
fn client_log(level: String, message: String) {
    // One bounded line: no newlines from the webview can forge log entries.
    let clean = |text: &str, limit: usize| -> String {
        text.chars().take(limit).map(|c| if c.is_control() { ' ' } else { c }).collect()
    };
    eprintln!("[webview:{}] {}", clean(&level, 16), clean(&message, 500));
}

/// Export filenames: short, plain and CSV only. Anything else is rejected.
fn export_filename_allowed(name: &str) -> bool {
    name.len() <= 120
        && name.ends_with(".csv")
        && !name.starts_with('.')
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        && !name.contains("..")
}

/// Save a report (CSV text) into the user's Downloads folder; never overwrites.
#[tauri::command]
fn save_download(app: tauri::AppHandle, filename: String, content: String) -> Result<String, String> {
    if !export_filename_allowed(&filename) {
        return Err("filename not allowed".into());
    }
    if content.len() > 20 * 1024 * 1024 {
        return Err("export too large".into());
    }
    let dir = app.path().download_dir().map_err(|e| e.to_string())?;
    let stem = filename.trim_end_matches(".csv").to_string();
    // create_new: never overwrite and never follow an existing (or dangling)
    // symlink; the check and the create are one atomic step.
    for counter in 0..1000 {
        let name = if counter == 0 { filename.clone() } else { format!("{stem}-{counter}.csv") };
        let target = dir.join(name);
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&target) {
            Ok(mut file) => {
                use std::io::Write;
                file.write_all(content.as_bytes()).map_err(|e| e.to_string())?;
                return Ok(target.display().to_string());
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error.to_string()),
        }
    }
    Err("too many files with this name".into())
}

/// Native print dialog (includes "Save as PDF"); WKWebView ignores window.print().
/// Async so the IPC call never blocks the main thread while the sheet is open.
#[tauri::command(async)]
fn print_page(window: tauri::WebviewWindow) -> Result<(), String> {
    window.print().map_err(|e| e.to_string())
}

/// Links the app shows (SEC filings, where to get a free key) open in the
/// user's browser. Only plain https URLs; the webview itself never navigates away.
fn external_url_allowed(url: &str) -> bool {
    url.len() <= 2048
        && url.starts_with("https://")
        && !url.chars().any(|c| c.is_whitespace() || c.is_control() || c == '"' || c == '\\')
}

#[tauri::command]
fn open_external(url: String) -> Result<(), String> {
    if !external_url_allowed(&url) {
        return Err("url not allowed".into());
    }
    Command::new("/usr/bin/open")
        .arg(&url)
        .status()
        .map_err(|e| e.to_string())
        .and_then(|status| if status.success() { Ok(()) } else { Err("open failed".into()) })
}

fn stop_core(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<CoreState>() {
        if let Ok(mut guard) = state.child.lock() {
            if let Some(mut child) = guard.take() {
                // Graceful stop for the whole group, then a hard kill as fallback.
                let group = format!("-{}", child.id());
                let _ = Command::new("/bin/kill").args(["-TERM", &group]).status();
                for _ in 0..30 {
                    if matches!(child.try_wait(), Ok(Some(_))) {
                        return;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(100));
                }
                let _ = Command::new("/bin/kill").args(["-KILL", &group]).status();
                let _ = child.wait();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // An externally started core can be used with HYVERION_API_BASE + CONTROL_API_TOKEN.
    let external = std::env::var("HYVERION_API_BASE")
        .ok()
        .zip(std::env::var("CONTROL_API_TOKEN").ok());

    let app = tauri::Builder::default()
        .setup(move |app| {
            let http = reqwest::Client::builder()
                .no_proxy()
                .timeout(std::time::Duration::from_secs(30))
                .build()
                .map_err(|err| format!("http client: {err}"))?;
            let state = match external.clone() {
                Some((api_base, token)) => CoreState {
                    session: Session { api_base, token, managed_core: false },
                    http: http.clone(),
                    child: Mutex::new(None),
                    start_error: Mutex::new(None),
                },
                None => {
                    let prepared = random_token().and_then(|token| {
                        free_loopback_port().map(|port| (token, port))
                    });
                    match prepared {
                        Ok((token, port)) => {
                            let session = Session {
                                api_base: format!("http://127.0.0.1:{port}"),
                                token,
                                managed_core: true,
                            };
                            let (child, error) = match start_core(&session, port) {
                                Ok(child) => (Some(child), None),
                                Err(error) => (None, Some(error)),
                            };
                            CoreState {
                                session,
                                http: http.clone(),
                                child: Mutex::new(child),
                                start_error: Mutex::new(error),
                            }
                        }
                        Err(error) => CoreState {
                            session: Session {
                                api_base: String::new(),
                                token: String::new(),
                                managed_core: true,
                            },
                            http: http.clone(),
                            child: Mutex::new(None),
                            start_error: Mutex::new(Some(error)),
                        },
                    }
                }
            };
            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_session,
            api_request,
            secret_set,
            secret_status,
            secret_delete,
            client_log,
            save_download,
            print_page,
            open_external
        ])
        .build(tauri::generate_context!())
        .expect("error while building Hyverion Quant AI");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_core(handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn external_links_are_https_only() {
        assert!(external_url_allowed("https://www.sec.gov/Archives/edgar/data/1.htm"));
        assert!(!external_url_allowed("http://example.com"));
        assert!(!external_url_allowed("file:///etc/passwd"));
        assert!(!external_url_allowed("https://a.com/ -a Terminal"));
    }

    #[test]
    fn export_filenames_are_plain_csv_only() {
        assert!(export_filename_allowed("hyverion-operaciones-2026-07-01_2026-07-31.csv"));
        assert!(!export_filename_allowed("../secrets.csv"));
        assert!(!export_filename_allowed("report.sh"));
        assert!(!export_filename_allowed(".hidden.csv"));
        assert!(!export_filename_allowed("a/b.csv"));
    }

    #[test]
    fn secret_allowlist_matches_python_names() {
        assert!(secret_name_allowed("broker:alpaca_paper:key_id"));
        assert!(secret_name_allowed("broker:alpaca_paper:secret_key"));
        // No live credential profile can be stored, and the retired venue is gone.
        assert!(!secret_name_allowed("broker:alpaca_live:key_id"));
        assert!(!secret_name_allowed("exchange:api_key"));
        assert!(secret_name_allowed("provider:openai:api_key"));
        assert!(secret_name_allowed("source:news_api_key"));
        assert!(secret_name_allowed("data:fred:api_key"));
        assert!(secret_name_allowed("data:finnhub:api_key"));
        assert!(secret_name_allowed("data:contact_email"));
        assert!(!secret_name_allowed("data:unknown:api_key"));
        assert!(!secret_name_allowed("provider:evil:api_key"));
        assert!(!secret_name_allowed("control_api_token"));
        assert!(!secret_name_allowed("exchange:api_passphrase"));
        assert!(!secret_name_allowed(""));
    }

    #[test]
    fn proxy_only_reaches_the_control_api() {
        assert!(api_path_allowed("/api/v1/snapshot"));
        assert!(api_path_allowed("/api/v1/operations/op-1/events"));
        assert!(api_path_allowed("/health/ready"));
        assert!(!api_path_allowed("/api/v1/../admin"));
        assert!(!api_path_allowed("http://evil.example/api/v1/x"));
        assert!(!api_path_allowed("/docs"));
        assert!(!api_path_allowed("/api/v1/x y"));
    }

    #[test]
    fn token_is_256_bits_hex() {
        let token = random_token().unwrap();
        assert_eq!(token.len(), 64);
        assert!(token.chars().all(|c| c.is_ascii_hexdigit()));
    }
}
