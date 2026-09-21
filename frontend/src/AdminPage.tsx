import { useEffect, useRef, useState } from "react";
import { request, type Session } from "./Access";
import { date, number } from "./data";

type Source = {
  id: string;
  provider: string;
  external_id: string;
  status: string;
  eligible: boolean;
  excluded: boolean;
  own_exclusion: boolean;
  exclusion?: {
    reason: string | null;
    created_at: string;
    actor: string;
  } | null;
  review_reason: string | null;
  latitude: number | null;
  longitude: number | null;
  proposed_location: { latitude: string; longitude: string } | null;
  precision: string | null;
  capabilities: Record<string, boolean>;
};
type Station = {
  id: string;
  name: string;
  status: string;
  province_code: string | null;
  latitude: number | null;
  longitude: number | null;
  altitude_m: number | null;
  exclusion: {
    reason: string | null;
    created_at: string;
    actor: string;
  } | null;
  sources: Source[];
};
type Job = {
  id: string;
  provider: string;
  kind: string;
  status: string;
  next_run_at: string;
  attempts: number;
  last_run: {
    status: string;
    started_at: string;
    finished_at: string | null;
    error_code: string | null;
    result: Record<string, number | boolean>;
  } | null;
};
type Provider = {
  code: string;
  name: string;
  status: string;
  credential: string;
  last_polled_at: string | null;
  observation_age_seconds: number | null;
  pause_reason: string | null;
  blocked_until: string | null;
  day_calls: number;
  daily_call_budget: number;
  capabilities: Record<string, boolean | number>;
};
type Duplicate = {
  id: string;
  source_id: string;
  other_source_id: string;
  distance_m: number;
  reason: string;
  source_name: string;
  other_source_name: string;
};
type Operations = {
  worker: { state: string; last_seen_at: string | null };
  overdue_jobs: number;
  next_job_at: string | null;
  providers: { provider: string; state: string; last_new_data_at: string | null }[];
};
type Audit = {
  id: string;
  actor: string;
  action: string;
  occurred_at: string;
  details: Record<string, unknown>;
};
type Confirmation = { station: Station; source?: Source; restore: boolean };
const tabs = {
  stations: "Estaciones",
  review: "Revisión",
  excluded: "Excluidas",
  providers: "Fuentes",
  discovery: "Descubrimiento",
  jobs: "Trabajos",
  audit: "Auditoría",
};
type Tab = keyof typeof tabs;
const labels: Record<string, string> = {
  active: "Activa",
  excluded: "Excluida",
  review: "Revisión",
  pending: "Pendiente",
  running: "En curso",
  retry: "Esperando reintento",
  cancelled: "Cancelado",
  completed: "Completado",
  succeeded: "Correcto",
  failed: "Falló",
  paused: "En pausa",
  verified: "Disponible",
  disabled: "Desactivada",
  pending_terms: "Permiso pendiente",
  pending_access: "Acceso pendiente",
  current: "Observaciones y detección",
  inventory: "Inventario histórico",
  catalog: "Coordenadas",
  verify: "Verificar ID",
  history: "Importar históricos",
  potential_duplicate: "Posible coincidencia entre estaciones",
  location_changed: "Traslado propuesto",
  missing_coordinates: "Faltan coordenadas",
  missing_in_feed: "Ausente en varias revisiones",
  outside: "Fuera de las cuatro provincias",
  new_sources: "Nuevos orígenes",
  updated_sources: "Revisados",
  received: "Registros recibidos",
  valid: "Válidos",
  invalid: "Inválidos",
  profiles_checked: "Fichas comprobadas",
  located: "Ubicados",
  coverage_incomplete: "Cobertura incompleta",
  found: "ID encontrado",
  complete: "Trabajo terminado",
  inserted: "Insertados",
  unchanged: "Sin cambios",
  revised: "Corregidos",
  no_requerida: "No requiere clave",
  falta_credencial: "Falta la credencial",
  gestionada_por_worker: "Gestionada en el servidor",
  configurada: "Clave configurada",
  alive: "En marcha",
  missing: "Sin latido reciente: revisar el worker",
  fresh: "Datos recientes",
  no_polls: "Todavía sin consultas",
  poll_overdue: "Recogida retrasada",
  no_data: "Todavía sin observaciones",
  responding_without_fresh_data: "Responde, pero no entrega datos recientes",
};
const label = (value: string) => labels[value] ?? value;

