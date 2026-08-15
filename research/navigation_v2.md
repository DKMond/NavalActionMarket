# Navigation v2 research snapshot

Navigation v2 is a static geography layer built from the 8192x8192 Tactical Map and the 207 native tactical-map port coordinates.

## Calibration

- 1 native tactical-map pixel = 0.2 Trader Tool K.
- Terrain classes: DEEP, SHALLOW, LAND.
- Terrain mask resolution: 2048x2048, 0.8 K per cell.
- Routing graph resolution: 1024x1024, 1.6 K per cell.
- Shallow-capable routing allows DEEP + SHALLOW.
- Deep-only routing allows DEEP only.
- Wind is deliberately not baked into these distances.

## Coverage

- Ports: 207.
- Unique unordered port pairs: 21,321.
- Both shallow and deep matrices were generated.
- Port coordinates are snapped to water navigation anchors. Raw port-to-anchor offsets remain diagnostics because map port coordinates can lie on or slightly inland from the rendered coastline.

## Selected validation routes

| Route | Straight K | Shallow K | Deep K | Deep detour |
|---|---:|---:|---:|---:|
| Ragged Cay -> Bimini | 218.78 | 221.09 | 353.71 | 61.67% |
| Nassau -> Pitt's Town | 164.22 | 179.74 | 221.18 | 34.68% |
| Road Rocks -> Bimini | 75.38 | 77.21 | 153.51 | 103.63% |
| Charleston -> Nassau | 395.58 | 419.19 | 444.38 | 12.33% |
| Great Corn -> Portobelo | 197.04 | 202.37 | 202.37 | 2.70% |
| Saint John's -> Roseau | 90.92 | 91.54 | 91.54 | 0.69% |

The Bahamas cases demonstrate the expected large deep-water detour around shallow banks while shallow-capable ships can take much more direct paths.

## Serving-layer normalization

Raster snapping can occasionally produce a computed anchor-to-anchor route a few K below direct port-to-port geometry. The production lookup clamps every routed distance to at least the straight Trader K lower bound rather than mutating the research matrix.

## Manual review flags

Only two ports have deep-anchor offsets above 12 K and remain flagged for gameplay validation:

- Guacata: about 20.86 K.
- Salamanca: about 22.29 K.

These warnings do not automatically make the ports unreachable. They indicate that the rendered coastline/harbour entry needs an in-game check before a strict deep-access rule is enforced.

## Integration target

The route engine should expose:

- `traderDistanceK`
- `shallowRouteK`
- `deepRouteK`
- `shallowValid`
- `deepValid`
- `shipWaterClass`
- `routeDistanceK`
- `profitPerK`

The generated compact matrix artifact is `data/navigation_v2.csv` in the working package. Until that artifact is committed, the new lookup intentionally falls back to straight Trader K rather than breaking route generation.
