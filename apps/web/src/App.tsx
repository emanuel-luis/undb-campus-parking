import { Link, NavLink, Outlet, Route, Routes } from 'react-router'
import { NOTICE } from './components/ui'
import { LotDetailPage } from './pages/LotDetailPage'
import { LotsPage } from './pages/LotsPage'
import { OperatorPage } from './pages/OperatorPage'
import { ReportsPage } from './pages/ReportsPage'

function Layout() {
  return (
    <>
      <a className="skip-link" href="#conteudo">
        Pular para o conteúdo
      </a>
      <header className="site-header">
        <Link to="/" className="brand">
          <img src="/brand/2026-06-logo-branca.png" alt="UNDB" width={120} height={36} />
          <span>Vagas</span>
        </Link>
        <nav aria-label="Principal">
          <NavLink to="/" end>
            Vagas
          </NavLink>
          <NavLink to="/relatorios">Relatórios</NavLink>
          <NavLink to="/operador">Operador</NavLink>
        </nav>
      </header>
      <main id="conteudo" tabIndex={-1}>
        <Outlet />
      </main>
      <footer className="site-footer">
        <p>{NOTICE}</p>
      </footer>
    </>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<LotsPage />} />
        <Route path="estacionamentos/:lotId" element={<LotDetailPage />} />
        <Route path="relatorios" element={<ReportsPage />} />
        <Route path="operador" element={<OperatorPage />} />
        <Route
          path="*"
          element={
            <section>
              <h1>Página não encontrada</h1>
              <Link to="/">Voltar para Vagas</Link>
            </section>
          }
        />
      </Route>
    </Routes>
  )
}
