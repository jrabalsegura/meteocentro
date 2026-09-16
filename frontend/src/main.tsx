import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

type Station = {
  id: string
  name: string
  province_code: string | null
  freshness: 'fresh' | 'stale' | 'unknown' | 'historical_only'
}

type StationPage = { items: Station[]; total: number }
type Provider = { code: string; name: string; status: string; attribution: string; license_url: string | null }
const providerLabels: Record<string, string> = {
  verified: 'Habilitada', paused: 'Recogida pausada', disabled: 'Deshabilitada',
  pending_terms: 'Pendiente de permiso de uso', pending_access: 'Pendiente de acceso',
}
const freshnessLabels = {
  fresh: 'Datos recientes',
  stale: 'Datos desactualizados',
  unknown: 'Sin observaciones',
  historical_only: 'Solo históricos',
}

function App() {
  const [stations, setStations] = useState<Station[]>([])
  const [status, setStatus] = useState('Comprobando API…')
  const [providers, setProviders] = useState<Provider[]>([])

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const ready = await fetch('/health/ready', { signal: controller.signal })
        if (!ready.ok) throw new Error('La base de datos o el esquema no están listos')
        const response = await fetch('/api/v1/stations?limit=50', { signal: controller.signal })
        if (!response.ok) throw new Error('No se pudo consultar la lista de estaciones')
        const page = (await response.json()) as StationPage
        setStations(page.items)
        setStatus(`${page.total} estaciones publicables en la base local`)
        const networks = await fetch('/api/v1/providers', { signal: controller.signal })
        if (networks.ok) setProviders(((await networks.json()) as { items: Provider[] }).items)
      } catch (error) {
        if (!controller.signal.aborted) setStatus(error instanceof Error ? error.message : 'Error de conexión')
      }
    }
    void load()
    return () => controller.abort()
  }, [])

  return (
    <main>
      <header>
        <div className="eyebrow">Madrid · Ávila · Segovia · Guadalajara</div>
        <h1>Meteocentro</h1>
        <p>Estaciones de las cuatro provincias</p>
      </header>
      <section className="notice" aria-label="Estado de los datos">
        <strong>Archivo local de observaciones</strong>
        <span>Las estaciones y sus datos se muestran cuando están disponibles y autorizados. Una red pausada conserva su archivo; sus datos pueden estar desactualizados.</span>
        {providers.length > 0 && <ul>{providers.map(provider => <li key={provider.code}>{provider.name}: {providerLabels[provider.status] ?? provider.status}</li>)}</ul>}
      </section>
      <section>
        <h2>Estaciones</h2>
        <p role="status">{status}</p>
        {stations.length > 0 && <ul>{stations.map(station => <li key={station.id}>{station.name} · {station.province_code ?? 'Provincia pendiente'} · {freshnessLabels[station.freshness]}</li>)}</ul>}
      </section>
      <footer>{providers.filter(provider => provider.code === 'meteoclimatic').map(provider => <p key={provider.code}>
        Datos de <a href="https://www.meteoclimatic.net/">{provider.attribution}</a> · <a href={provider.license_url ?? 'https://creativecommons.org/licenses/by-nc-nd/3.0/'}>CC BY-NC-ND 3.0</a>. Coordenadas aproximadas a minutos.
      </p>)}</footer>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