function Confirm({
  value,
  busy,
  onClose,
  onConfirm,
  error,
}: {
  value: Confirmation;
  busy: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
  error: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="admin-dialog"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      aria-labelledby="confirm-title"
    >
      <h2 id="confirm-title">
        {value.restore
          ? "Restaurar"
          : value.source
            ? "Pausar origen"
            : "Eliminar de la app"}
      </h2>
      {error && (
        <p role="alert" className="admin-error">
          {error}
        </p>
      )}
      <p>
        <strong>{value.station.name}</strong>
      </p>
      <p>
        {(value.source ? [value.source] : value.station.sources)
          .map((s) => `${s.provider.toUpperCase()} · ${s.external_id}`)
          .join(" / ")}
      </p>
      <p>
        {value.restore
          ? "Se reanudará su elegibilidad y la recogida que corresponda. El periodo excluido puede contener huecos. Se conservan las otras exclusiones y las revisiones pendientes."
          : value.source
            ? "Este origen dejará de verse y recogerse individualmente. Otros orígenes elegibles de la estación seguirán visibles, con su procedencia indicada."
            : "Desaparecerá del mapa, tablas, búsquedas, gráficos y CSV. Se cancelarán sus trabajos individuales y se descartarán sus registros en los lotes compartidos."}
      </p>
      <p>
        El histórico se conserva. Esta acción no modifica la estación en los
        servicios externos.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onConfirm(
            String(new FormData(event.currentTarget).get("reason") ?? ""),
          );
        }}
      >
        <label>
          Motivo opcional
          <textarea name="reason" maxLength={1000} autoFocus />
        </label>
        <div className="admin-actions">
          <button type="button" disabled={busy} onClick={onClose}>
            Cancelar
          </button>
          <button className={value.restore ? "" : "danger"} disabled={busy}>
            {busy
              ? "Guardando…"
              : value.restore
                ? "Confirmar restauración"
                : value.source
                  ? "Confirmar pausa"
                  : "Confirmar eliminación"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

function SourceReview({
  source,
  station,
  session,
  act,
}: {
  source: Source;
  station: Station;
  session: Session;
  act: (path: string, body: unknown) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [targets, setTargets] = useState<Station[]>([]);
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      void request<{ items: Station[] }>(
        `/admin/stations?q=${encodeURIComponent(query)}&limit=50`,
        session,
      )
        .then((data) => {
          if (active) setTargets(data.items.filter((s) => s.id !== station.id));
        })
        .catch(() => {});
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query, session, station.id]);
  return (
    <details className="source-review">
      <summary>Revisar ubicación o vinculación · {source.external_id}</summary>
      <p>
        {source.review_reason
          ? label(source.review_reason)
          : "Sin revisión pendiente"}
        . La revisión conserva las exclusiones y las series de cada origen.
      </p>
      {source.proposed_location && (
        <p>
          Posición propuesta: {source.proposed_location.latitude},{" "}
          {source.proposed_location.longitude}
        </p>
      )}
      <form
        className="admin-form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          const f = new FormData(event.currentTarget);
          void act(`/admin/sources/${source.id}/review/location`, {
            latitude: Number(f.get("latitude")),
            longitude: Number(f.get("longitude")),
            precision: f.get("precision"),
            evidence: f.get("evidence"),
          });
        }}
      >
        <label>
          Latitud
          <input
            name="latitude"
            type="number"
            step="0.000001"
            min="-90"
            max="90"
            defaultValue={
              source.proposed_location?.latitude ?? source.latitude ?? ""
            }
            required
          />
        </label>
        <label>
          Longitud
          <input
            name="longitude"
            type="number"
            step="0.000001"
            min="-180"
            max="180"
            defaultValue={
              source.proposed_location?.longitude ?? source.longitude ?? ""
            }
            required
          />
        </label>
        <label>
          Precisión
          <select name="precision" defaultValue={source.precision ?? "minute"}>
            <option value="minute">A minutos (aproximada)</option>
            <option value="exact">Exacta</option>
          </select>
        </label>
        <label>
          Evidencia de la ubicación
          <input name="evidence" minLength={3} maxLength={1000} required />
        </label>
        <button>Aprobar posición</button>
      </form>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const f = new FormData(event.currentTarget);
          void act(`/admin/sources/${source.id}/review/link`, {
            station_id: f.get("target"),
            evidence: f.get("evidence"),
          });
        }}
      >
        <h4>Vincular a una estación existente</h4>
        <label>
          Buscar destino
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <label>
          Estación de destino
          <select name="target" required>
            <option value="">Selecciona una estación</option>
            {targets.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} · {label(s.status)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Evidencia de que es la misma estación
          <input name="evidence" minLength={3} maxLength={1000} required />
        </label>
        <p>
          Al vincular a una excluida se aplica su exclusión. Al salir de una
          excluida se conserva una exclusión propia del origen.
        </p>
        <button>Confirmar vinculación</button>
      </form>
      {station.sources.length > 1 && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void act(`/admin/sources/${source.id}/review/split`, {
              evidence: new FormData(event.currentTarget).get("evidence"),
            });
          }}
        >
          <h4>Separar una vinculación errónea</h4>
          <label>
            Evidencia
            <input name="evidence" minLength={3} maxLength={1000} required />
          </label>
          <button>Separar conservando el histórico</button>
        </form>
      )}
    </details>
  );
}

