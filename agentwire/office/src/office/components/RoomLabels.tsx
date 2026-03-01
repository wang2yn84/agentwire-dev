import { useState, useEffect } from 'react'
import type { OfficeState } from '../engine/officeState.js'
import { TILE_SIZE } from '../types.js'

interface RoomLabelsProps {
  officeState: OfficeState
  containerRef: React.RefObject<HTMLDivElement | null>
  zoom: number
  panRef: React.RefObject<{ x: number; y: number }>
}

export function RoomLabels({
  officeState,
  containerRef,
  zoom,
  panRef,
}: RoomLabelsProps) {
  const [, setTick] = useState(0)
  useEffect(() => {
    // Slower tick than agent labels — room labels don't move
    let rafId = 0
    let frameCount = 0
    const tick = () => {
      frameCount++
      if (frameCount % 10 === 0) {
        setTick((n) => n + 1)
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [])

  const el = containerRef.current
  if (!el) return null
  const zones = Array.from(officeState.zones.values())
  if (zones.length === 0) return null

  const rect = el.getBoundingClientRect()
  const dpr = window.devicePixelRatio || 1
  const canvasW = Math.round(rect.width * dpr)
  const canvasH = Math.round(rect.height * dpr)
  const layout = officeState.getLayout()
  const mapW = layout.cols * TILE_SIZE * zoom
  const mapH = layout.rows * TILE_SIZE * zoom
  const deviceOffsetX = Math.floor((canvasW - mapW) / 2) + Math.round(panRef.current.x)
  const deviceOffsetY = Math.floor((canvasH - mapH) / 2) + Math.round(panRef.current.y)

  return (
    <>
      {zones.map((zone) => {
        // Skip hallways
        if (zone.type === 'hallway') return null
        if (!zone.label) return null

        // Position at top-center of zone rect
        const centerCol = zone.rect.col + zone.rect.w / 2
        const topRow = zone.rect.row
        const screenX = (deviceOffsetX + centerCol * TILE_SIZE * zoom) / dpr
        const screenY = (deviceOffsetY + topRow * TILE_SIZE * zoom) / dpr

        const isProject = zone.type === 'project'

        return (
          <div
            key={zone.id}
            style={{
              position: 'absolute',
              left: screenX,
              top: screenY - 4,
              transform: 'translateX(-50%)',
              pointerEvents: 'none',
              zIndex: 39,
            }}
          >
            <span
              style={{
                fontSize: isProject ? '18px' : '16px',
                fontWeight: isProject ? 'bold' : 'normal',
                fontStyle: isProject ? undefined : 'italic',
                color: isProject ? 'var(--pixel-accent, #4fc3f7)' : 'var(--pixel-text-dim, #888)',
                background: 'rgba(20, 20, 35, 0.75)',
                padding: '1px 6px',
                borderRadius: 2,
                whiteSpace: 'nowrap',
              }}
            >
              {zone.label}
            </span>
          </div>
        )
      })}
    </>
  )
}
