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
const freshnessLabels = {
  fresh: 'Datos recientes',
  stale: 'Datos desactualizados',
  unknown: 'Sin observaciones',
  historical_only: 'Solo históricos',
}

function App() {
  const [stations, setStations] = useState<Station[]>([])
  const [status, setStatus] = useState('Comprobando API…')

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
        <strong>Archivo local de AEMET</strong>
        <span>Las estaciones y sus datos se muestran cuando están disponibles en el archivo. Meteoclimatic aún no está incorporado.</span>
      </section>
      <section>
        <h2>Estaciones</h2>
        <p role="status">{status}</p>
        {stations.length > 0 && <ul>{stations.map(station => <li key={station.id}>{station.name} · {station.province_code ?? 'Provincia pendiente'} · {freshnessLabels[station.freshness]}</li>)}</ul>}
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
