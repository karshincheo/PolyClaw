import { useState } from 'react'

import './App.css'
import { AuthScreen } from './components/AuthScreen'
import { StatusPill } from './components/StatusPill'
import type { NavSection } from './lib/types'
import { usePrototypeDashboard } from './hooks/usePrototypeDashboard'
import { OpportunitiesPage } from './pages/OpportunitiesPage'
import { PositionsPage } from './pages/PositionsPage'
import { BacktestPage } from './pages/BacktestPage'

function App() {
  const [activeSection, setActiveSection] = useState<NavSection>('opportunities')
  const dashboard = usePrototypeDashboard()

  if (!dashboard.sessionUser) {
    return (
      <AuthScreen
        error={dashboard.authError}
        onSignIn={dashboard.signIn}
        onCreateAccount={dashboard.createAccount}
      />
    )
  }

  return (
    <div className="app-shell app-shell--no-sidebar">
      <div className="workspace workspace--full">
        <header className="topbar topbar--slim">
          <span className="topbar__app-name">PolyClaw</span>

          <div className="tab-toggle">
            {([
              { id: 'opportunities' as NavSection, label: 'Opportunities', badge: dashboard.scoredOpportunities.length || null },
              { id: 'positions' as NavSection, label: 'Positions', badge: dashboard.positions.length || null },
              { id: 'backtest' as NavSection, label: 'Backtest', badge: null },
            ]).map((item) => (
              <button
                key={item.id}
                className={`tab-toggle__item ${activeSection === item.id ? 'tab-toggle__item--active' : ''}`}
                type="button"
                onClick={() => setActiveSection(item.id)}
              >
                {item.label}
                {item.badge ? <span className="tab-toggle__badge">{item.badge}</span> : null}
              </button>
            ))}
          </div>

          <div className="topbar__actions">
            <StatusPill tone={dashboard.killSwitchEnabled ? 'critical' : 'positive'}>
              {dashboard.killSwitchEnabled ? 'Kill switch ON' : 'Paper ready'}
            </StatusPill>
            <div className="user-chip">
              <strong>{dashboard.sessionUser.name}</strong>
            </div>
            <button className="button button--ghost" type="button" onClick={dashboard.signOut}>
              Sign out
            </button>
          </div>
        </header>

        <main className="workspace__content">
          {activeSection === 'opportunities' ? (
            <OpportunitiesPage
              scoredOpportunities={dashboard.scoredOpportunities}
              scoredLoading={dashboard.scoredLoading}
              paperActionBlockedReason={dashboard.paperActionBlockedReason}
              onPlaceBet={dashboard.placeScoredBet}
            />
          ) : activeSection === 'backtest' ? (
            <BacktestPage />
          ) : (
            <PositionsPage
              positions={dashboard.positions}
              portfolioSummary={dashboard.paperSummary}
              sessionUser={dashboard.sessionUser}
              actionBlockedReason={dashboard.paperActionBlockedReason}
              pausedCategories={dashboard.pausedCategories}
              onClosePosition={dashboard.closePosition}
              onResizePosition={dashboard.resizePosition}
              onMarkReview={dashboard.markPositionReview}
              onPauseCategory={dashboard.pauseCategory}
              onAddNote={dashboard.addPositionNote}
            />
          )}
        </main>
      </div>
    </div>
  )
}

export default App
