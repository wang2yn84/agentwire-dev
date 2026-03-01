/**
 * Layout composer — generates a multi-room office building from project data.
 *
 * Each project gets its own room. Shared rooms (lobby, kitchen, library, meeting)
 * fill the first row. Rooms are arranged along corridors in a grid pattern.
 */

import { TileType, FurnitureType } from '../types.js'
import type { TileType as TileTypeVal, OfficeLayout, PlacedFurniture, Zone, FloorColor } from '../types.js'

// ── Room Template Definition ─────────────────────────────────────

interface RoomTemplate {
  /** Interior width (excluding walls) */
  w: number
  /** Interior height (excluding walls) */
  h: number
  /** Furniture placed at relative coordinates (0,0 = top-left interior) */
  furniture: Array<{ type: string; relCol: number; relRow: number }>
  /** Floor tile type for the room interior */
  floorTile: TileTypeVal
  /** Floor color for the room */
  floorColor: FloorColor
}

// ── Room Templates ───────────────────────────────────────────────

const PROJECT_ROOM: RoomTemplate = {
  w: 10,
  h: 8,
  furniture: [
    // Two desk clusters (2x2 desk + 4 chairs each)
    // Cluster 1: top-left
    { type: FurnitureType.DESK, relCol: 2, relRow: 2 },
    { type: FurnitureType.CHAIR, relCol: 2, relRow: 1 },
    { type: FurnitureType.CHAIR, relCol: 3, relRow: 4 },
    { type: FurnitureType.CHAIR, relCol: 1, relRow: 3 },
    { type: FurnitureType.CHAIR, relCol: 4, relRow: 2 },
    // Cluster 2: top-right
    { type: FurnitureType.DESK, relCol: 6, relRow: 2 },
    { type: FurnitureType.CHAIR, relCol: 6, relRow: 1 },
    { type: FurnitureType.CHAIR, relCol: 7, relRow: 4 },
    { type: FurnitureType.CHAIR, relCol: 5, relRow: 3 },
    { type: FurnitureType.CHAIR, relCol: 8, relRow: 2 },
    // Decor
    { type: FurnitureType.PLANT, relCol: 0, relRow: 0 },
    { type: FurnitureType.BOOKSHELF, relCol: 9, relRow: 5 },
    { type: FurnitureType.PC, relCol: 3, relRow: 2 },
    { type: FurnitureType.PC, relCol: 7, relRow: 2 },
  ],
  floorTile: TileType.FLOOR_1,
  floorColor: { h: 35, s: 30, b: 15, c: 0 },
}

const LOBBY: RoomTemplate = {
  w: 12,
  h: 8,
  furniture: [
    // Reception desk
    { type: FurnitureType.DESK, relCol: 5, relRow: 2 },
    { type: FurnitureType.CHAIR, relCol: 5, relRow: 4 },
    { type: FurnitureType.CHAIR, relCol: 6, relRow: 1 },
    // Waiting area chairs
    { type: FurnitureType.CHAIR, relCol: 1, relRow: 5 },
    { type: FurnitureType.CHAIR, relCol: 3, relRow: 5 },
    { type: FurnitureType.CHAIR, relCol: 9, relRow: 5 },
    // Decor
    { type: FurnitureType.PLANT, relCol: 0, relRow: 0 },
    { type: FurnitureType.PLANT, relCol: 11, relRow: 0 },
    { type: FurnitureType.COOLER, relCol: 10, relRow: 1 },
  ],
  floorTile: TileType.FLOOR_4,
  floorColor: { h: 35, s: 25, b: 10, c: 0 },
}

const KITCHEN: RoomTemplate = {
  w: 8,
  h: 8,
  furniture: [
    // Table (desk) with chairs
    { type: FurnitureType.DESK, relCol: 3, relRow: 3 },
    { type: FurnitureType.CHAIR, relCol: 3, relRow: 2 },
    { type: FurnitureType.CHAIR, relCol: 4, relRow: 5 },
    { type: FurnitureType.CHAIR, relCol: 2, relRow: 4 },
    { type: FurnitureType.CHAIR, relCol: 5, relRow: 3 },
    // Amenities
    { type: FurnitureType.COOLER, relCol: 7, relRow: 0 },
    { type: FurnitureType.LAMP, relCol: 0, relRow: 0 },
    { type: FurnitureType.PLANT, relCol: 0, relRow: 7 },
  ],
  floorTile: TileType.FLOOR_2,
  floorColor: { h: 25, s: 45, b: 5, c: 10 },
}

