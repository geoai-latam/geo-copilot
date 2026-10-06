import { Fragment } from 'react'
import { useAgentsPipeline } from '@/hooks/useAgentsPipeline'

export function AgentsPipe() {
  const { nodes, isRunning } = useAgentsPipeline()

  const anyTouched = nodes.some((n) => n.state !== 'idle')
  if (!anyTouched && !isRunning) return null

  return (
    <div className="pipe" role="status" aria-label="pipeline de agentes">
      {nodes.map((n, i) => (
        <Fragment key={n.id}>
          <div className={`pipe-node ${n.state}`} title={n.label}>
            <span className="dot" />
            {n.short}
          </div>
          {i < nodes.length - 1 && <span className="pipe-arrow">→</span>}
        </Fragment>
      ))}
    </div>
  )
}
