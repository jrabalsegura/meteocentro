import { useEffect, useState, type ReactNode } from "react";

export type Session = {
  authenticated: boolean;
  private_read: boolean;
  username?: string;
  csrf_token?: string;
  expires_at?: string;
};
const messages: Record<string, string> = {
  invalid_credentials: "Usuario o contraseña incorrectos.",
  login_limited: "Demasiados intentos. Espera 15 minutos.",
  authentication_required: "Tu sesión ha caducado. Vuelve a entrar.",
  origin_rejected: "El origen no coincide con APP_ORIGIN del servidor.",
  csrf_rejected:
    "La sesión de este formulario ya no es válida. Recarga la página.",
  provider_not_available:
    "La fuente está desactivada o requiere revisión en el servidor.",
  discovery_limited_15_minutes:
    "Espera 15 minutos entre búsquedas manuales en esta red.",
  restore_source_first: "Restaura primero la estación o el origen excluido.",
  respect_retry_delay:
    "El trabajo debe respetar su espera y la cuota de la fuente.",
  job_not_retryable: "El estado de este trabajo no permite reintentarlo.",
  location_outside_scope: "La posición está fuera de las cuatro provincias.",
  invalid_identity: "El ID no tiene el formato admitido por este proveedor.",
  worker_setup_required:
    "El worker debe configurar primero la recogida de esta fuente.",
};
export async function request<T>(
  path: string,
  session?: Session,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers:
      body === undefined
        ? undefined
        : {
            "Content-Type": "application/json",
            "X-CSRF-Token": session?.csrf_token ?? "",
          },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15000),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login")
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(
      messages[data.detail?.code] ??
        (response.status === 422
          ? "Revisa los valores del formulario."
          : "No se pudo completar la operación. Reintenta."),
    );
  }
  return data as T;
}

export function Access({
  children,
  management,
}: {
  children: (session: Session) => ReactNode;
  management: boolean;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const check = () => {
      void request<Session>("/auth/session")
        .then((s) => {
          if (active) {
            setSession(s);
            setError("");
          }
        })
        .catch(() => {
          if (active) {
            setSession(null);
            setError("No se pudo comprobar el acceso. Reintentando…");
          }
        });
    };
    const expired = () =>
      setSession((s) =>
        s ? { authenticated: false, private_read: s.private_read } : null,
      );
    const visibility = () => {
      if (!document.hidden) check();
    };
    const pageshow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        setSession(null);
        check();
      }
    };
    check();
    const timer = window.setInterval(visibility, 60000);
    window.addEventListener("session-expired", expired);
    window.addEventListener("pageshow", pageshow);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active = false;
      clearInterval(timer);
      window.removeEventListener("session-expired", expired);
      window.removeEventListener("pageshow", pageshow);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
  if (!session)
    return (
      <main className="access-page">
        <h1>Meteocentro</h1>
        <p role="status">{error || "Comprobando acceso…"}</p>
      </main>
    );
  if (session.authenticated || (!management && !session.private_read))
    return children(session);
  return (
    <main className="access-page">
      <a href="/">Meteocentro</a>
      <h1>Acceso privado</h1>
      <p>
        Entra con la cuenta del administrador para consultar y gestionar la
        aplicación.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const fields = new FormData(event.currentTarget);
          setBusy(true);
          setError("");
          void request<Session>("/auth/login", undefined, {
            username: fields.get("username"),
            password: fields.get("password"),
          })
            .then(setSession)
            .catch((e) => setError(e.message))
            .finally(() => setBusy(false));
        }}
      >
        <label>
          Usuario
          <input
            name="username"
            autoComplete="username"
            maxLength={100}
            required
          />
        </label>
        <label>
          Contraseña
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            maxLength={1024}
            required
          />
        </label>
        <button disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button>
      </form>
      {error && <p role="alert">{error}</p>}
      <p className="muted">
        La cuenta se crea desde el servidor. No hay registro abierto.
      </p>
    </main>
  );
}