const LIBRARY: RoomTemplate = {
  w: 10,
  h: 8,
  furniture: [
    // Bookshelves along walls
    { type: FurnitureType.BOOKSHELF, relCol: 0, relRow: 0 },
    { type: FurnitureType.BOOKSHELF, relCol: 0, relRow: 2 },
    { type: FurnitureType.BOOKSHELF, relCol: 0, relRow: 4 },
    { type: FurnitureType.BOOKSHELF, relCol: 9, relRow: 0 },
    { type: FurnitureType.BOOKSHELF, relCol: 9, relRow: 2 },
    // Reading area
    { type: FurnitureType.CHAIR, relCol: 4, relRow: 3 },
    { type: FurnitureType.CHAIR, relCol: 6, relRow: 3 },
    { type: FurnitureType.CHAIR, relCol: 5, relRow: 5 },
    // Decor
    { type: FurnitureType.LAMP, relCol: 3, relRow: 1 },
    { type: FurnitureType.PLANT, relCol: 8, relRow: 7 },
  ],
  floorTile: TileType.FLOOR_3,
  floorColor: { h: 280, s: 40, b: -5, c: 0 },
}

const MEETING_ROOM: RoomTemplate = {
  w: 8,
  h: 8,
  furniture: [
    // Conference table (desk) + chairs
    { type: FurnitureType.DESK, relCol: 3, relRow: 3 },
    { type: FurnitureType.CHAIR, relCol: 3, relRow: 2 },
    { type: FurnitureType.CHAIR, relCol: 4, relRow: 5 },
    { type: FurnitureType.CHAIR, relCol: 2, relRow: 4 },
    { type: FurnitureType.CHAIR, relCol: 5, relRow: 3 },
    // Whiteboard
    { type: FurnitureType.WHITEBOARD, relCol: 3, relRow: 0 },
    // Decor
    { type: FurnitureType.PLANT, relCol: 7, relRow: 0 },
  ],
  floorTile: TileType.FLOOR_5,
  floorColor: { h: 200, s: 25, b: 5, c: 0 },
}

// ── Color Palette for Project Rooms ──────────────────────────────

const PROJECT_COLORS: FloorColor[] = [
  { h: 35, s: 30, b: 15, c: 0 },   // warm beige
  { h: 25, s: 45, b: 5, c: 10 },    // warm brown
  { h: 200, s: 25, b: 5, c: 0 },    // cool blue
  { h: 150, s: 30, b: 5, c: 0 },    // sage green
  { h: 280, s: 20, b: 5, c: 0 },    // soft purple
  { h: 15, s: 35, b: 10, c: 0 },    // terracotta
  { h: 45, s: 35, b: 10, c: 0 },    // gold
  { h: 170, s: 30, b: 5, c: 0 },    // teal
]

// ── Corridor Constants ───────────────────────────────────────────

const CORRIDOR_WIDTH = 3
const CORRIDOR_FLOOR_TILE = TileType.FLOOR_4
const CORRIDOR_COLOR: FloorColor = { h: 35, s: 25, b: 10, c: 0 }
const WALL_PADDING = 1 // wall thickness around rooms

// ── Layout Composer ──────────────────────────────────────────────

export interface ProjectInfo {
  name: string
  path: string
}

/**
 * Compose a multi-room office building layout from a list of projects.
 * Each project gets a room. Shared rooms fill the first row.
 */