function Archive({
  stationId,
  session,
}: {
  stationId: string;
  session: Session;
}) {
  const [rows, setRows] = useState<
    {
      source_id: string;
      observed_at: string;
      product: string;
      metrics: Record<string, { value: number | null; unit: string }>;
    }[]
  >([]);
  const [daily, setDaily] = useState<
    {
      source_id: string;
      period_start: string;
      period_end: string;
      period_basis: string;
      method: string;
      metrics: Record<
        string,
        {
          minimum?: number;
          maximum?: number;
          mean?: number;
          total?: number;
          unit: string;
        }
      >;
    }[]
  >([]);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setRows([]);
    setDaily([]);
    void request<{ items: typeof rows; daily_summaries?: typeof daily }>(
      `/admin/stations/${stationId}/archive?offset=${offset}`,
      session,
    )
      .then((data) => {
        if (active) {
          setRows(data.items);
          setDaily(data.daily_summaries ?? []);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [offset, stationId, session]);
  return (
    <section>
      <h4>Archivo privado conservado</h4>
      {error && <p role="alert">{error}</p>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Observación</th>
              <th>Producto / origen</th>
              <th>Valores</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>{date(r.observed_at)}</td>
                <td>
                  {r.product}
                  <br />
                  <small>{r.source_id}</small>
                </td>
                <td>
                  {Object.entries(r.metrics)
                    .map(([k, v]) => `${k}: ${number(v.value)} ${v.unit}`)
                    .join(" · ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <p>Sin observaciones en esta página.</p>}
      {daily.length > 0 && (
        <>
          <h4>Resúmenes conservados</h4>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Periodo</th>
                  <th>Origen / método</th>
                  <th>Valores</th>
                </tr>
              </thead>
              <tbody>
                {daily.map((row, i) => (
                  <tr key={i}>
                    <td>
                      {date(row.period_start)} → {date(row.period_end)}
                      <br />
                      {row.period_basis}
                    </td>
                    <td>
                      {row.source_id}
                      <br />
                      {row.method}
                    </td>
                    <td>
                      {Object.entries(row.metrics)
                        .map(
                          ([key, value]) =>
                            `${key}: mín ${number(value.minimum)}, máx ${number(value.maximum)}, media ${number(value.mean)}, total ${number(value.total)} ${value.unit}`,
                        )
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <button disabled={!offset} onClick={() => setOffset((n) => n - 100)}>
        Anteriores
      </button>
      <button
        disabled={rows.length < 100 && daily.length < 100}
        onClick={() => setOffset((n) => n + 100)}
      >
        Siguientes
      </button>
    </section>
  );
}

export default function AdminPage({ session }: { session: Session }) {
  const [tab, setTab] = useState<Tab>("stations");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [stations, setStations] = useState<Station[]>([]);
  const [total, setTotal] = useState(0);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [operations, setOperations] = useState<Operations | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [duplicates, setDuplicates] = useState<Duplicate[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirm, setConfirm] = useState<Confirmation | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    const timer = setInterval(() => {
      if (!document.hidden) setRefresh((n) => n + 1);
    }, 15000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const timer = setTimeout(() => {
      const load = async () => {
        if (["stations", "review", "excluded"].includes(tab)) {
          const data = await request<{ items: Station[]; total: number }>(
            `/admin/stations?state=${tab === "stations" ? "all" : tab}&q=${encodeURIComponent(query)}&offset=${offset}`,
            session,
          );
          if (active) {
            setStations(data.items);
            setTotal(data.total);
          }
          if (tab === "review") {
            const d = await request<{ items: Duplicate[] }>(
              "/admin/duplicates",
              session,
            );
            if (active) setDuplicates(d.items);
          }
        } else if (tab === "audit") {
          const data = await request<{ items: Audit[] }>(
            `/admin/audit?offset=${offset}`,
            session,
          );
          if (active) setAudit(data.items);
        } else {
          const [p, j] = await Promise.all([
            request<{ items: Provider[]; operations?: Operations }>("/admin/providers", session),
            request<{ items: Job[] }>(`/admin/jobs?offset=${offset}`, session),
          ]);
          if (active) {
            setProviders(p.items);
            setOperations(p.operations ?? null);
            setJobs(j.items);
          }
        }
      };
      void load()
        .catch((e) => {
          if (active) {
            setStations([]);
            setJobs([]);
            setProviders([]);
            setAudit([]);
            setDuplicates([]);
            setError(e.message);
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }, 150);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [tab, query, offset, refresh, session]);
  async function act(path: string, body: unknown) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request(path, session, body);
      setConfirm(null);
      setNotice(
        "Cambio guardado. El catálogo se ha actualizado; las restauraciones pueden conservar huecos y revisiones pendientes.",
      );
      setRefresh((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const stationTab = ["stations", "review", "excluded"].includes(tab);
  return (
    <>
      <header className="site-header">
        <a className="brand" href="/">
          Meteocentro
        </a>
        <strong>Gestión privada</strong>
        <a href="/">Volver al mapa</a>
        <span>{session.username}</span>
        <button
          onClick={() => {
            void request("/auth/logout", session, {})
              .then(() => window.dispatchEvent(new Event("session-expired")))
              .catch((e) => setError(e.message));
          }}
        >
          Salir
        </button>
      </header>
      <main className="admin-page">
        <h1>Administración</h1>
        <p className="muted">
          Control del catálogo local, sus orígenes y trabajos de recogida.
        </p>
        <nav aria-label="Administración" className="admin-tabs">
          {Object.entries(tabs).map(([key, text]) => (
            <button
              key={key}
              aria-current={tab === key ? "page" : undefined}
              onClick={() => {
                setTab(key as Tab);
                setOffset(0);
                setSelected(null);
                setNotice("");
              }}
            >
              {text}
            </button>
          ))}
        </nav>
        {error && (
          <p role="alert" className="admin-error">
            {error}
          </p>
        )}
        {notice && (
          <p role="status" className="admin-notice">
            {notice}
          </p>
        )}
        {loading && <p role="status">Actualizando…</p>}
        <fieldset disabled={busy} className="admin-content">
          {stationTab && (
            <>
              <div className="admin-toolbar">
                <label>
                  Buscar estación o ID
                  <input
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setOffset(0);
                    }}
                  />
                </label>
                <span>{total} estaciones</span>
              </div>
              {stations.map((station) => (
                <article className="admin-station" key={station.id}>
                  <div className="admin-station-heading">
                    <div>
                      <h2>
                        <button
                          className="text-button"
                          onClick={() =>
                            setSelected(
                              selected === station.id ? null : station.id,
                            )
                          }
                        >
                          {station.name}
                        </button>
                      </h2>
                      <span className="admin-badge">
                        {label(station.status)}
                      </span>
                      <p>
                        {station.sources
                          .map(
                            (s) =>
                              `${s.provider.toUpperCase()} · ${s.external_id}`,
                          )
                          .join(" / ")}
                      </p>
                    </div>
                    <button
                      className={station.status === "excluded" ? "" : "danger"}
                      onClick={() =>
                        setConfirm({
                          station,
                          restore: station.status === "excluded",
                        })
                      }
                    >
                      {station.status === "excluded"
                        ? "Restaurar"
                        : "Eliminar de la app"}
                    </button>
                  </div>
                  {station.exclusion && (
                    <p>
                      Excluida el {date(station.exclusion.created_at)} por{" "}
                      {station.exclusion.actor}.{" "}
                      {station.exclusion.reason || "Sin motivo indicado."}
                    </p>
                  )}
                  {selected === station.id && (
                    <section>
                      <p>
                        Provincia: {station.province_code ?? "Pendiente"} ·
                        Ubicación: {station.latitude ?? "—"},{" "}
                        {station.longitude ?? "—"} · Altitud:{" "}
                        {station.altitude_m ?? "—"} m
                      </p>
                      {station.sources.map((source) => (
                        <div key={source.id} className="admin-source">
                          <h3>
                            {source.provider.toUpperCase()} ·{" "}
                            {source.external_id}
                          </h3>
                          <p>
                            {source.eligible
                              ? "Origen visible y elegible para recogida."
                              : "Origen no elegible. Revisa exclusiones, ubicación o estado de la fuente."}{" "}
                            {source.review_reason &&
                              label(source.review_reason)}
                          </p>
                          <p>
                            {source.exclusion && (
                              <>
                                Excluido el {date(source.exclusion.created_at)}{" "}
                                por {source.exclusion.actor}.{" "}
                                {source.exclusion.reason ||
                                  "Sin motivo indicado."}
                                <br />
                              </>
                            )}
                            Si se pausa solo este origen, los demás orígenes
                            elegibles siguen visibles con su propia procedencia.
                          </p>
                          <button
                            onClick={() =>
                              setConfirm({
                                station,
                                source,
                                restore: source.own_exclusion,
                              })
                            }
                          >
                            {source.own_exclusion
                              ? "Restaurar origen"
                              : "Pausar solo este origen"}
                          </button>
                          <SourceReview
                            source={source}
                            station={station}
                            session={session}
                            act={act}
                          />
                        </div>
                      ))}
                      <Archive stationId={station.id} session={session} />
                    </section>
                  )}
                </article>
              ))}
              {!loading && !stations.length && (
                <p>No hay estaciones para este filtro.</p>
              )}
              <div className="admin-actions">
                <button
                  disabled={!offset}
                  onClick={() => setOffset((n) => n - 50)}
                >
                  Anteriores
                </button>
                <span>
                  {offset + 1}–{Math.min(offset + 50, total)} de {total}
                </span>
                <button
                  disabled={offset + 50 >= total}
                  onClick={() => setOffset((n) => n + 50)}
                >
                  Siguientes
                </button>
              </div>
            </>
          )}
          {tab === "review" && (
            <section>
              <h2>Posibles coincidencias entre redes</h2>
              <p>
                La cercanía no acredita que sean la misma estación. Revisa sus
                ubicaciones antes de vincularlas desde la ficha.
              </p>
              {duplicates.map((d) => (
                <form
                  className="admin-station"
                  key={d.id}
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(`/admin/duplicates/${d.id}/distinct`, {
                      evidence: new FormData(e.currentTarget).get("evidence"),
                    });
                  }}
                >
                  <p>
                    {d.source_name} / {d.other_source_name} ·{" "}
                    {number(d.distance_m)} m
                  </p>
                  <label>
                    Motivo de la revisión
                    <input
                      name="evidence"
                      minLength={3}
                      maxLength={1000}
                      required
                    />
                  </label>
                  <button>Confirmar que son distintas</button>
                </form>
              ))}
              {!duplicates.length && <p>Sin coincidencias pendientes.</p>}
            </section>
          )}
          {(tab === "providers" || tab === "discovery") && (
            <div className="admin-grid">
              {operations && (
                <article className="admin-station">
                  <h2>Recogida de datos</h2>
                  <p>{label(operations.worker.state)}</p>
                  <p>Último latido: {operations.worker.last_seen_at ? date(operations.worker.last_seen_at) : "Sin registro"}</p>
                  <p>Trabajos retrasados: {operations.overdue_jobs}</p>
                  <p>Próximo trabajo: {operations.next_job_at ? date(operations.next_job_at) : "Sin programar"}</p>
                  {operations.providers.map((p) => (
                    <p key={p.provider}>{p.provider}: {label(p.state)}. Última incorporación: {p.last_new_data_at ? date(p.last_new_data_at) : "Sin datos nuevos"}</p>
                  ))}
                </article>
              )}
              {providers.map((p) => (
                <article className="admin-station" key={p.code}>
                  <h2>{p.name}</h2>
                  <p>
                    {label(p.status)} · {label(p.credential)}
                  </p>
                  <p>
                    Última consulta:{" "}
                    {p.last_polled_at
                      ? date(p.last_polled_at)
                      : "Sin consultas"}
                    <br />
                    Edad real del dato:{" "}
                    {p.observation_age_seconds == null
                      ? "Sin dato"
                      : `${Math.floor(p.observation_age_seconds / 60)} min`}
                    <br />
                    Peticiones de hoy: {p.day_calls} /{" "}
                    {p.daily_call_budget ?? "—"}
                  </p>
                  {p.pause_reason && (
                    <p>Revisión necesaria en el servidor: {p.pause_reason}</p>
                  )}
                  <details>
                    <summary>Capacidades</summary>
                    <ul>
                      {Object.entries(p.capabilities).map(([k, v]) => (
                        <li key={k}>
                          {label(k)}: {String(v)}
                        </li>
                      ))}
                    </ul>
                  </details>
                  {tab === "discovery" && (
                    <>
                      <p>
                        La cobertura depende de los productos disponibles. Los
                        IDs sin coordenadas válidas quedan en revisión. La
                        búsqueda respeta cuotas y trabajos activos.
                      </p>
                      <button
                        disabled={p.status !== "verified"}
                        onClick={() => {
                          void act(`/admin/discovery/${p.code}`, {});
                        }}
                      >
                        Buscar nuevas estaciones · {p.name}
                      </button>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          void act(`/admin/discovery/${p.code}`, {
                            external_id: new FormData(e.currentTarget).get(
                              "external_id",
                            ),
                          });
                        }}
                      >
                        <label>
                          ID de {p.name}
                          <input
                            name="external_id"
                            maxLength={28}
                            pattern={
                              p.code === "aemet"
                                ? "[A-Za-z0-9]{1,20}"
                                : "ES[A-Z0-9]{5,22}"
                            }
                            required
                          />
                        </label>
                        <button disabled={p.status !== "verified"}>
                          Verificar e incorporar ID
                        </button>
                      </form>
                    </>
                  )}
                </article>
              ))}
            </div>
          )}
          {(tab === "jobs" || tab === "discovery") && (
            <section>
              <h2>Progreso de trabajos</h2>
              {jobs.map((j) => (
                <article key={j.id} className="admin-station">
                  <h3>
                    {j.provider.toUpperCase()} · {label(j.kind)}
                  </h3>
                  <p>
                    {label(j.status)} · Próxima ejecución: {date(j.next_run_at)}{" "}
                    · Intentos: {j.attempts}
                  </p>
                  {j.last_run && (
                    <>
                      <p>
                        Última ejecución: {label(j.last_run.status)} ·{" "}
                        {date(j.last_run.started_at)}{" "}
                        {j.last_run.error_code && `· ${j.last_run.error_code}`}
                      </p>
                      <ul>
                        {Object.entries(j.last_run.result).map(([k, v]) => (
                          <li key={k}>
                            {label(k)}:{" "}
                            {typeof v === "boolean" ? (v ? "Sí" : "No") : v}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                  {j.status === "retry" && (
                    <button
                      onClick={() => {
                        void act(`/admin/jobs/${j.id}/retry`, {});
                      }}
                    >
                      Reintentar respetando la espera
                    </button>
                  )}
                </article>
              ))}
              {!jobs.length && <p>Sin trabajos registrados.</p>}
              <button
                disabled={!offset}
                onClick={() => setOffset((n) => n - 100)}
              >
                Anteriores
              </button>
              <button
                disabled={jobs.length < 100}
                onClick={() => setOffset((n) => n + 100)}
              >
                Siguientes
              </button>
            </section>
          )}
          {tab === "audit" && (
            <section>
              <h2>Registro de cambios</h2>
              {audit.map((a) => (
                <article className="admin-station" key={a.id}>
                  <strong>{a.action}</strong>
                  <p>
                    {date(a.occurred_at)} · {a.actor}
                  </p>
                  <p>{String(a.details.reason ?? a.details.evidence ?? "")}</p>
                </article>
              ))}
              <button
                disabled={!offset}
                onClick={() => setOffset((n) => n - 100)}
              >
                Anteriores
              </button>
              <button
                disabled={audit.length < 100}
                onClick={() => setOffset((n) => n + 100)}
              >
                Siguientes
              </button>
              <hr />
              <button
                onClick={() => {
                  void request("/auth/revoke-all", session, {})
                    .then(() =>
                      window.dispatchEvent(new Event("session-expired")),
                    )
                    .catch((e) => setError(e.message));
                }}
              >
                Cerrar todas mis sesiones
              </button>
            </section>
          )}
        </fieldset>
      </main>
      {confirm && (
        <Confirm
          error={error}
          value={confirm}
          busy={busy}
          onClose={() => setConfirm(null)}
          onConfirm={(reason) => {
            void act(
              `/admin/${confirm.source ? `sources/${confirm.source.id}` : `stations/${confirm.station.id}`}/${confirm.restore ? "restore" : "exclude"}`,
              { reason },
            );
          }}
        />
      )}
    </>
  );
}