export function composeOfficeLayout(
  projects: ProjectInfo[],
): OfficeLayout {
  // Shared rooms always present
  const sharedRooms: Array<{ id: string; label: string; type: Zone['type']; template: RoomTemplate }> = [
    { id: 'shared-lobby', label: 'Lobby', type: 'lobby', template: LOBBY },
    { id: 'shared-kitchen', label: 'Kitchen', type: 'shared', template: KITCHEN },
    { id: 'shared-library', label: 'Library', type: 'shared', template: LIBRARY },
    { id: 'shared-meeting', label: 'Meeting Room', type: 'shared', template: MEETING_ROOM },
  ]

  // Project rooms
  const projectRooms = projects.map((p, i) => ({
    id: `project-${p.name}`,
    label: p.name,
    type: 'project' as Zone['type'],
    template: { ...PROJECT_ROOM, floorColor: PROJECT_COLORS[i % PROJECT_COLORS.length] },
    projectPath: p.path,
  }))

  // All rooms to place
  const allRooms = [...sharedRooms, ...projectRooms]

  // Determine grid layout: rooms per row (target 3-4 rooms per row)
  const roomsPerRow = Math.min(4, Math.max(2, allRooms.length))

  // Calculate room cells: each cell is (template.w + 2*wall) wide, (template.h + 2*wall) tall
  // Plus corridors between rows and columns
  const rows: Array<typeof allRooms> = []
  for (let i = 0; i < allRooms.length; i += roomsPerRow) {
    rows.push(allRooms.slice(i, i + roomsPerRow))
  }

  // Calculate cell sizes per column and row
  const numCols = roomsPerRow
  const numRows = rows.length
  const colWidths = new Array(numCols).fill(0)
  const rowHeights = new Array(numRows).fill(0)

  for (let r = 0; r < numRows; r++) {
    for (let c = 0; c < rows[r].length; c++) {
      const t = rows[r][c].template
      const cellW = t.w + WALL_PADDING * 2
      const cellH = t.h + WALL_PADDING * 2
      if (cellW > colWidths[c]) colWidths[c] = cellW
      if (cellH > rowHeights[r]) rowHeights[r] = cellH
    }
  }

  // Total grid dimensions including corridors
  const totalW = colWidths.reduce((a, b) => a + b, 0) + (numCols - 1) * CORRIDOR_WIDTH + WALL_PADDING * 2
  const totalH = rowHeights.reduce((a, b) => a + b, 0) + (numRows - 1) * CORRIDOR_WIDTH + WALL_PADDING * 2

  // Initialize tile grid with VOID
  const tiles: TileTypeVal[] = new Array(totalW * totalH).fill(TileType.VOID)
  const tileColors: Array<FloorColor | null> = new Array(totalW * totalH).fill(null)
  const furniture: PlacedFurniture[] = []
  const zones: Zone[] = []

  // Helper: set tile at (col, row)
  const setTile = (col: number, row: number, tile: TileTypeVal, color: FloorColor | null) => {
    if (col < 0 || col >= totalW || row < 0 || row >= totalH) return
    tiles[row * totalW + col] = tile
    tileColors[row * totalW + col] = color
  }

  // Helper: compute room origin (top-left of wall boundary)
  const getRoomOrigin = (gridRow: number, gridCol: number): { x: number; y: number } => {
    let x = WALL_PADDING
    for (let c = 0; c < gridCol; c++) {
      x += colWidths[c] + CORRIDOR_WIDTH
    }
    let y = WALL_PADDING
    for (let r = 0; r < gridRow; r++) {
      y += rowHeights[r] + CORRIDOR_WIDTH
    }
    return { x, y }
  }

  // ── Place corridors ────────────────────────────────────────────

  // Horizontal corridors (between rows)
  for (let r = 0; r < numRows - 1; r++) {
    const origin = getRoomOrigin(r, 0)
    const corridorY = origin.y + rowHeights[r]
    for (let cy = 0; cy < CORRIDOR_WIDTH; cy++) {
      for (let cx = 0; cx < totalW - WALL_PADDING * 2; cx++) {
        setTile(WALL_PADDING + cx, corridorY + cy, CORRIDOR_FLOOR_TILE, CORRIDOR_COLOR)
      }
    }
    // Create hallway zone
    zones.push({
      id: `hallway-h-${r}`,
      label: '',
      type: 'hallway',
      rect: { col: WALL_PADDING, row: corridorY, w: totalW - WALL_PADDING * 2, h: CORRIDOR_WIDTH },
      seatUids: [],
    })
  }

  // Vertical corridors (between columns)
  for (let c = 0; c < numCols - 1; c++) {
    const origin = getRoomOrigin(0, c)
    const corridorX = origin.x + colWidths[c]
    for (let cy = 0; cy < totalH - WALL_PADDING * 2; cy++) {
      for (let cx = 0; cx < CORRIDOR_WIDTH; cx++) {
        setTile(corridorX + cx, WALL_PADDING + cy, CORRIDOR_FLOOR_TILE, CORRIDOR_COLOR)
      }
    }
    zones.push({
      id: `hallway-v-${c}`,
      label: '',
      type: 'hallway',
      rect: { col: corridorX, row: WALL_PADDING, w: CORRIDOR_WIDTH, h: totalH - WALL_PADDING * 2 },
      seatUids: [],
    })
  }

  // ── Place rooms ────────────────────────────────────────────────

  let uidCounter = 0
  const uid = (prefix: string) => `${prefix}-${uidCounter++}`

  for (let r = 0; r < numRows; r++) {
    for (let c = 0; c < rows[r].length; c++) {
      const room = rows[r][c]
      const template = room.template
      const origin = getRoomOrigin(r, c)

      // Interior origin (inside walls)
      const intX = origin.x + WALL_PADDING
      const intY = origin.y + WALL_PADDING

      // Draw walls (full cell boundary)
      for (let wy = 0; wy < rowHeights[r]; wy++) {
        for (let wx = 0; wx < colWidths[c]; wx++) {
          setTile(origin.x + wx, origin.y + wy, TileType.WALL, null)
        }
      }

      // Draw floor (interior)
      for (let fy = 0; fy < template.h; fy++) {
        for (let fx = 0; fx < template.w; fx++) {
          setTile(intX + fx, intY + fy, template.floorTile, template.floorColor)
        }
      }

      // Add doorways — openings in the walls connecting to corridors
      // Bottom doorway (connecting to horizontal corridor below)
      if (r < numRows - 1) {
        const doorX = intX + Math.floor(template.w / 2)
        // Open wall and corridor tiles for doorway
        for (let d = -1; d <= 0; d++) {
          setTile(doorX + d, origin.y + rowHeights[r] - 1, template.floorTile, template.floorColor)
          setTile(doorX + d, origin.y + rowHeights[r], CORRIDOR_FLOOR_TILE, CORRIDOR_COLOR)
        }
      }
      // Top doorway (connecting to horizontal corridor above)
      if (r > 0) {
        const doorX = intX + Math.floor(template.w / 2)
        for (let d = -1; d <= 0; d++) {
          setTile(doorX + d, origin.y, template.floorTile, template.floorColor)
          setTile(doorX + d, origin.y - 1, CORRIDOR_FLOOR_TILE, CORRIDOR_COLOR)
        }
      }
      // Right doorway (connecting to vertical corridor right)
      if (c < numCols - 1) {
        const doorY = intY + Math.floor(template.h / 2)
        for (let d = -1; d <= 0; d++) {
          setTile(origin.x + colWidths[c] - 1, doorY + d, template.floorTile, template.floorColor)
          setTile(origin.x + colWidths[c], doorY + d, CORRIDOR_FLOOR_TILE, CORRIDOR_COLOR)
        }
      }
      // Left doorway (connecting to vertical corridor left)
      if (c > 0) {
        const doorY = intY + Math.floor(template.h / 2)
        for (let d = -1; d <= 0; d++) {
          setTile(origin.x, doorY + d, template.floorTile, template.floorColor)
          setTile(origin.x - 1, doorY + d, CORRIDOR_FLOOR_TILE, CORRIDOR_COLOR)
        }
      }

      // Place furniture
      const seatUids: string[] = []
      for (const f of template.furniture) {
        const furnUid = uid(f.type)
        furniture.push({
          uid: furnUid,
          type: f.type,
          col: intX + f.relCol,
          row: intY + f.relRow,
        })
        // Track chair UIDs for zone seat mapping
        if (f.type === FurnitureType.CHAIR) {
          seatUids.push(furnUid)
        }
      }

      // Create zone
      zones.push({
        id: room.id,
        label: room.label,
        type: room.type,
        rect: { col: intX, row: intY, w: template.w, h: template.h },
        seatUids,
        ...('projectPath' in room ? { projectPath: (room as { projectPath: string }).projectPath } : {}),
      })
    }
  }

  return {
    version: 1,
    cols: totalW,
    rows: totalH,
    tiles,
    tileColors,
    furniture,
    zones,
  }
}
